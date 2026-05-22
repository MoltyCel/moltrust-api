#!/bin/bash
# Sprint 1.2.2 — Post-Deploy-Verifikation
set -uo pipefail

BASE="https://api.moltrust.ch"
FAIL=0

check_contains() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$actual" == *"$expected"* ]]; then
    echo "  ✓ $label"
  else
    echo "  ✗ $label"
    echo "    expected: $expected"
    echo "    actual:   $actual"
    FAIL=$((FAIL+1))
  fi
}

check_not_contains() {
  local label="$1" forbidden="$2" actual="$3"
  if [[ "$actual" != *"$forbidden"* ]]; then
    echo "  ✓ $label"
  else
    echo "  ✗ $label (still contains: $forbidden)"
    FAIL=$((FAIL+1))
  fi
}

echo "=== 1. Health ==="
H=$(curl -s -m 10 "$BASE/health")
check_contains "API up" '"status":"ok"' "$H"
check_contains "DB connected" '"database":"connected"' "$H"

echo ""
echo "=== 2. Seed-Liste ==="
S=$(curl -s -m 10 "$BASE/swarm/stats")
check_contains "TrustScout präsent" 'd34ed796a4dc4698' "$S"
check_contains "Ambassador präsent" 'ambassador0001' "$S"
check_not_contains "VCOne entfernt" '"did:moltrust:vcone"' "$S"
check_not_contains "662a7181 entfernt" '662a7181e0154998' "$S"

echo ""
echo "=== 3. TrustScout (d34ed796) ==="
T=$(curl -s -m 10 "$BASE/skill/trust-score/did:moltrust:d34ed796a4dc4698")
check_contains "trust_score 85" '"trust_score":85.0' "$T"
check_contains "grade A" '"grade":"A"' "$T"
check_contains "phase2 computation" '"computation_method":"phase2"' "$T"
TC=$(curl -s -m 10 "$BASE/a2a/agent-card/did:moltrust:d34ed796a4dc4698")
check_contains "Card name = TrustScout" '"name":"TrustScout"' "$TC"
check_not_contains "Card score nicht 0" '"score":0.0' "$TC"

echo ""
echo "=== 4. Ambassador (Sybil-Whitelist wirkt) ==="
A=$(curl -s -m 10 "$BASE/skill/trust-score/did:moltrust:ambassador0001")
check_contains "sybil_penalty 0" '"sybil_penalty":0' "$A"
SCORE=$(echo "$A" | grep -oP '"trust_score":\K[0-9.]+')
echo "  Ambassador trust_score=$SCORE (erwartet >80)"
AC=$(curl -s -m 10 "$BASE/a2a/agent-card/did:moltrust:ambassador0001")
check_contains "Card 200 (war 400)" '"name"' "$AC"

echo ""
echo "=== 5. Revozierte Stubs ==="
for STUB in 28a0984ab85d4c40 te5tharne550001; do
  R=$(curl -s -m 10 "$BASE/skill/trust-score/did:moltrust:$STUB")
  if [[ "$R" == *'"withheld":true'* ]] || [[ "$R" == *'revoked'* ]]; then
    echo "  ✓ $STUB revoziert/withheld"
  else
    echo "  ⚠ $STUB Status unklar"
    FAIL=$((FAIL+1))
  fi
done

echo ""
echo "=== 6. SeedRequest-Validator (Patch 2) ==="
V=$(curl -s -m 10 -o /dev/null -w "%{http_code}" \
  -X POST "$BASE/swarm/seed" -H 'Content-Type: application/json' \
  -d '{"did":"did:moltrust:newvanity","label":"x","base_score":50}')
if [[ "$V" == "422" ]]; then
  echo "  ✓ Vanity-DID rejected (422)"
else
  echo "  ✗ Vanity-DID accepted (HTTP $V) — Validator wirkt nicht"
  FAIL=$((FAIL+1))
fi

echo ""
echo "=== 7. avg_trust_score Stabilität ==="
PREV=""
STABLE=true
for i in 1 2 3 4 5; do
  AVG=$(curl -s -m 10 "$BASE/swarm/stats" | grep -oP '"avg_trust_score":\K[0-9.]+')
  echo "  Call $i: avg_trust_score=$AVG"
  if [[ -n "$PREV" && "$AVG" != "$PREV" ]]; then STABLE=false; fi
  PREV="$AVG"
  sleep 1
done
if $STABLE; then
  echo "  ✓ stabil über 5 Calls"
else
  echo "  ✗ avg_trust_score driftet — Patch 3 wirkt nicht"
  FAIL=$((FAIL+1))
fi

echo ""
echo "============================================="
if [[ $FAIL -eq 0 ]]; then
  echo "  ALLE CHECKS BESTANDEN"
else
  echo "  $FAIL FEHLER"
fi
echo "============================================="
exit $FAIL
