# Reply radar

`agents/reply_radar.py` drafts replies for @moltrust four times a day and sends
them to the Telegram stats channel with a button under each. What the button
does depends on where the post came from, and that is the thing to understand
before reading anything else here.

## The X reply restriction

Since February 2026 X answers a reply to a post we are not part of with:

```
403 — You can only reply to or quote posts where you are mentioned
      or are the author.
```

On every tier. It is not an access level we can buy out of, and it is not the
`client-not-enrolled` state our app is also in — enrolling the app in a Project
would not lift it.

Two consequences run through the whole file:

- **A mention is still ours to answer.** We are named in it, so `POST /2/tweets`
  with `reply.in_reply_to_tweet_id` works.
- **Everything from the list or from search is not.** Those drafts never reach
  the X client at all.

`REPLY_RADAR_ARMED` therefore arms exactly one path: the mention path. A list
draft has no API route to arm.

## What each draft carries

| source | button | what happens |
|---|---|---|
| mention | `✅ Posten` (callback) | the consumer re-runs both gates against freshly fetched sources, then posts |
| list, search | `↗ In X antworten` (URL) | opens `x.com/intent/post?in_reply_to=…&text=…` with the draft filled in; Lars presses send |
| any, blocked | none | a draft that failed the gates is shown and nothing else |

`🗑 Verwerfen` is a callback in all cases — declining is ours to record either
way.

The draft in the intent link is the one that passed both gates. It travels
through a browser instead of through our credentials; it is not re-written on
the way.

`handle_decision()` re-reads the source out of the message it was clicked
under (`Quelle (mention):`) and refuses anything that is not a mention, even
though no such button is ever sent. State can be lost or rebuilt; the message
the button sits under cannot.

## Proving a manual post

Handing a draft over does not end the measurement.

Every half hour the consumer reads `GET /2/users/<us>/tweets` and matches each
reply's `referenced_tweets[].id` against the drafts it handed out. A hit:

1. rewrites the Telegram message to `✅ Gepostet <link>`,
2. records the decision with `route: "manual"` and the follower count,
3. counts against the daily cap like any other post,
4. enters the `kind: "reply"` series in `digest_metrics.py`.

Nobody has to confirm anything. An offer nobody takes stops being watched after
24 hours — the draft stays in the chat, only the timeline read ends.

The consumer runs every five minutes because a button press should not wait;
the timeline read is rate-limited to half an hour inside it, because nobody
posts by hand in under a minute and every read is billed per resource.

## Sources

| source | endpoint | why |
|---|---|---|
| the `targets` list | `GET /2/lists/<id>/tweets` | curated, highest signal |
| search | `GET /2/tweets/search/recent`, five fixed queries | wider, noisier |
| mentions | `GET /2/users/<id>/mentions` | someone already spoke to us |

A post is a candidate when it is English, not a retweet, not ours, at least
eight words long, inside the lookback, and not already seen. The lookback is
not a constant: it runs from the last run plus half an hour, capped at twelve,
so changing the schedule does not silently change what gets read twice.

**Search runs once a day, at 10:00 UTC**; the list and mentions run at 06, 10,
14 and 18. X bills per resource returned, and the five searches were 103 of one
run's 157 objects. The list asks for no author expansion — every member's
handle and follower count is already in `config/reply_targets.json`, and a
profile read costs twice what a post does.

Caps: **3 per run, 8 per day**, and one draft per author per run, two per day
(mentions exempt). Tier 4 and anyone over a million followers is only answered
when the post is about agent identity, x402, ERC-8004 or the rest of the topic
set in `ON_TOPIC_RE`.

## One claim, on the post's subject

**A reply makes one core claim, and that claim is about what the post it
answers is about.** Not two claims, not one plus an aside. A reader has to be
able to say what the reply asserts in a sentence.

**Our own subjects come in only when the post raises them.** The blog, the
measurements, the standards work: all of it is off the table unless the post
put it on the table. Adjacency is not a reason.

The failure this fixes, from 02.10.2026. The post was a SlowMist alert about a
spoofable access check:

