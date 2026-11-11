#!/usr/bin/env python3
"""HTTP error counts for the security report, with own and foreign callers apart.

Reads `endpoint|status|ip|count` rows (psql -A -F'|') for the last 24 h on
stdin and prints report lines starting with ALERT or INFO.

Why (ops SEC-1025-API, 25.10.2026): the report of 25.10. alerted on 402, 429
and 401. A 402 is the product asking for payment. The 1 208 answers 429 on
/x402/verify in eight days all went to our own host: the hourly invariant
c-no-endpoint-affirms-a-revoked-did asking about every revoked DID in one go.
The 401 on /admin/me came from one foreign /24 running a pentest. Neither is
an error of ours, and a report that alerts on them every week is skimmed.

- 402 is not counted.
- 401 and 429 are listed as INFO above the threshold, with own/foreign split
  and the three busiest foreign /24.
- Any other 4xx alerts only when the foreign callers alone pass the
  threshold. Our own checks call routes the wrong way on purpose (on 25.10.
  moltrust-artefact-check sent 50 GET/HEAD a day to each of four POST routes
  and got 405); that is listed as INFO.
- A 5xx above the threshold always alerts.

request_log keeps addresses cut to /24, so "own" is the server's /24
(46.225.175.0/24, api.moltrust.ch is .218). A neighbour in the same Hetzner
/24 would count as own; the line still shows the count. Private addresses
(RFC 1918) are own as well: no request from the internet arrives with one,
and on 25.10. the docker bridge 172.17.0.0/24 sent 26 of the 52 probes of
/identity/resolve/did:moltrust:0000000000000000.
"""
from __future__ import annotations

import ipaddress
import sys
from collections import defaultdict

THRESHOLD = 20          # more than this many in 24 h, as before
NOT_ERRORS = {402}
INFO_ONLY = {401, 429}
OWN_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "::1/128", "46.225.175.0/24",
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")]
MAX_LINES = 5


def net24(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip or "?"
    if addr.version == 4:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    return str(ipaddress.ip_network(f"{ip}/48", strict=False))


def is_own(ip: str, nets=OWN_NETS) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in nets if n.version == addr.version)


def parse(lines):
    for line in lines:
        parts = line.rstrip("\n").split("|")
        if len(parts) != 4 or not parts[3].strip().isdigit():
            continue
        endpoint, status, ip, count = parts
        if not status.strip().isdigit():
            continue
        yield endpoint, int(status), ip.strip(), int(count)


def report(rows, own=is_own) -> list[str]:
    groups: dict[tuple[str, int], dict] = defaultdict(
        lambda: {"own": 0, "foreign": 0, "nets": defaultdict(int)})
    for endpoint, status, ip, count in rows:
        if status in NOT_ERRORS:
            continue
        g = groups[(endpoint, status)]
        if own(ip):
            g["own"] += count
        else:
            g["foreign"] += count
            g["nets"][net24(ip)] += count
    ranked = sorted(groups.items(), key=lambda kv: -(kv[1]["own"] + kv[1]["foreign"]))
    alerts: list[str] = []
    infos: list[str] = []
    for (endpoint, status), g in ranked:
        total = g["own"] + g["foreign"]
        if total <= THRESHOLD:
            continue
        top = sorted(g["nets"].items(), key=lambda kv: -kv[1])[:3]
        src = ", ".join(f"{n} {c}" for n, c in top) or "none"
        text = (f"{endpoint} {total}x HTTP {status} in 24h "
                f"(own {g['own']}, foreign {g['foreign']}; top foreign: {src})")
        if status >= 500:
            alerting = True
        elif status in INFO_ONLY:
            alerting = False
        else:
            alerting = g["foreign"] > THRESHOLD
        # Counted apart, so five busy INFO lines cannot push an ALERT out.
        (alerts if alerting else infos).append(("ALERT " if alerting else "INFO ") + text)
    return alerts[:MAX_LINES] + infos[:MAX_LINES]


if __name__ == "__main__":
    for line in report(parse(sys.stdin)):
        print(line)
