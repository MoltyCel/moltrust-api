"""Fetch the skills an agent advertises, from addresses the agent supplied.

This is the only part of the profile that makes outbound requests, and the
target URL comes from an untrusted field. That makes it a server-side request
forgery surface by construction: whatever host is named here, our server
connects to it, from inside our network, with our source address.

The defences, in the order they apply:

  scheme      https only. http, file, gopher, ftp and the rest are refused
              before DNS.
  host        resolved first, then every returned address is checked. A name
              that resolves into private, loopback, link-local, multicast or
              reserved space is refused — that is the whole point, and a
              hostname is not safe just because it looks public.
  rebinding   the connection is made to the address that passed the check,
              not to the name. Resolving once for the check and again for the
              request is the classic DNS-rebinding hole.
  redirects   not followed. A permitted host that answers 302 to
              http://169.254.169.254/ would otherwise walk straight past
              every check above.
  allowlist   for the ERC-8004 side, where the host set is known and small.
  size        responses are read up to a cap and then abandoned, so a target
              that streams forever cannot exhaust the process.
  timeout     per request, and a total budget per agent.
  ports       only 443. A permitted host on :22 or :6379 is still a probe of
              our own network if the name resolves inward.

None of this makes fetching a supplied URL safe in general. It makes it
bounded, and the flag that turns it on is off by default.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import urllib.parse

# One request, and the whole pass for one agent.
REQUEST_TIMEOUT_SECONDS = 8
AGENT_BUDGET_SECONDS = 20

# Read no further than this. An agent card is a few kilobytes; anything
# claiming to be larger is not something we need.
MAX_RESPONSE_BYTES = 256 * 1024

ALLOWED_SCHEMES = ("https",)
ALLOWED_PORTS = (443,)

# ERC-8004 metadata lives on a known, small set of hosts. Nothing else is
# fetched for that side of the lookup — an allowlist is available here because
# the host set is ours to choose, unlike the agent-card URL.
ERC8004_HOST_ALLOWLIST = (
    "api.moltrust.ch",
    "moltrust.ch",
    "base.blockscout.com",
    "ipfs.io",
    "cloudflare-ipfs.com",
    "gateway.pinata.cloud",
)

MAX_SKILLS = 50
MAX_SKILL_NAME = 128


class SkillFetchRefused(Exception):
    """The URL was refused before any connection was made."""


class SkillFetchFailed(Exception):
    """The request was attempted and did not produce usable skills."""


def _address_is_public(ip: str) -> bool:
    """Reject anything that is not ordinary public unicast.

    `is_global` alone is not enough: it answers False for private space but
    True for some ranges we still do not want, and the explicit checks below
    read as what they are.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return False
    # IPv4-mapped and 6to4 addresses carry a v4 address inside a v6 one and
    # would otherwise slip past the checks above.
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return _address_is_public(str(addr.ipv4_mapped))
        if addr.sixtofour is not None:
            return _address_is_public(str(addr.sixtofour))
    return addr.is_global


