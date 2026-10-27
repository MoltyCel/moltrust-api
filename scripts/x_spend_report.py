"""The measured daily rate against the budget. Reads the meter, calls nothing.

Runs after three regular days, then weekly with the Sunday stats. The point is
the comparison, not the number: a target nobody checks is a wish.

Every day also carries the three CM9 columns (Lars 22.10.2026): what the reply
radar's reading costs, what its sending costs, and what the CM5 measurement of
Lars' replies reads — each on its own, so the read cap Lars sets after the
measured week rests on the radar's reading alone (x_meter.radar_split).

    python scripts/x_spend_report.py --days 3 --send
    python scripts/x_spend_report.py --week 2026-11-02     # Mon-Sun, the CM9 table
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import x_meter
from app import tagesbericht_eingang

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
        c = x_meter.radar_split(r["day"])
        lines.append(f"  Radar lesen ${c['radar_read']:.3f} · Radar senden "
                     f"${c['radar_send']:.3f} · CM5-Messung ${c['cm5_read']:.3f}")
    if skipped:
        lines += ["", "Ausgenommen:"]
        lines += [f"· {d}: {why}" for d, why in skipped]

    text = "\n".join(lines)
    print(text)
    if send:
        # A measurement: daily report, not Telegram (ops NOTIFY-3c).
        tagesbericht_eingang.ablegen("x_spend_report", text)
    return 0


def week_table(monday: str) -> list[str]:
    """CM9 criterion 2: a Monday-to-Sunday UTC week, one row per day, the
    radar's reading, the radar's sending and the CM5 measurement each in a
    column of their own."""
    first = datetime.date.fromisoformat(monday)
    if first.weekday() != 0:
        raise ValueError(f"{monday} is not a Monday")
    head = ("Tag", "Radar lesen", "Radar senden", "CM5-Messung")
    lines = [f"\U0001f4b5 X-Kosten Radar, Woche {first:%d.%m.} bis "
             f"{first + datetime.timedelta(days=6):%d.%m.%Y} (UTC)", "",
             f"{head[0]:<12}{head[1]:>13}{head[2]:>14}{head[3]:>13}"]
    tot = dict.fromkeys(x_meter.RADAR_COLUMNS, 0.0)
    missing = []
    for i in range(7):
        day = (first + datetime.timedelta(days=i)).isoformat()
        c = x_meter.radar_split(day)
        if not c["ledger"]:
            missing.append(day)
        for k in tot:
            tot[k] += c[k]
        lines.append(f"{day:<12}{'$' + format(c['radar_read'], '.3f'):>13}"
                     f"{'$' + format(c['radar_send'], '.3f'):>14}"
                     f"{'$' + format(c['cm5_read'], '.3f'):>13}")
    lines.append(f"{'Summe':<12}{'$' + format(tot['radar_read'], '.3f'):>13}"
                 f"{'$' + format(tot['radar_send'], '.3f'):>14}"
                 f"{'$' + format(tot['cm5_read'], '.3f'):>13}")
    if missing:
        # A day without a single ledger row is not a cheap day, it is an
        # unmeasured one, and the week is then not a full week.
        lines += ["", "Ohne Ledger-Zeile (nicht gemessen): " + ", ".join(missing)]
    return lines


if __name__ == "__main__":
    if "--week" in sys.argv:
        i = sys.argv.index("--week")
        print("\n".join(week_table(sys.argv[i + 1])))
        raise SystemExit(0)
    n = 3
    if "--days" in sys.argv:
        i = sys.argv.index("--days")
        if i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])
    raise SystemExit(report(days=n, send="--send" in sys.argv))
