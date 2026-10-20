#!/bin/bash
# deploy.sh <repo> <sha>
#
# The single deploy path for moltrust-api, moltrust-web and moltguard. Called
# by the GitHub Actions workflow over SSH, where it is the forced command and
# the arguments arrive in SSH_ORIGINAL_COMMAND. Callable by hand with the same
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
MOLTGUARD_DIR=/home/moltstack/moltguard
API_HEALTH=http://127.0.0.1:8000/health
MOLTGUARD_UNIT=moltguard

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s %s\n' "$(ts)" "$1" | tee -a "$LOG" >&2; }

# DEPLOY_TEST=1 marks a rehearsal. The message still goes out — a silent test
# path is how a broken notifier stays unnoticed — but it says so in the first
# characters, so nobody reads it as an incident.
#
# Through app/notify.py, not past it. Until 2026-10-09 this function built the
# Telegram URL itself and posted with curl, which meant a deploy message
# appeared in no line of ~/selftest/telegram-sent.jsonl and a repetition was
# never throttled: on 07./08.10. that was 35 messages in 24 hours, 32 of them
# "ok". The gate, the throttle and the send log all sit in notify, and an
# exception to them is an exception to all three.
#
# The text goes over stdin, not as an argument — it carries newlines and
# whatever the failure reason happens to contain. As a here-string, not through
# a pipe: the exit code decides what gets logged, and in a pipeline it is the
# pipeline's and not the command's.
#
# No curl fallback. A fallback for the case where notify does not answer is the
# exception again, just rarer and harder to see. When notify cannot send, the
# console log says so and the deploy carries on; what must not happen is the
# deploy being blocked by its own notification.
telegram() {
  local text=$1 channel=${2:-alerts} rc
  [ "${DEPLOY_TEST:-0}" = 1 ] && text="[TEST] $text"
  # Die Secrets laden, wie es die alte Fassung tat. deploy.sh laeuft als
  # forced command ueber SSH, und dort ist nichts davon in der Umgebung. Das
  # Weglassen hat am 09.10.2026 den ersten Meldeweg stillgelegt: notify fand
  # die Chat-ID und keinen Token. notify faellt inzwischen selbst auf die
  # Datei zurueck; beides zu haben ist Absicht — die Funktion soll fuer sich
  # genommen vollstaendig sein und nicht von einer Eigenschaft des Aufrufers
  # leben.
  set -a; . /home/moltstack/.moltrust_secrets 2>/dev/null || true; set +a
  PYTHONPATH="$API_DIR" "$API_DIR/venv/bin/python" \
    -m app.notify --channel "$channel" --stdin <<<"$text"
  rc=$?
  # 3 and 4 are decisions, not faults: the gate is off because somebody set it
  # off, and a throttled repeat was already delivered once. 5 and 70 mean the
  # message is gone, and 70 is what notify exits with when it could not report
  # from a non-interactive process — it overrides the CLI's own 5.
  #
  # Returning non-zero here rather than swallowing it: on 09.10.2026 this
  # function wrote "console only" and returned 0, and for five minutes a failed
  # deploy would have reached nobody while every exit code still said fine.
  # A reporting point that cannot report is a fault of the run that needed it.
  case $rc in
    0) return 0 ;;
    3) log "telegram: gate off (MOLTRUST_NOTIFY), console only"; return 0 ;;
    4) log "telegram: throttled, same message already stood"; return 0 ;;
    5|70) log "telegram: CANNOT REPORT — no token or chat id. The message is lost."; return "$rc" ;;
    *) log "telegram: notify failed with exit $rc"; return "$rc" ;;
  esac
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

