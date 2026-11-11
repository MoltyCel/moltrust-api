#!/usr/bin/env bash
# For the invariant c-offsite-backup-fresh: is the newest snapshot in the
# off-site repository younger than 26 hours?
#
# It asks the repository itself, not the stamp file offsite_backup.sh writes:
# a stamp says a run believed it succeeded, the repository says what is there.
# The stamp is only used to name the last known good run when the repository
# cannot be reached.
#
# Prints "offsite-backup=ok" plus the age on success. Otherwise one plain
# sentence with what to do, which the selftest report carries to Telegram.
#
# Since ops BAK-REPAIR (02.11.2026): is the newest snapshot too old, or is there
# none, this script starts scripts/backup_repair.sh instead of asking Lars to
# ask the console. A repair that works prints two lines, the repair and then
# "offsite-backup=ok alter=0h"; the invariant is green and the selftest keeps
# the first line as a note in its report, nothing goes to Telegram. A repair
# that fails prints one sentence with the cause and what Lars has to do; that
# sentence goes to Telegram as before, once, because the notify throttle drops
# the same sentence in the following runs.
#
#   --nur-pruefen   only ask the repository, never repair. backup_repair.sh
#                   uses this before and after its run; SELFTEST_DRY_RUN=1
#                   (selftest.py --dry-run) has the same effect.
set -uo pipefail

NUR_PRUEFEN=0
[ "${1:-}" = "--nur-pruefen" ] && NUR_PRUEFEN=1
# selftest.py --dry-run arms nothing, and a repair is armed.
[ "${SELFTEST_DRY_RUN:-0}" = 1 ] && NUR_PRUEFEN=1

ENV_FILE="${OFFSITE_ENV:-${HOME}/.config/restic/offsite.env}"
RESTIC="${RESTIC_BIN:-${HOME}/bin/restic}"
STATE="${OFFSITE_STATE_DIR:-${HOME}/.local/state/offsite-backup}"
MAX_H="${OFFSITE_MAX_AGE_H:-26}"
LOG=/home/moltstack/moltstack/logs/offsite_backup.log
REPAIR="${OFFSITE_REPAIR_BIN:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/backup_repair.sh}"
# Longer than one whole repair (backup_repair.sh: 300 s dump, 900 s off-site
# copy, 30 s grace each, two checks), so a run that finds another repair under way waits for it and then
# finds nothing left to do. docs/invariants/c-offsite-backup-fresh.yaml gives
# the query the time for both.
REPAIR_LOCK_WAIT="${OFFSITE_REPAIR_LOCK_WAIT:-1300}"

last_known() {
  [ -r "$STATE/last-success" ] && printf ' Letzter bekannter Erfolg: %s.' "$(cat "$STATE/last-success")"
}

if [ ! -r "$ENV_FILE" ] || [ ! -x "$RESTIC" ]; then
  echo "Die Sicherung ausserhalb des Servers ist nicht eingerichtet: $ENV_FILE oder $RESTIC fehlt. Bitte die Console bitten, BAK-1025 Kriterium 3 nachzusehen."
  exit 0
fi
set -a; . "$ENV_FILE"; set +a

if ! json=$(timeout 120 "$RESTIC" snapshots --no-lock --tag daily --host moltstack --latest 1 --json 2>/dev/null); then
  echo "Der Sicherungsspeicher ausserhalb des Servers antwortet nicht.$(last_known) Bitte in der Hetzner Console nachsehen, ob die Storage Box laeuft, sonst die Console bitten, die Verbindung zu pruefen."
  exit 0
fi

age_h=$(printf '%s' "$json" | python3 -I -c '
import json, sys, datetime as d
s = json.load(sys.stdin)
if not s:
    print(-1); sys.exit()
t = d.datetime.fromisoformat(s[-1]["time"])
print(int((d.datetime.now(d.timezone.utc) - t).total_seconds() // 3600))') || age_h=-2

# Starts the repair and prints its outcome. $1 is the reason in plain words.
# On success the repair's own line becomes a note ("HINWEIS:", kept in the
# selftest report, not sent) and the repository is asked once more; only that
# answer can carry "offsite-backup=ok". The threshold for the second question
# is at least one hour, so the probe with OFFSITE_MAX_AGE_H=0 can turn green.
repair() {
  local out rc max
  out=$(OFFSITE_REPAIR_LOCK_WAIT="$REPAIR_LOCK_WAIT" "$REPAIR" "$1")
  rc=$?
  if [ "$rc" -ne 0 ]; then
    printf '%s\n' "$out"
    return
  fi
  printf 'HINWEIS: %s\n' "$out"
  max=$MAX_H
  [ "$max" -lt 1 ] && max=1
  OFFSITE_MAX_AGE_H=$max "${BASH_SOURCE[0]}" --nur-pruefen
}

case "$age_h" in
  -1)
    if [ "$NUR_PRUEFEN" = 1 ]; then
      echo "Im Sicherungsspeicher liegt noch keine taegliche Sicherung."
    else
      repair "keine taegliche Sicherung im Sicherungsspeicher"
    fi ;;
  -2) echo "Die Liste der Sicherungen war nicht lesbar. Bitte die Console bitten, $LOG zu lesen." ;;
  *)
    if [ "$age_h" -lt "$MAX_H" ]; then
      echo "offsite-backup=ok alter=${age_h}h"
    elif [ "$NUR_PRUEFEN" = 1 ]; then
      echo "Die letzte Sicherung ausserhalb des Servers ist ${age_h} Stunden alt, erwartet ist hoechstens ${MAX_H}."
    else
      repair "letzte Sicherung ausserhalb des Servers ${age_h} h alt, erwartet hoechstens ${MAX_H} h"
    fi ;;
esac
exit 0
