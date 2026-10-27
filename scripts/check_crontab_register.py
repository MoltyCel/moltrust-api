#!/usr/bin/env python3
"""Compare the three places that say what cron runs, and print every difference.

ops/crontab.txt is the snapshot, `crontab -l` is what runs, and the `cron`
field of each pipeline in config/expectations.yaml is what the register
measures against. check_crontab_snapshot.py compares the first two; nothing
compared the register with either (ops SV-1, criterion 4). A register entry
for a schedule that no longer runs keeps a pipeline red, and a run whose
schedule the register does not know is measured against the wrong gap.

A register entry matches a crontab line when the line has the same schedule
(the five time fields, whitespace collapsed) and its command names the
pipeline's `log` path. Pipelines with `cron: []` have no schedule of their own
and are skipped.

The snapshot/live part reuses check_crontab_snapshot.py. One difference there
is no finding: a one-shot line whose time has passed may have removed itself
(ops CJ7). It is printed as INFO, so a count like "94 live, 95 declared" has a
named reason.

Output: one line per finding, then the counts, then the number of findings as
the last line. Exit 0 without findings, 1 with, 2 when a source is unreadable.
`--live-file` reads the live crontab from a file instead of `crontab -l`.
"""
import argparse
import datetime
import os
import pathlib
import subprocess
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_crontab_snapshot as snap  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "ops" / "crontab.txt"
REGISTER = ROOT / "config" / "expectations.yaml"


def schedule(line):
    return " ".join(line.split()[:5])


def register_entries(register):
    """(pipeline, schedule, log) for every declared schedule."""
    out = []
    for p in register.get("pipelines") or []:
        log = p.get("log")
        for c in p.get("cron") or []:
            out.append((p["name"], " ".join(str(c).split()), log))
    return out


def lines_for(entry_sched, log, jobs):
    return [j for j in jobs if schedule(j) == entry_sched and log and log in j]


def compare(register, snap_text, live_text, now):
    """Return (findings, infos, counts)."""
    findings, infos = [], []
    snap_jobs, live_jobs = snap.jobs(snap_text), snap.jobs(live_text)

    for j in sorted(live_jobs - snap_jobs):
        findings.append(f"LIVE, NOT IN SNAPSHOT: {j[:160]}")
    for j in sorted(snap_jobs - live_jobs):
        if snap.expired_one_shot(j, now):
            infos.append(f"INFO one-shot past its time, gone from live (CJ7): {j[:160]}")
        else:
            findings.append(f"SNAPSHOT, NOT LIVE: {j[:160]}")

    entries = register_entries(register)
    for name, sched, log in entries:
        if not log:
            findings.append(f"REGISTER {name}: schedule '{sched}' without a log path to match on")
            continue
        for label, jobs in (("SNAPSHOT", snap_jobs), ("LIVE", live_jobs)):
            if not lines_for(sched, log, jobs):
                findings.append(f"REGISTER {name} '{sched}' ({log}): no line in {label}")

    declared = {(sched, log) for _, sched, log in entries}
    for p in register.get("pipelines") or []:
        log = p.get("log")
        if not log or not p.get("cron"):
            continue
        for label, jobs in (("SNAPSHOT", snap_jobs), ("LIVE", live_jobs)):
            for j in sorted(jobs):
                if log in j and (schedule(j), log) not in declared:
                    findings.append(f"{label} runs for {p['name']} ({log}) at '{schedule(j)}', "
                                    f"not in register: {j[:120]}")

    counts = (f"{len(live_jobs)} live, {len(snap_jobs)} snapshot, "
              f"{len(entries)} register schedules over "
              f"{sum(1 for p in register.get('pipelines') or [] if p.get('cron'))} pipelines")
    return sorted(set(findings)), infos, counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live-file", help="read the live crontab from this file")
    a = ap.parse_args(argv)
    try:
        if a.live_file:
            live_text = pathlib.Path(a.live_file).read_text()
        else:
            p = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=60)
            if p.returncode != 0:
                raise OSError(f"crontab -l: exit {p.returncode}")
            live_text = p.stdout
        snap_text = SNAPSHOT.read_text()
        register = yaml.safe_load(REGISTER.read_text())
    except (OSError, yaml.YAMLError) as exc:
        print(f"UNREADABLE: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(-1)
        return 2
    now = datetime.datetime.now(datetime.timezone.utc)
    findings, infos, counts = compare(register, snap_text, live_text, now)
    for line in findings + infos:
        print(line)
    print(counts)
    print(len(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
