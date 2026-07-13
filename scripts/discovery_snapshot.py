#!/usr/bin/env python3
"""
scripts/discovery_snapshot.py — Daily Discovery-Tracking snapshot.

Cron: 30 0 * * *  (00:30 UTC daily)
SPEC: docs/specs/2026-05-21_discovery-tracking-baseline-SPEC.md §3.5 + §5.2

Captures 5 sources → discovery_snapshots table (one row per day, UPSERT on
snapshot_at UNIQUE — idempotent on repeated same-day runs):
  - self_probes : HEAD/GET the 4 Discovery surfaces (sitemap, llms.txt,
                  /guard/openapi.json, /extendedAgentCard)
  - bot_hits    : parse nginx access logs (last 7 days), bot-UA × endpoint-class
  - github      : GH_TOKEN-authenticated repo + traffic API for 6 MoltyCel repos
  - gsc         : manual-pending (V0 per SPEC §9.1 — Lars updates via SQL)
  - errors      : collected non-fatal failures

Privacy (SPEC §3.7): nginx parser aggregates User-Agent × endpoint-class only.
No IPs persisted to payload.

Flags:
  --dry-run        assemble + print payload, no DB write, no alert.
  --date YYYY-MM-DD  override snapshot_at (for backfills + test-runs against a
                   throwaway date). Default: today (UTC).

Exit code: 0 on ok/partial, 1 on failed (all sources down).
"""
import argparse
import glob
import gzip
import json
import os
import re
import subprocess
import sys
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

from app import notify

DB = "moltstack"
NGINX_GLOB = "/var/log/nginx/access.log*"
GITHUB_REPOS = [
    "MoltyCel/moltrust-api", "MoltyCel/moltrust-web", "MoltyCel/moltguard",
    "MoltyCel/moltrust-mcp-server", "MoltyCel/moltrust-x402",
    "MoltyCel/moltrust-openclaw",
]
PROBE_DID = "did:moltrust:d34ed796a4dc4698"  # TrustScout seed — public, for extendedAgentCard auth

BOT_UAS = [
    "GPTBot", "ChatGPT-User", "ClaudeBot", "anthropic-ai",
    "PerplexityBot", "Perplexity-User",
    "Google-Extended", "Bytespider", "Applebot-Extended",
    "Googlebot", "Bingbot", "BingPreview",
    "DuckDuckBot", "YandexBot", "BaiduSpider", "facebookexternalhit",
    "Twitterbot", "LinkedInBot", "Slackbot", "TelegramBot",
]


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


# ── Source 1: Self-Probes ────────────────────────────────────────────
def collect_self_probes(errors):
    probes = {}
    try:
        r = requests.get("https://moltrust.ch/sitemap.xml", timeout=10)
        probes["sitemap.xml"] = {
            "status": r.status_code,
            "url_count": r.text.count("<loc>"),
            "byte_count": len(r.content),
        }
    except Exception as e:
        errors.append(f"self_probe sitemap.xml: {type(e).__name__}")
    try:
        r = requests.get("https://api.moltrust.ch/llms.txt", timeout=10)
        probes["llms.txt"] = {
            "status": r.status_code,
            "has_moltguard_block": "## MoltGuard sub-API" in r.text,
            "byte_count": len(r.content),
        }
    except Exception as e:
        errors.append(f"self_probe llms.txt: {type(e).__name__}")
    try:
        r = requests.get("https://api.moltrust.ch/guard/openapi.json", timeout=10)
        spec = r.json()
        probes["guard_openapi"] = {
            "status": r.status_code,
            "path_count": len(spec.get("paths", {})),
            "byte_count": len(r.content),
        }
    except Exception as e:
        errors.append(f"self_probe guard_openapi: {type(e).__name__}")
    try:
        r = requests.get(
            "https://api.moltrust.ch/extendedAgentCard",
            headers={"X-MolTrust-DID": PROBE_DID}, timeout=10,
        )
        exts = r.json().get("capabilities", {}).get("extensions", [])
        mg = sorted(
            "/".join(e["uri"].rstrip("/").split("/")[-2:])
            for e in exts
            if "moltguard" in e.get("uri", "") or "x402-pricing" in e.get("uri", "")
        )
        probes["extendedAgentCard"] = {
            "status": r.status_code,
            "moltguard_extensions_present": mg,
        }
    except Exception as e:
        errors.append(f"self_probe extendedAgentCard: {type(e).__name__}")
    return probes


