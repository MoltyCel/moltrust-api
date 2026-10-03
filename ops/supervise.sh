#!/bin/bash
# The server half of the mutual deadman, called by .github/workflows/supervise.yml.
#
# Two things happen here that must not happen inside the checks themselves: the
# heartbeat gets stamped and a correction may run. agents/supervision.py is
# read-only by design, so the supervisor's own bookkeeping lives out here.
#
#   ops/supervise.sh check [origin]   read-only, prints JSON, exit 0/1/2
#   ops/supervise.sh heal  [origin]   the positive list, then a second check
#
# `origin` says where the run came from and is an argument, not an environment
# variable: the deploy key carries a forced command and nothing in the
# environment crosses that boundary. workflow:<id> / dispatch:<id> /
# local:<user>@<host> / cron:<name>. Anything else, including nothing at all,
# is recorded as `unknown` and shows up in the collected report as a run whose
# origin nobody stated.
#
# The exit code is the contract with the workflow. Anything other than 0, 1 or
# 2 means the supervisor itself is broken, and the workflow says so rather than
# reading silence as health.
set -uo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

BASE=/home/moltstack/moltstack
VENV=$BASE/venv/bin/python
MODE=${1:-check}
ORIGIN=${2:-unknown}

cd "$BASE" || { echo '{"error":"no checkout at '"$BASE"'"}'; exit 3; }
set -a; . /home/moltstack/.moltrust_secrets 2>/dev/null || true; set +a
export PYTHONPATH=$BASE
export SUPERVISE_MODE=$MODE

"$VENV" ops/supervise_record.py stamp "$ORIGIN"

out=$(mktemp) || exit 3
trap 'rm -f "$out"' EXIT

case $MODE in
  check)
    # --alert, always. A red verdict that reaches nobody is the state this
    # whole path exists to end: on 03.10 the scheduled 16:17 run went red,
    # failed as designed, and sent nothing. Three kinds of red ring straight
    # through; the rest go into the twice-daily report.
    "$VENV" -m agents.supervision --json --alert > "$out"
    code=$?
    cat "$out"
    "$VENV" ops/supervise_record.py history "$out" "$ORIGIN"
    exit "$code"
    ;;
  heal)
    echo "--- vorher ---"
    "$VENV" -m agents.supervision
    echo
    echo "--- Korrektur, nur Positivliste ---"
    "$VENV" scripts/selfheal.py
    echo
    echo "--- nachher ---"
    "$VENV" -m agents.supervision --json --alert > "$out"
    code=$?
    "$VENV" -m agents.supervision
    "$VENV" ops/supervise_record.py history "$out" "$ORIGIN"
    echo "exit nachher=$code"
    exit "$code"
    ;;
  *)
    echo "usage: supervise.sh [check|heal] [origin]" >&2
    exit 3
    ;;
esac
