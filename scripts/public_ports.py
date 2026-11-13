#!/usr/bin/env python3
"""TCP ports that listen on an address reachable from outside the host.

Reads `ss -tlnH` on stdin and prints one port per line, sorted, for every
listener whose local address is not internal.

Why (ops SEC-1025-SRV, 25.10.2026): security_check.sh compared every listening
port against EXPECTED_PORTS and reported "Unexpected ports open: 3006". Port
3006 is the MolCheck verifier, bound to 127.0.0.1 since 20.10.2026 and closed
from outside. A port that only the host itself can reach is not open in the
sense the report means, so only listeners on reachable addresses are judged.

Why the tailnet counts as internal (ops PORT-1108, 08.11.2026): the report
called 4000 (litellm on 100.110.191.8), 34173 (tailscaled on
100.110.191.8%tailscale0) and 36318 (tailscaled on
[fd7a:115c:a1e0::1e39:bf09]%tailscale0) reachable from outside. All three are
reachable only inside the tailnet. The last one also exposed a parsing bug:
brackets were only stripped when the host ended on "]", so with a %interface
suffix the address never parsed and fell into the "unparsable = reachable"
branch.
"""
import ipaddress
import sys

# Loopback plus the Tailscale CGNAT range and the Tailscale IPv6 ULA prefix.
INTERNAL_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8",
    "::1/128",
    "100.64.0.0/10",
    "fd7a:115c:a1e0::/48",
)]
# A listener bound to one of these devices is reachable only through it.
INTERNAL_IFACES = {"lo", "tailscale0"}


def _split(local: str) -> tuple[str, str]:
    """Local address column -> (host, interface).

    '127.0.0.53%lo:53' -> ('127.0.0.53', 'lo'), '[::1]:5432' -> ('::1', ''),
    '[fd7a::1]%tailscale0:36318' -> ('fd7a::1', 'tailscale0'), '*:22' -> ('*', '').
    The interface is cut off first, then the brackets, so both the
    '[addr]%if' form of ss and the '[addr%if]' form come out clean.
    """
    host = local.rsplit(":", 1)[0]
    iface = ""
    if "%" in host:
        host, iface = host.split("%", 1)
    if host.startswith("["):
        host = host[1:]
    if host.endswith("]"):
        host = host[:-1]
    return host, iface.rstrip("]")


def is_internal(local: str) -> bool:
    host, iface = _split(local)
    if iface in INTERNAL_IFACES:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False        # '*' and anything unparsable count as reachable
    return any(addr in net for net in INTERNAL_NETS)


def public_ports(lines) -> list[int]:
    ports = set()
    for line in lines:
        cols = line.split()
        if len(cols) < 4 or cols[0] != "LISTEN":
            continue
        local = cols[3]
        port = local.rsplit(":", 1)[-1]
        if not port.isdigit() or is_internal(local):
            continue
        ports.add(int(port))
    return sorted(ports)


if __name__ == "__main__":
    for p in public_ports(sys.stdin):
        print(p)