> @aave v3 Loop Safe Module Loss: ~114.09 ETH
> Root Cause: FlashLoopAdapter's open()/close() access control only checks
> ISafe(msg.sender).isModuleEnabled(address(this)), which is spoofable

The draft answered it, and then kept going:

> 114.09 ETH gone because the module trusted the caller's own claim about
> itself. **The four agent-trust launches in one week (NVIDIA, Robinhood,
> OpenAI, Meta) repeat the pattern:** every check runs inside the operator's own
> estate, nothing a counterparty can verify independently.

The first sentence is a reply. The second is our own blog post, which that
thread never mentioned. Both halves are true and the reply still reads as an
advertisement — which is the thing a reader notices, not the truth of either
half.

The clause that produced it was in the prompt by design: *"Answering with our
own measurement is the strongest reply available."* True in general, and an
invitation to pivot. It now reads *"worth using when it speaks to the post's
subject … and it is still the wrong answer if the post was about something
else."*

**This one is not mechanically enforced, and a check would not have caught it.**
The repetition guard counts core claims through `claim_marks()`, which finds
numbers and named specifications — in that draft it finds `114` and nothing
else, because NVIDIA, Robinhood, OpenAI and Meta are names without figures. A
claim-count gate would have passed it. The rule lives in the instruction and in
this paragraph; what catches a breach is reading the draft in Telegram before
pressing the button.

## The three rules, and which of them are enforced

| rule | enforced by |
|---|---|
| no link | gate 2 (e) with `mode="reply"` — expects zero links |
| no pitch | `PRODUCT_RE` over the whole draft, not just the opener like gate 2 (d) |
| a number, or a counterexample | gate 2 (f) — a number |
| every claim sourced | gate 2 (h) — each claim must appear in the post being answered, or in a page the draft named and this run fetched |

The third is the honest gap. "A counterexample" is not mechanically detectable,
so the gate requires a figure or a named specification carrying one (ERC-8004,
RFC 8785, CVE-2026-…). A draft that carries a counterexample in prose is
blocked and goes to Telegram marked as blocked.

That failure mode is the intended one. A counterexample nobody can state with a
figure or a named document is usually an opinion, and an opinion is the thing
this radar exists not to send.

## Approval, and how it gets here

Telegram delivers button presses to `POST /telegram/webhook` in `app/main.py`,
which answers `answerCallbackQuery` inside the request (Telegram drops callback
ids after about 30 seconds) and writes the update to `telegram_inbox`. The
consumer claims rows from that table; `getUpdates` is gone, which is what made
a second consumer possible at all — it is exclusive per bot token and
ThreadWatch already held it.

## The counter

`--counts` and the Sunday stats report drafts sent, Posten, Verwerfen, open,
posted (split by route), and handed over but not yet posted.

`post` and `posted` are deliberately two numbers. A mention is decided by the
button and posted seconds later, so both move together. A list draft is decided
by Lars opening X, which we never see — it only becomes a post once the reply
shows up on our timeline. The gap between them is drafts handed over and not
sent.

## The targets list

Private list `targets` under @moltrust, id `2101805022954557791`, 74 members;
the approved file is `~/Downloads/x-targets.md` (75 approved, one unaddable —
see `docs/x-watchlist.md`). Members skip the `MIN_IMPRESSIONS` floor: they were
curated by hand and a second numeric bar would be curating twice.

## Running it

    python agents/reply_radar.py              # scheduled run, every 2h
    python agents/reply_radar.py --dry-run    # print, send nothing
    python agents/reply_radar.py --limit 1    # fewer drafts this run
    python agents/reply_radar.py --consume    # act on decisions, every 5 min
    python agents/reply_radar.py --counts     # the tally

State in `data/reply_radar_state.json`: seen ids capped at 2000, a per-day
counter, recent claims for the repetition guard, decisions, and
`manual_pending` — the drafts the timeline check is still looking for.

## Applying for automated replies

X runs an approval for automated replies. Status and requirements:
`docs/x-automated-reply-application.md`. Nothing has been submitted.
