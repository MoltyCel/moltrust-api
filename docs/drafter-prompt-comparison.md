# Old prompt against new against variant 3 — and a correction

Run 2026-10-04, 49 refused candidates, seeded sample, all three prompts over
identical inputs. Raw data `~/pending/prompt_compare-2026-10-04.json` (first
run) and `/tmp/pc-out/compare3.json` (this one), reproducible with
`scripts/prompt_compare.py --sample 50`.

## The correction first, because it inverts yesterday's answer

The first run of this comparison passed the gate **only the target post** as a
source. `agents/reply_radar.py` passes three things: the KB pages the draft
cited, the URLs it cited that the run then fetched, and the post itself.

So gate 2 (h) blocked eleven of twelve drafts for citing pages that were never
in the dictionary — and the conclusion I drew, *the new prompt is worse*, was a
property of my harness. The harness now replicates production line for line,
and the result reverses:

| | old | new | variant 3 |
|---|---:|---:|---:|
| drafts | 9 | 13 | 14 |
| **gate-pass** | **3** | **13** | **11** |
| blocked | 6 | **0** | 3 |
| sources given per draft | 3.0 | 2.6 | 2.7 |

Yesterday's table said 3 → 2. The honest one says **3 → 13**.

## Why the new rule wins

The old prompt's six blocks are mostly `g2f`, the substance floor — five drafts
with no figure at all. The new rule tells the model to answer a technical claim
about identity, ownership, signature, authorization or provability, and such
posts *have* figures in them; the drafts that follow carry one and cite a page
that the run fetches. **Thirteen of thirteen pass.**

## Variant 3 is not an improvement on it

The source rule and the citation index did what they were designed to do: **not
one `g2h` block in either new or v3.** But the design was answering a problem
that turned out to be my measurement, so the extra strictness only costs:

- 11 passes against 13
- its 3 blocks are gate **1** faults, not sourcing — `g1x_em_dash_appositive`
  twice and `g1x_superlative` once. The index gives it more figures to work
  with and the prose gets more ornate.
- one post it refused that the new rule answered and passed

**Recommendation: ship the new rule, not variant 3.** The index is worth
keeping as a tool — `scripts/citation_index.py`, 62 posts, 10 spec pages, 203
figures quoted verbatim — because the gap it documents is real: the drafter
sees four site pages plus the six newest posts at 1200 characters each, out of
sixty-nine. It just is not what was blocking drafts.

## What all three agree on

**Tiers 1 to 3 produced zero passes under every prompt.** 17 candidates, none.
All 27 passing drafts across the three variants came from tier 4, the search
leg. The sample is drawn from refusals, so this says the list's refusals stay
refused — it does not say the list is unproductive, since 12 of the 16
delivered replies that fortnight came from it.

## One decision this forces

The reply branch is being measured until 2026-10-18 against a fixed threshold
(median ≥ 30 impressions per reply, ≥ 1 profile click per 2 replies). Shipping
a prompt that more than quadruples the pass rate changes the drafter the
measurement is measuring.

It argues for shipping **now** rather than after: the window has produced no
replies at all so far, so there is nothing to contaminate — and without a
drafter that drafts, 18.10 would measure an empty set and discontinue the
branch on no evidence. That is a call for Lars, not a conclusion of this run.

## Method

- 50 of 690 refusals from the fourteen days to 2026-10-04, stratified by tier
  at the real distribution (1:3, 2:8, 3:6, 4:33), seeded so the sample redraws.
- Target posts from `cdn.syndication.twimg.com` — nothing billed to the X
  account. 49 of 50 still resolve.
- Each variant **appends** to the shipped prompt rather than replacing it, so
  the comparison measures one change at a time.
- Few-shot: the 16 drafts that actually cleared both gates, lifted from the log.
- Every draft gated exactly as the radar gates, sources included.
