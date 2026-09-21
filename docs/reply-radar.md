# Reply radar

`agents/reply_radar.py` drafts replies for @moltrust every two hours and sends
them to the Telegram stats channel. **It posts nothing.** There is no write path
to X in the file.

## What it does

Three sources, in the order they are trusted:

| source | endpoint | why |
|---|---|---|
| the `targets` list | `GET /2/lists/<id>/tweets` | curated, highest signal |
| search | `GET /2/tweets/search/recent`, five fixed queries | wider, noisier |
| mentions | `GET /2/users/<id>/mentions` | someone already spoke to us |

A post is a candidate when it is English, not a retweet, not ours, at least
eight words long, inside the three-hour lookback, and not already seen. The
list comes first, then whatever more people saw.

Caps: **3 per run, 8 per day.** Twelve runs a day makes the per-run number a
ceiling rather than a target.

## The three rules, and which of them are enforced

| rule | enforced by |
|---|---|
| no link | gate 2 (e) with `mode="reply"` — expects zero links |
| no pitch | `PRODUCT_RE` over the whole draft, not just the opener like gate 2 (d) |
| a number, or a counterexample | gate 2 (f) — a number |

The third is the honest gap. "A counterexample" is not mechanically detectable,
so the gate requires a figure or a named specification carrying one (ERC-8004,
RFC 8785, CVE-2026-…). A draft that carries a counterexample in prose is
blocked, goes to Telegram marked as blocked, and waits for a human — which is
where every draft goes today anyway.

That failure mode is the intended one. A counterexample nobody can state with a
figure or a named document is usually an opinion, and an opinion is the thing
this radar exists not to send.

## What is missing, and why it is not built

**Approval by reaction does not work yet, and the reason is structural.**

- Telegram delivers `message_reaction` updates only to a bot that asks for them
  explicitly via `allowed_updates`. They are not in the default set.
- `getUpdates` is **exclusive per bot token**. Two pollers on one token steal
  each other's updates. `scripts/threadwatch.py` already polls this token, twice
  a day at 08:00 and 18:00 UTC.

So a second poller for reactions would break ThreadWatch, and adding reactions
to ThreadWatch's poll would give the approval loop a twice-daily latency. Neither
is obviously right, and the decision is not the radar's to make. Until it is
made, drafts are read in Telegram and acted on by hand.

Once approval exists, posting should follow the pattern in
`scripts/revoke_inactive.py`: listing stays the default, and the write path
needs both an explicit flag and an armed environment variable.

## The targets list

Private list `targets` under @moltrust, id `2101805022954557791`, **empty**.
The 80 candidates are in `~/Downloads/x-targets.md`, each resolved live against
`/2/users/by`. They go in only after Lars approves that file; until then the
list source returns nothing and the radar runs on search and mentions alone.

## Running it

    python agents/reply_radar.py              # scheduled run
    python agents/reply_radar.py --dry-run    # print, send nothing
    python agents/reply_radar.py --limit 1    # fewer drafts this run

Cron: every two hours. State in `data/reply_radar_state.json` — seen ids,
capped at 2000, and a per-day counter that drops yesterday.
