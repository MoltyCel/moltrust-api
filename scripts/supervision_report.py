"""The week in self-tests. Sunday 06:50 UTC, alongside the existing report.

Counts rather than impressions: how often the supervisor ran, how often it
found something, what was corrected, and which pipeline keeps coming back.

The last of those is the point. **A pipeline that needs the same correction
three times in a week is reported as a construction fault and not repaired
again.** A repair that runs weekly is not maintenance, it is a workaround with
a cron entry, and the thing it works around never gets fixed because nothing
ever looks broken.

Reads data/supervision_history.jsonl (one row per supervisor run) and
data/selfheal_state.json (one timestamp per correction). Writes nothing except
the Telegram message.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify

BASE = os.path.expanduser("~/moltstack")
HISTORY = os.path.join(BASE, "data", "supervision_history.jsonl")
HEAL_STATE = os.path.join(BASE, "data", "selfheal_state.json")
# The invariant runner's GREEN fixes, one line per execution. A different store
# from selfheal's, because a different thing decided to run them — and both
# belong in the same weekly list, or the week looks quieter than it was.
AUTOFIX_LOG = os.path.expanduser("~/Downloads/selftest/autofix.jsonl")
EXPECTED_PER_DAY = 24          # the workflow runs at :17, every hour
REPEAT_IS_DESIGN_FAULT = 3     # same correction, same week


def rows(days: int) -> list[dict]:
    cut = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=days)).isoformat()
    out = []
    try:
        with open(HISTORY) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (r.get("at") or "") >= cut:
                    out.append(r)
    except FileNotFoundError:
        return []
    return out


def corrections(days: int) -> dict[str, int]:
    cut = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=days)).isoformat()
    try:
        st = json.load(open(HEAL_STATE))
    except Exception:
        return {}
    out: dict[str, int] = {}
    for key, stamps in (st.get("runs") or {}).items():
        n = sum(1 for s in stamps if s >= cut)
        if n:
            out[key] = n
    return out


def autofixes(days: int) -> list[dict]:
    """Executed GREEN autofixes inside the window, newest first."""
    cut = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=days)).isoformat()
    out = []
    try:
        with open(AUTOFIX_LOG) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (r.get("at") or "") >= cut:
                    out.append(r)
    except FileNotFoundError:
        return []
    return sorted(out, key=lambda r: r.get("at") or "", reverse=True)


def collect(days: int = 7) -> dict:
    hist = rows(days)
    lights = collections.Counter(r.get("light") for r in hist)
    offenders = collections.Counter()
    for r in hist:
        for check in (r.get("offenders") or {}):
            offenders[check] += 1
    fixes = corrections(days)
    repeats = {k: v for k, v in fixes.items() if v >= REPEAT_IS_DESIGN_FAULT}
    expected = EXPECTED_PER_DAY * days
    auto = autofixes(days)
    return {"days": days, "runs": len(hist), "expected_runs": expected,
            "autofixes": auto,
            "green": lights.get("green", 0), "yellow": lights.get("yellow", 0),
            "red": lights.get("red", 0), "broken": lights.get("broken", 0),
            "offenders": offenders.most_common(8), "fixes": fixes,
            "repeats": repeats}


def format_report(k: dict) -> str:
    L = [f"🔍 <b>Selbstüberwachung — {k['days']} Tage</b>", ""]
    missing = k["expected_runs"] - k["runs"]
    if k["runs"] == 0:
        # No history is not a clean week. It means the supervisor never ran, or
        # never reached the server, and both are worse than a red week.
        L += ["<b>Keine Selbsttests aufgezeichnet.</b> Das ist keine ruhige "
              "Woche — der Supervisor hat den Server nicht erreicht oder der "
              "Workflow lief nicht. Prüfen: Actions-Historie von "
              "<code>supervise</code>."]
        return "\n".join(L)
    L += [f"Läufe: <b>{k['runs']}</b> von {k['expected_runs']} erwartet"
          + (f" — <b>{missing} fehlen</b>" if missing > 0 else "")]
    L += [f"grün {k['green']} · gelb {k['yellow']} · rot {k['red']}"
          + (f" · kaputt {k['broken']}" if k["broken"] else "")]

    if k["offenders"]:
        L += ["", "<b>Auffällig, nach Häufigkeit</b>"]
        for check, n in k["offenders"]:
            L.append(f"· {check} — {n}×")
    if k["fixes"]:
        L += ["", "<b>Ausgeführte Korrekturen</b>"]
        for key, n in sorted(k["fixes"].items(), key=lambda kv: -kv[1]):
            L.append(f"· {key} — {n}×")
    if k.get("autofixes"):
        L += ["", "<b>Invarianten-Autofix (GRÜN), je Ausführung</b>"]
        for r in k["autofixes"]:
            mark = "✅" if r.get("ok") else "❌"
            L.append(f"· {mark} {r.get('at','?')[:16]} {r.get('invariante','?')} "
                     f"→ {r.get('fix','?')} (Lauf {r.get('lauf','?')} von "
                     f"{r.get('deckel','?')}) — Befund: {r.get('befund','?')}")
    if k["repeats"]:
        L += ["", "<b>⚠️ Konstruktionsfehler, nicht weiter reparieren</b>"]
        for key, n in sorted(k["repeats"].items(), key=lambda kv: -kv[1]):
            L.append(f"· <b>{key}</b> — {n}× in {k['days']} Tagen. Eine "
                     f"Korrektur, die {n}× pro Woche läuft, behebt nichts; sie "
                     f"hält den Defekt unsichtbar.")
    elif k["fixes"]:
        L += ["", f"Keine Korrektur {REPEAT_IS_DESIGN_FAULT}× oder häufiger — "
              f"nichts, was als Konstruktionsfehler zu melden wäre."]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args(argv)
    k = collect(a.days)
    report = format_report(k)
    print(report)
    if a.send:
        notify.send_telegram(report, channel=notify.STATS, parse_mode="HTML")
    # A week with a red, or with runs missing, exits non-zero so a cron wrapper
    # can tell the difference without parsing the text.
    return 1 if (k["red"] or k["broken"] or
                 k["runs"] < k["expected_runs"] * 0.9) else 0


if __name__ == "__main__":
    raise SystemExit(main())
