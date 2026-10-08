#!/bin/bash
# deploy.sh <repo> <sha>
#
# The single deploy path for moltrust-api and moltrust-web. Called by the
# GitHub Actions workflow over SSH, where it is the forced command and the
# arguments arrive in SSH_ORIGINAL_COMMAND. Callable by hand with the same
# two arguments.
#
# One deploy at a time (flock, waiting not failing), the requested commit and
# nothing else, a health probe afterwards, and a rollback to the previously
# deployed commit when the probe fails.
set -uo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

LOCK=/home/moltstack/.deploy.lock
STATE=/home/moltstack/.deployed
LOG=/home/moltstack/logs/deploy.log
WRAPPER=/usr/local/sbin/moltstack-webinstall

API_DIR=/home/moltstack/moltstack
WEB_DIR=/home/moltstack/moltrust-web
API_HEALTH=http://127.0.0.1:8000/health

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(ts)" "$1" | tee -a "$LOG" >&2; }

# DEPLOY_TEST=1 marks a rehearsal. The message still goes out — a silent test
# path is how a broken notifier stays unnoticed — but it says so in the first
# characters, so nobody reads it as an incident.
telegram() {
  local text=$1 token chat
  [ "${DEPLOY_TEST:-0}" = 1 ] && text="[TEST] $text"
  set -a; . /home/moltstack/.moltrust_secrets 2>/dev/null || true; set +a
  token=${TELEGRAM_BOT_TOKEN:-}; chat=${TELEGRAM_CHAT_ID:-}
  [ -n "$token" ] && [ -n "$chat" ] || { log "telegram: no credentials, console only"; return 0; }
  curl -sS -m 25 -o /dev/null \
    --data-urlencode "chat_id=$chat" --data-urlencode "text=$text" \
    "https://api.telegram.org/bot${token}/sendMessage" \
    || log "telegram: send failed"
}


# Ein erfolgreicher Deploy meldet nicht mehr einzeln. Am 07./08.10.2026 waren
# das 35 Nachrichten in 24 Stunden, 32 davon "ok" — und in dieser Menge ist die
# eine, die zaehlt, nicht mehr zu finden. Gezaehlt wird weiter; gemeldet wird
# einmal taeglich in der 08:00Z-Sammelmeldung des Selftests.
#
# Gescheitert und zurueckgerollt melden weiter sofort. Die Zeile hier kommt
# zusaetzlich, damit die Tagesmeldung auch "0 gescheitert" belegen kann statt
# es nur zu behaupten.
DEPLOY_LOG=/home/moltstack/selftest/deploy-log.jsonl

record_deploy() {
  local status=$1 sha=$2 dir
  dir=$(dirname "$DEPLOY_LOG")
  mkdir -p "$dir"
  if [ $? -ne 0 ]; then log "deploy-log: mkdir $dir fehlgeschlagen"; return 0; fi
  printf '{"ts":"%s","dienst":"%s","sha":"%s","status":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${REPO:-?}" "$sha" "$status" >> "$DEPLOY_LOG"
  if [ $? -ne 0 ]; then log "deploy-log: schreiben fehlgeschlagen"; return 0; fi
  chmod 600 "$DEPLOY_LOG"
  if [ $? -ne 0 ]; then log "deploy-log: chmod fehlgeschlagen"; fi
  return 0
}

