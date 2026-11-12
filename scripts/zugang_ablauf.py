"""Warn Lars before a credential with a fixed expiry runs out.

Reads config/zugaenge.yaml. For each entry it sends one Telegram message
(art zugang-abgelaufen, channel ALERTS) when the expiry is exactly
VORLAUF_TAGE days away and again on the day of expiry; on every other day it
prints one line with the days left and sends nothing. Runs daily from cron.

    python3 scripts/zugang_ablauf.py            # today
    python3 scripts/zugang_ablauf.py --heute 2027-01-31   # dry check of a date, no send
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify  # noqa: E402

REGISTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "zugaenge.yaml")
VORLAUF_TAGE = 7


def lesen(path: str = REGISTER) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        daten = yaml.safe_load(fh) or {}
    out = []
    for z in daten.get("zugaenge") or []:
        ablauf = z["ablauf"]
        if isinstance(ablauf, str):
            ablauf = dt.date.fromisoformat(ablauf)
        out.append({**z, "ablauf": ablauf})
    return out


def faellig(z: dict, heute: dt.date) -> bool:
    rest = (z["ablauf"] - heute).days
    return rest in (VORLAUF_TAGE, 0)


def text(z: dict, heute: dt.date) -> str:
    rest = (z["ablauf"] - heute).days
    wann = "heute" if rest == 0 else f"in {rest} Tagen"
    return (f"{z['name']} läuft {wann} ab ({z['ablauf']:%d.%m.%Y}): {z['beschreibung']}.\n"
            f"Erneuern: {' '.join(z['erneuern'].split())}\n"
            f"Antwort an einen Console-Tab, wenn er übernehmen soll.")


def main(argv=None, *, send=None, jetzt: dt.datetime | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heute", help="Datum YYYY-MM-DD; prüft nur, sendet nicht")
    ap.add_argument("--register", default=REGISTER)
    a = ap.parse_args(argv)
    probe = bool(a.heute)
    heute = dt.date.fromisoformat(a.heute) if probe else (jetzt or dt.datetime.now(dt.timezone.utc)).date()
    send = send or notify.send_telegram
    rc = 0
    for z in lesen(a.register):
        rest = (z["ablauf"] - heute).days
        if not faellig(z, heute):
            print(f"{z['name']}: {rest} Tage bis {z['ablauf']}, nichts zu melden")
            continue
        if probe:
            print(f"{z['name']}: würde melden — {text(z, heute).splitlines()[0]}")
            continue
        ok = send(text(z, heute), channel=notify.ALERTS, art="zugang-abgelaufen")
        print(f"{z['name']}: gemeldet ({'ok' if ok else 'nicht zugestellt'})")
        if not ok:
            rc = notify.MUTE_EXIT_CODE
    return rc


if __name__ == "__main__":
    sys.exit(main())