# ------------------------------------------------------- checkout protection
# The deployed checkouts belong to the deploy (WORKFLOW 11.7, 11.10). On
# 2026-10-10 a session copied two files into ~/moltstack with scp, patched them
# there and ran git pull; the deploy of #718 was refused because of it. Rules
# had said not to since 03.10. These two steps make the hand fail instead.
#
# 1. Tracked files are read-only after every checkout move. A scp, a patch
#    script or an editor writing a tracked file gets "Permission denied".
#    moltstack still owns the files and can chmod them back: this stops the
#    accident, not the intent (stage 2, a separate owner, was decided against
#    on 11.10.2026, WORKFLOW 11.10). Directories stay writable, so runtime
#    files and __pycache__ keep working; the runtime writers measured on
#    11.10.2026 are left out entirely. Symlinks and submodules are skipped:
#    chmod follows a link to wherever it points. git itself replaces a file by
#    unlink and create, so the next checkout is not hindered.
PROTECT_EXCLUDE_RE='^(logs/|data/|workers/content_scout/|state/|agents/workspace/|moltbook/state\.json$)|(^|/)__pycache__/'

protect_checkout() {  # protect_checkout <dir> [paths the service writes itself]
  local dir=$1 own=${2:-} rec mode path n=0
  while IFS= read -r -d '' rec; do
    mode=${rec%% *}; path=${rec#*$'\t'}
    case $mode in 100644|100755) ;; *) continue ;; esac
    [[ $path =~ $PROTECT_EXCLUDE_RE ]] && continue
    # The second argument is the one service that writes tracked files of its
    # own checkout at runtime (moltguard, MOLTGUARD_OWN_WRITES). Read-only
    # there would turn the service's own work into a permission error.
    if [ -n "$own" ] && [[ $path =~ $own ]]; then continue; fi
    printf '%s\0' "$path"
  done < <(git -C "$dir" ls-files -s -z) > "$dir/.git/protect.list"
  n=$(tr -cd '\0' < "$dir/.git/protect.list" | wc -c)
  if ! (cd "$dir" && xargs -0 -r chmod a-w -- < .git/protect.list); then
    log "protect: chmod failed in $dir — tracked files may be writable"
    return 0
  fi
  log "protect: $n tracked file(s) read-only in $dir"
  return 0
}

# 2. A reference-transaction hook in the checkout's .git: a change to
#    refs/heads/main or refs/remotes/origin/main that
#    does not come from deploy.sh (GIT_REFLOG_ACTION, set below) is logged to
#    ~/selftest/ref-guard.jsonl, and from REF_GUARD_ENFORCE_FROM on refused.
#    One week log-only (Lars, BJ1, 11.10.2026). The hook comes from this
#    commit, installed on every run, so a hand-edited hook is undone by the
#    next deploy. Worktrees share the hooks and the refs; their own branches
#    pass, a fetch from one of them does not (WORKFLOW 11.7).
install_ref_guard() {  # install_ref_guard <dir>
  local dir=$1 src="$API_DIR/ops/deploy/ref-guard-hook" dst
  dst="$dir/.git/hooks/reference-transaction"
  [ -f "$src" ] || { log "ref guard: $src not in this commit, not installed"; return 0; }
  if ! cmp -s "$src" "$dst" 2>/dev/null; then
    if install -m 755 "$src" "$dst"; then log "ref guard: installed in $dir"
    else log "ref guard: install into $dir failed"; fi
  fi
  return 0
}

# .checkout_owner names a session, not a machine (Lars, BJ2, 11.10.2026). It
# used to say "mac-console@Mac", and every session on that Mac read itself as
# the owner — the gap the 10.10. hand went through. The owner is the deploy,
# so the deploy writes it, with the session id of this run. No interactive
# session shares that id, so "am I the owner" is no for every one of them,
# which is the intent. The fields that state the rule are kept.
OWNER_FILE=/home/moltstack/.checkout_owner

write_checkout_owner() {  # write_checkout_owner <sha>
  python3 - "$OWNER_FILE" "$1" "$$" <<'PY' \
    || log "checkout owner: could not write $OWNER_FILE"
import datetime, json, os, socket, sys, getpass
path, sha, pid = sys.argv[1:4]
sid = os.getsid(int(pid))
try:
    doc = json.load(open(path))
except (OSError, ValueError):
    doc = {}
doc.update({
    "owner": "deploy.sh",
    "session": f"deploy.sh sid={sid} pid={pid}",
    "sid": sid,
    "pid": int(pid),
    "host": socket.gethostname(),
    "user": getpass.getuser(),
    "sha": sha,
    "declared_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
})
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(doc, fh, indent=1, ensure_ascii=False)
os.replace(tmp, path)
PY
  return 0
}

