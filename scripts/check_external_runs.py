#!/usr/bin/env python3
"""A declared schedule proves it fired. One that exists and never runs is a finding.

The watch that matters most is the one that does not run on the machine being
watched: everything on the server is monitored by something else on the server,
which works until the thing that stopped is the monitor. That half is
.github/workflows/supervise.yml, hourly at :17, calling the server over the
deploy path.

Which means the question "did the external schedule actually fire" is now part
of the system, and it cannot be answered from the server. On 2026-10-03, hours
after the workflow landed on main, it had zero scheduled runs — and the honest
reading of that was not obvious. GitHub drops the first ticks of a newly added
scheduled workflow, and gives no punctuality guarantee at all; the first tick
came 4 h 50 min after the file was merged. So:

  * a schedule inside its warm-up window reports WARMING, not green
  * a schedule that has missed more than one expected tick is a finding
  * one missed tick is tolerated, because GitHub drops them under load and
    alarming on that would train us to ignore this check
  * a schedule we cannot query is UNREADABLE, never zero

The cadence comes from the cron itself, so a weekly workflow is not measured
against a daily window. That was the other half of the confusion: an hourly
schedule and a Monday-morning schedule both had "no runs", and only one of
them meant anything.
"""
from __future__ import annotations

import argparse
import datetime
import statistics
import json
import os
import pathlib
import re
import sys

import httpx
from app import gh

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
REPO = os.environ.get("MOLTRUST_GH_REPO", "MoltyCel/moltrust-api")

# GitHub drops the first ticks after a scheduled workflow appears on the
# default branch. Measured on 2026-10-03: merged 11:27 UTC, first scheduled run
# 16:17 UTC. Eight hours is that gap with room, and it is a declared window
# rather than a silent exemption.
WARMUP = datetime.timedelta(hours=8)
# Two missed ticks tolerated, and the number is measured rather than chosen.
#
# Counted on 2026-10-04 over a 17-hour window: four scheduled runs of the
# hourly :17 schedule, at 16:17, 19:50, 23:03 and 02:38 UTC. Median gap 3.4 h,
# none of them on the declared minute. GitHub delivers about one tick in five
# and promises nothing; against that, "one missed tick" is red almost always,
# and a check that is red almost always is a check somebody mutes.
#
# Two is still not the delivered rate — at a 3.4-hour median even three ticks
# pass between runs regularly. It is the point where the finding still means
# something: a gap of three hours or more on an hourly schedule says the queue
# is slow, a gap that keeps growing says the schedule stopped. The ratio is the
# honest measure and `--ratio` reports it weekly.
#
# Since 09.10.2026 this no longer decides when the check speaks — MAX_TOLERANZ
# does, because counting ticks means something different at every cadence and
# the same three ticks were three hours hourly and eighteen hours at four a
# day. Kept for `--ratio`, which still reports per-tick hit rates.
TOLERATED_MISSES = 2

UTC = datetime.timezone.utc


def token():
    # One name since 2026-10-05. GH_TOKEN is gone from the secrets and
    # GITHUB_TOKEN never existed there, so a chain of three only means
    # three places to look when it fails.
    for name in (gh.NAME,):
        v = os.environ.get(name)
        if v:
            return v
    return None


API = "https://api.github.com"


def api(path):
    """One GET against the Actions API. httpx, like the rest of the watchers.

    urllib.request.urlopen would do the same job, and bandit flags it (B310)
    because it cannot see that the scheme is fixed. The finding is fair in
    general, and httpx is already a dependency here, so there is nothing to
    suppress.
    """
    r = httpx.get(f"{API}/{path}", timeout=30.0,
                  headers={"Accept": "application/vnd.github+json",
                           "User-Agent": "MolTrust-ExternalRunCheck/1.0",
                           "Authorization": f"Bearer {token()}"})
    r.raise_for_status()
    return r.json()


# ── cron ──────────────────────────────────────────────────────────────────────

