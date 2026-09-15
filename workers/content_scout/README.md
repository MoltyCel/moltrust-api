# Content-Scout (v0 — draft-and-queue only)

A scheduled drafter. It classifies the Discovery + NewsScout feeds, drafts GH
comments / blog posts for real hits, and drops them into `content_review_queue`
for human review in the Console. **It never publishes, comments, commits, or
deploys.** Publishing stays 100% manual.

## Pipeline (per run)
`ingest → classify (Haiku, all candidates) → [PASS] content-pull → draft (Opus)
→ verify-gate → insert queue row (pending_review) → one-way Telegram summary`

- **classify** — `claude-haiku-4-5`, every candidate → `{verdict, reason}`
  (PASS / WATCH / DROP). DROP rows are stored as `discarded` (idempotency, no
  re-spend); WATCH is queued info-only (no draft); PASS gets drafted.
- **content-pull (PASS only)** — Discovery via the **authenticated** GitHub API
  (issue body + latest comments); NewsScout via article extract (follows
  aggregator links to the primary source).
- **draft** — `claude-opus-4-8`. System prompt loads the guardrail docs at
  runtime (anti-KI-Sprech.md + my-voice-en.md, WORKFLOW.md, CLAUDE.md,
  website-deploy.md) — single source of truth, never inlined. The voice profiles
  (anti-KI-Sprech negative side, my-voice-en positive side) and website-deploy.md
  come from the moltrust-web shallow clone; WORKFLOW/CLAUDE from moltrust-api.
- **verify-gate** — flags spec-section / hash / quant claims; `verified` where an
  authoritative source is checkable (AAE on Datatracker), else `unverified`.
  `unverified` does not block queueing but blocks the later manual publish.

## NewsScout status — dormant in v0 (needs a news_scout.py fix)
`news_scout.py`'s only file artifact (`~/moltstack/data/news_sent_urls.json`) is a
dedup cache of **hashed `url_key()`s**, not URLs — the newsworthy content itself is
Telegram-only. There is no readable NewsScout feed to draft from, and per the build
spec we do **not** scrape Telegram. So the blog-draft path is wired but produces 0
items until `news_scout.py` is changed to persist real URLs + titles (a small,
separate change). The Discovery → GH-comment path is fully functional.

## Balance gate
At run start it reuses the existing monitor's approach (`scripts/check_credits.sh`)
— a tiny Haiku ping. On failure (quota / insufficient credit) it fires the
standard Telegram alert and runs **classify-only** (no drafting) that cycle. The
Anthropic API exposes no dollar balance, so the CLAUDE.md "< $10" rule is
enforced via this health probe, not a literal gauge.

## Discovery resilience (health.py)
The only real input is `~/moltycelbot/discovery_candidates.json`, written daily
~06:00 UTC by `scripts/discovery/discovery.py`. From 2026-08-11 to 2026-09-14 that
job failed every day and the scout still reported "0 lead(s)" — identical to a
normal evening run. Guards against a repeat:

- **Feed freshness** at run start: `discovery_health.json` (`last_ok_at`, or the
  date-only `last_ok` + feed mtime) older than `FEED_STALE_HOURS` (26h), missing
  health file, or missing feed → `🚨 Content-Scout ALERT`.
- **Zero-intake alarm**: per-run stats in `state/content_scout_runs.json`. Every
  run in the last `ZERO_INTAKE_WINDOW_HOURS` (36h) with 0 candidates and 0
  classified, at least `ZERO_INTAKE_MIN_RUNS` (3) runs spanning
  `ZERO_INTAKE_MIN_SPAN_HOURS` (20h, so a daily refresh fell in between) → ALERT.
  A normal 0-candidate evening run cannot trip it.
- Both alarms re-alert at most once per `ALARM_REALERT_HOURS` (24h, 30 min
  jitter slack) and send `✅ Content-Scout RECOVERED` when the condition clears.
- **Summary** always carries a `feed: fresh (…)` / `feed: STALE — …` /
  `feed: UNKNOWN — …` line.
- **Guards**: classify API error → item left unprocessed (retried next run, not
  stored as DROP), ALERT + exit 1 if all fail; content pull error → ALERT, lead
  retried next run; DB connect failure, unreadable feed or any crash → ALERT +
  exit 2.

Thresholds/paths: `config.py`, env overrides `CONTENT_SCOUT_*`.

## Console CLI (v0)
```
python -m workers.content_scout.cli list            # pending, one line each
python -m workers.content_scout.cli show <id>       # draft + verification block
python -m workers.content_scout.cli show <id> --write  # also writes to ~/content-scout-drafts/
python -m workers.content_scout.cli approve <id>    # marks ready — does NOT publish
python -m workers.content_scout.cli discard <id>
```

## Run manually
```
~/moltstack/venv/bin/python -m workers.content_scout.pipeline --dry-run --json
```

## Proposed cron (NOT enabled on merge — enable only after review)
Align to the feeds rather than tight-polling: Discovery refreshes ~06:00 UTC and
NewsScout runs 17:00 UTC, so draft shortly after each. Two runs/day keeps the
authenticated-GitHub and Anthropic spend bounded and avoids racing a
mid-refresh feed.
```
# /etc/cron.d/moltrust-content-scout  (do NOT install until confirmed)
30 6  * * *  moltstack  cd ~/moltstack && venv/bin/python -m workers.content_scout.pipeline >> logs/content_scout.log 2>&1
30 17 * * *  moltstack  cd ~/moltstack && venv/bin/python -m workers.content_scout.pipeline >> logs/content_scout.log 2>&1
```