# ------------------------------------------------------------- self-check
# Since 2026-10-08 this file lives in moltrust-api as ops/deploy/deploy.sh. The
# copy that runs is /home/moltstack/bin/deploy.sh, and it has to be the copy at
# the commit moltrust-api is deployed at. A copy that differs was changed by
# hand on the server, which is how deploy.sh changed at 07:19:24 that morning
# with nobody able to say by whom afterwards. A run that finds a mismatch does
# nothing, exits 1 and says so on ALERTS.
DEPLOY_SELF=/home/moltstack/bin/deploy.sh

alert() {  # telegram to the ALERTS channel
  # The channel is notify's business now, including the fall back to the
  # undivided chat when TELEGRAM_CHAT_ID_ALERTS does not exist yet. Picking the
  # chat id here and handing it over in the environment duplicated that
  # decision in a second place.
  telegram "$1" alerts
  return $?
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
# in publications/ and papers/), and JSON-LD contexts (contexts/ or ns/<name>/vN, no
# extension, served as application/ld+json by nginx; since 2026-10-08), and round
# results (rounds/rN/*.json, since 2026-10-11: a green deploy left r5 at version
# 1 in the web root while the checkout held version 2), and the one script the
# web root serves, registry-proof.py (since 2026-10-14, ops W1: it went in by
# hand on 27.09. from blog-deploy-stage, no deploy could ship a .py, and after
# D1 it reported three problems against the live file it is there to check). A new
# folder of pages is a change to this list, by PR.
WEB_ALLOW='^[^/]+\.html$
^(blog|publications|bindings/trust-registry|enterprise|partners|pricing|verify|admin|reseller|zh|ads)/[^/]+\.html$
^(img|assets|\.well-known)/
^(robots\.txt|llms\.txt|sitemap\.xml|agents\.txt|api-llms\.txt|api-robots\.txt|favicon\.ico|favicon\.svg|favicon-16x16\.png|favicon-32x32\.png|apple-touch-icon\.png|copy-code\.js|og-image(-v[0-9]+)?\.png)$
^blog/(feed\.xml|og-blog\.png)$
^[^/]+\.pdf$
^(publications|papers)/[^/]+\.pdf$
^(contexts|ns)/[a-z0-9-]+/v[0-9]+$
^rounds/r[0-9]+/[^/]+\.json$
^registry-proof\.py$'

# /.well-known/x402.json carries the receiving address, and the address lives
# in one place: BASE_ADDR in the secrets file (ops backlog S2, 2026-10-12).
# moltrust-web holds the manifest as .well-known/x402.json.tpl with the
# placeholder {{BASE_ADDR}}; the served file is rendered here on every web
# deploy and installed when it differs from the web root. Moving the wallet is
# a change to the secrets file and a web deploy of the same commit - no commit.
#
# Only BASE_ADDR is read from the secrets file, by name, never the whole file:
# this runs as the forced command and has no business holding the other keys.
SECRETS_FILE=${SECRETS_FILE:-/home/moltstack/.moltrust_secrets}

base_addr() {  # prints BASE_ADDR from $SECRETS_FILE, fails when missing or malformed
  local v
  v=$(grep -E '^(export[[:space:]]+)?BASE_ADDR=' "$SECRETS_FILE" 2>/dev/null | tail -n 1)
  v=${v#*=}; v=${v//\"/}; v=${v//\'/}; v=${v//[[:space:]]/}
  [[ $v =~ ^0x[0-9a-fA-F]{40}$ ]] || return 1
  printf '%s' "$v"
}

# render_x402 <web checkout> -> writes .well-known/x402.json from the template.
# 0 rendered, 2 no template in this commit (older commit), 1 failure.
render_x402() {
  local dir=$1 tpl out addr
  tpl="$dir/.well-known/x402.json.tpl"; out="$dir/.well-known/x402.json"
  [ -f "$tpl" ] || return 2
  addr=$(base_addr) || { log "x402: BASE_ADDR missing or malformed in $SECRETS_FILE"; return 1; }
  grep -q '{{BASE_ADDR}}' "$tpl" || { log "x402: template has no {{BASE_ADDR}} placeholder"; return 1; }
  sed "s/{{BASE_ADDR}}/$addr/g" "$tpl" > "$out.tmp" || { rm -f "$out.tmp"; return 1; }
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$out.tmp" 2>/dev/null \
    || { rm -f "$out.tmp"; log "x402: rendered manifest is not valid JSON"; return 1; }
  mv -f "$out.tmp" "$out"
}

# Every file deploy.sh puts into the web root leaves a line here: time, path,
# sha256 of the installed bytes, commit. scripts/webroot_write_watch.py reads
# it every five minutes and reports any write into the web root that has no
# matching line — a manual `sudo moltstack-webinstall`, a stray `install`, an
# editor. Until 14.10.2026 (ops W1) the only trace of such a write was the sudo
# journal, and registry-proof.py had sat there unbooked since 27.09.
#
# A failed ledger write is logged and does not stop the deploy: the watch then
# reports the file as written outside deploy.sh, which is loud, not silent.
WEBROOT_LEDGER=${WEBROOT_LEDGER:-/home/moltstack/selftest/webroot-writes.jsonl}
WEB_ROOT=${WEB_ROOT:-/var/www/html}

ledger_write() {  # ledger_write <relpath> - after a successful install
  local rel=$1 sum
  sum=$(sha256sum "$WEB_ROOT/$rel" 2>/dev/null | cut -c1-64)
  [ -n "$sum" ] || { log "ledger: cannot hash $rel in the web root"; return 0; }
  printf '{"ts":"%s","path":"%s","sha256":"%s","sha":"%s","writer":"deploy.sh"}\n' \
    "$(ts)" "$rel" "$sum" "${SHA:-?}" >> "$WEBROOT_LEDGER" \
    || { log "ledger: write failed for $rel"; return 0; }
  chmod 600 "$WEBROOT_LEDGER" 2>/dev/null || true
  return 0
}

webinstall() {  # webinstall <relpath> - the one way deploy.sh writes the web root
  sudo -n "$WRAPPER" "$1" >/dev/null || return 1
  ledger_write "$1"
}

# Served pages under docs/. docs/ is otherwise the repository's own
# documentation and stays fenced off above, so a served page there is named
# here singly, not by a pattern. docs/caep.html is in sitemap.xml and was
# fenced off with the rest of docs/: the web root kept its copy of 09.05.2026,
# and the change of 18.06.2026 that added the code-theme stylesheet to it never
# left the checkout (ops W2, Befund BR1, 14.10.2026).
WEB_DOCS_PAGES='docs/caep.html'

web_files() {
  { git diff --name-only --diff-filter=ACMRT "$1" "$2" -- . \
    | grep -E '\.(html|xml|txt|json|css|js|png|jpe?g|svg|webp|ico|pdf|woff2?)$|^(contexts|ns)/[a-z0-9-]+/v[0-9]+$|^registry-proof\.py$' \
    | grep -v -E '^(docs|scripts|checks|partials|test|tests|\.github)/' \
    | grep -v -E '(^|/)CLAUDE\.md$' \
    | grep -v -E '^blog/index\.html$' \
    | grep -E -f <(printf '%s\n' "$WEB_ALLOW")
    # shellcheck disable=SC2086 # a list of fixed paths, split on purpose
    git diff --name-only --diff-filter=ACMRT "$1" "$2" -- $WEB_DOCS_PAGES
  } || true
}

# web_sync_files: the docs pages whose web-root bytes are not the checkout's.
# web_files only sees what the commit range touched, so a page that was missed
# once stays behind until somebody edits it; BR1 sat behind for four months.
# These few are compared on every web deploy and shipped when they differ, the
# way x402.json is. Run from the web checkout.
web_sync_files() {
  local f
  for f in $WEB_DOCS_PAGES; do
    [ -f "$f" ] || continue
    cmp -s -- "$f" "$WEB_ROOT/$f" || printf '%s\n' "$f"
  done
}

# --------------------------------------------------------------- moltguard
# moltguard is a node service in its own checkout: built on the server (tsc
# into dist/, which is not in git) and restarted as the unit `moltguard`. Until
# 2026-10-16 it had no deploy path at all — the 402 invitation went live by
# hand, `npm run build` over SSH and a restart through the NOPASSWD rule, which
# is the one thing a deploy exists to make unnecessary (ops D3).
#
# Which paths reach the running service, positively listed: the sources it is
# compiled from, the landing page routes/health.ts reads at runtime, the
# dependency files and the compiler config. A commit range that touches none of
# them moves the checkout and does nothing else — no build, no restart. Same
# shape as WEB_ALLOW: a new folder the service reads is a change to this list,
# by PR.
MOLTGUARD_RUN='^src/
^public/
^package\.json$
^package-lock\.json$
^tsconfig\.json$'

# npm ci, and only when the dependency files moved: it drops node_modules and
# installs the lockfile exactly, dev dependencies included — tsc is one of
# them, so the build needs them there.
MOLTGUARD_DEPS='^package\.json$
^package-lock\.json$'

# What the service writes inside its own checkout. The trust-proof harness
# overwrites data/trust-proofs/latest.json on every run (src/routes/harness.ts),
# and the hourly jwks refresh in the crontab drops jwks.json beside it. That is
# the service doing its job, not a hand in the checkout: the deploy gate leaves
# exactly these paths out, the read-only protection does not touch them, and
# scripts/checkout_dirty.py carries the same list for the hourly check.
# Everything else in that checkout belongs to the deploy, as in the other two.
MOLTGUARD_OWN_WRITES='^data/trust-proofs/|^jwks\.json(\.new)?$'

moltguard_files() {  # moltguard_files <prev> <sha> — run-relevant paths in the range
  git diff --name-only --diff-filter=ACMRT "$1" "$2" -- . \
    | grep -E -f <(printf '%s\n' "$MOLTGUARD_RUN") || true
}

moltguard_deps_changed() {  # moltguard_deps_changed <prev> <sha>
  git diff --name-only --diff-filter=ACMRT "$1" "$2" -- . \
    | grep -q -E -f <(printf '%s\n' "$MOLTGUARD_DEPS")
}

# The port the health probe asks for, read from the service's .env the way
# BASE_ADDR is read from the secrets file: one line, by name, never the whole
# file. 3000 is the default in src/config.ts, so an .env without a PORT line is
# not a failure; a PORT that is not a number is.
moltguard_port() {  # moltguard_port <env file>
  local v
  v=$(grep -E '^(export[[:space:]]+)?PORT=' "$1" 2>/dev/null | tail -n 1)
  v=${v#*=}; v=${v//\"/}; v=${v//\'/}; v=${v//[[:space:]]/}
  [ -n "$v" ] || { printf '3000'; return 0; }
  [[ $v =~ ^[0-9]{1,5}$ ]] || return 1
  printf '%s' "$v"
}

# A deploy refuses to run on a checkout that carries uncommitted changes
# (2026-10-10). A path the service itself writes is not such a change. Without
# the second argument this is the check as it always was; moltrust-api and
# moltrust-web pass nothing.
dirty_tracked() {  # dirty_tracked <dir> [paths the service writes itself]
  local dir=$1 own=${2:-} line path
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    path=${line:3}
    path=${path##* -> }   # a rename names both sides; the new one is judged
    if [ -n "$own" ] && [[ $path =~ $own ]]; then continue; fi
    printf '%s\n' "$line"
  done < <(git -C "$dir" status --porcelain --untracked-files=no)
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
  *) echo "usage: deploy.sh <moltrust-api|moltrust-web|moltguard> <40-hex-sha|diagnose|supervise|superheal> [origin]" >&2; exit 2 ;;
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
case $REPO in moltrust-api|moltrust-web|moltguard) : ;; *) echo "unknown repo: $REPO" >&2; exit 2 ;; esac

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
  moltrust-api) DIR=$API_DIR;       OWN_WRITES= ;;
  moltrust-web) DIR=$WEB_DIR;       OWN_WRITES= ;;
  moltguard)    DIR=$MOLTGUARD_DIR; OWN_WRITES=$MOLTGUARD_OWN_WRITES ;;