def _field(spec, value, lo, hi):
    """Does one cron field match this value. Handles * , - / and lists."""
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, _, raw = part.partition("/")
            step = int(raw)
        if part in ("*", ""):
            start, end = lo, hi
        elif "-" in part:
            a, _, b = part.partition("-")
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def cron_matches(spec, when):
    m, h, dom, mon, dow = spec.split()
    if not (_field(m, when.minute, 0, 59) and _field(h, when.hour, 0, 23)
            and _field(mon, when.month, 1, 12)):
        return False
    # cron's day fields are an OR when both are restricted, which is the one
    # rule in cron everybody gets wrong.
    d_ok = _field(dom, when.day, 1, 31)
    w_ok = _field(dow, when.isoweekday() % 7, 0, 6)
    if dom.strip() == "*" and dow.strip() == "*":
        return True
    if dom.strip() == "*":
        return w_ok
    if dow.strip() == "*":
        return d_ok
    return d_ok or w_ok


def previous_fires(spec, now, count=2, horizon_days=40):
    """The last `count` times this cron was due, newest first."""
    t = now.replace(second=0, microsecond=0)
    out, limit = [], horizon_days * 24 * 60
    for _ in range(limit):
        t -= datetime.timedelta(minutes=1)
        if cron_matches(spec, t):
            out.append(t)
            if len(out) == count:
                return out
    return out  # fewer than asked for: the cron is rarer than the horizon


# ── workflows ─────────────────────────────────────────────────────────────────

def declared():
    """(file, [cron, …]) for every workflow carrying a schedule."""
    out = []
    for p in sorted(WORKFLOWS.glob("*.y*ml")):
        text = p.read_text(encoding="utf-8", errors="replace")
        if "schedule:" not in text:
            continue
        crons = re.findall(r"^\s*-\s*cron:\s*[\"']?([^\"'\n#]+)", text, re.M)
        crons = [c.strip() for c in crons if len(c.split()) == 5]
        if crons:
            out.append((p.name, crons))
    return out


def first_on_default(name):
    """When this workflow file first reached the default branch, from the API."""
    try:
        rows = api(f"repos/{REPO}/commits?path=.github/workflows/{name}&per_page=100")
    except (httpx.HTTPError, OSError, ValueError):
        return None
    stamps = [c.get("commit", {}).get("committer", {}).get("date") for c in rows]
    stamps = [s for s in stamps if s]
    if not stamps:
        return None
    return datetime.datetime.fromisoformat(min(stamps).replace("Z", "+00:00"))


def newest_scheduled(name):
    rows = api(f"repos/{REPO}/actions/workflows/{name}/runs"
               f"?event=schedule&per_page=1").get("workflow_runs") or []
    if not rows:
        return None
    return datetime.datetime.fromisoformat(rows[0]["created_at"].replace("Z", "+00:00"))


