#!/bin/bash
# Weekly Traffic Report — Montag 08:00 UTC
set +x  # Disable trace mode unconditionally — protects against caller-supplied -x flag dumping secrets
set -eo pipefail

# Load only the secrets we need (TELEGRAM_*) instead of sourcing the full file.
# This limits trace exposure if -x is ever forced by external tooling.
SECRETS_FILE=/home/moltstack/.moltrust_secrets
TELEGRAM_BOT_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' "$SECRETS_FILE" | cut -d= -f2- | tr -d '"')
TG_CHAT=$(grep '^TELEGRAM_CHAT_ID_STATS=' "$SECRETS_FILE" | cut -d= -f2- | tr -d '"')
# undivided-chat fallback: keeps this working before the split chats exist
[ -n "$TG_CHAT" ] || TG_CHAT=$(grep '^TELEGRAM_CHAT_ID=' "$SECRETS_FILE" | cut -d= -f2- | tr -d '"')

LOG="/var/log/nginx/access.log"
STATS_LOG="/home/moltstack/moltstack/logs/weekly_traffic.log"

# Top Endpoints (strip query params)
TOP_ENDPOINTS=$(awk '{print $7}' $LOG | cut -d'?' -f1 | sort | uniq -c | sort -rn | head -10)

# Unique IPs
UNIQUE_IPS=$(awk '{print $1}' $LOG | sort -u | wc -l)

# MCP stats
MCP_TOTAL=$(grep -c '/mcp' $LOG 2>/dev/null || echo 0)
MCP_AUTH=$(grep -c '/mcp.*api_key=mt_' $LOG 2>/dev/null || echo 0)
MCP_UNAUTH=$((MCP_TOTAL - MCP_AUTH))
MCP_429=$(awk '$7 ~ /\/mcp/ && $9 == 429' $LOG 2>/dev/null | wc -l)

# Active mt_ keys
ACTIVE_KEYS=$(grep '/mcp.*api_key=mt_' $LOG 2>/dev/null | grep -o 'api_key=mt_[^& ]*' | sort -u | wc -l)

# A2A discovery
A2A_CALLS=$(grep -c 'well-known/a2a\|well-known/agent' $LOG 2>/dev/null || echo 0)

# Top user agents
TOP_UA=$(awk -F'"' '{print $6}' $LOG | sort | uniq -c | sort -rn | head -5)

# Top profiles
TOP_PROFILES=$(grep '/mcp.*profile=' $LOG 2>/dev/null | grep -o 'profile=[^& ]*' | sort | uniq -c | sort -rn | head -5)

# LLM Visibility tracking (added 2026-05-08, Phase 5)
# Counts hits from LLM training/retrieval crawlers per robots.txt whitelist
LLM_BOTS=$(grep -ciE "GPTBot|ChatGPT-User|OAI-SearchBot|ClaudeBot|anthropic-ai|Claude-Web|Google-Extended|Applebot-Extended|PerplexityBot|cohere-ai|CCBot" $LOG 2>/dev/null || echo 0)
TOP_LLM_BOT=$(grep -iE "GPTBot|ChatGPT-User|OAI-SearchBot|ClaudeBot|anthropic-ai|Claude-Web|Google-Extended|Applebot-Extended|PerplexityBot|cohere-ai|CCBot" $LOG | awk -F'"' '{print $6}' | awk '{print $1}' | sort | uniq -c | sort -rn | head -3)
LLMS_TXT_HITS=$(grep -cE '/llms\.txt|/api-llms\.txt' $LOG 2>/dev/null || echo 0)
AGENT_CARD_HITS=$(grep -c '/.well-known/agent-card.json' $LOG 2>/dev/null || echo 0)

# A2A Discovery probers (specialized A2A/ERC-8004 ecosystem aggregators)
A2A_PROBERS=$(grep -ciE "8004scan|ERC-8004-Prober|Waggle" $LOG 2>/dev/null || echo 0)
TOP_A2A_PROBER=$(grep -iE "8004scan|ERC-8004-Prober|Waggle" $LOG | awk -F'"' '{print $6}' | awk '{print $1}' | sort | uniq -c | sort -rn | head -3)

# Build message
MSG="📈 <b>Weekly Traffic Report</b>

<b>Overview:</b>
Unique IPs: ${UNIQUE_IPS}
Active API Keys (mt_): ${ACTIVE_KEYS}

<b>MCP:</b>
Total: ${MCP_TOTAL} (${MCP_AUTH} auth / ${MCP_UNAUTH} unauth)
Rate-limited (429): ${MCP_429}
A2A Discovery: ${A2A_CALLS}

<b>Top Endpoints:</b>
$(echo "$TOP_ENDPOINTS" | head -5 | awk '{printf "  %s %s\n", $1, $2}')

<b>Top Profiles:</b>
$(echo "$TOP_PROFILES" | head -3 | awk '{printf "  %s %s\n", $1, $2}')

<b>LLM Visibility:</b>
LLM Bot Hits: ${LLM_BOTS}
llms.txt + api-llms.txt: ${LLMS_TXT_HITS}
agent-card.json: ${AGENT_CARD_HITS}

<b>Top LLM Bots:</b>
$(echo "$TOP_LLM_BOT" | awk '{printf "  %s %s\n", $1, $2}')

<b>A2A Discovery Probers:</b>
A2A/ERC-8004 ecosystem hits: ${A2A_PROBERS}
$(echo "$TOP_A2A_PROBER" | awk '{printf "  %s %s\n", $1, $2}')

$(date -u +'%Y-%m-%d %H:%M UTC')"

# Send Telegram
if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TG_CHAT" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TG_CHAT" \
        -d parse_mode="HTML" \
        --data-urlencode "text=$MSG" > /dev/null 2>&1
    echo "[$(date -u +%Y-%m-%dT%H:%M:%S)] Weekly report sent" >> "$STATS_LOG"
fi

echo "Weekly traffic report complete."