esac
cd "$DIR" || die "no checkout at $DIR"
# Every git step of this run is tagged in the reflog (2026-10-10). The
# deployed checkout belongs to the deploy (WORKFLOW 11.7); a fetch, checkout
# or reset there without this tag is a hand in the checkout, and the 08:00
# report names it (scripts/checkout_writes.py).
export GIT_REFLOG_ACTION="deploy.sh $REPO $SHA"
dirty=$(dirty_tracked "$DIR" "$OWN_WRITES")
[ -z "$dirty" ] || die "tracked files are modified in $DIR, refusing to deploy:
$dirty"
# A fetch by anyone else in this checkout while ours runs makes git refuse to
# move the ref ("cannot lock ref ... is at X but expected Y"). On 2026-10-09
# that cost the deploy of #705. One retry after a short pause; the race stays
# possible, a single collision no longer costs a deploy.
fetch_main() {
  local err
  if err=$(git fetch -q origin main 2>&1); then return 0; fi
  if printf '%s' "$err" | grep -q "cannot lock ref"; then
    log "git fetch: another fetch moved the ref meanwhile (cannot lock ref) - retrying once in 5 s"
    sleep 5
    if err=$(git fetch -q origin main 2>&1); then return 0; fi
  fi
  printf '%s\n' "$err" | while IFS= read -r l; do [ -n "$l" ] && log "git: $l"; done
  return 1
}
fetch_main || die "git fetch failed in $DIR"
git cat-file -e "$SHA^{commit}" 2>/dev/null || die "$SHA is not a commit in $DIR"
git merge-base --is-ancestor "$SHA" origin/main \
  || die "$SHA is not on origin/main — only merged commits deploy"

