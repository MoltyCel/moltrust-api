# ADR — Moltbook: duo traffic, posting cadence, and disclosure

**Date:** 2026-09-23 · **Decided by:** Lars Kroehl · **Status:** accepted

## Context

`lib/moltbook_duo.py` ran a scripted exchange between our two Moltbook
accounts, `u/moltrust-agent` and `u/moltguard_v1`, by cron since 2026-07-21.
One comment a day in each direction. Its own docstring called this "organic by
construction", meaning the rate limit.

Measured on 2026-09-23:

| | |
|---|---:|
| moltguard_v1 comments written under moltrust-agent posts | 75 of 76 lifetime — 98.7 % |
| moltrust-agent comments under moltguard_v1 posts | 68 |
| complete chains: post(A) → comment(B) → reply(A) | **39**, all one direction |
| comments our posts drew in 14 days that came from ourselves | 62 of 318 — 19.5 % |

Separately, both accounts had grown to three posts a day (four on Mondays for
moltrust-agent), from a "Phase 3" cron change on 2026-07-21, while the
one-post-a-day ceiling existed only inside `agents/moltbook_poster.py` and the
other schedules ran past it.

## Decisions

**1. The duo mechanic stays off, permanently.** The cron entries are commented
out, `run_duo` refuses whenever both sides are our accounts, and every writer
skips both accounts by name and by agent id through `is_our_account()`. None of
these three is to be removed.

The reasoning is not only the platform's rules. We sell the claim that agent
behaviour can be checked before a transaction. Two of our own accounts holding
a scripted conversation is manufactured engagement, and it would be found in
our own source, next to the word "organic".

**2. One post a day per account.** From 2026-09-23 the only posting schedules
are:

| Schedule | Account |
|---|---|
| `agents/moltbook_poster.py`, 09:00 UTC | moltrust-agent |
| `agents/moltguard.py post-deep`, 13:00 UTC | moltguard_v1 |

Commented out with date and reason: `agents/ambassador.py post` (14:00),
`agents/auditor.py --mode full` (Mondays 10:00), `agents/moltguard.py post-edu`
(17:00 and 21:00). Replies (`ambassador.py run`) and the read-only scans are
unaffected.

**3. No disclosure of the duo mechanic to Moltbook.** Decided by Lars. The
comments stay where they are; nothing is deleted, and the record in the
repository stays readable.

**4. The support enquiry is dropped.** The draft asking about the 517-against-668
post count and about whether spam marks are re-evaluated is discarded and will
not be sent. `help@moltbook.com` was established as the channel
(`https://www.moltbook.com/help`, "Still stuck?") and is recorded here only so
that nobody researches it a second time.

The 517-against-668 question therefore stays unanswered, and the rule that
follows from it stands: no Moltbook post figure appears in any report.

## Consequences

The Sunday line keeps reporting the spam share and `platform=moltbook`
registrations. Reply figures are quoted without the self-traffic — 110 foreign
replies on moltrust-agent posts and 146 on moltguard_v1 posts for the fortnight
to 2026-09-23, against 166 and 152 including our own.

`scripts/moltbook_writers.py` alarms on any scheduled writer not declared, so a
schedule added back without a decision surfaces on the next daily run.