die() { log "FAIL $*"; record_deploy failed "${SHA:-?}"; telegram "MolTrust deploy FAILED — ${REPO:-?} ${SHA:-?}
$*"; exit 1; }

# ------------------------------------------------------------- self-check
# Since 2026-10-08 this file lives in moltrust-api as ops/deploy/deploy.sh. The
# copy that runs is /home/moltstack/bin/deploy.sh, and it has to be the copy at
# the commit moltrust-api is deployed at. A copy that differs was changed by
# hand on the server, which is how deploy.sh changed at 07:19:24 that morning
# with nobody able to say by whom afterwards. A run that finds a mismatch does
# nothing, exits 1 and says so on ALERTS.
DEPLOY_SELF=/home/moltstack/bin/deploy.sh

alert() {  # telegram to the ALERTS channel, falling back to the default chat
  local text=$1
  set -a; . /home/moltstack/.moltrust_secrets 2>/dev/null || true; set +a
  local chat=${TELEGRAM_CHAT_ID_ALERTS:-${TELEGRAM_CHAT_ID:-}}
  TELEGRAM_CHAT_ID=$chat telegram "$text"
}

# self_check <self file> <api checkout> <state file of moltrust-api>
# 0 when the running file equals ops/deploy/deploy.sh at the deployed commit.
self_check() {
  local self=$1 api=$2 statefile=$3 want_sha want have
  want_sha=$(cut -f1 "$statefile" 2>/dev/null)
  if [ -z "$want_sha" ]; then
    SELF_CHECK_WHY="no deployed moltrust-api commit recorded in $statefile"; return 1
  fi
  if ! want=$(git -C "$api" show "$want_sha:ops/deploy/deploy.sh" 2>/dev/null | sha256sum | cut -c1-64) \
     || ! git -C "$api" cat-file -e "$want_sha:ops/deploy/deploy.sh" 2>/dev/null; then
    SELF_CHECK_WHY="ops/deploy/deploy.sh is not in the deployed commit $want_sha"; return 1
  fi
  have=$(sha256sum "$self" 2>/dev/null | cut -c1-64)
  if [ "$have" != "$want" ]; then
    SELF_CHECK_WHY="$self is ${have:-unreadable}, ops/deploy/deploy.sh at $want_sha is $want"; return 1
  fi
  return 0
}

# self_install <api checkout> <sha> <target>
# Writes ops/deploy/deploy.sh at <sha> to <target>, atomically: into a temp file
# beside the target, checked for syntax, then renamed over it. A failure at any
# step leaves the target as it was and removes the temp file. The running bash
# keeps reading the old file (rename gives the target a new inode), so the new
# version takes effect from the next run, never inside this one.
self_install() {
  local api=$1 sha=$2 target=$3 tmp
  tmp=$(mktemp "$(dirname "$target")/.deploy.sh.new.XXXXXX") || { SELF_INSTALL_WHY="mktemp failed"; return 1; }
  if ! git -C "$api" show "$sha:ops/deploy/deploy.sh" > "$tmp" 2>/dev/null; then
    rm -f "$tmp"; SELF_INSTALL_WHY="git show $sha:ops/deploy/deploy.sh failed"; return 1
  fi
  if [ ! -s "$tmp" ]; then rm -f "$tmp"; SELF_INSTALL_WHY="empty file at $sha"; return 1; fi
  if ! bash -n "$tmp" 2>/dev/null; then rm -f "$tmp"; SELF_INSTALL_WHY="syntax error in ops/deploy/deploy.sh at $sha"; return 1; fi
  if ! chmod 700 "$tmp"; then rm -f "$tmp"; SELF_INSTALL_WHY="chmod failed"; return 1; fi
  if ! mv -f "$tmp" "$target"; then rm -f "$tmp"; SELF_INSTALL_WHY="rename over $target failed"; return 1; fi
  return 0
}

# After a successful moltrust-api deploy: bring the running copy to the commit
# just deployed, so the next run's self-check finds them equal. Only this step
# writes deploy.sh; it is the one exception to the self-check.
install_self_if_changed() {
  local want have
  want=$(git -C "$API_DIR" show "$SHA:ops/deploy/deploy.sh" 2>/dev/null | sha256sum | cut -c1-64)
  have=$(sha256sum "$DEPLOY_SELF" 2>/dev/null | cut -c1-64)
  [ "$want" = "$have" ] && return 0
  if self_install "$API_DIR" "$SHA" "$DEPLOY_SELF"; then
    log "deploy.sh installed from $SHA ($want), effective from the next run"
  else
    log "FAIL deploy.sh install: $SELF_INSTALL_WHY"
    alert "MolTrust deploy.sh NOT installed — $SELF_INSTALL_WHY
moltrust-api $SHA is deployed; the next deploy will refuse until deploy.sh matches."
  fi
  return 0
}

# Which files of this commit range are actually served. The list comes from
# git, never from a staging directory — blog-deploy-stage never expires and
# on 2026-09-23 held 248 files from deploys months old.
#
# Since 2026-10-08 the list is positive: a file ships only if it matches one of
# the classes below. The exclusions before it stay as a second fence. An
# exclusion list ships whatever nobody thought of; on 2026-10-08 a folder of
# image masters would have gone to the web root because it was not docs/.
# Classes: pages (root and named folders; trouvart/ deliberately not), assets
# (img/, assets/, .well-known/), discovery files, documents (PDFs at the root,
# in publications/ and papers/), and JSON-LD contexts (contexts/<name>/vN, no
# extension, served as application/ld+json by nginx; since 2026-10-08). A new
# folder of pages is a change to this list, by PR.
WEB_ALLOW='^[^/]+\.html$
^(blog|publications|bindings/trust-registry|enterprise|partners|pricing|verify|admin|reseller|zh)/[^/]+\.html$
^(img|assets|\.well-known)/
^(robots\.txt|llms\.txt|sitemap\.xml|agents\.txt|api-llms\.txt|api-robots\.txt|favicon\.ico|favicon\.svg|favicon-16x16\.png|favicon-32x32\.png|apple-touch-icon\.png|copy-code\.js|og-image(-v[0-9]+)?\.png)$
^blog/(feed\.xml|og-blog\.png)$
^[^/]+\.pdf$
^(publications|papers)/[^/]+\.pdf$
^contexts/[a-z0-9-]+/v[0-9]+$'

web_files() {
  git diff --name-only --diff-filter=ACMRT "$1" "$2" -- . \
  | grep -E '\.(html|xml|txt|json|css|js|png|jpe?g|svg|webp|ico|pdf|woff2?)$|^contexts/[a-z0-9-]+/v[0-9]+$' \
  | grep -v -E '^(docs|scripts|checks|partials|test|tests|\.github)/' \
  | grep -v -E '(^|/)CLAUDE\.md$' \
  | grep -v -E '^blog/index\.html$' \
  | grep -E -f <(printf '%s\n' "$WEB_ALLOW") \
  || true
}

# The tests load the functions above and stop here. The deploy key's forced
# command passes no environment, so this cannot be set from outside.
if [ "${DEPLOY_SH_FUNCTIONS_ONLY:-0}" = 1 ]; then return 0 2>/dev/null || exit 0; fi

# ---------------------------------------------------------------- arguments
# Under the forced command the real arguments are in SSH_ORIGINAL_COMMAND.
if [ "$#" -eq 0 ] && [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
  # shellcheck disable=SC2086 # the fields are validated immediately below
  set -- $SSH_ORIGINAL_COMMAND
fi
# Two arguments for a deploy, three for supervise/superheal: the third is the
# run's origin, which has to travel as an argument because the forced command
# lets no environment across the SSH boundary.
case "$#" in
  2) : ;;
  3) : ;;
  *) echo "usage: deploy.sh <moltrust-api|moltrust-web> <40-hex-sha|diagnose|supervise|superheal> [origin]" >&2; exit 2 ;;
