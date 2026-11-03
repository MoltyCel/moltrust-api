# Reply radar — replies from Lars's account: the measurement

**Fixed on 2026-10-23, before the first reply goes out. It does not move.**
Decided by Lars on 22.10.2026 (ops CM5). The replies themselves are drafted by
the radar and sent from Lars's personal X account with the Send button (CM2,
CM3); this file says how they are judged.

## The window

| | |
|---|---|
| starts | the first reply **sent through CM3** (Send button, Lars's tokens) |
| interim report | four weeks (28 days) after the start |
| final report | eight weeks (56 days) after the start |

A reply counts towards a report if it was posted inside that report's window.
A reply posted by hand does not open the window (see "Copied replies").

## What is measured

**Per reply, once a day for its first 14 days:** impressions and profile
clicks, plus likes, replies and engagements. Profile clicks are served by X to
the author only, so these reads are signed with Lars's tokens, never with the
@MolTrust ones. The report uses the newest measurement of each reply.

Fourteen days, the same as the @MolTrust series in `scripts/digest_metrics.py`:
impressions on a reply are done inside a week. A reply posted in the last two
weeks of the window is therefore reported with fewer days behind it; its row
says how old it was (`age_hours`).

**Per day, for the account:** the follower count, and how many posts of his own
Lars made since the previous measurement (replies and reposts excluded). New
followers are counted towards the replies **only on a day without an own
post**; a day whose own-post count is unknown is not counted either.

## The threshold

The same bar the @MolTrust reply branch was judged by
(`docs/reply-radar.md`, "The threshold, fixed before the measurement"). Both
must hold:

| | threshold |
|---|---|
| median impressions per reply | **≥ 30** |
| profile clicks | **≥ 1 per 2 replies** (ratio ≥ 0.5) |

Median, not mean, and both, not either — for the reasons given there.

## Copied replies

When the Send button cannot post (account not unlocked, or sending paused by
the weekly cap), it hands Lars the text to copy (CM3 criterion 4, CM4
criterion 4). A reply posted that way **counts only once Lars reports its
URL**, and it is marked as copied in every row and in both reports:

```
python scripts/lars_reply_metrics.py --add-copied https://x.com/<handle>/status/<id> [--target <id>]
```

A reported post that X says is not from Lars's account is skipped, not counted.

## Where it runs and what it writes

`scripts/lars_reply_metrics.py`, daily at 18:05 UTC. Before the first reply
sent through CM3 it logs `window not started` and calls nothing.

| file | content |
|---|---|
| `data/reply_radar_lars.jsonl` | one line per reply: sent (`route: api`) or copied (`route: copied`) |
| `data/reply_radar_lars_metrics.jsonl` | one line per reply and day (`kind: reply`), one line per day (`kind: followers`) |
| `data/reply_radar_lars_state.json` | when the interim and the final report went out |

The rows are kept apart from the @MolTrust rows in `data/digest_metrics.jsonl`
on purpose: the two accounts are measured with different tokens and answer
different questions.

**Tokens.** `X_CONSUMER_KEY` and `X_CONSUMER_SECRET` of the existing app, plus
Lars's own access pair `X_LARS_ACCESS_TOKEN` and `X_LARS_ACCESS_SECRET` (the
names CM3 stores them under). There is no fallback to the @MolTrust access
pair: a read signed with it gets no profile clicks for Lars's replies, which
would look like a zero instead of a gap. Window open and tokens missing is an
error (exit 1), not a quiet day.

**Costs and caps.** Every read is booked in `data/x_meter.jsonl` under the
source `lars-reply-metrics`, so it can be reported in its own column (CM9). It
only reads, so it never counts against the weekly send cap of $0.50 (CM4), and
it consults neither that cap nor the daily read breaker: a cost pause does not
stop the measurement (Lars 22.10.2026). Estimated cost per run (c): one profile
read ($0.010), the own-post read (usually 0–2 posts), and one post read per
reply in its 14 days ($0.005 each); at two replies a day that is about $0.15 a
day at most.

## The two reports

Each is written once, on the first daily run on or after its date, into the
inbox of the daily report (`app/tagesbericht_eingang`), and appears in the next
selftest digest at 08:00 or 20:00 UTC. Neither goes to Telegram as a message of
its own (Lars 23.10.2026: measurement reports belong in the daily report). The
report carries the number of replies (sent and copied), median impressions,
profile clicks per reply, each against the threshold, and the new followers on
days without an own post. `interim_sent_at` and `final_sent_at` are set when
the report is filed. A report that could not be filed is not marked and is
tried again the next day; a digest that is not delivered keeps the inbox for
the next one. After the final report the measurement stops.

## Amendment 2026-10-31: replies posted through the link (ops CM22/CM23)

Lars, 31.10.2026: CM3 falls away (CM18). A draft reaches him as an intent link
(CM16) and he posts it himself from @LarsOnMoltrust. Measurement, threshold and
report days stay as above; only what opens the window and how the reads are
signed change.

- **The window** starts with the first reply sent through CM3 **or** the first
  reply found on Lars's timeline for a draft's target post (`route: link` in
  `data/reply_radar_lars.jsonl`), whichever is earlier. A copied reply still
  does not open it.
- **Finding the reply.** `agents/lars_reply_match.py`, called by the
  five-minute consumer before the twelve-hour sweep, at most every 30 minutes
  and only while a draft can still be closed. A post on Lars's timeline whose
  `referenced_tweets` holds `replied_to` = the draft's target, written after
  the draft was offered, closes the draft as `gesendet` with `route: link`
  (`data/reply_radar_decisions.jsonl`). A reply found after the sweep booked
  the draft `verfallen` overrides that line within 72 hours of the offer: a
  second line with `overrides: verfallen`, and readers take the last line per
  draft.
- **Tokens.** A read-only OAuth 2.0 user token (Authorization Code with PKCE,
  scopes `tweet.read users.read offline.access`, no `tweet.write`), granted
  once by Lars with `scripts/x_oauth2_read_grant.py` and kept in
  `~/.moltrust_secrets` as `X_LARS_OAUTH2_ACCESS_TOKEN`,
  `X_LARS_OAUTH2_REFRESH_TOKEN`, `X_LARS_OAUTH2_EXPIRES_AT`,
  `X_LARS_OAUTH2_SCOPE`, next to the app's `X_OAUTH2_CLIENT_ID` /
  `X_OAUTH2_CLIENT_SECRET`. The access token lives two hours and is refreshed
  in place (`app/x_lars_oauth2.py`). A stored grant with any other scope is not
  used. Still no fallback to the @MolTrust pair.
- **Profile clicks.** X serves `non_public_metrics` to "OAuth 1.0a or OAuth 2.0
  with user context for posts you own", for posts of the last 30 days
  (docs.x.com/x-api/fundamentals/metrics). The 14-day tracking stays inside that.
- **Costs.** The timeline reads go under the source `lars-reply-match`, the one
  users/me check at the grant under `lars-oauth2-grant`; both are reads in the
  CM5 column and never in the send cap.
