"""Is the X API still answering, or has the project run out of credits?

On 2026-09-25 at 12:05 UTC every X call started coming back

    402 {"detail": "credits depleted", ...}

for reads and writes alike. The digest stopped posting, the weekly proof post
failed, the reply radar went blind, and nothing measured anything. It was found
44 hours later, by hand, while looking at something else. Each agent had
written ERROR to its own log and nowhere else.

Two ways of noticing, because neither is sufficient alone:

  the probe   one cheap read per run. Catches the outage even when no agent
              has run since it began.
  the logs    the first `credits depleted` of the day, across every agent log.
              Catches it when the probe itself cannot run, costs nothing, and
              names which agent hit it first.

`/2/usage/tweets` would answer this properly, but it requires OAuth 2.0
app-only and every caller here signs OAuth 1.0a, so it answers 403 for us. The
proxy is the 402 itself.
"""
from __future__ import annotations

import datetime
import glob
import logging
import os
import re

import requests

log = logging.getLogger("x_budget")

LOG_DIR = os.path.expanduser("~/moltstack/logs")
OUR_USER_ID = "2023702578836779008"          # @moltrust

# Anything that means "we are out of budget", however X words it.
DEPLETED_RE = re.compile(r"credits[- ]depleted|\b402\b.*payment required", re.I)

# A log line starts with [2026-09-25T12:05:03].
STAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})\]")

# The cheapest read that still consumes budget, so it fails exactly when the
# agents do. /2/users/me does not — it answered 200 throughout the outage,
# which is why "the account is fine" was true and useless.
PROBE_URL = f"https://api.twitter.com/2/users/{OUR_USER_ID}/tweets"


def probe(auth) -> dict:
    """One read. `ok` False with `depleted` True is the case this exists for."""
    if auth is None:
        return {"ok": False, "depleted": False, "detail": "no X credentials"}
    try:
        r = requests.get(PROBE_URL, params={"max_results": 5}, auth=auth, timeout=20)
    except Exception as e:
        return {"ok": False, "depleted": False, "detail": f"{type(e).__name__}: {e}"}
    if r.status_code == 200:
        return {"ok": True, "depleted": False, "detail": "reads answer 200"}
    depleted = r.status_code == 402 or bool(DEPLETED_RE.search(r.text))
    return {"ok": False, "depleted": depleted,
            "detail": f"HTTP {r.status_code}: {r.text[:140]}"}


def first_402_today(now: datetime.datetime | None = None,
                    log_dir: str = LOG_DIR) -> dict | None:
    """The day's first depletion across every agent log, or None.

    Reads only today's lines, so a two-week-old outage in a rotated log cannot
    raise an alarm about today.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    day = now.strftime("%Y-%m-%d")
    earliest = None
    for path in sorted(glob.glob(os.path.join(log_dir, "*.log"))):
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    m = STAMP_RE.match(line)
                    if not m or m.group(1) != day:
                        continue
                    if not DEPLETED_RE.search(line):
                        continue
                    stamp = f"{m.group(1)}T{m.group(2)}"
                    if earliest is None or stamp < earliest["at"]:
                        earliest = {"at": stamp, "agent": os.path.basename(path),
                                    "line": line.strip()[:200]}
                    break          # the first of the day in this file is enough
        except OSError:
            continue
    return earliest


def check(auth, now: datetime.datetime | None = None,
          log_dir: str = LOG_DIR) -> dict:
    """What the watchdog reports. `ok` False means X is not usable right now."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    p = probe(auth)
    logged = first_402_today(now, log_dir)

    if p["depleted"]:
        detail = "credits depleted — reads and writes both refused"
        if logged:
            detail += f"; first seen today {logged['at']}Z in {logged['agent']}"
        return {"ok": False, "surface": "XBudget", "depleted": True,
                "detail": detail, "first_today": logged}
    if logged:
        # The probe passes and the logs show a 402 earlier today: either it has
        # just been topped up, or the budget is running out in bursts. Both are
        # worth one line.
        return {"ok": False, "surface": "XBudget", "depleted": False,
                "detail": (f"reads answer again, but {logged['agent']} hit "
                           f"credits-depleted at {logged['at']}Z today"),
                "first_today": logged}
    return {"ok": p["ok"], "surface": "XBudget", "depleted": False,
            "detail": p["detail"], "first_today": None}
