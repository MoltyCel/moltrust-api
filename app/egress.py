"""Outbound HTTPS to hosts named by someone else.

A did:web identifier, a revocation_check template and a delegator_aae_uri all
name a host the caller chose. Whatever host is named, this server connects to
it from inside our network with our source address, so each of them is a
server-side request forgery surface by construction. This module is the one
path such requests take.

The rules, in the order they apply:

  scheme      https only, before anything else is looked at.
  host        resolved once, and every returned address is checked. A name
              with one public and one loopback address is refused, not tried
              on its public half.
  address     the connection is made to an address that passed the check,
              and the certificate and Host header still carry the name. A
              second lookup between check and connect is the DNS-rebinding
              hole, and there is no second lookup here.
  redirects   followed up to MAX_REDIRECTS, and every target goes through the
              same rules as the first URL: scheme, host, address.
  size        the body is read up to a cap; one byte more is a failure, not a
              truncated document.
  time        one deadline for the whole fetch, redirects included.

Written against http.client because the connection has to go to a checked
address while TLS verifies the name. A socket is opened to the address and
wrapped with server_hostname set to the name; setting HTTPSConnection.host to
the name instead would make http.client resolve it again on connect.

Standard library only.
"""
from __future__ import annotations

import asyncio
import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse
from dataclasses import dataclass, field

MAX_REDIRECTS = 3
USER_AGENT = "moltrust-egress/1.0 (+https://moltrust.ch)"

# Named because a resolver answer is not the only way to reach them: a host
# file, a search domain or a split-horizon DNS can map these anywhere.
BANNED_HOST_NAMES = frozenset({
    "localhost",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
    "instance-data",
    "instance-data.ec2.internal",
})

# ADR-D3 v3 D-2 and its implementation checklist, plus the translation
# prefixes that carry an IPv4 address inside an IPv6 one. The flag checks in
# address_is_public cover most of this already; the list states it.
BANNED_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8",        # "this network"
    "10.0.0.0/8",       # RFC 1918
    "100.64.0.0/10",    # CGNAT, RFC 6598
    "127.0.0.0/8",      # loopback
    "169.254.0.0/16",   # link-local, cloud metadata at 169.254.169.254
    "172.16.0.0/12",    # RFC 1918
    "192.0.0.0/24",     # IETF protocol assignments
    "192.168.0.0/16",   # RFC 1918
    "198.18.0.0/15",    # benchmarking
    "224.0.0.0/4",      # multicast
    "240.0.0.0/4",      # reserved, includes 255.255.255.255
    "::/128",           # unspecified
    "::1/128",          # loopback
    "::ffff:0:0/96",    # IPv4-mapped
    "64:ff9b::/96",     # NAT64, RFC 6052
    "64:ff9b:1::/48",   # local-use NAT64, RFC 8215
    "2002::/16",        # 6to4
    "fc00::/7",         # unique local, includes AWS fd00:ec2::254
    "fe80::/10",        # link-local
    "ff00::/8",         # multicast
))


class EgressError(Exception):
    """Base class. Callers treat every subclass as "no usable answer"."""


class EgressRefused(EgressError):
    """The target broke a rule; no connection was made to it."""


class EgressFailed(EgressError):
    """A connection was attempted and produced no usable answer."""


@dataclass
class Response:
    status: int
    body: bytes
    url: str
    headers: dict = field(default_factory=dict)
    connected_to: list = field(default_factory=list)


