#!/usr/bin/env bash
# Smoke test for devto_publish.sh — DRY-RUN ONLY, no network, no secrets.
# Verifies: create vs update method/endpoint, missing-id error, bad-id error,
# stray-id-without-update error, H1 strip, payload JSON validity, idempotency.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SH="$HERE/devto_publish.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
MD="$TMP/post.md"
printf '# Doubled Title\n\nBody line one.\n\nBody line two.\n' > "$MD"

pass=0 fail=0
ok(){ echo "  PASS: $1"; pass=$((pass+1)); }
no(){ echo "  FAIL: $1"; fail=$((fail+1)); }

run(){ DEVTO_API_KEY="should-not-be-used" bash "$SH" "$@" 2>"$TMP/err"; }

echo "== 1. create dry-run -> POST /articles =="
out="$(run --md "$MD" --title "T" --canonical "https://moltrust.ch/x" --tags "a,b" --dry-run)"; rc=$?
[ $rc -eq 0 ] && grep -q "POST .*/articles$" <<<"$out" && ok "create method/endpoint" || no "create method/endpoint (rc=$rc)"
grep -q '"published": true' <<<"$out" && ok "create published=true default" || no "create published default"
json="$(sed -n '/^{/,$p' <<<"$out")"
body="$(jq -r '.article.body_markdown' <<<"$json" 2>/dev/null)"
grep -q "Doubled Title" <<<"$body" && no "leading H1 NOT stripped" || ok "leading H1 stripped from body"
grep -q "Body line one." <<<"$body" && ok "body content preserved" || no "body content lost"
jq -e . >/dev/null 2>&1 <<<"$json" && ok "create payload is valid JSON" || no "create payload JSON invalid"

echo "== 2. update dry-run with id -> PUT /articles/123 =="
out="$(run --update --id 123456 --md "$MD" --title "T" --canonical "https://moltrust.ch/x" --dry-run)"; rc=$?
[ $rc -eq 0 ] && grep -q "PUT .*/articles/123456$" <<<"$out" && ok "update method/endpoint" || no "update method/endpoint (rc=$rc)"

echo "== 3. update WITHOUT id -> error exit 2 =="
run --update --md "$MD" --title "T" --canonical "https://moltrust.ch/x" --dry-run >/dev/null; rc=$?
[ $rc -eq 2 ] && grep -qi "requires the dev.to article id" "$TMP/err" && ok "missing-id errors (exit 2)" || no "missing-id (rc=$rc)"

echo "== 4. update with non-numeric id -> error exit 2 =="
run --update --id abc --md "$MD" --title "T" --canonical "https://moltrust.ch/x" --dry-run >/dev/null; rc=$?
[ $rc -eq 2 ] && grep -qi "must be a positive integer" "$TMP/err" && ok "bad-id errors (exit 2)" || no "bad-id (rc=$rc)"

echo "== 5. --id without --update -> error exit 2 =="
run --id 9 --md "$MD" --title "T" --canonical "https://moltrust.ch/x" --dry-run >/dev/null; rc=$?
[ $rc -eq 2 ] && grep -qi "only valid with --update" "$TMP/err" && ok "stray-id errors (exit 2)" || no "stray-id (rc=$rc)"

echo "== 6. idempotency: two identical update dry-runs produce identical payload =="
a="$(run --update --id 7 --md "$MD" --title "T" --canonical "https://moltrust.ch/x" --tags "a,b" --dry-run | sed -n '/^{/,$p')"
b="$(run --update --id 7 --md "$MD" --title "T" --canonical "https://moltrust.ch/x" --tags "a,b" --dry-run | sed -n '/^{/,$p')"
[ "$a" = "$b" ] && [ -n "$a" ] && ok "identical inputs -> identical payload" || no "payload not stable"

echo "== 7. draft flag -> published=false =="
out="$(run --md "$MD" --title "T" --canonical "https://moltrust.ch/x" --draft --dry-run)"
grep -q '"published": false' <<<"$out" && ok "--draft sets published=false" || no "--draft"

echo
echo "RESULT: $pass passed, $fail failed"
[ $fail -eq 0 ]
