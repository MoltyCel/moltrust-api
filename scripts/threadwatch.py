#!/usr/bin/env python3
"""ThreadWatch — Stop-Gap Inbound Monitor for MolTrust.

Runs 2x/day via cron (08:00 + 18:00 UTC). Generates a consolidated Telegram
report on:
  1. External GitHub threads waiting for MolTrust response (urgent/active/stale)
  2. MolTrust agent health flags (suspensions, stuck queues, stale Moltbook posts)

Acknowledgments via Telegram bot commands (/ack /ack_list /ack_remove)
are fetched + applied at the start of each run, before report generation.

CLI flags:
  --dry-run                Skip Telegram send, write report to stdout/log
  --with-test-fixture      Inject a synthetic urgent thread (for tests)
  --process-acks-only      Only fetch + apply Telegram /ack commands, no report
"""

import argparse
import json
import logging
import os
import re
import sys
import html as html_mod
import time
from datetime import datetime, timezone, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import requests
import yaml

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE = Path.home() / "moltstack"
SCRIPT_DIR = BASE / "scripts"
CONFIG_FILE = SCRIPT_DIR / "threadwatch_config.yaml"
STATE_FILE = BASE / "state" / "threadwatch.json"
LOG_FILE = BASE / "logs" / "threadwatch.log"
SECRETS_FILE = Path.home() / ".moltrust_secrets"

# ─── CLI ──────────────────────────────────────────────────────────────────────

ap = argparse.ArgumentParser()
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--with-test-fixture", action="store_true")
ap.add_argument("--process-acks-only", action="store_true")
ARGS = ap.parse_args()

# ─── Logging ──────────────────────────────────────────────────────────────────

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
file_handler = TimedRotatingFileHandler(
    LOG_FILE, when="D", interval=1, backupCount=14, encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter(
    "[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
))
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter(
    "[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
))
log = logging.getLogger("threadwatch")
log.setLevel(logging.INFO)
log.addHandler(file_handler)
log.addHandler(stream_handler)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_secrets():
    secrets = {}
    if not SECRETS_FILE.exists():
        return secrets
    for line in SECRETS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        secrets[k.strip()] = v.strip().strip('"').strip("'")
    return secrets


def load_config():
    return yaml.safe_load(CONFIG_FILE.read_text())


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            log.warning("state file unreadable, starting fresh")
    return {
        "acknowledged": {},
        "pinned": {},
        "merged_announced": {},
        "last_run": None,
        "telegram_offset": 0,
    }


def save_state(s):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, indent=2, default=str))
    tmp.replace(STATE_FILE)


def parse_ts(s):
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def fmt_age(delta_seconds):
    h = delta_seconds / 3600
    if h < 24:
        return f"{int(h)}h"
    return f"{int(h / 24)}d"


# ─── GitHub API ───────────────────────────────────────────────────────────────

class GH:
    def __init__(self, token):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "MolTrust-ThreadWatch/1.0",
        })

    def get(self, url, params=None):
        r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json(), r.headers

    def get_paginated(self, url, params=None, max_pages=10):
        items = []
        for _ in range(max_pages):
            try:
                data, headers = self.get(url, params=params)
            except requests.exceptions.HTTPError as e:
                log.warning(f"GH error on {url}: {e}")
                return items
            if not isinstance(data, list):
                break
            items.extend(data)
            link = headers.get("Link", "")
            next_url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    m = re.search(r"<([^>]+)>", part)
                    if m:
                        next_url = m.group(1)
            if not next_url:
                break
            url = next_url
            params = None  # next-url already has params
        return items

    def rate_limit(self):
        try:
            data, _ = self.get("https://api.github.com/rate_limit")
            return data.get("resources", {}).get("core", {})
        except Exception as e:
            log.warning(f"rate_limit fetch failed: {e}")
            return {}


# ─── Telegram ─────────────────────────────────────────────────────────────────

def telegram_send(secrets, text, dry=False):
    if dry:
        log.info("[DRY-RUN] Would send Telegram (length=%d chars)", len(text))
        print("─── TELEGRAM (dry-run) ───")
        print(text)
        print("─── /TELEGRAM ───")
        return True
    token = secrets.get("TELEGRAM_BOT_TOKEN", "")
    chat = secrets.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        log.error("Telegram credentials missing")
        return False
    # Telegram has a 4096 char limit per message — split at line boundaries
    # so HTML tags stay intact across chunks
    MAX_LEN = 3900
    chunks = []
    current = ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > MAX_LEN:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    for chunk in chunks:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
            timeout=15,
        )
        if r.status_code != 200:
            log.error(f"Telegram send failed {r.status_code}: {r.text[:200]}")
            return False
    return True


