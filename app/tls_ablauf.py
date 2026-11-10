"""Days left on the TLS certificates of moltrust.ch and api.moltrust.ch.

One reader for the admin dashboard card "SSL Days" and the expiry warning in
scripts/security_check.sh. Origin (ops SSL-1110, Lars 10.11.2026): the card
showed "?" because the old reader in app/main.py called `_dt.strptime` on the
datetime module, raised AttributeError and swallowed it; it also read only
moltrust.ch. The weekly warning read only moltrust.ch as well.

    python -m app.tls_ablauf --min-tage 14

prints one line per host for the shell:

    OK <host> <days> <notAfter>       valid for at least --min-tage days
    KURZ <host> <days> <notAfter>     fewer days left than --min-tage
    FEHLT <host> <reason>             certificate could not be read
"""
from __future__ import annotations

import argparse
import datetime as dt
import socket
import ssl
import sys

HOSTS = ("moltrust.ch", "api.moltrust.ch")
TIMEOUT_S = 5


def ablauf(host: str, port: int = 443, timeout: float = TIMEOUT_S) -> dt.datetime:
    """notAfter of the certificate the host serves, as aware UTC datetime."""
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            cert = tls.getpeercert()
    return dt.datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), dt.timezone.utc)


def resttage(not_after: dt.datetime, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.timezone.utc)
    return int((not_after - now).total_seconds() // 86400)


def lesen(hosts=HOSTS, *, now: dt.datetime | None = None, abrufen=ablauf) -> dict:
    """{host: {"tage": int, "ablauf": iso} or {"fehler": str}} for every host."""
    out = {}
    for host in hosts:
        try:
            na = abrufen(host)
        except Exception as e:  # noqa: BLE001 - the reason is the result
            out[host] = {"fehler": type(e).__name__}
            continue
        out[host] = {"tage": resttage(na, now), "ablauf": na.isoformat()}
    return out


def zeilen(stand: dict, min_tage: int) -> list[str]:
    out = []
    for host, s in stand.items():
        if "fehler" in s:
            out.append(f"FEHLT {host} {s['fehler']}")
        else:
            out.append(f"{'KURZ' if s['tage'] < min_tage else 'OK'} {host} {s['tage']} {s['ablauf']}")
    return out


def kleinste(stand: dict) -> int | None:
    """Fewest days left over the hosts; None when any host could not be read."""
    if not stand or any("fehler" in s for s in stand.values()):
        return None
    return min(s["tage"] for s in stand.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-tage", type=int, default=14)
    a = ap.parse_args(argv)
    for z in zeilen(lesen(), a.min_tage):
        print(z)
    return 0


if __name__ == "__main__":
    sys.exit(main())
