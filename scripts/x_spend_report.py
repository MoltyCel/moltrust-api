"""The measured daily rate against the budget. Reads the meter, calls nothing.

Runs after three regular days, then weekly with the Sunday stats. The point is
the comparison, not the number: a target nobody checks is a wish.

    python scripts/x_spend_report.py --days 3 --send
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import x_meter
from app import notify

# Days when X refused everything, or when I ran things by hand to catch up.
# Averaging them in would answer a different question than "what does the
# normal day cost".
EXCLUDED_DAYS = {
    "2026-09-25": "402 credits depleted",
    "2026-09-26": "402 credits depleted",
    "2026-09-27": "Nachholung: Weekly Proof und filter-report von Hand",
}


def report(days: int = 3, send: bool = False) -> int:
    today = datetime.datetime.now(datetime.timezone.utc).date()
    rows, skipped = [], []
    day = today - datetime.timedelta(days=1)      # yesterday backwards
    while len(rows) < days and (today - day).days <= days + len(EXCLUDED_DAYS) + 7:
        stamp = day.isoformat()
        if stamp in EXCLUDED_DAYS:
            skipped.append((stamp, EXCLUDED_DAYS[stamp]))
        else:
            s = x_meter.spend(stamp)
            if s["ledger"]:
                rows.append(s)
        day -= datetime.timedelta(days=1)

    if not rows:
        print("no regular days measured yet")
        return 1

    avg = sum(r["usd"] for r in rows) / len(rows)
    target = x_meter.DAILY_TARGET_USD
    verdict = "unter dem Soll" if avg <= target else "über dem Soll"
    lines = [f"\U0001f4b5 X-Verbrauch: {len(rows)} Regeltage gemessen",
             "",
             f"Schnitt: ${avg:.2f}/Tag — {verdict} (${target:.2f})",
             f"Hochgerechnet: ${avg * 30:.2f}/Monat gegen "
             f"${x_meter.MONTHLY_TARGET_USD:.0f} Ziel",
             ""]
    for r in sorted(rows, key=lambda x: x["day"]):
        top = ", ".join(f"{k} {n}" for k, n in list(r["by_source"].items())[:2])
        lines.append(f"· {r['day']}: ${r['usd']:.2f} — {r['posts']} Posts, "
                     f"{r['users']} Profile, {r['writes']} Writes"
                     + (f" · {top}" if top else ""))
    if skipped:
        lines += ["", "Ausgenommen:"]
        lines += [f"· {d}: {why}" for d, why in skipped]

    text = "\n".join(lines)
    print(text)
    if send:
        notify.send_telegram(text, channel=notify.STATS)
    return 0


if __name__ == "__main__":
    n = 3
    if "--days" in sys.argv:
        i = sys.argv.index("--days")
        if i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])
    raise SystemExit(report(days=n, send="--send" in sys.argv))
