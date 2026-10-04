# Old prompt against new, 49 refused candidates — the new one is worse

Run 2026-10-04. Raw data: `/tmp/pc-out/compare.json`, reproducible with
`scripts/prompt_compare.py --sample 50` (seeded, so the same sample is drawn
again).

**Verdict: do not ship the new prompt.** It produces nearly twice as many
drafts and fewer that pass the gates.

| | old | new |
|---|---:|---:|
| drafts | 8 | **14** |
| of those, gate-pass | **3** | **2** |
| of those, blocked | 5 | **12** |

The criterion was fixed before the run: gate-pass decides, not the draft count.
A prompt that doubles drafts and halves deliveries has made things worse, because
a blocked draft costs a model call and a Telegram message and reaches nobody.

## Why it fails, and it is one reason

**Eleven of the twelve new blocks are `g2h` — the sources rule.**

| block reason | old | new |
|---|---:|---:|
| `g2h` every claim sourced | 2 | **11** |
| `g2f` substance floor | 1 | 1 |
| `g1x_fragment_coda` | 2 | 0 |
| `g1x_em_dash_appositive` | 1 | 0 |

The new rule tells the model to answer a technical claim about identity,
ownership, signature, authorization or provability. It does — and then it
reaches for a figure it cannot ground. "EU AI Act Article 12" recurs across the
blocked drafts, pulled from memory rather than from the post or from a page the
run fetched. Gate 2 (h) catches every one.

So the rule identified eight posts the old prompt refused and was right about
them, and then could not produce a sourced reply to any of them. The
bottleneck moved from *the drafter refuses* to *the drafter cannot ground
it* — which is progress in understanding and a regression in output.

## The part of the new rule that works

Its SKIP half does what it was written for. The old prompt drafted a reply to
this, **and the draft passed both gates**:

> @nvidia @SpaceXAI Agent Trust Gate™ has a major upgrade—and a completely new
> look. Explore the Exac…

That is a product announcement. The new rule refused it. So the headline 3 → 2
flatters the old prompt: one of its three passes is a reply to somebody's
promo, which is exactly what the radar exists not to send. Quality-adjusted the
two prompts tie at two, and the new one additionally declines an advertisement.

That is not enough to ship it. Twelve blocked drafts against five is a real
cost, and it falls on the one reader.

## What the sample also showed

**Tiers 1 to 3 produced nothing under either prompt** — 17 candidates, zero
drafts. All 22 drafts came from tier 4, the search leg. The sample is drawn
from refusals, so this says the curated list's refusals stay refused; it does
not say the list is unproductive — 12 of the 16 delivered replies in the same
fortnight came from it. But it does mean this prompt change would only ever
have moved tier 4.

## The next variant, if there is one

The failure is specific enough to fix: the new rule says *what* to answer and
not *where the figure comes from*. The variant worth testing adds the
constraint to the rule itself rather than leaving it to gate 2 —

> Answer only if the post itself states a checkable figure, or one of our pages
> carries one **on this subject**. If the claim is true and you cannot point at
> the number, say SKIP. A recalled statute number is not a source.

That is a testable change against the same 49 candidates, and the measurement
is already built. Not run, not shipped: the current result does not authorise a
second guess.

## Method, so the number can be trusted

- 50 of 690 refusals from the fourteen days to 2026-10-04, **stratified by
  tier** at the real distribution (1:3, 2:8, 3:6, 4:33), seeded.
- Target posts from `cdn.syndication.twimg.com`, the public embed endpoint —
  nothing billed against the X account. 49 of 50 still resolve.
- The new rule is **appended** to the shipped prompt, not substituted. The
  one-claim and sourcing rules are not what went wrong, and replacing them
  would make this measure two changes at once.
- Few-shot: the 16 drafts that actually cleared both gates, lifted from the
  log. Nothing written for the occasion.
- Both prompts saw identical inputs, and every draft went through gate 1 and
  gate 2 exactly as the radar runs them.
