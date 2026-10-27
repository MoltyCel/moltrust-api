#!/bin/bash
source ~/.moltrust_secrets
API_KEY=$(cat ~/.anthropic_key)
BALANCE=$(curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":5,"messages":[{"role":"user","content":"hi"}]}' \
  -o /dev/null -w "%{http_code}")

# Through app/notify.py since ops NOTIFY-2 (27.10.2026): gate, throttle and the
# send log apply here too. notify resolves token and chat id itself.
API_DIR="$(cd "$(dirname "$0")/.." && pwd)"
melden() {  # melden <channel> <text>
  PYTHONPATH="$API_DIR" "$API_DIR/venv/bin/python" -m app.notify \
    --channel "$1" --stdin <<<"$2" || echo "notify: not sent (rc $?)" >&2
}

if [ "$BALANCE" != "200" ]; then
  melden money "⚠️ MolTrust: Anthropic API failing (HTTP $BALANCE). Check credits!"
fi