esac
REPO=$1; SHA=$2; ORIGIN=${3:-unknown}
# Whitelisted, not passed through. This string reaches a script argument, so it
# is matched against the four shapes and anything else becomes `unknown` -
# which is a fault state the collected report names, not a silent pass.
case "$ORIGIN" in
  workflow:[0-9]*|dispatch:[0-9]*|cron:[A-Za-z0-9._-]*) : ;;
  local:*@*) : ;;
  *) ORIGIN=unknown ;;
esac
case $REPO in moltrust-api|moltrust-web) : ;; *) echo "unknown repo: $REPO" >&2; exit 2 ;; esac

# ----------------------------------------------------------------- diagnose
# Read-only, and it returns here: before the lock, before any git command,
# before anything is installed or restarted. The forced command on the deploy
# key means this is the only way a workflow can ask the server a question, so
# the branch is deliberately the narrowest possible one - a single literal
# argument, one script, no write path reachable from it.
if [ "$SHA" = diagnose ]; then
  [ "$REPO" = moltrust-api ] || { echo "diagnose runs in moltrust-api only" >&2; exit 2; }
  exec /home/moltstack/moltstack/venv/bin/python \
       /home/moltstack/moltstack/ops/diagnose.py
fi

# supervise / superheal: the two arguments the hourly GitHub workflow may send.
# Same shape as diagnose - a literal word, one script, and a return before the
# lock, the fetch and the install. `supervise` is read-only and prints JSON;
# `superheal` may run the positive list in scripts/selfheal.py, nothing else.
if [ "$SHA" = supervise ] || [ "$SHA" = superheal ]; then
  [ "$REPO" = moltrust-api ] || { echo "supervise runs in moltrust-api only" >&2; exit 2; }
  if [ "$SHA" = supervise ]; then
    exec /home/moltstack/moltstack/ops/supervise.sh check "$ORIGIN"
  fi
  exec /home/moltstack/moltstack/ops/supervise.sh heal "$ORIGIN"
