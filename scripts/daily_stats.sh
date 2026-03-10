#!/bin/bash
# MolTrust Daily Stats — runs at 07:00 and 19:00 UTC
set -eo pipefail

# --- Config ---
VENV="/home/moltstack/moltstack/venv"
LOG="/home/moltstack/moltstack/logs/daily_stats.log"
SECRETS="/home/moltstack/.moltrust_secrets"
MAILTO="info@moltrust.ch"
DB_NAME="moltstack"
DB_USER="moltstack"
MOLTBOOK_STATE="/home/moltstack/moltstack/moltbook/state.json"

# Load secrets
source "$SECRETS"
TG_TOKEN="$TELEGRAM_BOT_TOKEN"
TG_CHAT="$TELEGRAM_CHAT_ID" 

TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M UTC")
HOUR=$(date -u +"%H")

if [ "$HOUR" -lt 12 ]; then
    GREETING="Good morning"
else
    GREETING="Evening update"
fi

# --- Query DB ---
query() {
    psql -h localhost -U "$DB_USER" -d "$DB_NAME" -t -A -c "$1" 2>/dev/null | tr -d '[:space:]'
}

query_rows() {
    psql -h localhost -U "$DB_USER" -d "$DB_NAME" -t -A -c "$1" 2>/dev/null
}

# --- Agents & Credentials ---
TOTAL_AGENTS=$(query "SELECT COUNT(*) FROM agents WHERE lower(display_name) NOT LIKE '%probe%' AND lower(display_name) NOT LIKE '%test%' AND lower(display_name) NOT LIKE '%ambassador%'")
NEW_12H=$(query "SELECT COUNT(*) FROM agents WHERE created_at > now() - interval '12 hours' AND lower(display_name) NOT LIKE '%probe%' AND lower(display_name) NOT LIKE '%test%' AND lower(display_name) NOT LIKE '%ambassador%'")
TOTAL_CREDS=$(query "SELECT COUNT(*) FROM credentials")
TOTAL_RATINGS=$(query "SELECT COUNT(*) FROM ratings")
AVG_SCORE=$(query "SELECT COALESCE(ROUND(AVG(score)::numeric, 2), 0) FROM ratings")

PLATFORMS=$(query_rows "SELECT platform, COUNT(*) FROM agents WHERE lower(display_name) NOT LIKE '%probe%' AND lower(display_name) NOT LIKE '%test%' AND lower(display_name) NOT LIKE '%ambassador%' GROUP BY platform ORDER BY COUNT(*) DESC")

RECENT_5=$(query_rows "SELECT display_name, platform, created_at FROM agents WHERE lower(display_name) NOT LIKE '%probe%' AND lower(display_name) NOT LIKE '%test%' AND lower(display_name) NOT LIKE '%ambassador%' ORDER BY created_at DESC LIMIT 5")

# --- Credits ---
TOTAL_CREDIT_BALANCE=$(query "SELECT COALESCE(SUM(balance), 0) FROM credit_balances")
CREDITS_CONSUMED_12H=$(query "SELECT COALESCE(SUM(amount), 0) FROM credit_transactions WHERE tx_type = 'api_call' AND created_at > now() - interval '12 hours'")
CREDIT_TRANSFERS_12H=$(query "SELECT COUNT(*) FROM credit_transactions WHERE tx_type = 'transfer' AND created_at > now() - interval '12 hours'")
PAID_API_CALLS_12H=$(query "SELECT COUNT(*) FROM credit_transactions WHERE tx_type = 'api_call' AND created_at > now() - interval '12 hours'")
GRANTS_12H=$(query "SELECT COALESCE(SUM(amount), 0) FROM credit_transactions WHERE tx_type = 'grant' AND created_at > now() - interval '12 hours'")

