# Reply radar — closing record, October 2026

The reply branch of the reply radar ends on 18.10.2026 (Lars, decision of
12.10.2026, BW1–BW4; see `docs/reply-radar.md`). This file keeps the series
the decision rests on, so the decision stays evidenced after the branch and
its state are gone.

## The series: 9 replies, 29.09–12.10.2026

Fourteen days, 29.09 to 12.10.2026. Every reply in the series was posted by
hand from a radar draft (`route: manual`). Figures are the newest measurement
per reply, taken 2026-10-12 18:00 UTC by `scripts/digest_metrics.py`.

| posted (UTC) | reply id | impressions | likes | profile clicks |
|---|---|---:|---:|---:|
| 2026-09-29 13:08 | 2104921186408808682 | 19 | 0 | 0 |
| 2026-09-29 13:12 | 2104922323916878318 | 6 | 2 | 0 |
| 2026-10-02 07:00 | 2105915720366170602 | 10 | 0 | 0 |
| 2026-10-02 07:50 | 2105928419162689671 | 66 | 0 | 2 |
| 2026-10-02 10:59 | 2105975879981482399 | 65 | 1 | 4 |
| 2026-10-05 06:28 | 2106994977662546081 | 56 | 0 | 0 |
| 2026-10-05 11:42 | 2107073962404679753 | 14 | 0 | 1 |
| 2026-10-05 11:43 | 2107074122698158276 | 5 | 0 | 0 |
| 2026-10-08 09:59 | 2108135271959240975 | 59 | 0 | 0 |
| **9 replies** | | **300** | **3** | **7** |

Median 19 impressions per reply over the nine, 0.78 profile clicks per reply.

## Threshold and verdict

The threshold was fixed on 2026-10-04, before the window it judges
(`docs/reply-radar.md`, "The threshold, fixed before the measurement"). The
branch continues only if both hold:

| | threshold |
|---|---|
| median impressions per reply | ≥ 30 |
| profile clicks per reply | ≥ 0.5 |

The verdict window starts on 04.10 and holds the four replies of 05.10 and
08.10. `scripts/reply_impact.py`, run on 2026-10-12 against the 18:00
measurement:

| | measured | threshold | |
|---|---:|---:|---|
| median impressions per reply | 35 | ≥ 30 | met |
| profile clicks per reply | 0.25 (1 of 4) | ≥ 0.5 | not met |

**Verdict: discontinue.** One condition failed, and the rule needs both.

Read over all nine replies of the fourteen days, the two conditions swap: the
median is 19 (not met) and the click ratio 0.78 (met). The rule still needs
both, so the answer is the same.

## Earlier replies, 22.09–24.09.2026 (not in the series)

Eight replies, also posted by hand from radar drafts (`route: manual`),
before the fourteen days of the series. Newest measurement per reply. The ones from 22. and 23.09 were measured before
`profile_clicks` was recorded; their click count is unknown, not zero.

| posted (UTC) | reply id | impressions | likes | profile clicks | measured |
|---|---|---:|---:|---:|---|
| 2026-09-22 20:18 | 2102492639774032129 | 9 | 0 | — | 2026-09-23 18:00 |
| 2026-09-22 20:18 | 2102492794690355215 | 7 | 0 | — | 2026-09-23 18:00 |
| 2026-09-23 03:03 | 2102594614364434859 | 54 | 0 | — | 2026-09-23 18:00 |
| 2026-09-23 04:31 | 2102616725325857270 | 12 | 0 | — | 2026-09-23 18:00 |
| 2026-09-23 04:31 | 2102616890661364061 | 1 | 0 | — | 2026-09-23 18:00 |
| 2026-09-23 04:32 | 2102617119754014856 | 23 | 0 | — | 2026-09-23 18:00 |
| 2026-09-24 09:05 | 2103048243643093239 | 10 | 1 | 1 | 2026-10-08 18:00 |
| 2026-09-24 11:54 | 2103090751051559384 | 7 | 0 | 0 | 2026-10-08 18:00 |
| **8 replies** | | **123** | **1** | **1** (of 2 measured) | |

