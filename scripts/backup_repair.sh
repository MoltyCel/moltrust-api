#!/usr/bin/env bash
# Repairs an old or missing backup without Lars (ops BAK-REPAIR).
#
# Called by scripts/offsite_backup_check.sh (invariant c-offsite-backup-fresh)
# when the newest off-site snapshot is too old, and by scripts/security_check.sh
# section 7 when the newest DB dump is too old or missing. Until 2026-11-02 both
# only wrote a sentence asking Lars to ask the console to restart the backup;
# the console can do that itself, and does it here.
#
# What it does, inside one lock:
#   1. Is there a complete DB dump of today (UTC)? If not, run backup_db.sh
#      (not in this repository, path configurable) and check that the new dump
#      ends with "PostgreSQL database dump complete".
#   2. Is the newest off-site snapshot younger than OFFSITE_MAX_AGE_H, and was
#      no new dump made? Then there is nothing to do. Otherwise run
#      offsite_backup.sh with a short wait for the dump, because the dump is
#      fresh at this point.
#   3. Check dump and snapshot again, the snapshot through
#      offsite_backup_check.sh --nur-pruefen, the same query the invariant uses.
#
# Output: exactly one line on stdout. Exit 0: repaired (or nothing to do), the
# line says what was done. It never carries the marker "offsite-backup=ok":
# that one belongs to the answer of the repository, which the caller asks
# itself afterwards. Exit 1: repair
# failed, the line names the cause and what Lars has to do, in plain words; the
# caller carries it to Telegram (the selftest report or the security report).
# This script sends nothing itself. Exit 3: the cap is reached, nothing was
# started, the line repeats the failure already reported. Exit 75: another
# repair holds the lock and did not finish within OFFSITE_REPAIR_LOCK_WAIT
# seconds; nothing was started.
#
# Everything the steps print goes to $LOG, with the repository address, any
# user@host and the storage host replaced. Neither the line on stdout nor the
# log carries a password, a key or the repository address.
#
# Cap: at most OFFSITE_REPAIR_DECKEL repairs in 24 hours (CLAUDE.md "Kein Fix
# ohne Waechter": a repair that repeats three times a day repairs nothing).
# Beyond that it starts nothing and repeats the last failure line word for
# word, so the notify throttle sees the same fingerprint and Lars gets one
# message, not one per hour.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DB="${BACKUP_DB_SCRIPT:-/home/moltstack/backup_db.sh}"
OFFSITE="${OFFSITE_BACKUP_BIN:-$HERE/offsite_backup.sh}"
CHECK="${OFFSITE_CHECK_BIN:-$HERE/offsite_backup_check.sh}"
ENV_FILE="${OFFSITE_ENV:-${HOME}/.config/restic/offsite.env}"
DUMPS="${OFFSITE_DUMP_DIR:-${HOME}/backups}"
STATE="${OFFSITE_STATE_DIR:-${HOME}/.local/state/offsite-backup}"
LOG="${OFFSITE_REPAIR_LOG:-/home/moltstack/moltstack/logs/backup_repair.log}"
LOCK="${OFFSITE_REPAIR_LOCK:-$STATE/repair.lock}"
# 0 = do not wait. The callers pass a wait longer than one whole repair
# (DB_TIMEOUT + OFFSITE_TIMEOUT + checks), so a second caller waits for the
# first and then finds nothing to do.
LOCK_WAIT="${OFFSITE_REPAIR_LOCK_WAIT:-0}"
# pg_dump of the 311 MB database takes seconds (dump 170 MB, 26.10.2026); five
# minutes is room for a slow night, not an estimate.
DB_TIMEOUT="${OFFSITE_REPAIR_DB_TIMEOUT:-300}"
# The nightly restic run took 41 s on 02.11.2026 including forget, prune and
# check; fifteen minutes covers a first upload after a longer outage.
OFFSITE_TIMEOUT="${OFFSITE_REPAIR_OFFSITE_TIMEOUT:-900}"
# offsite_backup.sh waits up to OFFSITE_WAIT_MIN minutes for the dump of today,
# 60 in the nightly run. Here the dump has just been checked, so two minutes.
WAIT_MIN="${OFFSITE_REPAIR_WAIT_MIN:-2}"
# The cap from CLAUDE.md, three runs per 24 hours.
DECKEL="${OFFSITE_REPAIR_DECKEL:-3}"
# At or above this use of the dump filesystem a failed dump is read as a full
# disk. pg_dump needs about 170 MB; at 95 % of 75 GB under 4 GB are left.
DISK_MAX="${OFFSITE_REPAIR_DISK_MAX:-95}"
ANLASS="${1:-ohne Anlass}"