# --- Moltbook ---
MB_POSTS="n/a"
MB_UPVOTED="n/a"
MB_COMMENTED="n/a"
MB_WELCOMED="n/a"
if [ -f "$MOLTBOOK_STATE" ]; then
    MB_POSTS=$(python3 -c "import json; d=json.load(open('$MOLTBOOK_STATE')); print(d.get('post_index', 0))" 2>/dev/null || echo "n/a")
    MB_UPVOTED=$(python3 -c "import json; d=json.load(open('$MOLTBOOK_STATE')); print(len(d.get('upvoted', [])))" 2>/dev/null || echo "n/a")
    MB_COMMENTED=$(python3 -c "import json; d=json.load(open('$MOLTBOOK_STATE')); print(len(d.get('commented', [])))" 2>/dev/null || echo "n/a")
    MB_WELCOMED=$(python3 -c "import json; d=json.load(open('$MOLTBOOK_STATE')); print(len(d.get('welcomed', [])))" 2>/dev/null || echo "n/a")
fi

# --- Build Telegram message ---
TG_MSG="$GREETING — MolTrust Stats

Agents: $TOTAL_AGENTS total (+$NEW_12H last 12h)
Credentials: $TOTAL_CREDS
Ratings: $TOTAL_RATINGS (avg $AVG_SCORE/5)

Credits:
  Total balance: $TOTAL_CREDIT_BALANCE
  Consumed (12h): $CREDITS_CONSUMED_12H ($PAID_API_CALLS_12H paid calls)
  Granted (12h): $GRANTS_12H
  Transfers (12h): $CREDIT_TRANSFERS_12H

Moltbook:
  Posts: $MB_POSTS | Upvoted: $MB_UPVOTED
  Commented: $MB_COMMENTED | Welcomed: $MB_WELCOMED

Platforms:"

while IFS='|' read -r plat count; do
    [ -z "$plat" ] && continue
    TG_MSG+="
  $plat: $count"
done <<< "$PLATFORMS"

TG_MSG+="

Recent registrations:"

while IFS='|' read -r name plat ts; do
    [ -z "$name" ] && continue
    short_ts=$(echo "$ts" | cut -d. -f1)
    TG_MSG+="
  $name ($plat) — $short_ts"
done <<< "$RECENT_5"

TG_MSG+="

$TIMESTAMP"

# --- Send Telegram ---
if [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d chat_id="$TG_CHAT" \
        -d parse_mode="HTML" \
        --data-urlencode "text=$TG_MSG" > /dev/null 2>&1
    echo "[$(date -u +%Y-%m-%dT%H:%M:%S)] Telegram sent" >> "$LOG"
else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%S)] Telegram skipped (no token)" >> "$LOG"
fi

# --- Build email ---
SUBJECT="MolTrust Daily Stats — $TIMESTAMP"

EMAIL_BODY="<html><body style='margin:0;padding:0;background:#0a0a0f;font-family:Arial,sans-serif;'>
<table width='100%' cellpadding='0' cellspacing='0' style='background:#0a0a0f;padding:30px 20px;'>
<tr><td align='center'>
<table width='600' cellpadding='0' cellspacing='0' style='max-width:600px;width:100%;'>

<tr><td style='padding:20px 30px;text-align:center;'>
<span style='font-family:monospace;font-size:22px;font-weight:bold;'><span style='color:#d4a843;'>Mol</span><span style='color:#e8734a;'>Trust</span></span>
<div style='color:#8a8895;font-size:12px;margin-top:4px;'>$GREETING — $TIMESTAMP</div>
</td></tr>

<tr><td style='background:#16161f;border:1px solid #2a2a3a;border-radius:8px;padding:30px;'>

