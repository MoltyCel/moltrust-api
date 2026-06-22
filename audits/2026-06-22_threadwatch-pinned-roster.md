# Audit 2026-06-22 — ThreadWatch pinned-roster layer

**Console action.** Additive feature on repo-managed tooling (`scripts/threadwatch.py`).

## Problem

ThreadWatch reported only event-buckets (urgent/active/stale) with a `<30d` stale
cutoff → important QUIET threads (A2A #1716, cosai #99) dropped off the radar entirely.
**Silence on a tracked thread IS a signal** ("Nd no follow-up = done OR ball dropped")
and must stay visible.

## Change

Additive **pinned-roster layer** — NOT bucket tuning:
- New config `tracked_threads: [{repo, number, note, kind}]` (parallel to `watch_repos`/`watchlist`).
- Roster threads fetched + rendered EVERY run, no staleness cutoff, ABOVE the buckets.
  Per line: ref · state · "still seit Nd" · last actor. CLOSED/MERGED are explicit
  states so "erledigt" separates from "Ball verloren".
- Buckets (urgent/active/stale) + `classify_threads` left **byte-untouched**; roster
  reuses module primitives (parse_ts/fmt_age/identities/has_at_mention), mirrors the
  event-flatten deliberately to avoid editing classify.
- Telegram `/pin` `/pin_list` `/unpin` (mirror `/ack`) → dynamic pins in
  `state/threadwatch.json` (`pinned`). Effective roster = `tracked_threads` ∪ `pinned`.
- "Nothing waiting" line now also gated on roster waiting-state (no false "quiet").
- No de-dup: a pin that also moves shows in roster AND its bucket (by design).

## Rate-limit / secrets

Authenticated via existing `GH` class (Bearer `GH_TOKEN`), under the run-level
rate-limit gate. ~2 calls/pin/run (~8 for the start roster) — negligible vs 5000/h.
No token hardcoded; §6.4 / #9 respected. §12 N/A (internal tooling deploy).

## Start roster (verified refs, read-only resolved)

- `a2aproject/A2A#1716` — RFC: Authz layer for A2A AgentSkill invocations
- `cosai-oasis/ws4-secure-design-agentic-systems#99` — [RFC] Agent Credentials
- `x402-foundation/x402#2332` — Post-settlement accountability (NOT coinbase/x402)
- `MoltyCel/aae-conformance-vectors#2` — verification_mode enforced|asserted (single open issue; resolved, not assumed)

## Deploy

Generator is **cron** (`0 8,18 UTC`) — pull only, no restart. Verify: `post-sha == repo-sha`
+ one manual `--dry-run` showing the 📌 section.
