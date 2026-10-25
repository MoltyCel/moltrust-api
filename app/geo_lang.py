"""The language a moltrust.ch page shows, by the visitor's country (ops UC-VID).

moltrust.ch/use-cases shows German to visitors from DE, AT, CH and LI and
English to everyone else, and to anyone whose country is not known (Lars,
2026-10-25). The site sits on nginx without a CDN in front, so no country
header arrives with the request; GET /geo/lang answers it from a local copy of
the DB-IP "IP to Country Lite" database.

IP geolocation by DB-IP (https://db-ip.com), licensed CC BY 4.0. The page that
uses the answer links back to DB-IP, as the licence requires for web
applications.

The lookup never asks a third party and keeps nothing: no IP, no country.
scripts/geo_db_refresh.py replaces the file once a month; the reader notices
the new file by its modification time and reopens it.
"""
from __future__ import annotations

import ipaddress
import threading
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "geo" / "dbip-country-lite.mmdb"

#: Countries that get the German page.
GERMAN = frozenset({"DE", "AT", "CH", "LI"})

_lock = threading.Lock()
_reader = None
_mtime = None


def _open():
    """The current reader, reopened when the file changed; None without a file."""
    global _reader, _mtime
    try:
        mtime = DB_PATH.stat().st_mtime
    except OSError:
        return None
    with _lock:
        if _reader is None or mtime != _mtime:
            import maxminddb
            # The old reader is left to the garbage collector rather than
            # closed here: a lookup on another thread may still hold it.
            _reader = maxminddb.open_database(str(DB_PATH))
            _mtime = mtime
        return _reader


def country_for(ip: str | None) -> str | None:
    """ISO 3166 alpha-2 code for `ip`, or None when it is not known."""
    try:
        ipaddress.ip_address(ip or "")
    except ValueError:
        return None
    try:
        reader = _open()
        record = reader.get(ip) if reader is not None else None
    except Exception:  # noqa: BLE001 - an unreadable database means English, never a 500
        return None
    code = ((record or {}).get("country") or {}).get("iso_code")
    if isinstance(code, str) and len(code) == 2 and code.isalpha():
        return code.upper()
    return None


def lang_for(country: str | None) -> str:
    return "de" if country in GERMAN else "en"
