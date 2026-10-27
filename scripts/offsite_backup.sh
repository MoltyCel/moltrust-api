#!/usr/bin/env bash
# Daily encrypted, incremental copy of what this host cannot lose, to storage
# outside the Hetzner server (ops BAK-1025).
#
# Why this exists: until 2026-10-27 the daily DB dump (backup_db.sh, 03:00 UTC)
# and the code archive (backup_moltrust.sh) lay only on / of this host. A lost
# disk or a lost server took the backups with it.
#
# What it copies, as one restic snapshot per day:
#   - the newest DB dump from ~/backups, only once it is complete,
#   - the age-encrypted secrets from /var/backups/moltstack-secrets,
#   - /etc/nginx and /etc/systemd/system,
#   - the live crontab of this user,
#   - the source trees ~/moltstack, ~/moltguard and /var/www/html, without
#     .env*, virtualenvs, caches and logs. The tar.gz from backup_moltrust.sh is
#     left out on purpose: it is packed new each day, so restic could not
#     deduplicate it and 530 MB would land in the target every day.
# Not copied: ~/.moltrust_secrets in plain text and the age identity. Both only
# exist encrypted in the target, and the age identity not at all.
#
# Where the target and the key live: in ~/.config/restic/offsite.env, outside
# this public repository. It sets RESTIC_REPOSITORY and RESTIC_PASSWORD_FILE.
# The password file lies on this host only, never in the target; a second copy
# is kept by Lars outside the server. Without it the backup cannot be read.
#
# Retention: 7 daily, 4 weekly, 6 monthly snapshots.
#
# On success it writes the snapshot time to $STATE/last-success, which
# offsite_backup_check.sh compares against the repository.
#
# Restore one dump:
#   set -a; . ~/.config/restic/offsite.env; set +a
#   ~/bin/restic restore latest --tag daily --include '/home/moltstack/backups' --target /tmp/r
set -euo pipefail

ENV_FILE="${OFFSITE_ENV:-${HOME}/.config/restic/offsite.env}"
RESTIC="${RESTIC_BIN:-${HOME}/bin/restic}"
DUMPS="${OFFSITE_DUMP_DIR:-${HOME}/backups}"
STATE="${OFFSITE_STATE_DIR:-${HOME}/.local/state/offsite-backup}"
WAIT_MIN="${OFFSITE_WAIT_MIN:-60}"

die() { printf 'offsite_backup: %s\n' "$1" >&2; exit 1; }
say() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$1"; }

[ -r "$ENV_FILE" ] || die "$ENV_FILE fehlt"
[ -x "$RESTIC" ] || die "restic fehlt unter $RESTIC"
set -a; . "$ENV_FILE"; set +a
[ -n "${RESTIC_REPOSITORY:-}" ] || die "RESTIC_REPOSITORY nicht gesetzt"
[ -r "${RESTIC_PASSWORD_FILE:-/nonexistent}" ] || die "Passwortdatei nicht lesbar"

# The dump of today, and only a finished one: pg_dump writes this line last.
# backup_db.sh starts at 03:00 and needs a few seconds; wait up to WAIT_MIN
# minutes so a slow night does not turn into a missing backup.
dump=""
for _ in $(seq 1 "$WAIT_MIN"); do
  cand=$(ls -1t "$DUMPS"/moltstack_"$(date -u +%Y%m%d)"_*.sql 2>/dev/null | head -1 || true)
  if [ -n "$cand" ] && tail -c 4096 "$cand" | grep -q 'PostgreSQL database dump complete'; then
    dump=$cand; break
  fi
  sleep 60
done
[ -n "$dump" ] || die "kein vollstaendiger DB-Dump von heute in $DUMPS"
say "Dump: $dump ($(stat -c %s "$dump") Byte)"

mkdir -p "$STATE"
chmod 700 "$STATE"
crontab -l > "$STATE/crontab.txt"

"$RESTIC" backup --tag daily --host moltstack --one-file-system \
  --exclude '.env*' --exclude 'venv' --exclude '.venv' --exclude 'node_modules' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  --exclude "${HOME}/moltstack/logs" \
  "$dump" \
  /var/backups/moltstack-secrets \
  /etc/nginx /etc/systemd/system \
  "$STATE/crontab.txt" \
  "${HOME}/moltstack" "${HOME}/moltguard" /var/www/html

"$RESTIC" forget --tag daily --host moltstack \
  --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune

"$RESTIC" check

snap=$("$RESTIC" snapshots --tag daily --host moltstack --latest 1 --json \
  | python3 -I -c 'import json,sys; s=json.load(sys.stdin)[-1]; print(s["short_id"], s["time"])')
printf '%s\n' "$snap" > "$STATE/last-success"
say "fertig: Snapshot $snap"
