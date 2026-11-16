#!/usr/bin/env bash
# Wait until the npm registry serves NAME@VERSION, with an upper bound.
#
# Origin: run 43832080931 (ops NPM-TP, 16.11.2026). The first publish of
# @moltrust/x402 over Trusted Publishing went through, and the install probe
# right after it failed with ETARGET: 2.2.1 was not in the registry index yet
# after about 3.5 minutes. A rerun of the failed job was green. The probe had
# guessed a waiting time; this asks the registry instead.
#
# --prefer-online makes npm revalidate its cached packument instead of
# answering from a copy fetched before the publish.
#
#   bash scripts/npm_warte_auf_version.sh @moltrust/x402 2.2.1
#
# NPM_WARTE_VERSUCHE (default 40) and NPM_WARTE_PAUSE in seconds (default 15)
# set the bound: 10 minutes. NPM names the npm binary (tests use a fake).
# Exit 0 as soon as the version is there, 1 when it never shows up, 2 on usage.
set -u

NAME=${1:-}
VERSION=${2:-}
[ -n "$NAME" ] && [ -n "$VERSION" ] || { echo "usage: $0 NAME VERSION" >&2; exit 2; }
VERSUCHE=${NPM_WARTE_VERSUCHE:-40}
PAUSE=${NPM_WARTE_PAUSE:-15}
NPM=${NPM:-npm}

i=1
while [ "$i" -le "$VERSUCHE" ]; do
  GOT=$("$NPM" view "$NAME@$VERSION" version --prefer-online 2>/dev/null || true)
  if [ "$GOT" = "$VERSION" ]; then
    echo "$NAME@$VERSION is on the registry (attempt $i of $VERSUCHE)"
    exit 0
  fi
  if [ "$i" -lt "$VERSUCHE" ]; then
    echo "$NAME@$VERSION not on the registry yet (attempt $i of $VERSUCHE), next in ${PAUSE}s"
    sleep "$PAUSE"
  fi
  i=$((i + 1))
done
echo "::error::$NAME@$VERSION is not on the registry after $VERSUCHE attempts, $((VERSUCHE * PAUSE))s"
exit 1
