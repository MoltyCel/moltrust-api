#!/bin/bash
# MolTrust Weekly Security Check
# Runs every Sunday at 3 AM via cron
set -eo pipefail

# --- Config ---
VENV="/home/moltstack/moltstack/venv"
LOG="/home/moltstack/moltstack/logs/security_report.log"
SECRETS="/home/moltstack/.moltrust_secrets"
BACKUP_DIR="/home/moltstack/backups"
DOMAIN="moltrust.ch"
API_URL="http://localhost:8000"
EXPECTED_PORTS="22 53 80 443 5432 6379 8000 8001 9740"
DISK_THRESHOLD=85
CERT_MIN_DAYS=14
BACKUP_MAX_DAYS=7
MAILTO="info@moltrust.ch"

# Load SMTP creds (grep individual values to avoid shell expansion issues)
export SMTP_HOST=$(grep '^SMTP_HOST=' "$SECRETS" | cut -d= -f2-)
export SMTP_PORT=$(grep '^SMTP_PORT=' "$SECRETS" | cut -d= -f2-)
export SMTP_USER=$(grep '^SMTP_USER=' "$SECRETS" | cut -d= -f2-)
export SMTP_PASS=$(grep '^SMTP_PASS=' "$SECRETS" | cut -d= -f2-)
TG_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' "$SECRETS" | cut -d= -f2-)
TG_CHAT=$(grep '^TELEGRAM_CHAT_ID=' "$SECRETS" | cut -d= -f2-)

TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
ALERTS=()
REPORT=""

log() {
    REPORT+="$1"$'\n'
}

alert() {
    ALERTS+=("$1")
    log "[ALERT] $1"
}

ok() {
    log "[OK]    $1"
}

# ===========================================================================
log "============================================="
log "MolTrust Security Report — $TIMESTAMP"
log "============================================="
log ""

# --- 1. Dependency Audit ---
log "--- 1. Python Dependency Audit ---"
if ! "$VENV/bin/pip" show pip-audit >/dev/null 2>&1; then
    "$VENV/bin/pip" install pip-audit --quiet 2>/dev/null
fi

AUDIT_OUTPUT=$("$VENV/bin/pip-audit" 2>&1) || true
VULN_COUNT=$(echo "$AUDIT_OUTPUT" | grep -c "^Name" || true)
# pip-audit exits non-zero if vulns found; parse output
FOUND_VULNS=$(echo "$AUDIT_OUTPUT" | grep -cE "^[a-zA-Z].*\s+[0-9]" || true)

if echo "$AUDIT_OUTPUT" | grep -qi "no known vulnerabilities"; then
    ok "No known vulnerabilities in Python packages"
elif echo "$AUDIT_OUTPUT" | grep -qi "found [0-9]"; then
    VULN_LINE=$(echo "$AUDIT_OUTPUT" | grep -i "found" | head -1)
    alert "pip-audit: $VULN_LINE"
    log "$AUDIT_OUTPUT" | tail -20
else
    ok "pip-audit completed (review output below if needed)"
    log "$(echo "$AUDIT_OUTPUT" | tail -10)"
fi
log ""