def telegram_get_updates(secrets, offset):
    token = secrets.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return []
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": offset, "timeout": 0, "limit": 100},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        return r.json().get("result", [])
    except Exception as e:
        log.warning(f"telegram_get_updates error: {e}")
        return []


def process_ack_commands(secrets, state):
    """Fetch new Telegram messages, process /ack /ack_list /ack_remove."""
    chat_id = str(secrets.get("TELEGRAM_CHAT_ID", ""))
    offset = state.get("telegram_offset", 0)
    updates = telegram_get_updates(secrets, offset)
    n = 0
    for u in updates:
        state["telegram_offset"] = u.get("update_id", offset) + 1
        msg = u.get("message", {}) or {}
        if str(msg.get("chat", {}).get("id", "")) != chat_id:
            continue
        text = (msg.get("text") or "").strip()
        if not text or not text.startswith("/"):
            continue

        if text.startswith("/ack_list"):
            acks = state.get("acknowledged", {})
            if not acks:
                telegram_send(secrets, "📋 No active acks.", dry=ARGS.dry_run)
            else:
                lines = ["📋 <b>Active acknowledgments:</b>", ""]
                for k, v in acks.items():
                    until = v.get("until", "?")
                    note = v.get("note", "")
                    lines.append(f"• <code>{k}</code> — until {until}")
                    if note and note != "via /ack":
                        lines.append(f"   <i>{html_mod.escape(note)}</i>")
                telegram_send(secrets, "\n".join(lines), dry=ARGS.dry_run)
            n += 1
        elif text.startswith("/ack_remove"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                telegram_send(secrets, "Usage: /ack_remove &lt;repo&gt;#&lt;num&gt;", dry=ARGS.dry_run)
                continue
            key = parts[1].strip()
            removed = state.get("acknowledged", {}).pop(key, None)
            if removed:
                telegram_send(secrets, f"✅ Removed ack for <code>{key}</code>", dry=ARGS.dry_run)
            else:
                telegram_send(secrets, f"⚠️ No ack found for <code>{key}</code>", dry=ARGS.dry_run)
            n += 1
        elif text.startswith("/ack"):
            parts = text.split(maxsplit=2)
            if len(parts) < 2:
                telegram_send(secrets, "Usage: /ack &lt;repo&gt;#&lt;num&gt; [days] [note]", dry=ARGS.dry_run)
                continue
            key = parts[1].strip()
            days = 7
            note = "via /ack"
            if len(parts) > 2:
                rest = parts[2].strip()
                # try first token as int (days)
                try:
                    bits = rest.split(maxsplit=1)
                    days = int(bits[0])
                    note = bits[1] if len(bits) > 1 else "via /ack"
                except ValueError:
                    note = rest
            until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
            state.setdefault("acknowledged", {})[key] = {
                "until": until,
                "acked_at": datetime.now(timezone.utc).isoformat(),
                "note": note,
            }
            telegram_send(secrets, f"✅ Acked <code>{key}</code> for {days}d (until {until[:10]})", dry=ARGS.dry_run)
            n += 1
        elif text.startswith("/pin_list"):
            pins = state.get("pinned", {})
            if not pins:
                telegram_send(secrets, "📌 No dynamic pins. (Config pins live in threadwatch_config.yaml → tracked_threads.)", dry=ARGS.dry_run)
            else:
                lines = ["📌 <b>Dynamic pins (state):</b>", ""]
                for k, v in pins.items():
                    note = v.get("note", "")
                    lines.append(f"• <code>{k}</code>")
                    if note and note != "via /pin":
                        lines.append(f"   <i>{html_mod.escape(note)}</i>")
                lines.append("")
                lines.append("<i>(+ config pins in threadwatch_config.yaml → tracked_threads)</i>")
                telegram_send(secrets, "\n".join(lines), dry=ARGS.dry_run)
            n += 1
        elif text.startswith("/unpin"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                telegram_send(secrets, "Usage: /unpin &lt;repo&gt;#&lt;num&gt;", dry=ARGS.dry_run)
                continue
            key = parts[1].strip()
            removed = state.get("pinned", {}).pop(key, None)
            if removed:
                telegram_send(secrets, f"✅ Unpinned <code>{key}</code>", dry=ARGS.dry_run)
            else:
                telegram_send(secrets, f"⚠️ No dynamic pin <code>{key}</code> (config pins: edit tracked_threads in yaml)", dry=ARGS.dry_run)
            n += 1
        elif text.startswith("/pin"):
            parts = text.split(maxsplit=2)
            if len(parts) < 2 or "#" not in parts[1]:
                telegram_send(secrets, "Usage: /pin &lt;repo&gt;#&lt;num&gt; [note]", dry=ARGS.dry_run)
                continue
            key = parts[1].strip()
            repo_part, num_part = key.rsplit("#", 1)
            try:
                num_int = int(num_part)
            except ValueError:
                telegram_send(secrets, f"⚠️ Bad ref <code>{key}</code> — expected repo#num", dry=ARGS.dry_run)
                continue
            note = parts[2].strip() if len(parts) > 2 else "via /pin"
            state.setdefault("pinned", {})[key] = {
                "repo": repo_part,
                "number": num_int,
                "note": note,
                "kind": "issue",
                "pinned_at": datetime.now(timezone.utc).isoformat(),
            }
            telegram_send(secrets, f"📌 Pinned <code>{key}</code> — always shown in roster", dry=ARGS.dry_run)
            n += 1
    if n:
        log.info(f"processed {n} Telegram command(s)")
    return state


def is_acknowledged(state, key, now):
    ack = state.get("acknowledged", {}).get(key)
    if not ack:
        return False
    until = parse_ts(ack.get("until"))
    if until and until > now:
        return True
    # expired — clean up
    state["acknowledged"].pop(key, None)
    return False


# ─── Mention detection ────────────────────────────────────────────────────────

def has_at_mention(text, identities):
    """Returns matched identity if @<identity> in text (case-insensitive)."""
    if not text:
        return None
    t = text.lower()
    for ident in identities:
        if f"@{ident.lower()}" in t:
            return ident
    return None


def has_keyword(text, keywords):
    if not text:
        return False
    t = text.lower()
    return any(k.lower() in t for k in keywords)


# ─── Repo crawl ───────────────────────────────────────────────────────────────

def crawl_repo(gh, repo, since_iso):
    """Returns list of (issue_dict, comments_list)."""
    log.info(f"crawl {repo}")
    url = f"https://api.github.com/repos/{repo}/issues"
    issues = gh.get_paginated(url, params={
        "state": "all",
        "since": since_iso,
        "per_page": 100,
        "sort": "updated",
        "direction": "desc",
    }, max_pages=3)
    if not issues:
        return []
    results = []
    for issue in issues:
        num = issue.get("number")
        if num is None:
            continue
        try:
            comments, _ = gh.get(
                f"https://api.github.com/repos/{repo}/issues/{num}/comments",
                params={"per_page": 100},
            )
            if not isinstance(comments, list):
                comments = []
        except Exception as e:
            log.warning(f"comments {repo}#{num}: {e}")
            comments = []
        results.append((issue, comments))
    return results


def crawl_discussion(gh, repo, number):
    """Fetch one GitHub Discussion + its comments as an issue-like tuple.

    Discussions live at a different REST endpoint than issues
    (/repos/{owner}/{repo}/discussions/{n}) and are NOT returned by the
    /repos/{owner}/{repo}/issues listing crawl_repo uses — so pinned
    discussions need an explicit fetch.

    The discussion dict is shape-compatible with the issue dict
    classify_threads consumes (number/title/body/user/created_at/
    updated_at/html_url), so downstream logic doesn't fork. Comments
    are paginated up to max_pages=5 (= up to 500) since high-traffic
    discussions easily exceed the 100/page single-call cap that
    crawl_repo lives with for normal issues.
    """
    log.info(f"crawl {repo}#discussion-{number}")
    try:
        disc, _ = gh.get(
            f"https://api.github.com/repos/{repo}/discussions/{number}"
        )
    except Exception as e:
        log.warning(f"discussion {repo}#{number}: {e}")
        return None
    if not isinstance(disc, dict) or disc.get("number") is None:
        log.warning(f"discussion {repo}#{number}: unexpected payload")
        return None
    comments = gh.get_paginated(
        f"https://api.github.com/repos/{repo}/discussions/{number}/comments",
        params={"per_page": 100},
        max_pages=5,
    )
    if not isinstance(comments, list):
        comments = []
    return (disc, comments)


# ─── Thread classification ────────────────────────────────────────────────────

def classify_threads(repo_results, config, now):
    identities = [i for i in config["moltrust_identities"]]
    identities_lower = set(i.lower() for i in identities)
    keywords = config.get("mention_keywords", [])
    thr = config["thresholds"]

    threads = []
    for repo, issues_with_comments in repo_results:
        for issue, comments in issues_with_comments:
            num = issue.get("number")
            if num is None:
                continue
            key = f"{repo}#{num}"
            body = issue.get("body", "") or ""
            issue_user = (issue.get("user", {}) or {}).get("login", "") or ""

            events = []
            if issue.get("created_at"):
                events.append({
                    "actor": issue_user,
                    "ts": issue.get("created_at"),
                    "body": body,
                    "type": "issue_body",
                })
            for c in comments:
                events.append({
                    "actor": (c.get("user", {}) or {}).get("login", "") or "",
                    "ts": c.get("created_at"),
                    "body": c.get("body", "") or "",
                    "type": "comment",
                })

            we_commented = any(e["actor"].lower() in identities_lower for e in events)
            mentions_us = any(has_keyword(e["body"], keywords) for e in events)
            if not (we_commented or mentions_us):
                continue

            external_events = [
                e for e in events
                if e["actor"].lower() not in identities_lower and e.get("ts")
            ]
            moltrust_events = [
                e for e in events
                if e["actor"].lower() in identities_lower and e.get("ts")
            ]
            if not external_events:
                continue

            external_events.sort(key=lambda e: e["ts"])
            moltrust_events.sort(key=lambda e: e["ts"])

            last_ext = external_events[-1]
            last_mt = moltrust_events[-1] if moltrust_events else None

            ext_ts = parse_ts(last_ext["ts"])
            mt_ts = parse_ts(last_mt["ts"]) if last_mt else None
            if ext_ts is None:
                continue

            if mt_ts is not None and mt_ts >= ext_ts:
                continue  # we already replied

            mention_match = has_at_mention(last_ext["body"], identities)
            delta_h = (now - ext_ts).total_seconds() / 3600

            if delta_h < thr["urgent_hours"] and mention_match and we_commented:
                urgency = "urgent"
            elif delta_h < thr["active_days"] * 24 and we_commented:
                urgency = "active"
            elif delta_h < thr["stale_days"] * 24 and (mentions_us or we_commented):
                urgency = "stale"
            else:
                continue

            threads.append({
                "key": key,
                "url": issue.get("html_url", f"https://github.com/{repo}/issues/{num}"),
                "title": (issue.get("title") or "").strip(),
                "urgency": urgency,
                "last_external_actor": last_ext["actor"],
                "last_external_ts": last_ext["ts"],
                "last_external_snippet": (last_ext["body"] or "").strip().split("\n")[0][:120],
                "last_moltrust_ts": last_mt["ts"] if last_mt else None,
                "delta_hours": delta_h,
                "mentioned_directly": mention_match,
                "we_commented": we_commented,
            })
    return threads


# ─── Pinned roster (always shown, no staleness cutoff) ────────────────────────

def fetch_pinned(gh, repo, number, kind="issue"):
    """Fetch one pinned thread (issue/PR or discussion) → (item, comments) | None.

    Pinned threads are fetched EVERY run regardless of activity, so a quiet
    tracked thread stays visible. Authenticated (GH class, Bearer PAT) — no
    unauth poll (§6.4). ~2 calls per pin (item + comments)."""
    if kind == "discussion":
        return crawl_discussion(gh, repo, number)
    try:
        item, _ = gh.get(f"https://api.github.com/repos/{repo}/issues/{number}")
    except Exception as e:
        log.warning(f"pinned fetch {repo}#{number}: {e}")
        return None
    if not isinstance(item, dict) or item.get("number") is None:
        log.warning(f"pinned {repo}#{number}: unexpected payload")
        return None
    try:
        comments, _ = gh.get(
            f"https://api.github.com/repos/{repo}/issues/{number}/comments",
            params={"per_page": 100},
        )
        if not isinstance(comments, list):
            comments = []
    except Exception as e:
        log.warning(f"pinned comments {repo}#{number}: {e}")
        comments = []
    return (item, comments)


def analyze_pinned(repo, number, item, comments, config, now, note=""):
    """Build a roster entry — ALWAYS produced, no staleness cutoff and no
    we-already-replied drop (unlike classify_threads).

    Reuses the module primitives (parse_ts, fmt_age, identities). The event
    flatten is mirrored from classify_threads ON PURPOSE so classify_threads /
    the bucket logic stay byte-untouched. CLOSED/MERGED are explicit states so
    'erledigt' is distinguishable from 'Ball verloren'."""
    key = f"{repo}#{number}"
    identities_lower = set(i.lower() for i in config["moltrust_identities"])

    title = (item.get("title") or "").strip()
    url = item.get("html_url", f"https://github.com/{repo}/issues/{number}")

    # "still seit Nd" from the last activity timestamp the fetch already returns
    # (GitHub bumps updated_at on new comments) — no second fetch source.
    last_activity = parse_ts(item.get("updated_at"))
    still = fmt_age((now - last_activity).total_seconds()) if last_activity else "?"

    events = []
    if item.get("created_at"):
        events.append({"actor": (item.get("user", {}) or {}).get("login", "") or "",
                       "ts": item.get("created_at")})
    for c in comments:
        events.append({"actor": (c.get("user", {}) or {}).get("login", "") or "",
                       "ts": c.get("created_at")})
    events = [e for e in events if e.get("ts")]
    events.sort(key=lambda e: e["ts"])
    last_actor = events[-1]["actor"] if events else "—"

    ext = [e for e in events if e["actor"].lower() not in identities_lower]
    ours = [e for e in events if e["actor"].lower() in identities_lower]
    last_ext = ext[-1] if ext else None
    last_ours = ours[-1] if ours else None

    pr = item.get("pull_request")
    raw_state = item.get("state", "open")
    done = False
    waiting = False
    if pr and pr.get("merged_at"):
        state_label = "🟣 MERGED"
        done = True
    elif raw_state == "closed":
        state_label = "⬛ CLOSED"
        done = True
    elif last_ext and (not last_ours or last_ours["ts"] < last_ext["ts"]):
        state_label = "⏳ waiting (they spoke last)"
        waiting = True
    elif last_ours and (not last_ext or last_ours["ts"] >= last_ext["ts"]):
        state_label = "✓ replied (ball with them)"
    else:
        state_label = "· quiet"

    merged = bool(pr and pr.get("merged_at"))
    return {
        "key": key, "url": url, "title": title,
        "state_label": state_label, "still": still,
        "last_actor": last_actor, "note": note, "done": done, "waiting": waiting,
        "is_pr": bool(pr), "merged": merged,
        "merged_at": pr.get("merged_at") if merged else None,
    }


def build_roster_entry(gh, spec, config, now):
    """Fetch + analyze one roster spec {repo, number, note, kind}."""
    repo, number = spec["repo"], spec["number"]
    fetched = fetch_pinned(gh, repo, number, spec.get("kind", "issue"))
    if not fetched:
        return {
            "key": f"{repo}#{number}",
            "url": f"https://github.com/{repo}/issues/{number}",
            "title": "", "state_label": "⚠️ fetch failed",
            "still": "?", "last_actor": "—",
            "note": spec.get("note", ""), "done": False, "waiting": False,
        }
    item, comments = fetched
    # Authoritative PR-merge check. The issues API carries pull_request.merged_at,
    # but for a CLOSED PR we confirm via the pulls API (canonical merged=true) so a
    # merged PR is distinguished from a closed-unmerged one. Only closed PRs incur
    # the extra call; open PRs cannot be merged, so merged_at stays null there.
    if item.get("pull_request") and item.get("state") == "closed":
        try:
            pr_obj, _ = gh.get(f"https://api.github.com/repos/{repo}/pulls/{number}")
            if isinstance(pr_obj, dict):
                item.setdefault("pull_request", {})["merged_at"] = (
                    pr_obj.get("merged_at") if pr_obj.get("merged") else None
                )
        except Exception as e:
            log.warning(f"pr-merge check {repo}#{number}: {e}")
    return analyze_pinned(repo, number, item, comments, config, now,
                          note=spec.get("note", ""))


# ─── Notifications fetch (light enrichment) ───────────────────────────────────

def fetch_notifications(gh, since_iso):
    """Returns list of notification dicts."""
    url = "https://api.github.com/notifications"
    return gh.get_paginated(url, params={
        "all": "true",
        "participating": "true",
        "since": since_iso,
        "per_page": 50,
    }, max_pages=5)


# ─── Agent health probes ──────────────────────────────────────────────────────


def probe_moltycel_queue(config):
    out = {"name": "MoltyCel queue", "flags": []}
    pending_dir = Path(config["agent_health"]["moltycel_pending_dir"])
    if not pending_dir.exists():
        out["pending_count"] = 0
        return out
    files = list(pending_dir.glob("*.json"))
    out["pending_count"] = len(files)
    if files:
        oldest = min(files, key=lambda p: p.stat().st_mtime)
        out["oldest_age_h"] = (time.time() - oldest.stat().st_mtime) / 3600
        out["oldest"] = oldest.name
        if len(files) >= 5 or out["oldest_age_h"] > 24:
            out["flags"].append(f"{len(files)} pending, oldest {int(out['oldest_age_h'])}h")
    return out


def probe_moltbook_logs(config, now):
    out = {"name": "moltbook agents", "agents": {}, "flags": []}
    stale_h = config["agent_health"]["agent_stale_hours"]
    log_files = config["agent_health"]["moltbook_logs"]
    success_patterns = [
        "POSTED to",
        "Posted!",
        "Posted to",
        "POST https://www.moltbook.com",
    ]
    for name, path in log_files.items():
        info = {"path": path}
        p = Path(path)
        if not p.exists():
            info["status"] = "log missing"
            out["flags"].append(f"{name}: log file missing")
            out["agents"][name] = info
            continue
        # tail-scan last N KB
        try:
            with open(p, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 200_000))
                tail = f.read().decode("utf-8", errors="replace")
        except Exception as e:
            info["status"] = f"read error: {e}"
            out["agents"][name] = info
            continue
        last_success_line = None
        for line in reversed(tail.splitlines()):
            if any(p in line for p in success_patterns):
                last_success_line = line
                break
        info["last_success_line"] = last_success_line
        # parse timestamp prefix [2026-04-24Txx:xx:xx]
        if last_success_line:
            m = re.match(r"\[?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", last_success_line)
            if m:
                t = parse_ts(m.group(1) + "+00:00")
                if t:
                    age_h = (now - t).total_seconds() / 3600
                    info["last_success_age_h"] = age_h
                    if age_h > stale_h:
                        out["flags"].append(f"{name}: no success in {int(age_h)}h")
        else:
            out["flags"].append(f"{name}: no success line in last 200KB of log")
        out["agents"][name] = info
    return out


def probe_endpoint_state(config):
    out = {"name": "endpoint probe", "flags": []}
    p = Path(config["agent_health"]["endpoint_probe_state"])
    if not p.exists():
        return out
    try:
        data = json.loads(p.read_text())
    except Exception:
        return out
    down = []
    for path, st in data.items():
        if st.get("last_status") != "up" or st.get("alerted_down"):
            down.append(path)
    if down:
        out["flags"].append(f"down: {', '.join(down)}")
    out["data"] = data
    return out


# ─── Test fixture ─────────────────────────────────────────────────────────────

def synthetic_test_thread(now):
    return {
        "key": "TEST_FIXTURE/synthetic#999",
        "url": "https://github.com/TEST_FIXTURE/synthetic/issues/999",
        "title": "[TEST FIXTURE] Synthetic urgent thread for ThreadWatch validation",
        "urgency": "urgent",
        "last_external_actor": "test_actor",
        "last_external_ts": (now - timedelta(hours=4)).isoformat(),
        "last_external_snippet": "@MoltyCel can you confirm the integration scope?",
        "last_moltrust_ts": None,
        "delta_hours": 4.0,
        "mentioned_directly": "MoltyCel",
        "we_commented": True,
    }


# ─── Report formatting ────────────────────────────────────────────────────────

def fmt_report(threads_by_urgency, agent_probes, run_ts, config, suppressed_count=0, roster=None, merge_alerts=None):
    lines = []
    lines.append("🔭 <b>ThreadWatch</b> — " + run_ts.strftime("%Y-%m-%d %H:%M UTC"))
    lines.append("")

    # PR-merge transition alerts — fire once, pinned to the very top. A merged
    # pinned PR usually unblocks a deferred own-task, so it must not be buried in
    # the roster's persistent 🟣 MERGED label.
    if merge_alerts:
        lines.append("🔀 <b>PINNED PR MERGED</b> — deferred task now live")
        lines.append("")
        for r in merge_alerts:
            note = (r.get("note") or "").strip()
            note_html = f" — <i>{html_mod.escape(note[:140])}</i>" if note else ""
            lines.append(f"🔀 <b>{html_mod.escape(r['key'])} MERGED</b>{note_html}")
            lines.append(f"  → {r['url']}")
            lines.append("")

    total_urgent = len(threads_by_urgency.get("urgent", []))
    total_active = len(threads_by_urgency.get("active", []))
    total_stale = len(threads_by_urgency.get("stale", []))
    agent_flag_count = sum(len(p.get("flags", [])) for p in agent_probes)

    pinned_n = len(roster or [])
    lines.append(
        f"📊 {total_urgent} urgent · {total_active} active · "
        f"{total_stale} stale · ⚠️ {agent_flag_count} agent flags"
        + (f" · 📌 {pinned_n} pinned" if pinned_n else "")
    )
    if suppressed_count:
        lines.append(f"   <i>(plus {suppressed_count} acknowledged, suppressed)</i>")
    lines.append("")

    # Pinned roster — always shown, no staleness cutoff. Additive ABOVE the
    # activity buckets: roster = "I track this", buckets = "and it moved".
    if roster:
        lines.append("📌 <b>PINNED ROSTER</b> — always shown, no cutoff")
        lines.append("")
        for r in roster:
            lines.extend(fmt_roster_line(r))
        lines.append("")

    max_per = config["thresholds"]["max_per_category"]

    if total_urgent:
        lines.append("🔴 <b>URGENT</b> — direct mention, no reply, &lt;48h")
        lines.append("")
        for t in threads_by_urgency["urgent"][:max_per]:
            lines.extend(fmt_thread(t))
        if total_urgent > max_per:
            lines.append(f"   <i>… and {total_urgent - max_per} more (see log)</i>")
        lines.append("")

    if total_active:
        lines.append("🟡 <b>ACTIVE</b> — we commented, external follow-up, no reply")
        lines.append("")
        for t in threads_by_urgency["active"][:max_per]:
            lines.extend(fmt_thread(t))
        if total_active > max_per:
            lines.append(f"   <i>… and {total_active - max_per} more (see log)</i>")
        lines.append("")

    if total_stale:
        lines.append("🟢 <b>STALE</b> — MolTrust mentioned, no reply, &lt;30d")
        lines.append("")
        for t in threads_by_urgency["stale"][:max_per]:
            lines.extend(fmt_thread(t))
        if total_stale > max_per:
            lines.append(f"   <i>… and {total_stale - max_per} more (see log)</i>")
        lines.append("")

    if agent_flag_count:
        lines.append("⚠️ <b>AGENT HEALTH</b>")
        lines.append("")
        for p in agent_probes:
            for f in p.get("flags", []):
                lines.append(f"• <b>{html_mod.escape(p['name'])}</b>: {html_mod.escape(str(f))}")
        lines.append("")

    roster_waiting = sum(1 for r in (roster or []) if r.get("waiting"))
    if not (total_urgent or total_active or total_stale or agent_flag_count or roster_waiting):
        lines.append("✅ Nothing waiting. Inbox quiet.")
        lines.append("")

    lines.append("<i>/ack repo#num [days] · /ack_list · /ack_remove repo#num</i>")
    lines.append("<i>/pin repo#num [note] · /pin_list · /unpin repo#num</i>")
    return "\n".join(lines)


def fmt_roster_line(r):
    """Render one pinned-roster entry (Telegram HTML)."""
    note = r.get("note", "")
    note_html = f" — <i>{html_mod.escape(note)}</i>" if note else ""
    hint = " · <i>/unpin?</i>" if r.get("done") else ""
    title_html = html_mod.escape((r.get("title") or "")[:90])
    actor_html = html_mod.escape(str(r.get("last_actor", "—")))
    out = [
        f"📌 <b>{r['key']}</b> — {r['state_label']} · still {r['still']} · last: {actor_html}{hint}",
    ]
    if title_html:
        out.append(f"  <i>{title_html}</i>")
    out.append(f"  → {r['url']}{note_html}")
    out.append("")
    return out


def fmt_thread(t):
    """Render one thread as 2 lines (Telegram HTML)."""
    age = fmt_age(t["delta_hours"] * 3600)
    actor = t["last_external_actor"]
    mention = " 📌@" if t.get("mentioned_directly") else ""
    snippet = t.get("last_external_snippet", "")
    snippet_html = html_mod.escape(snippet[:100])
    return [
        f"• <b>{t['key']}</b> — {actor}{mention} · {age} ago",
        f"  <i>{snippet_html}</i>",
        f"  → {t['url']}",
        "",
    ]


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)
    log.info(f"=== ThreadWatch run @ {now.isoformat()} (dry_run={ARGS.dry_run}, fixture={ARGS.with_test_fixture}) ===")

    secrets = load_secrets()
    if not secrets.get("GH_TOKEN") and not ARGS.process_acks_only:
        log.error("GH_TOKEN missing — cannot run report")
        sys.exit(1)

    config = load_config()
    state = load_state()

    # 1. Process Telegram /ack commands first
    state = process_ack_commands(secrets, state)
    save_state(state)

    if ARGS.process_acks_only:
        log.info("--process-acks-only mode: done.")
        return

    # 2. Rate limit pre-check
    gh = GH(secrets["GH_TOKEN"])
    rl = gh.rate_limit()
    remaining = rl.get("remaining", 0)
    log.info(f"GH rate-limit: {remaining}/{rl.get('limit', '?')} remaining")
    if remaining < config["thresholds"]["min_rate_limit_remaining"]:
        log.warning(f"rate limit too low ({remaining} < {config['thresholds']['min_rate_limit_remaining']}) — skipping run")
        return

    # 3. Crawl watchlist
    since_iso = (now - timedelta(days=7)).isoformat().replace("+00:00", "Z")
    repo_results = []
    for repo in config["watchlist"]:
        try:
            r = crawl_repo(gh, repo, since_iso)
            repo_results.append((repo, r))
        except Exception as e:
            log.warning(f"crawl {repo} failed: {e}")

    # 3b. Crawl pinned discussions. Discussions don't appear in /issues,
    # so each one needs an explicit fetch driven by config.watch_discussions.
    for disc_cfg in config.get("watch_discussions", []) or []:
        drepo, dnum = disc_cfg.get("repo"), disc_cfg.get("number")
        if not drepo or dnum is None:
            log.warning(f"watch_discussions entry missing repo/number: {disc_cfg}")
            continue
        try:
            item = crawl_discussion(gh, drepo, dnum)
            if item:
                repo_results.append((drepo, [item]))
        except Exception as e:
            log.warning(f"crawl discussion {drepo}#{dnum} failed: {e}")

    # 3c. Build the pinned roster — ALWAYS fetched + shown, no staleness cutoff.
    # Effective roster = config.tracked_threads ∪ state.pinned (dynamic /pin).
    # Additive to the buckets; not de-duped (a pin that also moves shows in both).
    roster_specs = {}
    for tcfg in config.get("tracked_threads", []) or []:
        rrepo, rnum = tcfg.get("repo"), tcfg.get("number")
        if not rrepo or rnum is None:
            log.warning(f"tracked_threads entry missing repo/number: {tcfg}")
            continue
        roster_specs[f"{rrepo}#{rnum}"] = {
            "repo": rrepo, "number": rnum,
            "note": tcfg.get("note", ""), "kind": tcfg.get("kind", "issue"),
        }
    for pkey, pv in (state.get("pinned") or {}).items():
        if pkey not in roster_specs and pv.get("repo") and pv.get("number") is not None:
            roster_specs[pkey] = {
                "repo": pv["repo"], "number": pv["number"],
                "note": pv.get("note", ""), "kind": pv.get("kind", "issue"),
            }
    roster = []
    for rkey, spec in roster_specs.items():
        try:
            roster.append(build_roster_entry(gh, spec, config, now))
        except Exception as e:
            log.warning(f"roster {rkey} failed: {e}")
    log.info(f"pinned roster: {len(roster)} thread(s)")
    for r in roster:
        log.info(f"  [roster] {r['key']} | {r['state_label']} | still {r['still']} | last={r['last_actor']}")

    # 3d. PR-merge transition alerts — fire ONCE per pinned PR when it flips to
    # merged. State (merged_announced) makes it a distinct one-time event instead
    # of only the persistent 🟣 MERGED roster label, which is easy to miss and
    # never signals "the deferred own-task is now unblocked".
    merged_announced = state.setdefault("merged_announced", {})
    merge_alerts = []
    for r in roster:
        if r.get("merged") and r["key"] not in merged_announced:
            merge_alerts.append(r)
            merged_announced[r["key"]] = r.get("merged_at") or now.isoformat()
    if merge_alerts:
        log.info(f"merge alerts (first-time): {[r['key'] for r in merge_alerts]}")
        save_state(state)  # persist the once-fired guard before the report send

    # 4. Classify threads
    all_threads = classify_threads(repo_results, config, now)
    log.info(f"classified {len(all_threads)} threads pre-ack-filter")

    # 5. Inject test fixture if requested
    if ARGS.with_test_fixture:
        all_threads.insert(0, synthetic_test_thread(now))
        log.info("test fixture injected")

    # 6. Apply acknowledgments
    suppressed = 0
    visible = []
    for t in all_threads:
        if is_acknowledged(state, t["key"], now):
            suppressed += 1
            continue
        visible.append(t)
    log.info(f"after ack filter: {len(visible)} visible, {suppressed} suppressed")

    # 7. Bucket by urgency, sort newest external first
    by_urgency = {"urgent": [], "active": [], "stale": []}
    for t in visible:
        by_urgency[t["urgency"]].append(t)
    for k in by_urgency:
        by_urgency[k].sort(key=lambda x: x["delta_hours"])

    # Log full list for archival
    log.info("─── full thread list (visible, by urgency) ───")
    for u in ("urgent", "active", "stale"):
        for t in by_urgency[u]:
            log.info(
                f"  [{u}] {t['key']} | actor={t['last_external_actor']} "
                f"age={fmt_age(t['delta_hours']*3600)} mention={t.get('mentioned_directly')} "
                f"url={t['url']}"
            )

    # 8. Agent health probes
    probes = []
    try:
        probes.append(probe_moltycel_queue(config))
    except Exception as e:
        log.warning(f"moltycel queue probe: {e}")
    try:
        probes.append(probe_moltbook_logs(config, now))
    except Exception as e:
        log.warning(f"moltbook logs probe: {e}")
    try:
        probes.append(probe_endpoint_state(config))
    except Exception as e:
        log.warning(f"endpoint probe: {e}")

    for p in probes:
        log.info(f"probe {p['name']}: flags={p.get('flags', [])}")

    # 9. Build + send report
    report = fmt_report(by_urgency, probes, now, config, suppressed_count=suppressed, roster=roster, merge_alerts=merge_alerts)
    log.info(f"report length: {len(report)} chars")

    sent = telegram_send(secrets, report, dry=ARGS.dry_run)
    if sent:
        log.info("report sent" if not ARGS.dry_run else "dry-run report shown")
    else:
        log.error("report send failed")

    # 10. Persist state
    state["last_run"] = now.isoformat()
    save_state(state)
    log.info("=== run complete ===")


if __name__ == "__main__":
    main()