<table width='100%' cellpadding='0' cellspacing='0' style='margin-bottom:20px;'>
<tr>
<td style='text-align:center;padding:12px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:6px;width:25%;'>
<div style='color:#d4a843;font-family:monospace;font-size:28px;font-weight:bold;'>$TOTAL_AGENTS</div>
<div style='color:#8a8895;font-size:11px;text-transform:uppercase;letter-spacing:1px;'>Agents</div>
</td>
<td width='8'></td>
<td style='text-align:center;padding:12px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:6px;width:25%;'>
<div style='color:#5cb85c;font-family:monospace;font-size:28px;font-weight:bold;'>+$NEW_12H</div>
<div style='color:#8a8895;font-size:11px;text-transform:uppercase;letter-spacing:1px;'>New (12h)</div>
</td>
<td width='8'></td>
<td style='text-align:center;padding:12px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:6px;width:25%;'>
<div style='color:#e8734a;font-family:monospace;font-size:28px;font-weight:bold;'>$TOTAL_CREDS</div>
<div style='color:#8a8895;font-size:11px;text-transform:uppercase;letter-spacing:1px;'>Credentials</div>
</td>
<td width='8'></td>
<td style='text-align:center;padding:12px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:6px;width:25%;'>
<div style='color:#60a5fa;font-family:monospace;font-size:28px;font-weight:bold;'>$TOTAL_RATINGS</div>
<div style='color:#8a8895;font-size:11px;text-transform:uppercase;letter-spacing:1px;'>Ratings</div>
</td>
</tr>
</table>

<!-- Credits Row -->
<table width='100%' cellpadding='0' cellspacing='0' style='margin-bottom:20px;'>
<tr>
<td style='text-align:center;padding:12px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:6px;width:25%;'>
<div style='color:#d4a843;font-family:monospace;font-size:28px;font-weight:bold;'>$TOTAL_CREDIT_BALANCE</div>
<div style='color:#8a8895;font-size:11px;text-transform:uppercase;letter-spacing:1px;'>Total Credits</div>
</td>
<td width='8'></td>
<td style='text-align:center;padding:12px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:6px;width:25%;'>
<div style='color:#e8734a;font-family:monospace;font-size:28px;font-weight:bold;'>$CREDITS_CONSUMED_12H</div>
<div style='color:#8a8895;font-size:11px;text-transform:uppercase;letter-spacing:1px;'>Used (12h)</div>
</td>
<td width='8'></td>
<td style='text-align:center;padding:12px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:6px;width:25%;'>
<div style='color:#5cb85c;font-family:monospace;font-size:28px;font-weight:bold;'>$PAID_API_CALLS_12H</div>
<div style='color:#8a8895;font-size:11px;text-transform:uppercase;letter-spacing:1px;'>Paid Calls (12h)</div>
</td>
<td width='8'></td>
<td style='text-align:center;padding:12px;background:#0a0a0f;border:1px solid #2a2a3a;border-radius:6px;width:25%;'>
<div style='color:#60a5fa;font-family:monospace;font-size:28px;font-weight:bold;'>$CREDIT_TRANSFERS_12H</div>
<div style='color:#8a8895;font-size:11px;text-transform:uppercase;letter-spacing:1px;'>Transfers (12h)</div>
</td>
</tr>
</table>

<div style='height:1px;background:#2a2a3a;margin:16px 0;'></div>

<!-- Moltbook -->
<h3 style='color:#e8e6e1;font-size:14px;margin:0 0 10px;'>Moltbook</h3>
<table width='100%' cellpadding='0' cellspacing='0' style='margin-bottom:16px;'>
<tr>
<td style='color:#8a8895;font-size:13px;padding:4px 0;'>Posts</td>
<td style='color:#d4a843;font-family:monospace;font-size:13px;text-align:right;padding:4px 0;'>$MB_POSTS</td>
</tr>
<tr>
<td style='color:#8a8895;font-size:13px;padding:4px 0;'>Upvoted</td>
<td style='color:#d4a843;font-family:monospace;font-size:13px;text-align:right;padding:4px 0;'>$MB_UPVOTED</td>
</tr>
<tr>
<td style='color:#8a8895;font-size:13px;padding:4px 0;'>Commented</td>
<td style='color:#d4a843;font-family:monospace;font-size:13px;text-align:right;padding:4px 0;'>$MB_COMMENTED</td>
</tr>
<tr>
<td style='color:#8a8895;font-size:13px;padding:4px 0;'>Welcomed</td>
<td style='color:#d4a843;font-family:monospace;font-size:13px;text-align:right;padding:4px 0;'>$MB_WELCOMED</td>
</tr>
</table>