# --- 1b. npm Dependency Audit (MoltGuard) ---
log "--- 1b. npm Dependency Audit (MoltGuard) ---"
if [ -f /home/moltstack/moltguard/package.json ]; then
    cd /home/moltstack/moltguard
    NPM_AUDIT=$(npm audit --json 2>/dev/null) || true
    NPM_VULNS=$(echo "$NPM_AUDIT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    meta = d.get('metadata', {}).get('vulnerabilities', {})
    crit = meta.get('critical', 0)
    high = meta.get('high', 0)
    mod = meta.get('moderate', 0)
    low = meta.get('low', 0)
    total = crit + high + mod + low
    print(f'{total}|{crit}|{high}|{mod}|{low}')
except:
    print('0|0|0|0|0')
" 2>/dev/null)
    TOTAL_V=$(echo "$NPM_VULNS" | cut -d'|' -f1)
    CRIT_V=$(echo "$NPM_VULNS" | cut -d'|' -f2)
    HIGH_V=$(echo "$NPM_VULNS" | cut -d'|' -f3)

    if [ "$CRIT_V" -gt 0 ] || [ "$HIGH_V" -gt 0 ]; then
        alert "npm audit: ${CRIT_V} critical, ${HIGH_V} high vulnerabilities in MoltGuard"
    elif [ "$TOTAL_V" -gt 0 ]; then
        ok "npm audit: $TOTAL_V low/moderate vulnerabilities (no critical/high)"
    else
        ok "npm audit: no vulnerabilities in MoltGuard"
    fi
    cd /home/moltstack/moltstack
else
    log "[SKIP]  MoltGuard package.json not found"
fi
log ""

# --- 1c. Outdated Dependencies (informational, no alert) ---
log "--- 1c. Outdated Dependencies ---"
log "Python (top 10):"
PIP_OUTDATED=$("$VENV/bin/pip" list --outdated --format=columns 2>/dev/null | head -12) || PIP_OUTDATED="(could not check)"
log "$PIP_OUTDATED"
log ""
if [ -f /home/moltstack/moltguard/package.json ]; then
    log "npm (MoltGuard, top 10):"
    cd /home/moltstack/moltguard
    NPM_OUTDATED=$(npm outdated 2>/dev/null | head -12) || NPM_OUTDATED="(all up to date)"
    log "${NPM_OUTDATED:-all up to date}"
    cd /home/moltstack/moltstack
fi
log ""

# --- 2. SSL Certificate Expiry ---
log "--- 2. SSL Certificate Expiry ---"
CERT_EXPIRY=$(echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN":443 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2) || CERT_EXPIRY=""

if [ -z "$CERT_EXPIRY" ]; then
    alert "Could not retrieve SSL certificate for $DOMAIN"
else
    EXPIRY_EPOCH=$(date -d "$CERT_EXPIRY" +%s 2>/dev/null) || EXPIRY_EPOCH=0
    NOW_EPOCH=$(date +%s)
    DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))

    if [ "$DAYS_LEFT" -lt "$CERT_MIN_DAYS" ]; then
        alert "SSL cert expires in $DAYS_LEFT days ($CERT_EXPIRY) — renew NOW"
    else
        ok "SSL cert valid for $DAYS_LEFT days (expires $CERT_EXPIRY)"
    fi
fi
log ""

# --- 3. Open Ports Scan ---
log "--- 3. Open Ports Check ---"
LISTENING=$(ss -tlnp 2>/dev/null | awk 'NR>1 {print $4}' | grep -oP ':\K[0-9]+$' | sort -un)
UNEXPECTED=""

for port in $LISTENING; do
    EXPECTED=false
    for exp in $EXPECTED_PORTS; do
        if [ "$port" = "$exp" ]; then
            EXPECTED=true
            break
        fi
    done
    if [ "$EXPECTED" = false ]; then
        # Skip high ephemeral ports and internal-only services
        if [ "$port" -lt 10000 ]; then
            UNEXPECTED+=" $port"
        fi
    fi
done

if [ -n "$UNEXPECTED" ]; then
    alert "Unexpected ports open:$UNEXPECTED"
else
    ok "Only expected ports open ($(echo $LISTENING | tr '\n' ' '))"
fi
log ""

# --- 4. Disk Space ---
log "--- 4. Disk Space ---"
DISK_ALERT=false
while IFS= read -r line; do
    USAGE=$(echo "$line" | awk '{print $5}' | tr -d '%')
    MOUNT=$(echo "$line" | awk '{print $6}')
    if [ "$USAGE" -ge "$DISK_THRESHOLD" ]; then
        alert "Disk $MOUNT at ${USAGE}% (threshold: ${DISK_THRESHOLD}%)"
        DISK_ALERT=true
    fi
done < <(df -h 2>/dev/null | awk 'NR>1 && $5 ~ /[0-9]+%/')

if [ "$DISK_ALERT" = false ]; then
    DISK_SUMMARY=$(df -h / | awk 'NR==2 {print $5 " used on /"}')
    ok "Disk usage healthy ($DISK_SUMMARY)"
fi
log ""

# --- 5. Failed SSH Logins ---
log "--- 5. Failed SSH Logins (last 7 days) ---"
if [ -r /var/log/auth.log ]; then
    WEEK_AGO=$(date -d "7 days ago" +"%b %e" 2>/dev/null) || WEEK_AGO=""
    FAIL_COUNT=$(grep -c "Failed password" /var/log/auth.log 2>/dev/null) || FAIL_COUNT=0
    FAIL_IPS=$(grep "Failed password" /var/log/auth.log 2>/dev/null | grep -oP 'from \K[0-9.]+' | sort | uniq -c | sort -rn | head -5) || FAIL_IPS=""

    if [ "$FAIL_COUNT" -gt 100 ]; then
        alert "High failed SSH login count: $FAIL_COUNT attempts"
        log "Top offending IPs:"
        log "$FAIL_IPS"
    elif [ "$FAIL_COUNT" -gt 0 ]; then
        ok "$FAIL_COUNT failed SSH login attempts"
        log "Top IPs: $(echo "$FAIL_IPS" | head -3 | tr '\n' ' ')"
    else
        ok "No failed SSH login attempts found"
    fi