# ── Source 2: nginx Bot-Hits ─────────────────────────────────────────
LOG_RE = re.compile(
    r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+) [^"]*" (\d+) \d+ "[^"]*" "([^"]*)"'
)


def classify_ua(ua):
    if not ua:
        return None
    ua_l = ua.lower()
    for bot in BOT_UAS:
        if bot.lower() in ua_l:
            return bot
    if re.search(r"\b(bot|spider|crawler)\b", ua_l):
        return "Other-Crawlers"
    return None


def classify_endpoint(path):
    if not path or not path.startswith("/"):
        return "unknown"
    if path.startswith("/blog/"):
        return "web/blog"
    if path.startswith("/publications/") or re.match(r"^/[^/]+\.pdf$", path):
        return "web/publications"
    if path == "/":
        return "web/root"
    if path in ("/sitemap.xml", "/robots.txt", "/llms.txt") or path.startswith("/.well-known/"):
        return "discovery-surface"
    if path.endswith(".html"):
        return "web/static-page"
    if path.startswith("/guard/"):
        return "api/guard"
    if path.startswith("/identity/") or path.startswith("/skill/") or path.startswith("/swarm/"):
        return "api/identity"
    if path in ("/openapi.json", "/extendedAgentCard", "/health", "/stats", "/docs", "/mcp"):
        return "api/discovery-surface"
    return "api/other"


def collect_bot_hits(errors):
    cutoff = datetime.now() - timedelta(days=7)
    hits = defaultdict(lambda: defaultdict(int))
    stats = {"lines_total": 0, "lines_in_window": 0, "files": 0}
    try:
        for path in sorted(glob.glob(NGINX_GLOB)):
            stats["files"] += 1
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rt", errors="replace") as fh:
                for line in fh:
                    stats["lines_total"] += 1
                    m = LOG_RE.match(line)
                    if not m:
                        continue
                    _ip, time_str, _method, req_path, _status, ua = m.groups()
                    try:
                        dt = datetime.strptime(time_str.split(" ")[0], "%d/%b/%Y:%H:%M:%S")
                    except ValueError:
                        continue
                    if dt < cutoff:
                        continue
                    stats["lines_in_window"] += 1
                    bot = classify_ua(ua)
                    if bot:
                        hits[bot][classify_endpoint(req_path)] += 1
    except Exception as e:
        errors.append(f"bot_hits nginx-parse: {type(e).__name__}")
    return {bot: dict(c) for bot, c in hits.items()}, stats


# ── Source 3: GitHub ─────────────────────────────────────────────────
def collect_github(errors):
    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        return {"_fetch_status": "pat-not-configured"}
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    out = {}
    for repo in GITHUB_REPOS:
        try:
            base = requests.get(f"https://api.github.com/repos/{repo}", headers=hdr, timeout=15).json()
            entry = {
                "stars": base.get("stargazers_count", 0),
                "forks": base.get("forks_count", 0),
                "watchers": base.get("subscribers_count", base.get("watchers_count", 0)),
                "visibility": base.get("visibility", "?"),
            }
            for kind in ("clones", "views"):
                t = requests.get(
                    f"https://api.github.com/repos/{repo}/traffic/{kind}",
                    headers=hdr, timeout=15,
                ).json()
                entry[f"{kind}_14d_count"] = t.get("count", 0)
                entry[f"{kind}_14d_uniques"] = t.get("uniques", 0)
            out[repo] = entry
        except Exception as e:
            errors.append(f"github {repo}: {type(e).__name__}")
            out[repo] = {"_error": type(e).__name__}
    return out


# ── DB UPSERT ────────────────────────────────────────────────────────
def upsert_snapshot(snapshot_date, payload, status):
    payload_json = json.dumps(payload, ensure_ascii=False)
    # dollar-quoted literal — JSON never contains the $disco$ token, so this is
    # injection-safe without escaping.
    sql = (
        "INSERT INTO discovery_snapshots (snapshot_at, payload, source_run_status) "
        f"VALUES ('{snapshot_date}'::date, $disco${payload_json}$disco$::jsonb, '{status}') "
        "ON CONFLICT (snapshot_at) DO UPDATE "
        "SET payload = EXCLUDED.payload, generated_at = NOW(), "
        "source_run_status = EXCLUDED.source_run_status "
        "RETURNING id, snapshot_at, source_run_status, octet_length(payload::text);"
    )
    r = subprocess.run(["psql", "-d", DB, "-c", sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"psql upsert failed: {r.stderr.strip()}")
    return r.stdout.strip()


# ── Telegram alert ───────────────────────────────────────────────────
def telegram_alert(text):
    if not notify.telegram_allowed("discovery_snapshot.telegram_alert"):
        return
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        log("WARN: TELEGRAM_BOT_TOKEN/CHAT_ID not set — alert skipped")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data={"chat_id": chat, "text": text}, timeout=10,
        )
    except Exception as e:
        log(f"WARN: telegram alert failed: {type(e).__name__}")


# ── Main ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="assemble + print payload, no DB write, no alert")
    ap.add_argument("--date", metavar="YYYY-MM-DD",
                    help="override snapshot_at (backfill / throwaway test-run)")
    args = ap.parse_args()

    if args.date:
        try:
            snapshot_date = datetime.strptime(args.date, "%Y-%m-%d").date().isoformat()
        except ValueError:
            log(f"FATAL: --date must be YYYY-MM-DD, got '{args.date}'")
            return 1
    else:
        snapshot_date = datetime.now(timezone.utc).date().isoformat()
    log(f"discovery_snapshot start — snapshot_at={snapshot_date} dry_run={args.dry_run}")
    errors = []

    self_probes = collect_self_probes(errors)
    log(f"  self_probes: {len(self_probes)}/4 surfaces captured")

    bot_hits, bh_stats = collect_bot_hits(errors)
    log(f"  bot_hits: {len(bot_hits)} bots, "
        f"{sum(sum(c.values()) for c in bot_hits.values())} hits "
        f"({bh_stats['lines_in_window']} log-lines in 7d window)")

    github = collect_github(errors)
    gh_ok = sum(1 for v in github.values() if isinstance(v, dict) and "_error" not in v and "_fetch_status" not in v)
    log(f"  github: {gh_ok}/{len(GITHUB_REPOS)} repos captured")

    # source_run_status: GSC manual-pending is NOT an error.
    critical_sources = 3  # self_probes, bot_hits, github
    failed_sources = sum([
        len(self_probes) == 0,
        len(bot_hits) == 0 and bh_stats["lines_total"] == 0,
        gh_ok == 0 and "GH_TOKEN" in os.environ,
    ])
    if failed_sources == 0:
        status = "ok"
    elif failed_sources < critical_sources:
        status = "partial"
    else:
        status = "failed"

    payload = {
        "self_probes": self_probes,
        "gsc": {
            "fetch_status": "manual-pending",
            "last_7d": {"impressions": None, "clicks": None, "indexed_urls": None,
                        "ctr": None, "avg_position": None},
            "note": "V0 manual paste per SPEC §9.1.",
        },
        "bot_hits": bot_hits,
        "github": github,
        "errors": errors,
        "meta": {
            "spec_version": "1.0",
            "generated_by": "scripts/discovery_snapshot.py (cron)",
            "parser_stats": {
                "nginx_lines_total": bh_stats["lines_total"],
                "nginx_lines_in_window": bh_stats["lines_in_window"],
                "log_files_parsed": bh_stats["files"],
            },
        },
    }

    if args.dry_run:
        log(f"DRY-RUN — status would be '{status}', errors={errors}")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    try:
        result = upsert_snapshot(snapshot_date, payload, status)
        log(f"  upsert ok: {result}")
    except Exception as e:
        log(f"FATAL: {e}")
        telegram_alert(f"⚠️ discovery_snapshot {snapshot_date}: DB upsert FAILED — {e}")
        return 1

    log(f"discovery_snapshot done — status={status}")
    if status != "ok":
        telegram_alert(
            f"⚠️ discovery_snapshot {snapshot_date}: status={status}\n"
            f"errors: {'; '.join(errors) if errors else '(none)'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
