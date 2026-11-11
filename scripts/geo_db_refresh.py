#!/usr/bin/env python3
"""geo-db-refresh — the monthly copy of the DB-IP country database behind GET /geo/lang (ops UC-VID).

Downloads the DB-IP "IP to Country Lite" database (MMDB, CC BY 4.0, no
account) for the current month, or the previous month while the current one
is not out yet, checks that it opens and places 8.8.8.8 in the US, and puts it
at data/geo/dbip-country-lite.mmdb in one rename. app/geo_lang.py reopens it
on its next lookup.

A file that fails the check never replaces the one in place: /geo/lang keeps
answering from the last good copy, or English for everyone without one. Every
run writes a heartbeat {timestamp, status, detail}.

IP geolocation by DB-IP (https://db-ip.com).

  python scripts/geo_db_refresh.py
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import io
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "data" / "geo" / "dbip-country-lite.mmdb"
HEARTBEAT = ROOT / "data" / "geo_db_refresh_heartbeat.json"
URL = "https://download.db-ip.com/free/dbip-country-lite-{month}.mmdb.gz"
UA = "moltrust-geo-db-refresh/1 (+https://moltrust.ch)"
#: The country file is about 8 MB unpacked; anything far larger is not it.
MAX_BYTES = 200 * 1024 * 1024
PROBE = ("8.8.8.8", "US")


def months(now: dt.datetime) -> list[str]:
    first = now.replace(day=1)
    previous = (first - dt.timedelta(days=1)).replace(day=1)
    return [f"{first:%Y-%m}", f"{previous:%Y-%m}"]


def download(month: str, dest: Path) -> None:
    req = urllib.request.Request(URL.format(month=month), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:  # nosec B310 - fixed https URL
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("download larger than expected")
    with gzip.open(io.BytesIO(data)) as gz, dest.open("wb") as out:
        copied = 0
        while chunk := gz.read(1 << 20):
            copied += len(chunk)
            if copied > MAX_BYTES:
                raise ValueError("database larger than expected")
            out.write(chunk)


def check(path: Path) -> int:
    """Raises unless the file opens and the probe IP is placed right; returns its size."""
    import maxminddb
    with maxminddb.open_database(str(path)) as reader:
        record = reader.get(PROBE[0]) or {}
    code = (record.get("country") or {}).get("iso_code")
    if code != PROBE[1]:
        raise ValueError(f"{PROBE[0]} resolves to {code!r}, expected {PROBE[1]}")
    return path.stat().st_size


def heartbeat(status: str, detail: str, now: dt.datetime) -> None:
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT.with_suffix(".tmp")
    tmp.write_text(json.dumps({"timestamp": now.isoformat(timespec="seconds"),
                               "status": status, "detail": detail}, indent=2))
    os.replace(tmp, HEARTBEAT)
    print(f"geo db refresh: {status}: {detail}")


def main(argv=None, now: dt.datetime | None = None, fetch=download) -> int:
    now = now or dt.datetime.now(dt.timezone.utc)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    tmp = TARGET.with_name(TARGET.name + ".new")
    problems = []
    for month in months(now):
        try:
            fetch(month, tmp)
            size = check(tmp)
        except Exception as exc:  # noqa: BLE001 - try the previous month, then report
            problems.append(f"{month}: {type(exc).__name__}: {str(exc)[:120]}")
            tmp.unlink(missing_ok=True)
            continue
        os.replace(tmp, TARGET)
        heartbeat("ok", f"DB-IP country lite {month}, {size} bytes, {PROBE[0]} -> {PROBE[1]}", now)
        return 0
    kept = "kept the previous file" if TARGET.exists() else "no file in place, /geo/lang answers en"
    heartbeat("error", "; ".join(problems) + f"; {kept}", now)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
