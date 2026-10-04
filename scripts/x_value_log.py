#!/usr/bin/env python3
"""One row a day: what X cost and what came back. For the 17.10. reading.

The band was raised on 2026-10-04 from $25 a month to $60 without anybody
knowing what the money buys. That is the open question, and it cannot be
answered backwards: the ledger holds cost per day but follower counts and
registrations were never written down beside it.

So this writes them down, one line per UTC day, append-only, and refuses to
overwrite a day it already has. The reading happens on 2026-10-17 with
`--evaluate`, which divides the deltas by the dollars and says nothing about
causation — a follower gained on a day we spent $2 is not a follower the $2
bought, and the report says so rather than implying otherwise.

    python3 scripts/x_value_log.py              # append today, 23:50 UTC
    python3 scripts/x_value_log.py --evaluate   # the reading
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents import x_meter  # noqa: E402

LOG = os.path.expanduser("~/moltstack/data/x_value.jsonl")
START = datetime.date(2026, 10, 5)      # the first full day under the new band
READING = datetime.date(2026, 10, 17)


def psql(sql):
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-t", "-c", sql],
        capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise RuntimeError(f"psql: {out.stderr[:160]}")
    return out.stdout.strip()


def followers():
    """Our own follower count, or None. None is written as None, never as 0."""
    try:
        import requests

        from agents import x_post
        auth = x_post.get_auth()
        if not auth:
            return None, "keine X-Credentials"
        r = requests.get("https://api.twitter.com/2/users/by/username/moltrust",
                         params={"user.fields": "public_metrics"}, auth=auth,
                         timeout=20)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        m = ((r.json() or {}).get("data") or {}).get("public_metrics") or {}
        n = m.get("followers_count")
        return (n, None) if n is not None else (None, "kein public_metrics")
    except ModuleNotFoundError as e:
        return None, f"{e.name} fehlt in diesem Interpreter"
    except Exception as e:
        return None, type(e).__name__


def row_for(day: str) -> dict:
    s = x_meter.spend(day=day)
    f, why = followers()
    reg = psql(f"""SELECT count(*) FROM agents
                    WHERE revoked_at IS NULL AND agent_type <> 'system'
                      AND created_at::date = date '{day}'""")
    reg_x = psql(f"""SELECT count(*) FROM agents
                      WHERE revoked_at IS NULL AND agent_type <> 'system'
                        AND created_at::date = date '{day}'
                        AND coalesce(platform, '') IN ('x', 'twitter')""")
    return {"day": day, "usd": s["usd"], "posts": s["posts"],
            "profiles": s["users"], "writes": s["writes"],
            "followers": f, "followers_error": why,
            "agents_registered": int(reg), "agents_platform_x": int(reg_x),
            "at": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds")}


def existing() -> dict:
    out = {}
    try:
        with open(LOG) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("day"):
                    out[r["day"]] = r
    except FileNotFoundError:
        pass
    return out


def evaluate(rows: dict) -> str:
    days = sorted(d for d in rows if d >= START.isoformat())
    if len(days) < 2:
        return (f"{len(days)} Tage aufgezeichnet seit {START:%d.%m.} — "
                f"zu wenig für eine Auswertung. Keine Zahl.")
    usd = sum(rows[d]["usd"] or 0 for d in days)
    posts = sum(rows[d]["posts"] or 0 for d in days)
    profiles = sum(rows[d]["profiles"] or 0 for d in days)
    writes = sum(rows[d]["writes"] or 0 for d in days)
    regs = sum(rows[d]["agents_registered"] or 0 for d in days)
    regs_x = sum(rows[d]["agents_platform_x"] or 0 for d in days)

    f_first = next((rows[d]["followers"] for d in days
                    if rows[d].get("followers") is not None), None)
    f_last = next((rows[d]["followers"] for d in reversed(days)
                   if rows[d].get("followers") is not None), None)
    missing = [d for d in days if rows[d].get("followers") is None]

    L = [f"X je Dollar — {days[0]} bis {days[-1]}, {len(days)} Tage", ""]
    L.append(f"Ausgaben      ${usd:.2f}  (${usd/len(days):.2f}/Tag, "
             f"Band ${x_meter.DAILY_BREAK_USD:.2f})")
    L.append(f"gelesen       {posts} Posts, {profiles} Profile")
    L.append(f"geschrieben   {writes} Posts")
    if f_first is not None and f_last is not None:
        d = f_last - f_first
        L.append(f"Follower      {f_first} → {f_last}  ({d:+d})")
        if usd > 0 and d > 0:
            L.append(f"              ${usd/d:.2f} je gewonnenem Follower")
        elif d <= 0:
            L.append(f"              kein Zugewinn — keine Kostenzahl je Follower")
    else:
        L.append("Follower      nicht aufgezeichnet — keine Zahl")
    if missing:
        L.append(f"              {len(missing)} Tage ohne Followerzahl: "
                 f"{', '.join(missing[:5])}")
    L.append(f"Registrierungen {regs} insgesamt, davon {regs_x} mit platform=x")
    if usd > 0 and regs_x > 0:
        L.append(f"              ${usd/regs_x:.2f} je Registrierung über X")
    elif regs_x == 0:
        L.append("              keine über X — die Ausgaben lassen sich "
                 "keiner Registrierung zuordnen")
    L += ["", "Das sind Quotienten, keine Ursachen. Ein Follower an einem Tag "
          "mit $2 Ausgaben ist kein Follower, den die $2 gekauft haben; die "
          "Zahl sagt, was ein Kanal kostet, nicht was er bewirkt."]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--day", help="ein bestimmter Tag, Vorgabe heute")
    ap.add_argument("--send", action="store_true", help="Ergebnis nach STATS")
    a = ap.parse_args()

    rows = existing()
    if a.evaluate:
        out = evaluate(rows)
        print(out)
        if a.send:
            from app import notify
            notify.send_telegram("MolTrust — " + out, channel=notify.STATS)
        return 0

    day = a.day or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    if day in rows:
        # Append-only, and a day is written once. A second write would make the
        # sums wrong in a file whose whole purpose is a sum.
        print(f"{day} steht schon im Protokoll — nichts geschrieben.")
        return 0
    r = row_for(day)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{day}: ${r['usd']:.3f} · {r['posts']} Posts · {r['profiles']} Profile"
          f" · {r['writes']} Writes · Follower "
          f"{r['followers'] if r['followers'] is not None else r['followers_error']}"
          f" · {r['agents_registered']} neue Agents "
          f"({r['agents_platform_x']} über X)")
    if datetime.date.fromisoformat(day) >= READING:
        print(f"\nLesetag erreicht ({READING:%d.%m.}):\n")
        print(evaluate(existing()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
