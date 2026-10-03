#!/bin/bash
# The server half of the mutual deadman, called by .github/workflows/supervise.yml.
#
# Two things happen here that must not happen inside selftest.py: the heartbeat
# gets stamped, and a correction may run. selftest.py is read-only by design, so
# the supervisor's own bookkeeping lives out here.
#
#   ops/supervise.sh check    read-only, prints JSON, exit 0/1/2 = green/yellow/red
#   ops/supervise.sh heal     the positive list, then a second check
#
# The exit code is the contract with the workflow: 0 nothing to do, 1 a
# deviation the positive list can name, 2 unknown. Anything else is a broken
# supervisor and the workflow says so rather than reading silence as health.
set -uo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

BASE=/home/moltstack/moltstack
VENV=$BASE/venv/bin/python
HEARTBEAT=$BASE/data/supervise_heartbeat.json
MODE=${1:-check}

cd "$BASE" || { echo '{"error":"no checkout"}'; exit 3; }
set -a; . /home/moltstack/.moltrust_secrets 2>/dev/null || true; set +a
export PYTHONPATH=$BASE

# Stamped before the check, not after: a run that dies half way still proves
# the workflow reached the server, and that is the only thing this file claims.
# The run id comes from the workflow so the server can name which run it was.
python3 - "$HEARTBEAT" "${GITHUB_RUN_ID:-manual}" <<'PY'
import datetime, json, os, sys
path, run = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    json.dump({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "run": run}, f)
os.chmod(path, 0o640)
PY

case $MODE in
  check)
    "$VENV" -m agents.supervision --json
    exit $?
    ;;
  heal)
    echo "--- vorher ---"
    "$VENV" -m agents.supervision
    before=$?
    echo "--- Korrektur, nur Positivliste ---"
    "$VENV" scripts/selfheal.py
    echo "--- nachher ---"
    "$VENV" -m agents.supervision
    after=$?
    echo "exit vorher=$before nachher=$after"
    exit "$after"
    ;;
  *)
    echo "usage: supervise.sh [check|heal]" >&2
    exit 3
    ;;
esac