PREV=$(prev_recorded)
[ -n "$PREV" ] || PREV=$(git rev-parse HEAD)
log "previous=$PREV target=$SHA"

checkout() {  # checkout <sha>, fast-forward only on the way up, hard on the way back
  local want=$1 rc
  git checkout -q main || return 1
  if git merge-base --is-ancestor "$(git rev-parse HEAD)" "$want"; then
    git merge -q --ff-only "$want"; rc=$?
  else
    git reset -q --hard "$want"; rc=$?   # rollback direction
  fi
  # After every move, the rollback included: the files git just wrote are
  # writable again until this runs.
  protect_checkout "$(pwd)" "${OWN_WRITES:-}"
  install_ref_guard "$(pwd)"
  return $rc
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

# What is installed, against what requirements.lock pins, before the service
# restarts on it. Every divergence that existed on 2026-10-17 is written down
# in ops/lockdrift-bekannt.txt with its version pair; anything else stops the
# deploy here and the caller rolls back.
#
# The gap this closes: requirements.txt declares ranges, the lock pins exact
# versions, and nothing ever compared the two. a2a-sdk sat on 1.1.0 while the
# lock said 1.1.2, through every deploy, because pip leaves a version that
# satisfies the range (ops DEP-1).
#
# Skipped when the script is not in the commit being deployed, the way
# verify_versions skips: a commit from before this check must still deploy.
lock_check() {
  [ -f "$API_DIR/scripts/lock_drift.py" ] || { log "lock drift: script not in this commit, skipped"; return 0; }
  local out code
  out=$(cd "$API_DIR" && ./venv/bin/python scripts/lock_drift.py --quiet 2>&1)
  code=$?
  printf '%s\n' "$out" | while IFS= read -r l; do [ -n "$l" ] && log "lock drift: ${l#lock drift: }"; done
  case $code in
    0) return 0 ;;
    1) log "FAIL lock drift: an installed version differs from requirements.lock and is not in ops/lockdrift-bekannt.txt"
       alert "MolTrust deploy stopped — installed versions differ from requirements.lock
