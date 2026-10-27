#!/bin/bash
# Weekly Traffic Report — Montag 08:00 UTC
set +x  # Disable trace mode unconditionally — protects against caller-supplied -x flag dumping secrets
set -eo pipefail

# Through app/notify.py since ops NOTIFY-2 (27.10.2026): gate, throttle and the
# send log apply here too. No secrets are read here: notify resolves the
# Telegram token and chat id itself, so nothing secret sits in this shell.
API_DIR="$(cd "$(dirname "$0")/.." && pwd)"

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

# Credentials that expire. A token nobody renewed is a Monday spent finding out
# why a publish failed; 2026-09-27 was one of those, twice over.
NPM_TOKEN_EXPIRES="2026-12-27"
NPM_DAYS_LEFT=$(( ( $(date -u -d "$NPM_TOKEN_EXPIRES" +%s) - $(date -u +%s) ) / 86400 ))

# Build message
MSG="📈 <b>Weekly Traffic Report</b>

<b>Overview:</b>
Unique IPs: ${UNIQUE_IPS}
Active API Keys (mt_): ${ACTIVE_KEYS}
npm token expires: ${NPM_TOKEN_EXPIRES} (${NPM_DAYS_LEFT} days — renewal is a human step)

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
if PYTHONPATH="$API_DIR" "$API_DIR/venv/bin/python" -m app.notify \
     --channel stats --parse-mode HTML --stdin <<<"$MSG"; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%S)] Weekly report sent" >> "$STATS_LOG"
fi

echo "Weekly traffic report complete."
