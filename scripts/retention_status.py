"""DSGVO-Retention in der Sammelmeldung (seit 19.10.2026, ops KO1).

agents/retention_cleanup.py laeuft taeglich um 03:30 UTC und schickte bis
heute bei jedem Lauf eine eigene Telegram-Meldung mit den Loeschzahlen. Das
ist eine Routinezahl, keine Nachricht: sie steht jetzt als eine Zeile in der
Sammelmeldung von scripts/selftest.py --digest (08:00 und 20:00 UTC).

Der Lauf schreibt sein Ergebnis in eine kleine Zustandsdatei neben den
anderen notify-Zustaenden (MOLTRUST_NOTIFY_STATE_DIR, sonst ~/selftest), die
Sammelmeldung liest sie. Liegt der letzte Lauf laenger als STALE_HOURS
zurueck oder fehlt die Datei, sagt die Zeile genau das: ein ausgebliebener
Lauf darf nicht aussehen wie ein ruhiger Tag.

Der Abbruch (Rollup gescheitert, nichts geloescht) bleibt eine Sofortmeldung
ueber app/notify.py; hier steht er zusaetzlich, damit die Zeile nicht den
Lauf vom Vortag als letzten Stand zeigt.
"""
from __future__ import annotations

import datetime as dt
import json
import os

FILENAME = "retention-letzter-lauf.json"
# 03:30-Lauf, Sammelmeldungen um 08:00 und 20:00: der aelteste regulaere
# Abstand sind 16,5 h. 26 h lassen einen verspaeteten Lauf durch, aber keinen
# ausgefallenen.
STALE_HOURS = 26


def state_path() -> str:
    """Zur Aufrufzeit aufgeloest, damit ein Test den Pfad umlenken kann."""
    base = os.environ.get("MOLTRUST_NOTIFY_STATE_DIR", "").strip() \
        or os.path.expanduser("~/selftest")
    return os.path.join(base, FILENAME)


def record(*, ok: bool, request_log: int = 0, contact_inbox: int = 0,
           days: int = 0, error: str = "", now: dt.datetime | None = None,
           path: str | None = None) -> None:
    """Das Ergebnis eines Laufs festhalten. Ueberschreibt den vorigen Stand."""
    now = now or dt.datetime.now(dt.timezone.utc)
    path = path or state_path()
    doc = {"ts": now.isoformat(timespec="seconds"), "ok": bool(ok),
           "request_log": int(request_log), "contact_inbox": int(contact_inbox),
           "days": int(days), "error": error}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    os.replace(tmp, path)


def lines(now: dt.datetime | None = None, path: str | None = None) -> list[str]:
    """Genau eine Zeile fuer die Sammelmeldung."""
    now = now or dt.datetime.now(dt.timezone.utc)
    path = path or state_path()
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        ts = dt.datetime.fromisoformat(doc["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt.timezone.utc)
    except FileNotFoundError:
        return ["DSGVO Retention: kein Lauf verzeichnet"]
    except (ValueError, KeyError, TypeError) as exc:
        return [f"DSGVO Retention: Zustand nicht lesbar ({type(exc).__name__})"]
    if now - ts > dt.timedelta(hours=STALE_HOURS):
        return [f"DSGVO Retention: kein Lauf seit {ts:%d.%m. %H:%M}Z"]
    if not doc.get("ok"):
        return [f"DSGVO Retention: abgebrochen ({doc.get('error') or '?'}), "
                f"request_log nicht bereinigt (Lauf {ts:%d.%m. %H:%M}Z)"]
    return [f"DSGVO Retention: {doc.get('request_log', 0)} request_log, "
            f"{doc.get('contact_inbox', 0)} contact_inbox gelöscht, "
            f"{doc.get('days', 0)} Tage verdichtet (Lauf {ts:%d.%m. %H:%M}Z)"]


if __name__ == "__main__":
    print("\n".join(lines()))