$(printf '%s' "$out" | grep -c 'not recorded') package(s) not in ops/lockdrift-bekannt.txt
moltrust-api $SHA is not deployed; the service keeps running $PREV"
       return 1 ;;
    *) # Cannot read an input. Not knowing is not the same as being fine, and
       # this check sits in front of a restart: it refuses rather than passes.
       log "FAIL lock drift: cannot compare (exit $code)"
       alert "MolTrust deploy stopped — the lock comparison could not run (exit $code)
moltrust-api $SHA is not deployed"
       return 1 ;;
  esac
}

deploy_api() {
  checkout "$SHA" || return 1
  if ! git diff --quiet "$PREV" "$SHA" -- requirements.txt 2>/dev/null; then
    log "requirements.txt changed, installing"
    ./venv/bin/pip install -q -r requirements.txt || return 1
  fi
  lock_check || return 1
  sudo -n /usr/bin/systemctl restart moltstack.service || return 1
  sleep 3
  local code; code=$(probe_api) || { log "health probe: $code"; return 1; }
  log "health probe: $code"
  restart_mcp_if_changed
  write_server_card
}

# /.well-known/mcp/server-card.json names every tool the hosted MCP endpoint
# serves, read from its live tools/list after the restart above (ops
# SMITHERY-EXIT, 20.10.2026). Runs on every API deploy, restart or not: it costs
# one local request and keeps the card from going stale. A failure keeps the
# previous card and is reported; it never rolls the API back.
write_server_card() {
  [ -f "$API_DIR/scripts/gen_mcp_server_card.py" ] || { log "server card: script not in this commit, skipped"; return 0; }
  local out
  if out=$(cd "$API_DIR" && ./venv/bin/python scripts/gen_mcp_server_card.py 2>&1); then
    log "$out"
  else
    log "FAIL $out"
    alert "MolTrust deploy: MCP server card not rewritten after moltrust-api $SHA
$out"
  fi
  return 0
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
  local xr=0
  render_x402 "$WEB_DIR" || xr=$?
  case $xr in
    0) if cmp -s "$WEB_DIR/.well-known/x402.json" /var/www/html/.well-known/x402.json; then
         log "x402: manifest rendered, web root already current"
       else
         webinstall .well-known/x402.json || { log "install failed: .well-known/x402.json"; return 1; }
         log "x402: manifest rendered from BASE_ADDR and installed"
       fi ;;
    2) log "x402: no template in this commit, manifest left as shipped" ;;
    *) log "x402: render failed - nothing installed"; return 1 ;;
  esac
  local files; files=$( { web_files "$PREV" "$SHA"; web_sync_files; } | sort -u)
  if [ -z "$files" ]; then
    log "nothing served changed between $PREV and $SHA"
    return 0
  fi
  log "shipping $(echo "$files" | wc -l | tr -d ' ') file(s):"
  echo "$files" | while read -r f; do log "  $f"; done
  echo "$files" | while read -r f; do
    webinstall "$f" || { log "install failed: $f"; exit 1; }
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