<div style='height:1px;background:#2a2a3a;margin:16px 0;'></div>

<h3 style='color:#e8e6e1;font-size:14px;margin:0 0 10px;'>Platforms</h3>
<table width='100%' cellpadding='0' cellspacing='0'>"

while IFS='|' read -r plat count; do
    [ -z "$plat" ] && continue
    EMAIL_BODY+="<tr>
<td style='color:#8a8895;font-size:13px;padding:4px 0;'>$plat</td>
<td style='color:#d4a843;font-family:monospace;font-size:13px;text-align:right;padding:4px 0;'>$count</td>
</tr>"
done <<< "$PLATFORMS"

EMAIL_BODY+="</table>

<div style='height:1px;background:#2a2a3a;margin:16px 0;'></div>

<h3 style='color:#e8e6e1;font-size:14px;margin:0 0 10px;'>Recent Registrations</h3>
<table width='100%' cellpadding='0' cellspacing='0'>"

while IFS='|' read -r name plat ts; do
    [ -z "$name" ] && continue
    short_ts=$(echo "$ts" | cut -d. -f1)
    EMAIL_BODY+="<tr>
<td style='color:#e8e6e1;font-size:13px;padding:4px 0;'>$name</td>
<td style='color:#8a8895;font-size:12px;padding:4px 8px;'>$plat</td>
<td style='color:#555566;font-family:monospace;font-size:11px;text-align:right;padding:4px 0;'>$short_ts</td>
</tr>"
done <<< "$RECENT_5"

EMAIL_BODY+="</table>

</td></tr>

<tr><td style='padding:16px 30px;text-align:center;'>
<p style='color:#555566;font-size:11px;margin:0;'>MolTrust Daily Stats — CryptoKRI GmbH</p>
</td></tr>

</table>
</td></tr>
</table>
</body></html>"

# --- Send email ---
TMPFILE=$(mktemp /tmp/daily_stats_XXXXXX.html)
echo "$EMAIL_BODY" > "$TMPFILE"

EMAIL_SUBJECT="$SUBJECT" \
EMAIL_TO="$MAILTO" \
REPORT_FILE="$TMPFILE" \
python3 -c "
import smtplib, os
from email.mime.text import MIMEText

with open(os.environ['REPORT_FILE']) as f:
    body = f.read()

msg = MIMEText(body, 'html')
msg['From'] = os.environ['SMTP_USER']
msg['To'] = os.environ['EMAIL_TO']
msg['Subject'] = os.environ['EMAIL_SUBJECT']

try:
    with smtplib.SMTP(os.environ['SMTP_HOST'], int(os.environ['SMTP_PORT'])) as s:
        s.starttls()
        s.login(os.environ['SMTP_USER'], os.environ['SMTP_PASS'])
        s.send_message(msg)
    print('Email sent to ' + os.environ['EMAIL_TO'])
except Exception as e:
    print(f'Failed to send email: {e}')
"

rm -f "$TMPFILE"

# --- Log ---
echo "[$(date -u +%Y-%m-%dT%H:%M:%S)] agents=$TOTAL_AGENTS new_12h=$NEW_12H creds=$TOTAL_CREDS ratings=$TOTAL_RATINGS credits=$TOTAL_CREDIT_BALANCE consumed_12h=$CREDITS_CONSUMED_12H paid_calls_12h=$PAID_API_CALLS_12H transfers_12h=$CREDIT_TRANSFERS_12H mb_posts=$MB_POSTS mb_upvoted=$MB_UPVOTED" >> "$LOG"
echo "Daily stats complete."