## Posted after the series closed

Three more replies went out after the 2026-10-12 18:00 measurement that
closes the series. All three were posted by hand from radar drafts
(`route: manual`, source `list`); each decision is recorded in
`data/reply_radar_state.json`. None is counted in the series or in the
verdict.

| posted (UTC) | reply id | answering |
|---|---|---|
| 2026-10-12 18:46 | 2109717267998978270 | 2109653075204141313 |
| 2026-10-15 03:17 | 2110570811417624635 | 2110294662120661076 |
| 2026-10-15 20:38 | 2110832776870015361 | 2110752342907781539 |

## End state, 18.10.2026

The decision run: `scripts/reply_impact.py --send`, 2026-10-18 07:30 UTC,
window from 2026-10-04, against the newest measurement per reply (the
2026-10-17 18:00 run of `scripts/digest_metrics.py`). Its window holds six
replies: the four of 05.10 and 08.10 and the two of 15.10.

| posted | reply id | impressions | likes | profile clicks |
|---|---|---:|---:|---:|
| 2026-10-05 06:28 | 2106994977662546081 | 56 | 0 | 0 |
| 2026-10-05 11:42 | 2107073962404679753 | 14 | 0 | 1 |
| 2026-10-05 11:43 | 2107074122698158276 | 5 | 0 | 0 |
| 2026-10-08 09:59 | 2108135271959240975 | 59 | 0 | 0 |
| 2026-10-15 03:17 | 2110570811417624635 | 2 | 0 | 0 |
| 2026-10-15 20:38 | 2110832776870015361 | 5 | 0 | 0 |
| **6 replies** | | **141** | **0** | **1** |

| | measured | threshold | |
|---|---:|---:|---|
| median impressions per reply | 9.5 | ≥ 30 | not met |
| profile clicks per reply | 0.17 (1 of 6) | ≥ 0.5 | not met |

The digest in the same window: 14 posts, median 9 impressions per post.

**Verdict: discontinue.** Both conditions failed. The reply branch is off
from 18.10.2026: the consumer runs without `REPLY_RADAR_ARMED` and the reply
budget is zero. The 06:05 reading list keeps running; it drafts nothing and
does not write to X.

Two notes on the figures. The run's summary line prints the median as 10,
rounded; the threshold line of the same run computes 9.5, the mean of 5 and
14. The reply of 12.10 18:46 (2109717267998978270) has no row in
`data/digest_metrics.jsonl`, so the run does not count it.

## Followers

@MolTrust had 35 followers on 2026-10-04 18:00 UTC and 36 on 2026-10-12 18:00
UTC, read from the digest rows of the same file. These are net figures for the
account: the replies, the daily digest and the blog syndication share one
follower count, so a change cannot be attributed to the replies. The
threshold leaves the follower delta out for that reason.

## Raw rows

`docs/evidence/reply-radar-series-2026-10-12.jsonl` is a byte-for-byte copy of
every `"kind": "reply"` row of `data/digest_metrics.jsonl` as of the
2026-10-12T18:00 measurement: 137 rows, all measurements of all 17 replies
above, the newest `measured_at` 2026-10-12T18:00:04Z. Read from the server
with `cat` and filtered with `grep '"kind": "reply"'`, nothing rewritten. The
rows hold ids, timestamps and counts; no credential or personal data.

    sha256  82f06cc517377229a4ddcf7e73de44e2b3fce0d78fcda476baf9c667f5c95660
            docs/evidence/reply-radar-series-2026-10-12.jsonl

Check with `shasum -a 256 docs/evidence/reply-radar-series-2026-10-12.jsonl`.

## Closed

This file is closed as of 18.10.2026. No further additions (Lars, CJ4).