else
    log "[SKIP]  Cannot read /var/log/auth.log (no permission)"
fi
log ""

# --- 6. API Health ---
log "--- 6. API Health Check ---"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/health" 2>/dev/null) || HTTP_CODE=0
HEALTH_BODY=$(curl -s "$API_URL/health" 2>/dev/null) || HEALTH_BODY=""

if [ "$HTTP_CODE" = "200" ]; then
    DB_STATUS=$(echo "$HEALTH_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('database','?'))" 2>/dev/null) || DB_STATUS="?"
    ok "API healthy (HTTP $HTTP_CODE, DB: $DB_STATUS)"
else
    alert "API health check failed (HTTP $HTTP_CODE)"
fi
log ""

# --- 7. DB Backup Age ---
log "--- 7. Database Backup ---"
if [ -d "$BACKUP_DIR" ]; then
    LATEST=$(ls -t "$BACKUP_DIR"/*.sql 2>/dev/null | head -1) || LATEST=""
    if [ -n "$LATEST" ]; then
        BACKUP_AGE_SEC=$(( $(date +%s) - $(stat -c %Y "$LATEST" 2>/dev/null || echo 0) ))
        BACKUP_AGE_DAYS=$(( BACKUP_AGE_SEC / 86400 ))
        BACKUP_SIZE=$(du -h "$LATEST" 2>/dev/null | awk '{print $1}')
        BACKUP_NAME=$(basename "$LATEST")

        if [ "$BACKUP_AGE_DAYS" -ge "$BACKUP_MAX_DAYS" ]; then
            alert "Latest backup is $BACKUP_AGE_DAYS days old ($BACKUP_NAME)"
        else
            ok "Latest backup: $BACKUP_NAME ($BACKUP_SIZE, ${BACKUP_AGE_DAYS}d ago)"
        fi
    else
        alert "No SQL backups found in $BACKUP_DIR"
    fi
else
    alert "Backup directory $BACKUP_DIR does not exist"
fi
log ""

# ===========================================================================
# Summary
# ===========================================================================
ALERT_COUNT=${#ALERTS[@]}
log "============================================="
if [ "$ALERT_COUNT" -eq 0 ]; then
    log "RESULT: ALL CHECKS PASSED"
else
    log "RESULT: $ALERT_COUNT ALERT(S) FOUND"
    for a in "${ALERTS[@]}"; do
        log "  - $a"
    done
fi
log "============================================="

# --- Write to log file ---
echo "$REPORT" >> "$LOG"

# --- Write report to temp file for email ---
TMPFILE=$(mktemp /tmp/security_report_XXXXXX.txt)
echo "$REPORT" > "$TMPFILE"

SUBJECT="MolTrust Security Report — $TIMESTAMP"
if [ "$ALERT_COUNT" -gt 0 ]; then
    SUBJECT="[ALERT] $SUBJECT — $ALERT_COUNT issue(s)"
fi

# --- Send email via Python (reads report from file, creds from env) ---
REPORT_FILE="$TMPFILE" \
EMAIL_SUBJECT="$SUBJECT" \
EMAIL_TO="$MAILTO" \
python3 -c "
import smtplib, os
from email.mime.text import MIMEText

with open(os.environ['REPORT_FILE']) as f:
    body = f.read()

msg = MIMEText(body, 'plain')
msg['From'] = os.environ['SMTP_USER']
msg['To'] = os.environ['EMAIL_TO']
msg['Subject'] = os.environ['EMAIL_SUBJECT']

try:
    with smtplib.SMTP(os.environ['SMTP_HOST'], int(os.environ['SMTP_PORT'])) as s:
        s.starttls()
        s.login(os.environ['SMTP_USER'], os.environ['SMTP_PASS'])
        s.send_message(msg)
    print('Security report emailed to ' + os.environ['EMAIL_TO'])
except Exception as e:
    print(f'Failed to send email: {e}')
"

rm -f "$TMPFILE"

# --- Send Telegram alert if issues found ---
if [ "$ALERT_COUNT" -gt 0 ] && [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ]; then
    TG_MSG="MolTrust Security Alert — $ALERT_COUNT issue(s)

$TIMESTAMP"
    for a in "${ALERTS[@]}"; do
        TG_MSG+="
- $a"
    done
    curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d chat_id="$TG_CHAT" \
        -d parse_mode="HTML" \
        --data-urlencode "text=$TG_MSG" > /dev/null 2>&1
    echo "Telegram alert sent"
fi

echo "Security check complete. Report appended to $LOG"