probe_moltguard() {  # probe_moltguard <port>
  local code
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    code=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$1/health" 2>/dev/null || echo 000)
    [ "$code" = 200 ] && { echo "$code"; return 0; }
    sleep 2
  done
  echo "$code"; return 1
}

# The build runs on the server, from the checkout: the same two commands the
# hand ran on 16.10., in an order a deploy can repeat. dist/ is not in git —
# what is deployed is the source, and the compiled copy is a product of this
# step. npm output is kept and logged only when a command fails; a tsc error
# that nobody can read afterwards is a deploy nobody can fix.
moltguard_build() {
  local out
  command -v npm >/dev/null || { log "FAIL npm is not on the deploy PATH"; return 1; }
  if moltguard_deps_changed "$PREV" "$SHA"; then
    log "package.json or package-lock.json changed, installing"
    if ! out=$(cd "$MOLTGUARD_DIR" && npm ci --no-audit --no-fund 2>&1); then
      printf '%s\n' "$out" | tail -n 10 | while IFS= read -r l; do [ -n "$l" ] && log "npm: $l"; done
      log "FAIL npm ci"; return 1
    fi
  fi
  if ! out=$(cd "$MOLTGUARD_DIR" && npm run build 2>&1); then
    printf '%s\n' "$out" | tail -n 10 | while IFS= read -r l; do [ -n "$l" ] && log "tsc: $l"; done
    log "FAIL npm run build"; return 1
  fi
  return 0
}