def ratio(now, days=7):
    """How many due ticks actually fired, per declared schedule.

    The hourly check answers "did it fire at all"; this answers "how often",
    which is the number that decides whether a timing expectation may be built
    on the schedule. Separate modes on one script rather than two scripts: the
    cron parser and the GitHub reads are the same, and a second copy of them
    would drift.

    A tick counts as hit when a scheduled run started inside its own cadence
    window, so GitHub starting a 19:17 tick at 19:50 is a hit and not a miss.
    Late is a measurement of its own, reported beside the ratio.

    **`runs_seen` can exceed `fired`, and that is correct.** A run whose tick
    fell inside the warm-up is not counted, because its tick is not counted as
    due either. On 2026-10-04 supervise.yml had four scheduled runs and a
    ratio of 2/13: the 16:17 and 19:50 runs belong to ticks GitHub was still
    allowed to ignore. Reporting both numbers so the difference is visible
    rather than looking like a lost run.
    """
    out = []
    for name, crons in declared():
        born = first_on_default(name)
        rows = api(f"repos/{REPO}/actions/workflows/{name}/runs"
                   f"?event=schedule&per_page=100").get("workflow_runs") or []
        fired = sorted(datetime.datetime.fromisoformat(
            r["created_at"].replace("Z", "+00:00")) for r in rows)
        for spec in crons:
            # Every tick due in the window, newest first from previous_fires.
            horizon = min(days, 40)
            due = [t for t in previous_fires(spec, now, count=2000,
                                             horizon_days=horizon)
                   if t >= now - datetime.timedelta(days=days)]
            if born:
                # A schedule owes nothing for ticks that predate its file, and
                # nothing inside the warm-up GitHub spends ignoring it.
                due = [t for t in due if t >= born + WARMUP]
            if not due:
                out.append({"workflow": name, "cron": spec, "due": 0,
                            "fired": 0, "pct": None,
                            "note": "kein faelliger Takt im Fenster"})
                continue
            step = _cadence(spec, due)
            hits, delays = 0, []
            for t in due:
                run = next((f for f in fired if t <= f < t + step), None)
                if run:
                    hits += 1
                    delays.append((run - t).total_seconds() / 60)
            out.append({"workflow": name, "cron": spec, "due": len(due),
                        "fired": hits,
                        "pct": round(hits / len(due) * 100),
                        "worst_delay_minutes": round(max(delays)) if delays else None,
                        # statistics.median, not the upper middle value: with
                        # two samples the latter reports the worst delay as the
                        # median, which reads as "typically 47 minutes late"
                        # when the samples were 46 and 22.
                        "median_delay_minutes": round(statistics.median(delays))
                        if delays else None,
                        "runs_seen": len(fired)})
    return out


# How late a run may be and still count as that tick's run. Capped, rather than
# being the whole cadence: with the cadence as the window a six-hourly schedule
# would accept a run that is five hours and fifty minutes late, which measures
# nothing. Two hours is the agreed slack for GitHub's scheduler — on
# 2026-10-09 its delay on an hourly schedule reached tens of minutes routinely.
MAX_TOLERANZ = datetime.timedelta(hours=2)