fi
[[ $SHA =~ ^[0-9a-f]{40}$ ]] || { echo "sha must be 40 lowercase hex: $SHA" >&2; exit 2; }

if ! self_check "$DEPLOY_SELF" "$API_DIR" "$STATE/moltrust-api"; then
  log "FAIL self-check: $SELF_CHECK_WHY — nothing deployed"
  # A refusal leaves the checkout and ~/.deployed where they were, so nothing
  # else shows it; this line is what the 08:00 report counts.
  record_deploy refused "$SHA"
  alert "MolTrust deploy REFUSED — deploy.sh differs from the repository
$SELF_CHECK_WHY
${REPO} ${SHA} was not deployed."
  exit 1
fi

mkdir -p "$STATE" "$(dirname "$LOG")"

# ------------------------------------------------------------------- lock
# -w 1800: a queued deploy waits for the one in front of it rather than racing
# it. Two deploys of the same repo never overlap.
#
# The lock alone said nothing about who held it. On 2026-10-03 a deploy was
# refused because another session had left uncommitted changes in the checkout,
# and the only way to find out who was to go and look. Beside the lock there is
# now a note naming the pid, the start time, the repo and the sha, so a waiting
# run says what it waits for and a crashed one leaves a trail.
INFO=$LOCK.info

field() { sed -n "s/^$1=//p" "$INFO" 2>/dev/null; }

holder() {
  if [ ! -f "$INFO" ]; then echo "unknown (no note beside the lock)"; return; fi
  local pid alive
  pid=$(field pid)
  alive=dead
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then alive=alive; fi
  echo "pid ${pid:-?} ($alive), started $(field started), $(field repo) $(field sha), owner $(field owner)"
}

exec 9>"$LOCK"
if ! flock -n 9; then
  log "waiting: another run holds the lock - $(holder)"
  if ! flock -w 1800 9; then
    log "FAIL another deploy held the lock for 30 min - $(holder)"
    record_deploy refused "$SHA"
    exit 3
  fi
fi

# Held. A note left behind by a run that died is reported and overwritten; the
# trap clears ours on the way out, including on failure, which is when it counts.
if [ -f "$INFO" ]; then
  stale_pid=$(field pid)
  if [ -n "$stale_pid" ] && ! kill -0 "$stale_pid" 2>/dev/null; then
    log "note from a run that did not finish: $(holder)"
  fi
fi
{
  echo "pid=$$"
  echo "started=$(ts)"
  echo "repo=$REPO"
  echo "sha=$SHA"
  echo "owner=$(cat /home/moltstack/.checkout_owner.name 2>/dev/null || echo unknown)"
} > "$INFO"
trap 'rm -f "$INFO"' EXIT

log "START $REPO $SHA (pid $$)"

prev_recorded() { [ -f "$STATE/$REPO" ] && cut -f1 "$STATE/$REPO" || true; }
record() { printf '%s\t%s\t%s\n' "$1" "$(ts)" "$2" > "$STATE/$REPO"; }

# ------------------------------------------------------------ fetch + gate
case $REPO in
  moltrust-api) DIR=$API_DIR ;;
  moltrust-web) DIR=$WEB_DIR ;;
esac
cd "$DIR" || die "no checkout at $DIR"
dirty=$(git status --porcelain --untracked-files=no)
[ -z "$dirty" ] || die "tracked files are modified in $DIR, refusing to deploy:
$dirty"
git fetch -q origin main || die "git fetch failed in $DIR"
git cat-file -e "$SHA^{commit}" 2>/dev/null || die "$SHA is not a commit in $DIR"
git merge-base --is-ancestor "$SHA" origin/main \
  || die "$SHA is not on origin/main — only merged commits deploy"

