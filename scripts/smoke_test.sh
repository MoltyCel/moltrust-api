#!/usr/bin/env bash
#
# Smoke test for MolTrust API
# Usage: ./scripts/smoke_test.sh [base_url] [api_key]
#
set -eo pipefail

BASE="${1:-http://localhost:8000}"
KEY="${2:-dev_test_key_local}"
PASS=0
FAIL=0

check() {
    local name="$1" method="$2" path="$3" expected_status="$4"
    shift 4

    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" \
        -X "$method" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $KEY" \
        "$@" \
        "$BASE$path" 2>/dev/null || echo "000")

    if [ "$status" = "$expected_status" ]; then
        echo "  PASS  $name (HTTP $status)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  $name (expected $expected_status, got $status)"
        FAIL=$((FAIL + 1))
    fi
}

echo ""
echo "MolTrust API Smoke Test"
echo "======================="
echo "Target: $BASE"
echo ""

# --- Health & Basics ---
echo "Health & Basics"
check "GET /health" GET "/health" 200
check "GET /credits/pricing" GET "/credits/pricing" 200
check "GET /.well-known/did.json" GET "/.well-known/did.json" 200

# --- Identity ---
echo ""
echo "Identity"
AGENT_NAME="smoke_$(date +%s)"
check "POST /identity/register" POST "/identity/register" 200 \
    -d "{\"platform\":\"smoke_test\",\"display_name\":\"$AGENT_NAME\"}"

check "GET /identity/resolve (bad DID)" GET "/identity/resolve/did:moltrust:nonexistent" 400

# --- Credentials ---
echo ""
echo "Credentials"
check "POST /credentials/verify (empty)" POST "/credentials/verify" 422 \
    -d '{}'

# --- Auth enforcement ---
echo ""
echo "Auth enforcement"
auth_status=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -H "X-API-Key: invalid_key_xxx" \
    -d '{"platform":"test","display_name":"No Key"}' \
    "$BASE/identity/register" 2>/dev/null || echo "000")
if [ "$auth_status" = "403" ] || [ "$auth_status" = "401" ]; then
    echo "  PASS  POST /identity/register (bad key) (HTTP $auth_status)"
    PASS=$((PASS + 1))
else
    echo "  FAIL  POST /identity/register (bad key) (expected 403, got $auth_status)"
    FAIL=$((FAIL + 1))
fi

# --- Summary ---
echo ""
echo "======================="
echo "Results: $PASS passed, $FAIL failed"
echo ""

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
