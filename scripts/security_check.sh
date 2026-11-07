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
# Ports allowed on an address reachable from outside. Loopback listeners are
# not judged (scripts/public_ports.py): on 25.10.2026 the report called the
# MolCheck verifier on 127.0.0.1:3006 an open port (ops SEC-1025-SRV).
EXPECTED_PORTS="22 80 443"
DISK_THRESHOLD=85
CERT_MIN_DAYS=14
BACKUP_MAX_DAYS=7
MAILTO="info@moltrust.ch"

TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
ALERTS=()
ALERT_CLASSES=()
REPORT=""

log() {
    REPORT+="$1"$'\n'
}

# Every alert carries a class (ops SEC-MELD, rule MELD-1): Telegram only when
# Lars has to decide or do something.
#   HANDLUNG  Lars has to decide or act; goes to Telegram.
#   AUSNAHME  money, security incident, outage of a live service, root
#             one-liner; goes to Telegram at once.
#   ABLAGE    worked off without Lars (dependency with a fix, error rates of
#             foreign callers, a check that could not run); log and mail only.
# On 01.11.2026 six ABLAGE findings (one pip-audit line, five high-error
# endpoints from a crawler) went to Telegram because nothing told them apart.
# tests/test_security_check_meld.py holds the class of every call.
alert() {
    case "$1" in
        HANDLUNG|AUSNAHME|ABLAGE) ;;
        *) echo "security_check.sh: alert without class: $*" >&2; return 2 ;;
    esac
    ALERT_CLASSES+=("$1")
    ALERTS+=("$2")
    log "[ALERT] [$1] $2"
}

ok() {
    log "[OK]    $1"
}

# pip_audit_split.py lines: a production dependency with a fixed release, or
# output that could not be read, is dependency upkeep (ABLAGE).
feed_pip_audit() {
    local line
    while IFS= read -r line; do
        case "$line" in
            ALERT\ *) alert ABLAGE "${line#ALERT }" ;;
            INFO\ *)  log "[INFO]  ${line#INFO }" ;;
            OK\ *)    ok "${line#OK }" ;;
        esac
    done
}

# security_http_errors.py lines: error rates of callers, a 5xx included, are
# looked at and fixed without Lars; an outage shows in the health check (ABLAGE).
ERR_ALERTS=0
feed_http_errors() {
    local line
    while IFS= read -r line; do
        case "$line" in
            ALERT\ *) alert ABLAGE "high-error endpoint: ${line#ALERT }"; ERR_ALERTS=$((ERR_ALERTS + 1)) ;;
            INFO\ *)  log "[INFO]  ${line#INFO }" ;;
        esac
    done
}

notify_send() {
    # Through app/notify.py since ops NOTIFY-2 (27.10.2026): gate, throttle and
    # the send log apply here too. notify resolves token and chat id itself.
    local api_dir
    api_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    PYTHONPATH="$api_dir" "$api_dir/venv/bin/python" -m app.notify \
        --channel alerts --parse-mode HTML --art sicherheit-handgriff --stdin
}

# Telegram gets the HANDLUNG and AUSNAHME alerts only, in one message.
send_telegram() {
    local i msg count=0 body=""
    for i in "${!ALERTS[@]}"; do
        case "${ALERT_CLASSES[$i]}" in
            HANDLUNG|AUSNAHME)
                count=$((count + 1))
                body+=$'\n'"- [${ALERT_CLASSES[$i]}] ${ALERTS[$i]}" ;;
        esac
    done
    if [ "$count" -eq 0 ]; then
        echo "Telegram: nothing to send (${#ALERTS[@]} alert(s), none HANDLUNG or AUSNAHME)"
        return 0
    fi
    msg="MolTrust Security Alert — $count issue(s)

$TIMESTAMP$body"
    if notify_send <<<"$msg"; then
        echo "Telegram alert sent"
    else
        echo "Telegram alert not sent (notify rc $?)"
    fi
}