PREV=$(prev_recorded)
[ -n "$PREV" ] || PREV=$(git rev-parse HEAD)
log "previous=$PREV target=$SHA"

checkout() {  # checkout <sha>, fast-forward only on the way up, hard on the way back
  local want=$1
  git checkout -q main || return 1
  if git merge-base --is-ancestor "$(git rev-parse HEAD)" "$want"; then
    git merge -q --ff-only "$want"
  else
    git reset -q --hard "$want"   # rollback direction
  fi
}

# ------------------------------------------------------------------ deploy
probe_api() {
  local code
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    code=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "$API_HEALTH" 2>/dev/null || echo 000)
    [ "$code" = 200 ] && { echo "$code"; return 0; }
    sleep 2
  done
  echo "$code"; return 1
}

deploy_api() {
  checkout "$SHA" || return 1
  if ! git diff --quiet "$PREV" "$SHA" -- requirements.txt 2>/dev/null; then
    log "requirements.txt changed, installing"
    ./venv/bin/pip install -q -r requirements.txt || return 1
  fi
  sudo -n /usr/bin/systemctl restart moltstack.service || return 1
  sleep 3
  local code; code=$(probe_api) || { log "health probe: $code"; return 1; }
  log "health probe: $code"
  restart_mcp_if_changed
}

# moltrust-mcp-http runs services/mcp_http.py and the moltrust-mcp-server
# package from requirements.txt in its own unit. Until 2026-10-08 nothing
# restarted it, so a change there went live only after a restart by hand (#682
# that day). Restarted only when one of those changed: every restart drops the
# open MCP sessions. A failure is reported and never rolls the API back — the
# API deploy itself is fine.
MCP_UNIT=moltrust-mcp-http.service

restart_mcp_if_changed() {
  if git diff --quiet "$PREV" "$SHA" -- services/ requirements.txt 2>/dev/null; then
    return 0
  fi
  log "services/ or requirements.txt changed, restarting $MCP_UNIT"
  if ! sudo -n /usr/bin/systemctl restart "$MCP_UNIT"; then
    log "FAIL restart $MCP_UNIT"
    alert "MolTrust deploy: $MCP_UNIT did not restart after moltrust-api $SHA
the API is deployed; the MCP server runs the previous code"
    return 0
  fi
  sleep 3
  if systemctl is-active --quiet "$MCP_UNIT"; then
    log "$MCP_UNIT: active"
  else
    log "FAIL $MCP_UNIT not active after restart"
    alert "MolTrust deploy: $MCP_UNIT is not active after restart (moltrust-api $SHA)"
  fi
  return 0
}

deploy_web() {
  [ -x "$WRAPPER" ] || { log "$WRAPPER is missing — the sudoers hardening has not been applied yet"; return 1; }
  checkout "$SHA" || return 1
  # website-deploy.md 4.1a3 — an internal pre-publish note must not reach the
  # web root. Fatal before any install: a note saying the page is not ready
  # stops nothing once the page is live. On 2026-10-05 three pages carried one,
  # one of them since 2026-07-09, and it was a button press from LinkedIn.
  #
  # Only the repo checks run here. The index/feed coupling needs the generated
  # registers, and the index is regenerated by cron every 15 minutes, so right
  # after an install it legitimately lags the new page. That half is reported
  # below, never fatal.
  if [ -f "$WEB_DIR/scripts/predeploy_gate.py" ]; then
    local gout
    # 4.1a7: a published versioned artifact (contexts/, versioned PDFs) is not
    # changed against the previously deployed commit. Passed only when the
    # gate in this commit knows the option; an older gate would refuse it.
    local gargs=()
    if grep -q -- '--immutable-from' "$WEB_DIR/scripts/predeploy_gate.py"; then
      gargs=(--immutable-from "$PREV")
    fi
    if ! gout=$(cd "$WEB_DIR" && python3 scripts/predeploy_gate.py "${gargs[@]}" 2>&1); then
      printf '%s\n' "$gout" | while IFS= read -r gl; do [ -n "$gl" ] && log "gate: $gl"; done
      log "predeploy gate REFUSED $SHA — nothing installed"
      return 1
    fi
    log "predeploy gate: ok"
  else
    log "predeploy gate: script not in this commit, skipped"
  fi
  local files; files=$(web_files "$PREV" "$SHA")
  if [ -z "$files" ]; then
    log "nothing served changed between $PREV and $SHA"
    return 0
  fi
  log "shipping $(echo "$files" | wc -l | tr -d ' ') file(s):"
  echo "$files" | while read -r f; do log "  $f"; done
  echo "$files" | while read -r f; do
    sudo -n "$WRAPPER" "$f" >/dev/null || { log "install failed: $f"; exit 1; }
  done || return 1
  # Each shipped path must answer, and the home page must still answer.
  local bad=0 code
  for p in / $(echo "$files" | sed 's#^#/#'); do
    code=$(curl -sS -m 10 -o /dev/null -w '%{http_code}' "https://moltrust.ch${p}" 2>/dev/null || echo 000)
    case $code in 200|301|302) log "probe $p -> $code" ;; *) log "probe $p -> $code"; bad=1 ;; esac
  done
  [ "$bad" -eq 0 ] || return 1
  if [ -f "$WEB_DIR/scripts/predeploy_gate.py" ]; then
    local cout
    cout=$(cd "$WEB_DIR" && python3 scripts/predeploy_gate.py --live 2>&1) || true
    printf '%s\n' "$cout" | grep -i "coupling" | while IFS= read -r cl; do
      [ -n "$cl" ] && log "gate: $cl"
    done
  fi
}

