#!/usr/bin/env python3
"""Did the attestation watch send both of its daily digests?

The watch at scripts/watch_attestation_window.py sends one digest at 08:00 and
one at 20:00 UTC, every day, including days on which it found nothing. That is
the point: a watch that reports "0 / 0" is distinguishable from a watch that
has stopped running, and a watch that only speaks when it has something to say
is not. Which is why this check exists at all — on 2026-10-05 the first version
of that watch ran twelve times, alerted twelve times about the normal state,
and wrote nothing to its log, and none of that was visible from the outside.

So a missing digest is a finding here, not an absence of findings. Unreadable
state is also a finding: no state file means either the watch never ran or
somebody removed its memory, and neither is green.

Prints the number of missing digest slots over the last 24 hours as the last
line. Exit 0 when none, 1 when some, 2 when the answer cannot be established.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

UTC = dt.timezone.utc
STATE = os.path.expanduser("~/.attestation_watch_state.json")
DIGEST_HOURS = (8, 20)

# One cron tick is 15 minutes. A digest written within the tick that follows
# its slot is on time; the grace keeps a slow run from reading as a miss.
GRACE = dt.timedelta(minutes=30)


def due_slots(now: dt.datetime) -> list[dt.datetime]:
    """Digest slots whose time plus grace has passed, within the last 24 h."""
    out = []
    for day in (now.date(), now.date() - dt.timedelta(days=1)):
        for h in DIGEST_HOURS:
            t = dt.datetime.combine(day, dt.time(h), tzinfo=UTC)
            if t + GRACE <= now and now - t <= dt.timedelta(hours=24):
                out.append(t)
    return sorted(out)


def main() -> int:
    now = dt.datetime.now(UTC)
    try:
        with open(STATE) as f:
            st = json.load(f)
    except FileNotFoundError:
        print(f"UNREADABLE: {STATE} fehlt — die Wache hat nie gelaufen oder ihr "
              "Gedaechtnis ist weg", file=sys.stderr)
        print(-1)
        return 2
    except Exception as exc:  # noqa: BLE001 - a broken state file is not green either
        print(f"UNREADABLE: {STATE} nicht lesbar: {type(exc).__name__}", file=sys.stderr)
        print(-1)
        return 2

    sent = st.get("digests")
    if sent is None:
        print("UNREADABLE: Zustandsdatei ohne digests-Feld — alte Fassung der Wache "
              "oder ueberschrieben", file=sys.stderr)
        print(-1)
        return 2

    slots = due_slots(now)
    if not slots:
        print("keine faellige Meldung in den letzten 24 h")
        print(0)
        return 0

    missing = 0
    for slot in slots:
        key = f"{slot:%Y-%m-%dT%H}"
        if key in sent:
            print(f"ok {key}Z — gesendet {sent[key]}")
        else:
            missing += 1
            print(f"FEHLT {key}Z — faellig seit "
                  f"{(now - slot).total_seconds() / 3600:.1f} h, keine Meldung")

    # The watch writes last_run on every tick. A fresh last_run with a missing
    # digest means the watch runs and does not report; a stale one means it
    # does not run. The distinction is the whole reason this file exists, so
    # say which it is instead of leaving it to be guessed.
    last = st.get("last_run")
    if missing and last:
        try:
            age = (now - dt.datetime.fromisoformat(last)).total_seconds() / 60
            print(f"  letzter Lauf der Wache vor {age:.0f} min — "
                  f"{'sie laeuft und meldet nicht' if age < 30 else 'sie laeuft nicht'}")
        except Exception:  # noqa: BLE001 - a malformed timestamp is its own hint
            print(f"  last_run unlesbar: {last!r}")
    elif missing:
        print("  kein last_run in der Zustandsdatei")

    print(missing)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
