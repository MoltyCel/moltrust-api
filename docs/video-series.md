# The video series

Clip 1 of 3 went out on 2026-10-02, native on X and Bluesky, by hand on
LinkedIn. What it is measured against, and what cannot be measured, is written
down here because both halves decide what happens to clips 2 and 3.

## The two networks are never one number

X publishes impressions. **Bluesky's AppView publishes no view count at all**,
and that is not a gap to be worked around — there is no endpoint, no undocumented
field, and no substitute metric that means the same thing. Any single "video
reach" figure would be X's number wearing both names.

So:

| | X | Bluesky |
|---|---|---|
| impressions | reported | **`null`, permanently** |
| likes, reposts, replies | reported | reported |
| follower count | reported | reported |

`null`, not `0`: a zero reads as *nobody watched*, which is a claim. The weekly
report carries Bluesky on its own line for the same reason — folded into a total
it would silently lower X's per-post figure.

Bluesky is judged on what it reports: likes, reposts, replies and follower
movement.

## The follower column starts on 2026-10-02

Both series — `kind="video"` and `kind="digest"` — carry `followers_now`, read
once per day from the single call the daily run already makes. Not a second
request: a true at-posting figure would need a read inside `herald_v3`, which is
another billed request for a number that moves by ones.

Digest rows written before 2026-10-02 have no such column. **It is not
reconstructed.** `DIGEST_FOLLOWERS_SINCE` names the date and the comparison
prints it, so the missing weeks read as missing rather than as zeros.

## The media upload rate is an assumption

X's pricing page prices post creation per request and does not price a media
upload separately. The upload is therefore booked at the write rate, $0.015,
under the source name `media-upload` so the assumption stays visible instead of
disappearing into the post count.

**It has not been verified against what X actually charged.** No response header
carries it — a probe on 2026-10-02 found only `x-rate-limit-*` — and
`/2/usage/tweets` requires OAuth 2.0 app-only while every caller here signs
OAuth 1.0a. The consumed figure is visible only in the developer portal, which
is a human read.

So the check is: before clip 2, compare the portal's credit consumption for
2026-10-02 against the booked `$0.440`. A difference is the real rate of a media
upload, and `USD_PER_POST_WRITE` gets a sibling constant rather than a guess.

## The decision on clips 2 and 3

Taken from the number on 2026-10-09, not before:

- **video impressions per post below digest** → clips 2 and 3 pause.
- **above** → one clip a week.

Per post, because one video against seven digests in a week would otherwise make
the digest look seven times better at being watched.

The first reading, 90 minutes after posting, was 2.0 against 14.8 — which says
nothing yet. A video takes longer than a text post before the timeline serves
it, and that is exactly why the decision waits for the seventh day.
