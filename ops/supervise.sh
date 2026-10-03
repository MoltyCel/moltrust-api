#!/bin/bash
# The server half of the mutual deadman, called by .github/workflows/supervise.yml.
#
# Two things happen here that must not happen inside the checks themselves: the
# heartbeat gets stamped and a correction may run. agents/supervision.py is
# read-only by design, so the supervisor's own bookkeeping lives out here.
#
#   ops/supervise.sh check    read-only, prints JSON, exit 0/1/2 = green/yellow/red
#   ops/supervise.sh heal     the positive list, then a second check
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

cd "$BASE" || { echo '{"error":"no checkout at '"$BASE"'"}'; exit 3; }
set -a; . /home/moltstack/.moltrust_secrets 2>/dev/null || true; set +a
export PYTHONPATH=$BASE
export SUPERVISE_MODE=$MODE

"$VENV" ops/supervise_record.py stamp

out=$(mktemp) || exit 3
trap 'rm -f "$out"' EXIT

case $MODE in
  check)
    "$VENV" -m agents.supervision --json > "$out"
    code=$?
    cat "$out"
    "$VENV" ops/supervise_record.py history "$out"
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
    "$VENV" -m agents.supervision --json > "$out"
    code=$?
    "$VENV" -m agents.supervision
    "$VENV" ops/supervise_record.py history "$out"
    echo "exit nachher=$code"
    exit "$code"
    ;;
  *)
    echo "usage: supervise.sh [check|heal]" >&2
    exit 3
    ;;
esac