rollback() {
  log "ROLLBACK to $PREV"
  case $REPO in
    moltrust-api)
      checkout "$PREV" && sudo -n /usr/bin/systemctl restart moltstack.service && sleep 3
      local code; code=$(probe_api) || true
      log "post-rollback health: $code"
      ;;
    moltrust-web)
      local files; files=$(web_files "$PREV" "$SHA")
      checkout "$PREV" || { log "rollback checkout failed"; return 1; }
      echo "$files" | while read -r f; do
        [ -n "$f" ] || continue
        if [ -f "$WEB_DIR/$f" ]; then sudo -n "$WRAPPER" "$f" >/dev/null || log "rollback install failed: $f"
        else log "rollback: $f did not exist at $PREV, left in place"; fi
      done
      ;;
  esac
}

# After a successful api deploy: does the running code carry the rule the
# commit says it does. On 2026-10-05 the radar logged `prompt v3-2026-10-05`
# and drafted under the old rule for twelve hours, because a file the prompt
# needed was not on main and the safeguard dropped the rule as designed. The
# version string was right and every other signal was green.
#
# Never fatal, and never a rollback trigger. A mismatch usually means the
# commit is incomplete — which is what happened — and rolling back to the
# previous commit does not fix that. It reports, and --alert sends on its own
# as a single message, deliberately outside the twice-daily report.
verify_versions() {
  [ "$REPO" = moltrust-api ] || return 0
  [ -f "$API_DIR/scripts/deploy_verify.py" ] || { log "version check: script not in this commit, skipped"; return 0; }
  set -a; . /home/moltstack/.moltrust_secrets 2>/dev/null || true; set +a
  local out code
  out=$(cd "$API_DIR" && PYTHONPATH="$API_DIR" ./venv/bin/python scripts/deploy_verify.py --alert --sha "$SHA" --dienst "$REPO" 2>&1)
  code=$?
  printf '%s\n' "$out" | while IFS= read -r l; do [ -n "$l" ] && log "verify: $l"; done
  if [ "$code" -eq 0 ]; then
    log "version check: every versioned path carries the committed rule"
  else
    log "version check: MISMATCH (exit $code) — alert sent, deploy left in place"
  fi
  return 0
}

case $REPO in
  moltrust-api) deploy_api; ok=$? ;;
  moltrust-web) deploy_web; ok=$? ;;
esac

if [ "${ok:-1}" -eq 0 ]; then
  record "$SHA" ok
  log "OK $REPO $SHA"
  [ "$REPO" = moltrust-api ] && install_self_if_changed
  verify_versions
  record_deploy ok "$SHA"
  exit 0
fi

rollback
record "$PREV" "rolled-back-from:$SHA"
log "ROLLED BACK $REPO to $PREV"
record_deploy rolled-back "$SHA"
telegram "MolTrust deploy FAILED — $REPO
tried $SHA, rolled back to $PREV
see $LOG"
exit 1
