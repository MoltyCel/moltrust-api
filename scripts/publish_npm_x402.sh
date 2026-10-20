#!/bin/bash
# Publish @moltrust/x402 and prove it installs. Everything but the token.
#
# Both tokens in ~/.moltrust_secrets answered 401 on 2026-09-27, which is the
# only reason the package sat at 1.0.1 on npm while the repo carried 2.0.0.
# This script is what runs once a fresh one is in place, so the credential is
# the only decision left:
#
#   1. npm token for the @moltrust scope with publish rights. It has to bypass
#      2FA: the account requires it for writes, and a token without the bypass
#      is refused at the publish step with EOTP even though it authenticates and
#      resolves the scope (seen 2026-09-27). A granular token with the bypass
#      set works — that is what published 2.0.0 on 2026-09-28.
#   2. put it in ~/.moltrust_secrets as NPM_TOKEN=npm_...
#   3. bash scripts/publish_npm_x402.sh
#
# The other way, if the token stays interactive: pass a fresh code from the
# authenticator as the first argument. It is valid for about thirty seconds, so
# this only works while someone is watching.
#
#   bash scripts/publish_npm_x402.sh 123456
#
# It refuses rather than guesses: a token that cannot see the scope, a version
# already on the registry, or a dirty tree all stop it before the publish.
set -eo pipefail

REPO=/home/moltstack/moltstack
PKG="$REPO/packages/x402"
export PATH="$HOME/.npm-global/bin:$PATH"
set -a; . "$HOME/.moltrust_secrets"; set +a

[ -n "${NPM_TOKEN:-}" ] || { echo "NPM_TOKEN nicht gesetzt — nichts getan."; exit 2; }

NPMRC=$(mktemp); trap 'rm -f "$NPMRC"' EXIT
printf '//registry.npmjs.org/:_authToken=%s\n' "$NPM_TOKEN" > "$NPMRC"
export NPM_CONFIG_USERCONFIG="$NPMRC"

WHO=$(npm whoami 2>&1) || { echo "Token abgelehnt: $WHO"; exit 2; }
echo "angemeldet als: $WHO"

VERSION=$(node -p "require('$PKG/package.json').version")
NAME=$(node -p "require('$PKG/package.json').name")
echo "$NAME@$VERSION"

# Already there? Then this is a re-run, not a publish. npm would reject it
# anyway; saying so plainly beats reading npm's error.
if npm view "$NAME@$VERSION" version >/dev/null 2>&1; then
  echo "$NAME@$VERSION liegt bereits auf npm — nichts zu tun."; exit 0
fi

# A tool signature in a published README is not removable afterwards.
if grep -qiE "generated with|claude code|co-authored-by" "$PKG/README.md"; then
  echo "README traegt eine Werkzeugsignatur — nicht veroeffentlicht."; exit 1
fi

cd "$REPO"
[ -z "$(git status --porcelain -- packages/x402)" ] || {
  echo "packages/x402 hat uncommittete Aenderungen — nicht veroeffentlicht."; exit 1; }

echo "=== Parity-Vektoren, alle drei Implementierungen ==="
node packages/x402/test/run-parity.js 2>/dev/null || node --test packages/x402/test/ 2>&1 | tail -5

OTP="${1:-}"
echo "=== publish ==="
cd "$PKG"
if [ -n "$OTP" ]; then
  npm publish --access public --otp="$OTP"
else
  npm publish --access public
fi

echo "=== Installationsprobe aus leerem Verzeichnis ==="
TMP=$(mktemp -d); trap 'rm -rf "$TMP" "$NPMRC"' EXIT
cd "$TMP" && npm init -y >/dev/null
# Ohne Token, damit die Probe den oeffentlichen Weg nimmt und nicht unseren.
NPM_CONFIG_USERCONFIG=/dev/null npm install --no-audit --no-fund "$NAME@$VERSION" 2>&1 | tail -3
NPM_CONFIG_USERCONFIG=/dev/null node -e "
  const m = require('$NAME');
  const have = Object.keys(m);
  console.log('  geladen, exportiert:', have.slice(0,8).join(', '));
  if (!have.length) { console.error('  leeres Modul'); process.exit(1); }
"
echo "=== fertig: $NAME@$VERSION ==="
