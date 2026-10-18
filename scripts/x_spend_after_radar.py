"""X spend for the week before and the week after the reply radar was switched off.

The reply branch ended on 2026-10-18 and its budget went to zero (Lars, CJ1).
This reports, once on 2026-10-25 (Lars, CJ5), what X cost in the seven days
before and the seven days after, so the saving is measured rather than assumed.
The reading list at 06:05 keeps reading X (CJ2), so the second week is not
expected to be zero.

Reads the meter, calls nothing. A day without a ledger is named, and the week
it belongs to gets no total: a sum over six days reported as a week is the
partial-read shape this repo has paid for before.

    python scripts/x_spend_after_radar.py [--send]
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import x_meter
from app import notify

SWITCH_OFF = datetime.date(2026, 10, 18)
WEEK = 7


def week(start: datetime.date) -> dict:
    days = [x_meter.spend((start + datetime.timedelta(days=i)).isoformat())
            for i in range(WEEK)]
    missing = [d["day"] for d in days if not d["ledger"]]
    total = None if missing else round(sum(d["usd"] for d in days), 3)
    return {"start": start.isoformat(),
            "end": (start + datetime.timedelta(days=WEEK - 1)).isoformat(),
            "days": days, "missing": missing, "usd": total}


def report(send: bool = False, today: datetime.date | None = None) -> int:
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    if today < SWITCH_OFF + datetime.timedelta(days=WEEK):
        print(f"the week after {SWITCH_OFF} is not over yet")
        return 1
    before = week(SWITCH_OFF - datetime.timedelta(days=WEEK))
    after = week(SWITCH_OFF)

    lines = ["\U0001f4b5 X-Verbrauch vor und nach der Radar-Abschaltung "
             f"({SWITCH_OFF.isoformat()})", ""]
    for name, w in (("Woche davor", before), ("Woche danach", after)):
        head = (f"${w['usd']:.2f}" if w["usd"] is not None
                else "keine Summe, Ledger fehlt für " + ", ".join(w["missing"]))
        lines.append(f"<b>{name}</b> {w['start']} bis {w['end']}: {head}")
        for d in w["days"]:
            if d["ledger"]:
                lines.append(f"· {d['day']}: ${d['usd']:.2f} — {d['posts']} Posts, "
                             f"{d['users']} Profile, {d['writes']} Writes")
            else:
                lines.append(f"· {d['day']}: kein Ledger")
        lines.append("")
    if before["usd"] is not None and after["usd"] is not None:
        diff = after["usd"] - before["usd"]
        lines.append(f"Differenz: ${diff:+.2f} je Woche "
                     f"(Leseliste 06:05 liest weiter, CJ2)")
    else:
        lines.append("Differenz: nicht berechnet, eine Woche ist unvollständig")

    text = "\n".join(lines)
    print(text)
    if send:
        notify.send_telegram(text, channel=notify.STATS, parse_mode="HTML")
    return 0 if before["usd"] is not None and after["usd"] is not None else 2


if __name__ == "__main__":
    raise SystemExit(report(send="--send" in sys.argv))
