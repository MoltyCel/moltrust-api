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
set -a; . /home/moltstack/.moltrust_secrets 2>/dev/null; set +a
# undivided-chat fallback: keeps this working before the split chats exist
TG_CHAT="${TELEGRAM_CHAT_ID_STATS:-$TELEGRAM_CHAT_ID}"
if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TG_CHAT" ]; then
  curl -s -m 15 -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage"     -d "chat_id=$TG_CHAT" --data-urlencode "text=Bazaar-Index-Check: $TAIL" > /dev/null
fi
