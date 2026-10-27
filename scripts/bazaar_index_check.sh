#!/bin/bash
# One-shot: is MolTrust in the CDP x402 Bazaar discovery index yet?
# Listing is a by-product of settling through the CDP facilitator with the
# bazaar extension; first settlement was 2026-09-15 14:00 UTC.
OUT=/home/moltstack/logs/bazaar_index.log
{
  echo "--- $(date -u +%Y-%m-%dT%H:%M:%SZ) ---"
  /usr/bin/python3 /home/moltstack/moltstack/scripts/bazaar_index_check.py
} >> "$OUT" 2>&1
TAIL=$(tail -3 "$OUT" | tr "\n" " ")
# Through app/notify.py since ops NOTIFY-2 (27.10.2026): gate, throttle and the
# send log apply here too. notify resolves token and chat id itself.
API_DIR="$(cd "$(dirname "$0")/.." && pwd)"
melden() {  # melden <channel> <text>
  PYTHONPATH="$API_DIR" "$API_DIR/venv/bin/python" -m app.notify \
    --channel "$1" --stdin <<<"$2" || echo "notify: not sent (rc $?)" >&2
}
melden stats "Bazaar-Index-Check: $TAIL"