RUNS="$STATE/repair-runs"
LAST_FAIL="$STATE/repair-last-failure"

mkdir -p "$STATE" 2>/dev/null
chmod 700 "$STATE" 2>/dev/null
mkdir -p "$(dirname "$LOG")" 2>/dev/null

# --- what must not reach the log -------------------------------------------
REPO_VALUE=""
PASS_VALUE=""
if [ -r "$ENV_FILE" ]; then
  REPO_VALUE=$( (set -a; . "$ENV_FILE" >/dev/null 2>&1; printf '%s' "${RESTIC_REPOSITORY:-}") )
  PASS_VALUE=$( (set -a; . "$ENV_FILE" >/dev/null 2>&1; printf '%s' "${RESTIC_PASSWORD:-}") )
fi
# The host of an sftp repository ("sftp:user@host:/path" or "sftp://user@host/x"),
# which ssh errors name without the rest of the address.
REPO_HOST=""
if [ -n "$REPO_VALUE" ]; then
  REPO_HOST=${REPO_VALUE#*@}
  [ "$REPO_HOST" = "$REPO_VALUE" ] && REPO_HOST=""
  REPO_HOST=${REPO_HOST%%[:/]*}
fi

scrub() {
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    [ -n "$REPO_VALUE" ] && line=${line//"$REPO_VALUE"/<repository>}
    [ -n "$PASS_VALUE" ] && line=${line//"$PASS_VALUE"/<passwort>}
    while [[ $line =~ [A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+ ]]; do
      line=${line//"${BASH_REMATCH[0]}"/<zugang>}
    done
    [ -n "$REPO_HOST" ] && line=${line//"$REPO_HOST"/<host>}
    printf '%s\n' "$line"
  done
}

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$1" | scrub >> "$LOG"; }

# --- the one line on stdout ------------------------------------------------
fail() {
  # $1 cause, $2 what Lars has to do
  local msg="Die Sicherung ist alt und die automatische Reparatur ist gescheitert: $1. Lars muss: $2. Protokoll: $LOG."
  printf '%s\n' "$msg" > "$LAST_FAIL"
  log "GESCHEITERT: $1"
  printf '%s\n' "$msg"
  exit 1
}

CONSOLE="im Console-Tab BAK-REPAIR nennen, die Console liest das Protokoll und behebt es"

today_dump() {
  # Newest complete dump of today (UTC), the same rule offsite_backup.sh uses.
  local cand
  cand=$(ls -1t "$DUMPS"/moltstack_"$(date -u +%Y%m%d)"_*.sql 2>/dev/null | head -1)
  [ -n "$cand" ] || return 1
  tail -c 4096 "$cand" | grep -q 'PostgreSQL database dump complete' || return 1
  printf '%s\n' "$cand"
}

disk_use() {
  df -P "$DUMPS" 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5}'
}

# Cause and what to do, read from the scrubbed output of a failed step.
# $1 step name, $2 exit code, $3 output file.
classify() {
  local step=$1 rc=$2 out=$3 use
  use=$(disk_use)
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    if [ "$step" = backup_db ]; then
      fail "backup_db.sh lief laenger als $DB_TIMEOUT Sekunden und wurde abgebrochen" "$CONSOLE"
    fi
    fail "offsite_backup.sh lief laenger als $OFFSITE_TIMEOUT Sekunden und wurde abgebrochen, die Storage Box antwortet vermutlich nicht" \
      "in der Hetzner Console nachsehen, ob die Storage Box laeuft; laeuft sie, $CONSOLE"
  fi
  if grep -qi 'No space left on device' "$out" || { [ -n "$use" ] && [ "$use" -ge "$DISK_MAX" ]; }; then
    # The percentage goes to the log, not into the line: a sentence that
    # changes with every percent is a new message to the notify throttle.
    log "Platte unter $DUMPS zu ${use:-?} % belegt"
    fail "die Serverplatte unter $DUMPS ist voll, der Dump passt nicht mehr darauf" \
      "auf der Serverplatte Platz freigeben, das geht nur mit sudo; welche Dateien weg koennen, nennt die Console im Console-Tab"
  fi
  if [ "$step" = backup_db ]; then
    fail "backup_db.sh endete mit Code $rc und hat keinen vollstaendigen Dump geschrieben" "$CONSOLE"
  fi
  if grep -qi 'wrong password' "$out"; then
    fail "der Schluessel passt nicht mehr zum Sicherungsspeicher" \
      "die Passwortdatei ~/.config/restic/offsite.pass aus dem Passwortmanager wiederherstellen (Ablage vom 27.10.2026)"
  fi
  if grep -qiE 'already locked|anderer Lauf von offsite_backup.sh' "$out"; then
    fail "der Sicherungsspeicher ist durch einen anderen restic-Lauf gesperrt" "$CONSOLE"
  fi
  if grep -qiE 'permission denied|connection refused|connection timed out|connection reset|no route to host|could not resolve|host key verification|unable to open (repository|config)|subprocess ssh|ssh: ' "$out"; then
    fail "die Storage Box nimmt keine Verbindung an oder lehnt den Zugang ab" \
      "in der Hetzner Console pruefen, ob die Storage Box laeuft und der SSH-Zugang noch freigeschaltet ist"
  fi
  if grep -qiE 'offsite\.env fehlt|RESTIC_REPOSITORY nicht gesetzt|Passwortdatei nicht lesbar|restic fehlt' "$out"; then
    fail "die Einrichtung der Sicherung ist unvollstaendig (Zugangsdatei, Passwortdatei oder restic fehlt)" "$CONSOLE"
  fi
  fail "offsite_backup.sh endete mit Code $rc" "$CONSOLE"
}

TMP=$(mktemp -d "${TMPDIR:-/tmp}/backup_repair.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

run_step() {
  # $1 name, $2 timeout in seconds, rest the command. Sets STEP_RC.
  local name=$1 to=$2
  shift 2
  log "$name startet (Deckel ${to} s)"
  timeout --kill-after=30 "$to" "$@" > "$TMP/$name.raw" 2>&1 9>&-
  STEP_RC=$?
  scrub < "$TMP/$name.raw" > "$TMP/$name.out"
  sed "s/^/  [$name] /" "$TMP/$name.out" >> "$LOG"
  log "$name endete mit Code $STEP_RC"
}

# --- lock ------------------------------------------------------------------
exec 9>>"$LOCK"
if [ "$LOCK_WAIT" -gt 0 ]; then
  flock -w "$LOCK_WAIT" 9; got=$?
else
  flock -n 9; got=$?
fi
if [ "$got" -ne 0 ]; then
  log "Sperre $LOCK belegt, dieser Lauf startet nichts (Anlass: $ANLASS)"
  echo "Die Sicherung ist alt und eine andere Reparatur haelt die Sperre noch nach $LOCK_WAIT Sekunden, laenger als ein ganzer Lauf dauern darf; dieser Lauf hat nichts gestartet. Lars muss: $CONSOLE. Protokoll: $LOG."
  exit 75
fi

log "Anlass: $ANLASS"

# --- what is needed, decided inside the lock -------------------------------
need_dump=1
dump=$(today_dump) && need_dump=0
snap_before=$("$CHECK" --nur-pruefen 2>/dev/null | tail -1)
need_snap=1
case "$snap_before" in *offsite-backup=ok*) need_snap=0 ;; esac

if [ "$need_dump" -eq 0 ] && [ "$need_snap" -eq 0 ]; then
  log "nichts zu tun: $(basename "$dump") vollstaendig, $snap_before"
  echo "Sicherung war schon frisch, nichts neu gestartet: Dump $(basename "$dump") vollstaendig, Snapshot ${snap_before#*offsite-backup=ok }."
  exit 0
fi

# --- cap ---------------------------------------------------------------------
now=$(date -u +%s)
recent=0
if [ -r "$RUNS" ]; then
  recent=$(awk -v since=$((now - 86400)) '$1 >= since {n++} END {print n+0}' "$RUNS")
fi
if [ "$recent" -ge "$DECKEL" ]; then
  log "Deckel erreicht: $recent Reparaturen in 24 h, dieser Lauf startet nichts"
  if [ -s "$LAST_FAIL" ]; then
    cat "$LAST_FAIL"
  else
    echo "Die Sicherung ist alt und die automatische Reparatur ist nach $DECKEL Laeufen in 24 Stunden ausgesetzt. Lars muss: $CONSOLE. Protokoll: $LOG."
  fi
  exit 3
fi
printf '%s\n' "$now" >> "$RUNS"
log "Lauf $((recent + 1)) von $DECKEL in 24 h; Dump von heute fehlt: $need_dump, Snapshot: $snap_before"

# --- 1. the dump -------------------------------------------------------------
if [ "$need_dump" -eq 1 ]; then
  mkdir -p "$DUMPS" 2>/dev/null
  if [ ! -x "$BACKUP_DB" ]; then
    fail "backup_db.sh fehlt oder ist nicht ausfuehrbar unter $BACKUP_DB" "$CONSOLE"
  fi
  run_step backup_db "$DB_TIMEOUT" "$BACKUP_DB"
  if ! dump=$(today_dump); then
    classify backup_db "$STEP_RC" "$TMP/backup_db.out"
  fi
  log "Dump neu: $(basename "$dump")"
fi

# --- 2. the off-site copy ----------------------------------------------------
# OFFSITE_LOCK_WAIT: should the nightly run still hold its lock, wait for it
# inside the step's own timeout instead of failing at once.
run_step offsite_backup "$OFFSITE_TIMEOUT" \
  env OFFSITE_WAIT_MIN="$WAIT_MIN" OFFSITE_LOCK_WAIT=$((OFFSITE_TIMEOUT / 2)) "$OFFSITE"
if [ "$STEP_RC" -ne 0 ]; then
  classify offsite_backup "$STEP_RC" "$TMP/offsite_backup.out"
fi

# --- 3. check again ------------------------------------------------------------
if ! dump=$(today_dump); then
  fail "nach der Reparatur liegt kein vollstaendiger Dump von heute in $DUMPS" "$CONSOLE"
fi
# After a repair the snapshot has to be from this hour, whatever threshold the
# caller used. With OFFSITE_MAX_AGE_H=0 (the probe in the job text) nothing
# would pass otherwise.
snap_after=$(OFFSITE_MAX_AGE_H=1 "$CHECK" --nur-pruefen 2>/dev/null | tail -1)
case "$snap_after" in
  *offsite-backup=ok*) ;;
  *) fail "offsite_backup.sh lief durch, der Sicherungsspeicher meldet danach aber: $snap_after" "$CONSOLE" ;;
esac

rm -f "$LAST_FAIL"
log "repariert: $(basename "$dump"), $snap_after"
echo "Sicherung repariert ($ANLASS): $([ "$need_dump" -eq 1 ] && echo "backup_db.sh und ")offsite_backup.sh neu gestartet, Dump $(basename "$dump") vollstaendig, Snapshot danach ${snap_after#*offsite-backup=ok }. Protokoll: $LOG."
exit 0
