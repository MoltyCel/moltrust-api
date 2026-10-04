"""After an evergreen run: did exactly one post go out, and is it now locked.

Runs once, twenty minutes after the scheduled evergreen slot. Two questions,
and the second is the one that costs something if it is wrong:

1. **Exactly one post?** `run_evergreen` takes `candidates[0]` and returns, so
   one is the design. Counted anyway, because the design was also one post per
   run on 2026-10-03 when the feed backfill could have fired 28.

2. **Does the cooldown hold?** The register gets an entry with today's date and
   `_posted_recently` must return True for it, so the next run on Thursday
   picks the next post and not the same one. A register write that silently
   failed looks identical to a successful run until Thursday repeats it.

Read-only. It reports; it does not repair.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import syndicate
from app import notify

BASE = os.path.expanduser("~/moltstack")
LOG = os.path.join(BASE, "logs", "syndicate.log")


def runs_today(day: str) -> list[dict]:
    """Evergreen runs from the log, with what each of them posted."""
    out = []
    try:
        lines = open(LOG, errors="replace").read().splitlines()
    except OSError:
        return out
    cur = None
    for line in lines:
        if not line.startswith("[") or "] " not in line:
            continue
        at, msg = line[1:].split("] ", 1)
        if at[:10] != day:
            continue
        msg = msg.split(": ", 1)[-1].strip()
        if msg.startswith("EVERGREEN"):
            cur = {"at": at, "posted": [], "due": None, "skipped": None}
            out.append(cur)
        elif cur is None:
            continue
        elif "posts are due" in msg:
            cur["due"] = msg
        elif msg.startswith("Posted:") or msg.startswith("X:"):
            cur["posted"].append(msg)
        elif "nothing due" in msg or "0 of" in msg:
            cur["skipped"] = msg
    return out


def check(day: str | None = None) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    day = day or now.strftime("%Y-%m-%d")
    runs = runs_today(day)
    reg = syndicate.load_register()
    # The field is `last`. Checked against the live register rather than
    # guessed: `last_posted` and `at` both read plausibly and both would have
    # produced an empty set, which is indistinguishable from "nothing ran" —
    # a verifier that reports all-clear because it looked in the wrong place is
    # worse than no verifier.
    fresh = {link: e for link, e in reg.items()
             if str(e.get("last") or "")[:10] == day}
    locked, unlocked = [], []
    for link in fresh:
        if syndicate._posted_recently(reg.get(link, {}), now):
            locked.append(link)
        else:
            unlocked.append(link)
    items = syndicate.fetch_feed()
    cands = syndicate.evergreen_candidates(items, reg, now) if items else []
    return {"day": day, "runs": len(runs), "posted_today": sorted(fresh),
            "locked": locked, "unlocked": unlocked,
            "next_candidate": cands[0]["link"] if cands else None,
            "candidates_left": len(cands),
            "log": runs}


def format_report(k: dict) -> str:
    L = [f"🌲 <b>Evergreen-Nachprüfung {k['day']}</b>", ""]
    L.append(f"Läufe im Log: {k['runs']}")
    n = len(k["posted_today"])
    if n == 0:
        L += ["", "<b>Kein Eintrag mit heutigem Datum im Register.</b> Entweder "
              "lief nichts, oder der Post ging raus und die Registerzeile "
              "fehlt — das wäre der Fall, der am Donnerstag denselben Post "
              "wiederholt. Log prüfen."]
        return "\n".join(L)
    L.append(f"Heute registriert: <b>{n}</b>")
    for link in k["posted_today"]:
        L.append(f"· {link.rsplit('/', 1)[-1]}")
    if n > 1:
        L += ["", f"<b>⚠️ {n} Einträge, erwartet war 1.</b> Ein Lauf postet "
              f"candidates[0] und kehrt zurück; mehr als einer heißt, dass "
              f"zwei Läufe gefeuert haben oder die Sperre nicht griff."]
    if k["unlocked"]:
        L += ["", f"<b>⚠️ Cooldown greift nicht</b> für: "
              f"{', '.join(l.rsplit('/', 1)[-1] for l in k['unlocked'])} — "
              f"am Donnerstag käme derselbe Post wieder."]
    else:
        L += ["", f"Cooldown greift: {len(k['locked'])} von {n} gesperrt "
              f"({syndicate.EVERGREEN_COOLDOWN_DAYS} Tage)."]
    L += ["", f"Nächster Kandidat: "
          f"{(k['next_candidate'] or '—').rsplit('/', 1)[-1]} "
          f"({k['candidates_left']} übrig)"]
    if k["next_candidate"] and k["next_candidate"].rsplit("/", 1)[-1] in [
            p.rsplit("/", 1)[-1] for p in k["posted_today"]]:
        L += ["", "<b>⚠️ Der nächste Kandidat ist der, der heute lief.</b> "
              "Genau das soll der Cooldown verhindern."]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--day")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args(argv)
    k = check(a.day)
    text = format_report(k)
    print(text)
    if a.send:
        notify.send_telegram(text, channel=notify.STATS, parse_mode="HTML")
    return 1 if (k["unlocked"] or len(k["posted_today"]) > 1) else 0


if __name__ == "__main__":
    raise SystemExit(main())
