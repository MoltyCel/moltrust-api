#!/bin/bash
# Weekly: has the Taskmarket legal bundle left draft?
#
# Two bounties were funded while the bundle was version 2026-07-draft-2 with
# enforcement off — writes go through, but nothing is accepted and no agreed
# terms sit over the escrow. That was a deliberate call; this watches for the
# moment it needs revisiting, which is the moment the bundle is published.
set -o pipefail
export PATH="$HOME/.npm-global/bin:$PATH"

STATE=$(taskmarket legal status 2>/dev/null) || exit 0
STATUS=$(echo "$STATE" | python3 -c "import json,sys; print(json.load(sys.stdin)['data'].get('status',''))" 2>/dev/null)
ACCEPTED=$(echo "$STATE" | python3 -c "import json,sys; print(json.load(sys.stdin)['data'].get('accepted'))" 2>/dev/null)
VERSION=$(echo "$STATE" | python3 -c "import json,sys; print(json.load(sys.stdin)['data'].get('bundleVersion',''))" 2>/dev/null)

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) status=$STATUS accepted=$ACCEPTED version=$VERSION"

# Silent while it stays a draft. Speaks up the moment it is not, because that is
# when someone has to decide whether to accept before more money goes in.
if [ "$STATUS" != "draft" ]; then
    set -a && source "$HOME/.moltrust_secrets" && set +a
    MSG="MolTrust — taskmarket: Rechtsbuendel nicht mehr Entwurf

status    $STATUS
version   $VERSION
accepted  $ACCEPTED

Vor jeder weiteren Einzahlung entscheiden, ob angenommen wird.
Aktuell 10 USDC in Escrow auf zwei Bounties."
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID_ALERTS:-$TELEGRAM_CHAT_ID}" --data-urlencode "text=$MSG" >/dev/null
fi
