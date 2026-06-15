#!/usr/bin/env bash
# devto_publish.sh — publish OR update a markdown file on dev.to via the API.
# Deterministic, secret-safe. Called by the publish runbook AFTER the
# canonical blog version is live on moltrust.ch.
#
# Secret hygiene (per house rules):
#   - never `set -x`
#   - API key is read from a secret file, never echoed, never placed in argv
#     (curl reads the header via a process-substituted --config, printf is a
#      bash builtin so the key never appears in `ps`)
#   - --dry-run builds and prints the request WITHOUT loading the key or
#     touching the network, so it is safe to run/test without secrets.
#
# Usage:
#   # create (default):
#   ./devto_publish.sh --md POST.md --title "T" --canonical URL --tags "a,b" [--draft]
#
#   # update an existing article (idempotent PUT):
#   ./devto_publish.sh --update --id 123456 \
#       --md POST.md --title "T" --canonical URL --tags "a,b" [--draft]
#
#   # preview the exact request without calling dev.to:
#   ./devto_publish.sh --update --id 123456 --md POST.md --title "T" --canonical URL --dry-run
#
# Env / secrets:
#   DEVTO_API_KEY  — from env if set, else sourced from SECRET_FILE
#                    (default ~/.moltrust_secrets, line: DEVTO_API_KEY=...)

set -euo pipefail

MD="" TITLE="" CANONICAL="" TAGS="" PUBLISHED=true
ACTION="create" ARTICLE_ID="" DRY_RUN=false
SECRET_FILE="${SECRET_FILE:-$HOME/.moltrust_secrets}"
API_BASE="${DEVTO_API_BASE:-https://dev.to/api}"

while [ $# -gt 0 ]; do
  case "$1" in
    --md)         MD="$2"; shift 2 ;;
    --title)      TITLE="$2"; shift 2 ;;
    --canonical)  CANONICAL="$2"; shift 2 ;;
    --tags)       TAGS="$2"; shift 2 ;;
    --draft)      PUBLISHED=false; shift ;;
    --update)     ACTION="update"; shift ;;
    --id|--article-id) ARTICLE_ID="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=true; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- argument validation -----------------------------------------------------
[ -n "$MD" ] && [ -f "$MD" ]      || { echo "missing/invalid --md" >&2; exit 2; }
[ -n "$TITLE" ]                   || { echo "missing --title" >&2; exit 2; }
[ -n "$CANONICAL" ]               || { echo "missing --canonical (blog must be live first)" >&2; exit 2; }
command -v jq   >/dev/null        || { echo "jq required" >&2; exit 3; }
command -v curl >/dev/null        || { echo "curl required" >&2; exit 3; }

# --update needs an article id, and it must look like a positive integer.
if [ "$ACTION" = "update" ]; then
  [ -n "$ARTICLE_ID" ] || { echo "missing --id: --update requires the dev.to article id" >&2; exit 2; }
  case "$ARTICLE_ID" in
    ''|*[!0-9]*) echo "invalid --id '$ARTICLE_ID': must be a positive integer" >&2; exit 2 ;;
  esac
elif [ -n "$ARTICLE_ID" ]; then
  echo "--id is only valid with --update (create assigns its own id)" >&2; exit 2
fi

# --- method + endpoint (PUT is idempotent → re-running --update is safe) ------
if [ "$ACTION" = "update" ]; then
  METHOD="PUT";  URL="$API_BASE/articles/$ARTICLE_ID"
else
  METHOD="POST"; URL="$API_BASE/articles"
fi

# --- build payload -----------------------------------------------------------
# dev.to wants body_markdown WITHOUT a leading H1 (the title field renders the
# headline). Strip a single leading "# ..." line if present so it isn't doubled.
BODY_TMP="$(mktemp)"; PAYLOAD="$(mktemp)"
chmod 600 "$BODY_TMP" "$PAYLOAD"
trap 'rm -f "$BODY_TMP" "$PAYLOAD"' EXIT
awk 'NR==1 && /^# / {next} {print}' "$MD" > "$BODY_TMP"

jq -n \
  --arg  t "$TITLE" \
  --arg  c "$CANONICAL" \
  --argjson pub "$PUBLISHED" \
  --rawfile b "$BODY_TMP" \
  --arg  tags "$TAGS" \
  '{article:{title:$t, body_markdown:$b, published:$pub, canonical_url:$c,
             tags:(($tags|split(",")|map(select(length>0))))}}' > "$PAYLOAD"

# --- dry-run: print the request and stop (no key, no network) ----------------
if [ "$DRY_RUN" = true ]; then
  echo "[dry-run] $METHOD $URL"
  echo "[dry-run] published=$PUBLISHED action=$ACTION${ARTICLE_ID:+ id=$ARTICLE_ID}"
  echo "[dry-run] payload:"
  jq . "$PAYLOAD"
  exit 0
fi

# --- load the key without printing it ----------------------------------------
if [ -z "${DEVTO_API_KEY:-}" ]; then
  [ -f "$SECRET_FILE" ] || { echo "no DEVTO_API_KEY in env and $SECRET_FILE not found" >&2; exit 4; }
  # shellcheck disable=SC1090
  DEVTO_API_KEY="$(grep -E '^DEVTO_API_KEY=' "$SECRET_FILE" | head -n1 | cut -d= -f2-)"
fi
[ -n "${DEVTO_API_KEY:-}" ] || { echo "DEVTO_API_KEY is empty" >&2; exit 4; }

# --- send. api-key header fed via --config from a builtin-printf process ------
# substitution, so it is never in curl's argv (ps-safe). --request sets PUT/POST.
RESP="$(curl --silent --show-error --fail \
  --request "$METHOD" \
  --config <(printf 'header = "api-key: %s"\n' "$DEVTO_API_KEY") \
  -H "Content-Type: application/json" \
  --data @"$PAYLOAD" \
  "$URL")"

# Only the public URL is printed — never the key.
echo "$RESP" | jq -r '"'"$ACTION"'ed: " + (.url // "no url in response")'
