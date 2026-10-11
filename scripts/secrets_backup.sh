#!/usr/bin/env bash
# Encrypted backup of ~/.moltrust_secrets with age, outside $HOME, no plaintext.
#
# Why this exists: on 2026-10-03 eleven hand-made plaintext copies were found in
# $HOME holding 28 credentials that were still live, the oldest from 2026-06-03,
# plus two more under ~/secret-backups and ~/cleanup-backups. Every one of them
# was a `cp` before an edit.
#
# age rather than gpg because the only gpg key on this host is ed25519 [SC] with
# no encryption subkey — it cannot encrypt at all.
#
# What this enforces so it cannot be forgotten at 2am:
#   - the target lies outside $HOME, so a careless rm or a home-directory backup
#     cannot reach or carry the secrets,
#   - the target is not world-writable, because an encrypted file in a shared
#     directory can still be replaced with someone else's,
#   - age reads stdin and writes the target directly: no temp file, no
#     intermediate plaintext, nothing a crash can leave behind,
#   - the result is 0600 and carries a UTC timestamp,
#   - the run fails unless the result decrypts again,
#   - the run fails if a plaintext copy of the secrets lies in $HOME, and names
#     it. The six copies deleted on 2026-10-11 were all a `cp` before an edit,
#     none came from this script. Before editing ~/.moltrust_secrets, run this
#     script instead of copying the file (CLAUDE.md).
#
# Only the PUBLIC recipient is needed to make a backup. Keep the private
# identity off this host if you can: next to the file it protects it only helps
# against the backup directory leaking on its own.
#
# Restore:  age -d -i <identity> <file>.age > ~/.moltrust_secrets && chmod 600 ~/.moltrust_secrets
set -euo pipefail

SRC="${HOME}/.moltrust_secrets"
DIR="${SECRETS_BACKUP_DIR:-/var/backups/moltstack-secrets}"
IDENT="${SECRETS_BACKUP_IDENTITY:-${HOME}/.config/age/secrets.key}"
RECIPIENT="${SECRETS_BACKUP_RECIPIENT:-}"

die() { printf 'secrets_backup: %s\n' "$1" >&2; exit 1; }

# Every plaintext copy of the secrets under $HOME, one path per line.
find_copies() {
  find "$HOME" -xdev -path "$HOME/.cache" -prune -o -type f \
    \( -name '.moltrust_secrets?*' -o -name 'moltrust_secrets*' \
       -o -path "$HOME/cleanup-backups/*/.moltrust_secrets" \) \
    ! -name '*.age' -print 2>/dev/null | sort
}

# --check-copies: only the scan, for the daily invariant c-secrets-no-plaintext-copy.
# Paths first, the count as the last line.
if [ "${1:-}" = --check-copies ]; then
  find_copies
  find_copies | grep -c . || true
  exit 0
fi

[ -r "$SRC" ] || die "$SRC nicht lesbar"
command -v age >/dev/null || die "age fehlt"

case "$DIR" in
  "$HOME"|"$HOME"/*) die "Ziel liegt in \$HOME ($DIR) — genau der Fehler, den das hier verhindern soll" ;;
esac
[ -d "$DIR" ] || die "Ziel $DIR existiert nicht. Einmalig als root:
    sudo install -d -o $(id -un) -g $(id -gn) -m 700 $DIR"
[ -w "$DIR" ] || die "Ziel $DIR ist nicht beschreibbar"
perm=$(stat -c %a "$DIR")
case "$perm" in
  *7|*6|*3|*2) die "Ziel $DIR ist welt-schreibbar ($perm) — Rechte auf 700 setzen" ;;
esac

# The recipient may be given directly, so a host can make backups without ever
# holding the private identity.
if [ -z "$RECIPIENT" ]; then
  [ -r "$IDENT" ] || die "weder SECRETS_BACKUP_RECIPIENT gesetzt noch Identitaet $IDENT lesbar"
  RECIPIENT=$(age-keygen -y "$IDENT") || die "Empfaenger aus $IDENT nicht ableitbar"
fi

OUT="$DIR/moltrust_secrets.$(date -u +%Y%m%dT%H%M%SZ).age"
umask 077
age --encrypt --recipient "$RECIPIENT" --output "$OUT.partial" < "$SRC"
chmod 600 "$OUT.partial"
mv -- "$OUT.partial" "$OUT"

keys=$(grep -cE '^[A-Za-z_][A-Za-z0-9_]*=' "$SRC" || true)
printf 'secrets_backup: %s\n' "$OUT"
printf '  %s Schluessel verschluesselt, %s Byte, Empfaenger %s\n' \
  "$keys" "$(stat -c %s "$OUT")" "$RECIPIENT"

# Prove it decrypts before anyone relies on it. The plaintext never reaches disk.
if [ -r "$IDENT" ]; then
  back=$(age -d -i "$IDENT" "$OUT" | grep -cE '^[A-Za-z_][A-Za-z0-9_]*=' || true)
  if [ "$back" = "$keys" ]; then
    printf '  Rueckprobe: entschluesselbar, %s von %s Schluesseln wiedergefunden\n' "$back" "$keys"
  else
    printf '  Rueckprobe FEHLGESCHLAGEN: %s statt %s Schluessel — Datei nicht verwenden\n' \
      "$back" "$keys" >&2
    exit 1
  fi
else
  printf '  Rueckprobe uebersprungen: Identitaet nicht auf diesem Host (gewollt)\n'
fi

# Plaintext copies next to the original are the leak this script replaces.
# Reported after the backup, so a fresh encrypted copy exists before anyone
# deletes them. Only paths are printed, never content.
if [ -n "$(find_copies)" ]; then
  printf '  KLARTEXT-KOPIEN in %s — nicht neben das Original kopieren, sondern dieses Skript nutzen:\n' "$HOME" >&2
  find_copies | sed 's/^/    /' >&2
  exit 3
fi
printf '  keine Klartext-Kopie in %s\n' "$HOME"
