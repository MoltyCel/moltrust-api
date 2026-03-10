#!/bin/bash
source ~/.moltrust_secrets
API_KEY=$(cat ~/.anthropic_key)
BALANCE=$(curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":5,"messages":[{"role":"user","content":"hi"}]}' \
  -o /dev/null -w "%{http_code}")

if [ "$BALANCE" != "200" ]; then
  curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID}" \
    -d "text=⚠️ MolTrust: Anthropic API failing (HTTP $BALANCE). Check credits!"
fi
