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
# A measurement: it goes into the selftest digest, not to Telegram on its own
# (ops NOTIFY-3c, 27.10.2026; through app/notify.py since NOTIFY-2 before that).
API_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHONPATH="$API_DIR" "$API_DIR/venv/bin/python" -m app.tagesbericht_eingang \
  --quelle bazaar_index_check --stdin <<<"Bazaar-Index-Check: $TAIL" \
  || echo "daily report inbox: not filed (rc $?)" >&2