# Section 7, the age of the DB dump. A dump that is too old or missing is
# repaired by scripts/backup_repair.sh (ops BAK-REPAIR, decision Lars
# 02.11.2026); until then this section wrote an ABLAGE line and nobody acted on
# it. A repair that works stands in the log and the mail report as [OK] and
# [INFO]; one that fails is a HANDLUNG with the cause and what Lars has to do.
BACKUP_REPAIR="${BACKUP_REPAIR_BIN:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/backup_repair.sh}"
check_backup() {
    local latest age_sec age_days size name why out rc
    latest=""
    if [ -d "$BACKUP_DIR" ]; then
        latest=$(ls -t "$BACKUP_DIR"/*.sql 2>/dev/null | head -1) || latest=""
    fi
    if [ -n "$latest" ]; then
        age_sec=$(( $(date +%s) - $(stat -c %Y "$latest" 2>/dev/null || echo 0) ))
        age_days=$(( age_sec / 86400 ))
        size=$(du -h "$latest" 2>/dev/null | awk '{print $1}')
        name=$(basename "$latest")
        if [ "$age_days" -lt "$BACKUP_MAX_DAYS" ]; then
            ok "Latest backup: $name ($size, ${age_days}d ago)"
            return 0
        fi
        why="newest DB dump $name is $age_days days old"
    elif [ -d "$BACKUP_DIR" ]; then
        why="no SQL dump in $BACKUP_DIR"
    else
        why="backup directory $BACKUP_DIR does not exist"
    fi
    log "[INFO]  $why, starting scripts/backup_repair.sh"
    rc=0
    out=$(OFFSITE_DUMP_DIR="$BACKUP_DIR" OFFSITE_REPAIR_LOCK_WAIT=1300 \
          "$BACKUP_REPAIR" "security_check: $why") || rc=$?
    if [ "$rc" -eq 0 ]; then
        ok "Backup repaired: $out"
    else
        alert HANDLUNG "backup repair failed: $out"
    fi
}

# Tests source the functions above without running the checks.
if [ "${SECURITY_CHECK_FUNCTIONS_ONLY:-0}" = 1 ]; then return 0 2>/dev/null || exit 0; fi

# Load SMTP creds (grep individual values to avoid shell expansion issues)
export SMTP_HOST=$(grep '^SMTP_HOST=' "$SECRETS" | cut -d= -f2-)
export SMTP_PORT=$(grep '^SMTP_PORT=' "$SECRETS" | cut -d= -f2-)
export SMTP_USER=$(grep '^SMTP_USER=' "$SECRETS" | cut -d= -f2-)
export SMTP_PASS=$(grep '^SMTP_PASS=' "$SECRETS" | cut -d= -f2-)

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

# Production dependencies (reached from requirements.txt) alert when a fixed
# release exists; tooling in the same venv (pip, pip-audit's own filelock) and
# advisories without a fix are listed (ops SEC-1025-API, 25.10.2026).
AUDIT_JSON=$("$VENV/bin/pip-audit" -f json --progress-spinner off 2>/dev/null) || true
if [ -z "$AUDIT_JSON" ]; then
    alert ABLAGE "pip-audit produced no output"
else
    feed_pip_audit < <(printf '%s' "$AUDIT_JSON" | "$VENV/bin/python" -I /home/moltstack/moltstack/scripts/pip_audit_split.py /home/moltstack/moltstack/requirements.txt)
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
        alert ABLAGE "npm audit: ${CRIT_V} critical, ${HIGH_V} high vulnerabilities in MoltGuard"
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
# Read whole, then cut: under pipefail `pip list | head` failed every week,
# because head closes the pipe and pip dies on SIGPIPE (ops SEC-1025-API).
PIP_OUTDATED=$("$VENV/bin/pip" list --outdated --format=columns 2>/dev/null) || PIP_OUTDATED="(could not check)"
log "$(printf '%s\n' "$PIP_OUTDATED" | head -12)"
log ""
if [ -f /home/moltstack/moltguard/package.json ]; then
    log "npm (MoltGuard, top 10):"
    cd /home/moltstack/moltguard
    # npm outdated exits 1 when something is outdated; that is the answer, not a failure.
    NPM_OUTDATED=$(npm outdated 2>/dev/null) || true
    log "$(printf '%s\n' "${NPM_OUTDATED:-all up to date}" | head -12)"
    cd /home/moltstack/moltstack
fi
log ""

# --- 1d. Critical Dependency Version Tracking ---
log "--- 1d. Critical Dependency Versions ---"
if [ -f /home/moltstack/moltguard/package.json ]; then
    cd /home/moltstack/moltguard
    # MoltGuard depends on @x402/hono and @x402/evm; @x402/core is not one of
    # its dependencies and read "not installed" every week (ops SEC-1025-API).
    # The installed version comes from node_modules, not the declared range.
    for pkg in @x402/hono @x402/evm; do
        CURRENT_X402=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "node_modules/$pkg/package.json" 2>/dev/null) || CURRENT_X402="not installed"
        LATEST_X402=$(curl -s --max-time 5 "https://registry.npmjs.org/$pkg/latest" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('version','unknown'))" 2>/dev/null) || LATEST_X402="unknown"
        log "$pkg: installed=$CURRENT_X402 latest=$LATEST_X402"
        if [ "$CURRENT_X402" != "$LATEST_X402" ] && [ "$LATEST_X402" != "unknown" ]; then
            log "[INFO]  $pkg update available: $CURRENT_X402 → $LATEST_X402"
        fi
    done
    cd /home/moltstack/moltstack
fi
log ""

# --- 1e. W3C Spec Versions (quarterly manual check) ---
log "--- 1e. W3C Spec Versions (quarterly reminder) ---"
log "VC Data Model: https://www.w3.org/TR/vc-data-model-2.0/"
log "DID Core: https://www.w3.org/TR/did-core/"
log "(Check manually each quarter for breaking changes)"
log ""

# --- 2. SSL Certificate Expiry ---
log "--- 2. SSL Certificate Expiry ---"
CERT_EXPIRY=$(echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN":443 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2) || CERT_EXPIRY=""

if [ -z "$CERT_EXPIRY" ]; then
    alert AUSNAHME "Could not retrieve SSL certificate for $DOMAIN"
else
    EXPIRY_EPOCH=$(date -d "$CERT_EXPIRY" +%s 2>/dev/null) || EXPIRY_EPOCH=0
    NOW_EPOCH=$(date +%s)
    DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))

    if [ "$DAYS_LEFT" -lt "$CERT_MIN_DAYS" ]; then
        alert AUSNAHME "SSL cert expires in $DAYS_LEFT days ($CERT_EXPIRY) — renew NOW"
    else
        ok "SSL cert valid for $DAYS_LEFT days (expires $CERT_EXPIRY)"
    fi
fi
log ""

# --- 3. Open Ports Scan ---
log "--- 3. Open Ports Check ---"
LISTENING=$(ss -tlnH 2>/dev/null | python3 -I /home/moltstack/moltstack/scripts/public_ports.py) || LISTENING="?"
UNEXPECTED=""

if [ "$LISTENING" = "?" ]; then
    alert ABLAGE "Open ports could not be read (ss or public_ports.py failed)"
else
    for port in $LISTENING; do
        case " $EXPECTED_PORTS " in
            *" $port "*) ;;
            *) UNEXPECTED+=" $port" ;;
        esac
    done
    LOOPBACK_ONLY=$(ss -tlnH 2>/dev/null | awk '{print $4}' | grep -oP ':\K[0-9]+$' | sort -un | grep -vxF -f <(printf '%s\n' $LISTENING) | tr '\n' ' ')
    if [ -n "$UNEXPECTED" ]; then
        alert AUSNAHME "Unexpected ports reachable from outside:$UNEXPECTED"
    else
        ok "Only expected ports reachable from outside ($(echo $LISTENING))"
    fi
    log "[INFO]  loopback only: ${LOOPBACK_ONLY:-none}"
fi
log ""

# --- 4. Disk Space ---
log "--- 4. Disk Space ---"
DISK_ALERT=false
while IFS= read -r line; do
    USAGE=$(echo "$line" | awk '{print $5}' | tr -d '%')
    MOUNT=$(echo "$line" | awk '{print $6}')
    if [ "$USAGE" -ge "$DISK_THRESHOLD" ]; then
        alert AUSNAHME "Disk $MOUNT at ${USAGE}% (threshold: ${DISK_THRESHOLD}%)"
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
        alert ABLAGE "High failed SSH login count: $FAIL_COUNT attempts"
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
    alert AUSNAHME "API health check failed (HTTP $HTTP_CODE)"
fi
log ""

# --- 7. DB Backup Age ---
log "--- 7. Database Backup ---"
check_backup
log ""

# --- 8. Discovery & Error Health ---
log "--- 8. Discovery & Error Health ---"

# 8a. Public /.well-known/* discovery endpoints must resolve to 200 (regression guard).
# -L follows redirects: a 301 alias (e.g. /.well-known/x402 -> x402.json) that
# resolves to 200 counts as healthy, not a false alert.
# Tested via the PUBLIC url — several are nginx-static (not the app), so
# $API_URL (localhost:8000) would false-404 them.
for path in "/.well-known/agent-card.json" "/.well-known/agent.json" \
            "/.well-known/did.json" "/.well-known/x402.json" \
            "/.well-known/x402" "/.well-known/jwks.json"; do
    DSTATUS=$(curl -sL -o /dev/null -w "%{http_code}" "https://api.moltrust.ch${path}" 2>/dev/null || echo 000)
    if [ "$DSTATUS" = "200" ]; then
        ok "discovery ${path} -> 200"
    else
        alert AUSNAHME "discovery endpoint ${path} returned HTTP ${DSTATUS}"
    fi
done

# 8b. High-error endpoints (>20 errors / 24h), excluding scanner/bot noise and
# the bare '/' probe (uptime monitors) — surfaces real endpoint regressions.
# 402 is not an error, 401 and 429 are listed with own/foreign split and the
# busiest foreign /24, everything else alerts (scripts/security_http_errors.py,
# ops SEC-1025-API).
ERR_ROWS=$(psql moltstack -t -A -F'|' -c "
SELECT endpoint, status_code, ip, COUNT(*)
FROM request_log
WHERE status_code >= 400
  AND ts > NOW() - INTERVAL '24 hours'
  AND endpoint <> '/'
  AND endpoint !~* '(wp-includes|wp-admin|xmlrpc|/\.env|\.php|/\.git)'
GROUP BY endpoint, status_code, ip;" 2>/dev/null) || ERR_ROWS="?"

if [ "$ERR_ROWS" = "?" ]; then
    alert ABLAGE "request_log could not be read for the error check"
else
    ERR_LINES=$(printf '%s\n' "$ERR_ROWS" | python3 -I /home/moltstack/moltstack/scripts/security_http_errors.py) || ERR_LINES="ALERT error check failed (security_http_errors.py)"
    feed_http_errors <<< "$ERR_LINES"
    [ "$ERR_ALERTS" -eq 0 ] && ok "no high-error endpoints (>20/24h, excl. scanner noise, 401/402/429)"
fi

# 8c. Callers who get ONLY 404s (>=5) and never a 200 in 24h — potential
# discovery failures (they want an endpoint we don't serve). Info, not alert.
DISC_FAIL=$(psql moltstack -t -A -F'|' -c "
SELECT ip,
       COALESCE(MAX(user_agent), '?'),
       COUNT(*) FILTER (WHERE status_code = 404),
       STRING_AGG(DISTINCT endpoint, ', ') FILTER (WHERE status_code = 404)
FROM request_log
WHERE ts > NOW() - INTERVAL '24 hours'
  AND endpoint !~* '(wp-includes|wp-admin|xmlrpc|/\.env|\.php|/\.git)'
GROUP BY ip
HAVING COUNT(*) FILTER (WHERE status_code = 404) >= 5
   AND COUNT(*) FILTER (WHERE status_code = 200) = 0
ORDER BY 3 DESC
LIMIT 3;" 2>/dev/null || true)

if [ -n "$DISC_FAIL" ]; then
    log "[INFO]  Callers hitting only 404s (may not find us):"
    while IFS='|' read -r ip ua cnt eps; do
        [ -z "$ip" ] && continue
        log "  - ${ip} (${ua}): ${cnt}x -> ${eps}"
    done <<< "$DISC_FAIL"
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
    for i in "${!ALERTS[@]}"; do
        log "  - [${ALERT_CLASSES[$i]}] ${ALERTS[$i]}"
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

# --- Telegram: HANDLUNG and AUSNAHME only (ops SEC-MELD) ---
send_telegram

echo "Security check complete. Report appended to $LOG"