deploy_moltguard() {
  local port files code
  port=$(moltguard_port "$MOLTGUARD_DIR/.env") \
    || { log "moltguard: PORT in the service .env is not a number — nothing deployed"; return 1; }
  files=$(moltguard_files "$PREV" "$SHA")
  # A migration is applied by hand and by judgement; the deploy says so rather
  # than running it or passing over it in silence.
  if git diff --name-only --diff-filter=ACMRT "$PREV" "$SHA" -- migrations/ 2>/dev/null | grep -q .; then
    log "migrations/ changed in this range — a migration is applied by hand, the deploy does not run it"
  fi
  checkout "$SHA" || return 1
  if [ -z "$files" ]; then
    log "nothing the service runs changed between $PREV and $SHA — no build, no restart"
    code=$(probe_moltguard "$port") || { log "health probe: $code"; return 1; }
    log "health probe: $code"
    return 0
  fi
  log "building $(echo "$files" | wc -l | tr -d ' ') changed path(s):"
  echo "$files" | while read -r f; do log "  $f"; done
  moltguard_build || return 1
  # /usr/bin/systemctl, as everywhere else in this file: the hand that
  # restarted the service on 16.10. ran `sudo -n systemctl restart moltguard`,
  # and PATH resolves that to /usr/bin. A sudoers rule matches the path it was
  # written with, so the proven one is the one to use.
  sudo -n /usr/bin/systemctl restart "$MOLTGUARD_UNIT" \
    || { log "FAIL systemctl restart $MOLTGUARD_UNIT"; return 1; }
  sleep 3
  code=$(probe_moltguard "$port") || { log "health probe: $code"; return 1; }
  log "health probe: $code"
}

rollback() {
  log "ROLLBACK to $PREV"
  case $REPO in
    moltrust-api)
      checkout "$PREV" && sudo -n /usr/bin/systemctl restart moltstack.service && sleep 3
      local code; code=$(probe_api) || true
      log "post-rollback health: $code"
      ;;
    moltguard)
      local port code
      checkout "$PREV" || { log "rollback checkout failed"; return 1; }
      moltguard_build || log "rollback build failed"
      sudo -n /usr/bin/systemctl restart "$MOLTGUARD_UNIT" || log "rollback restart failed"
      sleep 3
      port=$(moltguard_port "$MOLTGUARD_DIR/.env") || port=3000
      code=$(probe_moltguard "$port") || true
      log "post-rollback health: $code"
      ;;
    moltrust-web)
      local files; files=$(web_files "$PREV" "$SHA")
      checkout "$PREV" || { log "rollback checkout failed"; return 1; }
      echo "$files" | while read -r f; do
        [ -n "$f" ] || continue
        if [ -f "$WEB_DIR/$f" ]; then webinstall "$f" || log "rollback install failed: $f"
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
  moltguard)    deploy_moltguard; ok=$? ;;
esac

if [ "${ok:-1}" -eq 0 ]; then
  record "$SHA" ok
  log "OK $REPO $SHA"
  [ "$REPO" = moltrust-api ] && install_self_if_changed
  [ "$REPO" = moltrust-api ] && write_checkout_owner "$SHA"
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