def address_is_public(ip: str) -> bool:
    """True only for ordinary public unicast."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if any(addr in net for net in BANNED_NETWORKS if net.version == addr.version):
        return False
    if (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_multicast or addr.is_reserved or addr.is_unspecified):
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.teredo is not None:
        return False
    return addr.is_global


def resolve_and_check(host: str, port: int) -> list[str]:
    """Resolve `host` once and return its addresses, or refuse.

    Every address is checked, not only the first. A literal address skips DNS
    but not the check.
    """
    name = host.lower().rstrip(".")
    if not name:
        raise EgressRefused("no host")
    if name in BANNED_HOST_NAMES:
        raise EgressRefused(f"host {name!r} is not allowed")
    try:
        literal = ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not address_is_public(str(literal)):
            raise EgressRefused(f"address {literal} is not public")
        return [str(literal)]
    try:
        infos = socket.getaddrinfo(name, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise EgressFailed(f"host does not resolve: {exc}") from exc
    addresses: list[str] = []
    for info in infos:
        ip = info[4][0].split("%", 1)[0]
        if not address_is_public(ip):
            raise EgressRefused(f"host {name!r} resolves to non-public address {ip}")
        if ip not in addresses:
            addresses.append(ip)
    if not addresses:
        raise EgressFailed("host resolves to nothing")
    return addresses


def check_url(url: str) -> tuple[str, int, str, list[str]]:
    """Validate a URL and resolve its host: (host, port, path, addresses)."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise EgressRefused("url missing or too long")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise EgressRefused(f"scheme {parts.scheme!r} is not allowed, https only")
    if parts.username or parts.password:
        raise EgressRefused("url carries credentials")
    host = parts.hostname
    if not host:
        raise EgressRefused("url has no host")
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise EgressRefused(f"bad port: {exc}") from exc
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return host.lower().rstrip("."), port, path, resolve_and_check(host, port)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TCP to the checked address, TLS and Host header for the name."""

    def __init__(self, host: str, address: str, port: int, *, timeout: float,
                 context: ssl.SSLContext):
        super().__init__(host, port, timeout=timeout, context=context)
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


_CONNECTION = _PinnedHTTPSConnection  # replaced in tests


def _once(host: str, port: int, path: str, address: str, *, accept: str,
          max_bytes: int, timeout: float, context: ssl.SSLContext):
    host_header = host if port == 443 else f"{host}:{port}"
    if ":" in host and not host.startswith("["):
        host_header = f"[{host}]" if port == 443 else f"[{host}]:{port}"
    conn = _CONNECTION(host, address, port, timeout=timeout, context=context)
    try:
        conn.request("GET", path, headers={
            "Host": host_header, "Accept": accept, "User-Agent": USER_AGENT,
        })
        resp = conn.getresponse()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        if 300 <= resp.status < 400:
            return resp.status, headers, b""
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise EgressFailed(f"response declares {declared} bytes, cap {max_bytes}")
        body = resp.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise EgressFailed(f"response exceeds {max_bytes} bytes")
        return resp.status, headers, body
    finally:
        conn.close()


def fetch(url: str, *, accept: str = "application/json", max_bytes: int = 256 * 1024,
          timeout: float = 3.0, max_redirects: int = MAX_REDIRECTS) -> Response:
    """GET `url` under every rule in the module docstring.

    Raises EgressRefused when a rule refuses the URL or a redirect target,
    EgressFailed when the request ran and gave no usable answer. Any status
    other than a redirect is returned; deciding what a 404 means is the
    caller's business.
    """
    deadline = time.monotonic() + timeout
    context = ssl.create_default_context()
    connected: list[str] = []
    current = url
    for _hop in range(max_redirects + 1):
        host, port, path, addresses = check_url(current)
        last_error: Exception | None = None
        result = None
        for address in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EgressFailed("deadline exceeded")
            try:
                connected.append(address)
                result = _once(host, port, path, address, accept=accept,
                               max_bytes=max_bytes, timeout=remaining, context=context)
                break
            except EgressError:
                raise
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                last_error = exc
        if result is None:
            raise EgressFailed(f"no address answered: {last_error.__class__.__name__}: {last_error}")
        status, headers, body = result
        if 300 <= status < 400:
            location = headers.get("location")
            if not location:
                raise EgressFailed(f"HTTP {status} without Location")
            current = urllib.parse.urljoin(current, location)
            continue
        return Response(status=status, body=body, url=current, headers=headers,
                        connected_to=connected)
    raise EgressRefused(f"more than {max_redirects} redirects")


async def afetch(url: str, **kwargs) -> Response:
    """fetch() off the event loop."""
    return await asyncio.to_thread(fetch, url, **kwargs)