def _cadence(spec, due):
    """How long after a due tick a run still counts as that tick's run.

    The gap between consecutive due ticks, taken from the ticks themselves —
    not from the cron string: "17 * * * *" and "0 9 * * 2,4" need different
    windows, and reading the gap off the schedule's own fire times gets both
    right without a second parser. Capped at MAX_TOLERANZ so a rare schedule
    does not get a window wide enough to accept anything.
    """
    if len(due) >= 2:
        gaps = sorted(abs((a - b).total_seconds()) for a, b in zip(due, due[1:]))
        takt = datetime.timedelta(seconds=gaps[len(gaps) // 2])
    else:
        takt = datetime.timedelta(hours=1)
    return min(takt, MAX_TOLERANZ)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ratio", action="store_true",
                    help="hit rate per schedule instead of the pass/fail count")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.ratio:
        if not token():
            print("UNREADABLE: kein GitHub-Token", file=sys.stderr)
            return 2
        rows = ratio(datetime.datetime.now(UTC), args.days)
        if args.json:
            print(json.dumps(rows, indent=1))
            return 0
        for r in rows:
            if r.get("note"):
                print(f"{r['workflow']} ({r['cron']}): {r['note']}")
                continue
            late = ""
            if r.get("worst_delay_minutes") is not None:
                late = (f" · Verzug median {r['median_delay_minutes']} min, "
                        f"max {r['worst_delay_minutes']} min")
            seen = ""
            if r.get("runs_seen") and r["runs_seen"] != r["fired"]:
                seen = (f" · {r['runs_seen']} Laeufe insgesamt, "
                        f"{r['runs_seen'] - r['fired']} zu Takten im "
                        f"Aufwaermfenster")
            print(f"{r['workflow']} ({r['cron']}): {r['fired']}/{r['due']} "
                  f"Takte = {r['pct']} %{late}{seen}")
        return 0
    return _check(datetime.datetime.now(UTC))


def _check(now):
    if not token():
        print("UNREADABLE: kein GitHub-Token in der Umgebung "
              "(MOLTYCEL_GH_TOKEN)", file=sys.stderr)
        print(-1)
        return 2

    schedules = declared()
    if not schedules:
        # The external watch is declared in code. No schedule at all means the
        # watch is gone, which is the loudest version of this finding.
        print("UNREADABLE: kein Workflow mit schedule gefunden", file=sys.stderr)
        print(-1)
        return 2

    findings = 0
    for name, crons in schedules:
        try:
            last = newest_scheduled(name)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            print(f"UNREADABLE {name}: {type(exc).__name__}", file=sys.stderr)
            print(-1)
            return 2

        born = first_on_default(name)
        if born and now - born < WARMUP and last is None:
            print(f"WARMING {name}: seit {(now - born).total_seconds()/3600:.1f} h "
                  f"auf main, GitHub laesst die ersten Takte aus "
                  f"(Fenster {WARMUP.total_seconds()/3600:.0f} h)", file=sys.stderr)
            continue
        # A schedule cannot be blamed for ticks that predate its own file. The
        # first version of this check held a Monday-morning workflow to a tick
        # from 21 September; the file reached main on 1 October. Same mistake as
        # the three before it: the comparison was arithmetically right and
        # about the wrong thing.
        earliest = (born + WARMUP) if born else None

        # Die Frist: der juengste faellige Takt, der mehr als MAX_TOLERANZ
        # zurueckliegt. Wer bis dahin nicht gelaufen ist, hat ihn verpasst.
        #
        # Vorher war die Frist der (TOLERATED_MISSES + 1)-te Takt von hinten,
        # also drei ausgelassene Takte. Bei stuendlichem Plan waren das drei
        # Stunden Stille — und weil GitHub von einem stuendlichen Plan etwa ein
        # Drittel liefert, stand die Invariante dauerhaft auf WARN. Bei vier
        # Laeufen am Tag waeren dieselben drei Takte achtzehn Stunden Stille,
        # und damit deckt die Wache den Fall nicht mehr ab, fuer den sie da
        # ist: dass der Server schweigt.
        #
        # Mit der Toleranz gerechnet statt in Takten gezaehlt, meldet die Wache
        # spaetestens acht Stunden nach dem letzten Lauf — sechs Stunden Takt
        # plus zwei Stunden Nachsicht fuer GitHubs Planer.
        for spec in crons:
            fires = previous_fires(spec, now, count=12)
            if not fires:
                print(f"UNREADABLE {name}: cron {spec!r} feuert nicht innerhalb "
                      f"des Horizonts", file=sys.stderr)
                print(-1)
                return 2
            faellig = [f for f in fires if now - f >= MAX_TOLERANZ]
            if not faellig:
                print(f"ok {name} ({spec}) — jeder Takt im Fenster ist "
                      f"juenger als die Toleranz von "
                      f"{MAX_TOLERANZ.total_seconds()/3600:.0f} h",
                      file=sys.stderr)
                continue
            deadline = faellig[0]
            # Order matters, and the first attempt had it wrong: a schedule
            # that fired is green whether or not it is young, so the "too
            # young" clause only ever excuses a would-be finding.
            if last is not None and last >= deadline:
                print(f"ok {name} ({spec}) — letzter geplanter Lauf "
                      f"{last.isoformat()}", file=sys.stderr)
            elif earliest and deadline < earliest:
                print(f"ZU JUNG {name} ({spec}) — faellig war "
                      f"{deadline.isoformat()}, die Datei liegt erst seit "
                      f"{born.isoformat()} auf main; noch kein Takt schuldig",
                      file=sys.stderr)
            elif last is None:
                findings += 1
                print(f"NIE GEFEUERT: {name} ({spec}) — faellig seit "
                      f"{deadline.isoformat()}, kein einziger geplanter Lauf",
                      file=sys.stderr)
            else:
                findings += 1
                print(f"VERPASST: {name} ({spec}) — letzter geplanter Lauf "
                      f"{last.isoformat()}, faellig war {deadline.isoformat()} "
                      f"und die Toleranz von "
                      f"{MAX_TOLERANZ.total_seconds()/3600:.0f} h ist vorbei",
                      file=sys.stderr)
    print(findings)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
