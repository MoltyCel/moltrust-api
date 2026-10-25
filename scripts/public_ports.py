#!/usr/bin/env python3
"""TCP ports that listen on an address reachable from outside the host.

Reads `ss -tlnH` on stdin and prints one port per line, sorted, for every
listener whose local address is not loopback.

Why (ops SEC-1025-SRV, 25.10.2026): security_check.sh compared every listening
port against EXPECTED_PORTS and reported "Unexpected ports open: 3006". Port
3006 is the MolCheck verifier, bound to 127.0.0.1 since 20.10.2026 and closed
from outside. A port that only the host itself can reach is not open in the
sense the report means, so only non-loopback listeners are judged.
"""
import ipaddress
import sys


def _host(local: str) -> str:
    """'127.0.0.53%lo:53' -> '127.0.0.53', '[::1]:5432' -> '::1', '*:22' -> '*'."""
    host = local.rsplit(":", 1)[0]
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host.split("%", 1)[0]


def is_loopback(local: str) -> bool:
    host = _host(local)
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False        # '*' and anything unparsable count as reachable


def public_ports(lines) -> list[int]:
    ports = set()
    for line in lines:
        cols = line.split()
        if len(cols) < 4 or cols[0] != "LISTEN":
            continue
        local = cols[3]
        port = local.rsplit(":", 1)[-1]
        if not port.isdigit() or is_loopback(local):
            continue
        ports.add(int(port))
    return sorted(ports)


if __name__ == "__main__":
    for p in public_ports(sys.stdin):
        print(p)
