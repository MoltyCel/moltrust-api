#!/usr/bin/env python3
"""The server runs what main says, or somebody hears about it.

On 21.10.2026 four merges to main got no workflow run at all: c8dfa8c,
23fe433, 345138b and a062d80. No deploy, no CI, nothing. GitHub had lost the
push events; the events API lists none for them, and GitHub's status page
reported delayed webhook deliveries that afternoon. 345138b went live three and
a half hours later, carried by the next merge that did get its event, and
nothing on our side noticed the gap.

The cause is outside this repository, so this does not fix GitHub. It closes
the gap behind it, from the server, because a GitHub schedule is no backstop
for a GitHub outage (supervise.yml measured a third of hourly runs delivered):

1. Catch-up. When main's head has been on main for CATCH_UP_MIN minutes, the
   server does not run it and no deploy run exists for it, the push event was
   lost. Dispatch deploy.yml once for that head. A head that has a deploy run,
   even a failed one, is never dispatched again: a failed deploy rolled back
   for a reason, and repeating it is not this script's call.
2. Alert. When the server has differed from main for more than ALERT_MIN
   minutes, one Telegram message per head, in plain words with what to do.

    deploy_match.py           cron: catch-up and alert
    deploy_match.py --count   invariant: read-only, last line 0 or 1
    deploy_match.py --probe   send the alert text once, marked as a probe

The age is measured from the head's commit time, which for a squash merge is
the merge time. Last line of --count is the number of deviations: 0 when the
server runs main or the head is younger than ALERT_MIN, 1 otherwise.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

import requests

WURZEL = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL))

from app import gh, notify  # noqa: E402

REPO = "MoltyCel/moltrust-api"
API = f"https://api.github.com/repos/{REPO}"
WORKFLOW = "deploy.yml"
CHECKOUT = pathlib.Path("/home/moltstack/moltstack")
STATE = pathlib.Path(os.path.expanduser("~/.deploy_match.json"))
CATCH_UP_MIN = 15
ALERT_MIN = 30


def _get(path: str, tok: str) -> dict:
    r = requests.get(f"{API}{path}", timeout=20, headers={
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r.json()


def main_head(tok: str) -> tuple[str, dt.datetime]:
    c = _get("/commits/main", tok)
    when = dt.datetime.fromisoformat(
        c["commit"]["committer"]["date"].replace("Z", "+00:00"))
    return c["sha"], when


def deploy_runs(sha: str, tok: str) -> int:
    return _get(f"/actions/workflows/{WORKFLOW}/runs?head_sha={sha}&per_page=5",
                tok)["total_count"]


def dispatch(tok: str) -> None:
    r = requests.post(f"{API}/actions/workflows/{WORKFLOW}/dispatches",
                      timeout=20, json={"ref": "main"}, headers={
                          "Authorization": f"Bearer {tok}",
                          "Accept": "application/vnd.github+json"})
    r.raise_for_status()


def server_head(checkout: pathlib.Path = CHECKOUT) -> str:
    return subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True,
                          timeout=10).stdout.strip()


def load_state(path: pathlib.Path = STATE) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: pathlib.Path = STATE) -> None:
    path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def alert_text(server: str, head: str, minutes: int, runs: int,
               dispatched: str | None) -> str:
    if dispatched:
        lauf = (f"GitHub hat dazu keinen Deploy-Lauf gestartet. Der Nachhol-Lauf "
                f"von {dispatched} UTC hat den Server nicht auf main gebracht.")
    elif runs:
        lauf = ("Es gibt einen Deploy-Lauf dazu, aber der Server hat den Stand "
                "nicht übernommen. Vermutlich ist der Deploy gescheitert oder "
                "hängt.")
    else:
        lauf = "Es gibt keinen Deploy-Lauf dazu."
    return (
        f"Deploy hängt: Der Server läuft auf {server[:7]}, auf main liegt seit "
        f"{minutes} Minuten {head[:7]}. {lauf}\n\n"
        "Was tun: In GitHub unter Actions → deploy → Run workflow den Deploy "
        "von main starten. Scheitert er, steht der Grund in logs/deploy.log "
        "auf dem Server und im Log des Laufs.")


def check(now: dt.datetime, head: str, merged: dt.datetime, server: str,
          runs: int, state: dict, *, act: bool) -> tuple[int, list[str], dict]:
    """Decide for one reading. Returns (deviations, actions, new state).

    actions is a list of "dispatch" and "alert"; with act=False it is always
    empty and the state is returned unchanged.
    """
    if server == head:
        return 0, [], ({} if act else state)
    minutes = int((now - merged).total_seconds() // 60)
    deviation = 1 if minutes > ALERT_MIN else 0
    if not act:
        return deviation, [], state
    new = dict(state) if state.get("head") == head else {"head": head}
    actions = []
    if minutes >= CATCH_UP_MIN and runs == 0 and "dispatched" not in new:
        actions.append("dispatch")
        new["dispatched"] = now.strftime("%H:%M")
    if deviation and "alerted" not in new:
        actions.append("alert")
        new["alerted"] = now.strftime("%Y-%m-%dT%H:%MZ")
    return deviation, actions, new


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--count", action="store_true")
    g.add_argument("--probe", action="store_true")
    a = ap.parse_args(argv)

    tok = gh.token()
    try:
        if not tok:
            raise RuntimeError(f"{gh.NAME} is not set")
        head, merged = main_head(tok)
        server = server_head()
        runs = deploy_runs(head, tok) if server != head else 0
    except (requests.RequestException, subprocess.SubprocessError,
            OSError, RuntimeError, KeyError, ValueError) as e:
        print(f"UNREADABLE: {type(e).__name__}: {e}")
        return 2

    now = _now()
    minutes = int((now - merged).total_seconds() // 60)

    if a.probe:
        text = ("PROBE, nichts zu tun. So sieht die Meldung aus:\n\n"
                + alert_text(server, head, ALERT_MIN + 1, runs, None))
        ok = notify.send_telegram(text, channel=notify.ALERTS)
        print(f"probe sent={ok}")
        return 0 if ok else 1

    state = load_state()
    deviation, actions, new = check(now, head, merged, server, runs, state,
                                    act=not a.count)
    if server == head:
        print(f"  server = main {head[:7]}")
    else:
        print(f"  server {server[:7]}, main {head[:7]} for {minutes} min, "
              f"deploy runs for main: {runs}")
    if "dispatch" in actions:
        try:
            dispatch(tok)
            print(f"  dispatched {WORKFLOW} for main")
        except requests.RequestException as e:
            print(f"  dispatch failed: {type(e).__name__}: {e}")
            new.pop("dispatched", None)
    if "alert" in actions:
        text = alert_text(server, head, minutes, runs, new.get("dispatched"))
        if notify.send_telegram(text, channel=notify.ALERTS):
            print("  alert sent")
        else:
            print("  alert not sent")
            new.pop("alerted", None)
    if not a.count and new != state:
        save_state(new)
    print(deviation)
    return 0


if __name__ == "__main__":
    sys.exit(main())