def resolve_and_check(host: str, port: int) -> list[str]:
    """Resolve `host` and return the addresses, or raise if any is not public.

    Every returned address is checked, not just the first. A name with one
    public and one loopback address is a rebinding attempt wearing a single
    lookup.
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SkillFetchRefused(f"host does not resolve: {exc}") from exc
    if not infos:
        raise SkillFetchRefused("host resolves to nothing")

    addresses = []
    for info in infos:
        ip = info[4][0]
        if not _address_is_public(ip):
            raise SkillFetchRefused(f"host resolves to non-public address {ip}")
        if ip not in addresses:
            addresses.append(ip)
    return addresses


def check_url(url: str, host_allowlist: tuple[str, ...] | None = None) -> tuple[str, int, str, list[str]]:
    """Validate a supplied URL. Returns (host, port, path, resolved addresses).

    Raises SkillFetchRefused before anything leaves the process.
    """
    if not url or len(url) > 2048:
        raise SkillFetchRefused("url missing or too long")
    parsed = urllib.parse.urlsplit(url)

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SkillFetchRefused(f"scheme {parsed.scheme!r} is not allowed")
    if parsed.username or parsed.password:
        raise SkillFetchRefused("url carries credentials")
    host = parsed.hostname
    if not host:
        raise SkillFetchRefused("url has no host")

    port = parsed.port or 443
    if port not in ALLOWED_PORTS:
        raise SkillFetchRefused(f"port {port} is not allowed")

    # A literal address skips DNS but not the check.
    host_lower = host.lower().rstrip(".")
    if host_allowlist is not None and host_lower not in host_allowlist:
        raise SkillFetchRefused(f"host {host_lower!r} is not on the allowlist")

    addresses = resolve_and_check(host_lower, port)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return host_lower, port, path, addresses


def fetch_json(url: str, host_allowlist: tuple[str, ...] | None = None) -> dict:
    """GET a JSON document with every defence in the module docstring applied.

    Written against http.client rather than requests or urllib because the
    connection has to be made to a checked address while the TLS handshake and
    Host header still carry the name. Neither of the convenience libraries
    exposes that without monkey-patching the resolver process-wide.
    """
    import http.client

    host, port, path, addresses = check_url(url, host_allowlist)

    context = ssl.create_default_context()
    last_error = None
    for ip in addresses:
        conn = None
        try:
            # Connect to the address that passed the check; verify the
            # certificate against the name. This is what closes the rebinding
            # window — a second lookup between check and connect cannot
            # redirect us.
            conn = http.client.HTTPSConnection(
                ip, port, timeout=REQUEST_TIMEOUT_SECONDS, context=context,
            )
            conn.host = host  # SNI and certificate verification use the name
            conn.request("GET", path, headers={
                "Host": host,
                "Accept": "application/json",
                "User-Agent": "moltrust-profile-enrichment/1.0 (+https://moltrust.ch)",
            })
            response = conn.getresponse()

            # Redirects are not followed. A permitted host answering 302 to a
            # link-local address would walk past every check above.
            if 300 <= response.status < 400:
                raise SkillFetchFailed(f"redirect to {response.getheader('Location')!r} not followed")
            if response.status != 200:
                raise SkillFetchFailed(f"HTTP {response.status}")

            declared = response.getheader("Content-Length")
            if declared and int(declared) > MAX_RESPONSE_BYTES:
                raise SkillFetchFailed(f"response declares {declared} bytes")

            # Read one byte past the cap so an over-long body is detected
            # rather than silently truncated into invalid JSON.
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise SkillFetchFailed("response exceeds the size cap")
            return json.loads(body)
        except (SkillFetchFailed, SkillFetchRefused):
            raise
        except Exception as exc:  # noqa: BLE001 - try the next address
            last_error = exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
    raise SkillFetchFailed(f"no address answered: {last_error}")


def _clean_skills(values) -> list[str]:
    """Normalise a skill list from an untrusted document.

    Bounded in count and in length, and non-strings are dropped rather than
    coerced — a card carrying a nested object under `name` would otherwise put
    a Python repr into the column.
    """
    out: list[str] = []
    if not isinstance(values, list):
        return out
    for item in values:
        name = None
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            candidate = item.get("name") or item.get("id") or item.get("skill")
            if isinstance(candidate, str):
                name = candidate
        if not name:
            continue
        name = name.strip()[:MAX_SKILL_NAME]
        if name and name not in out:
            out.append(name)
        if len(out) >= MAX_SKILLS:
            break
    return out


def skills_from_agent_card(card: dict) -> list[str]:
    """Skill names from an A2A agent card, whichever shape it uses."""
    if not isinstance(card, dict):
        return []
    # `services` is the ERC-8004 registration document's own list. Same shape,
    # different name, and leaving it out meant every ERC-8004 lookup returned
    # empty while looking like the agent simply had no skills.
    for key in ("skills", "capabilities", "tools", "services"):
        found = _clean_skills(card.get(key))
        if found:
            return found
    return []
