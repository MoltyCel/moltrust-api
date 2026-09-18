# Agent counting — one definition

Canonical SQL: [`app/sql/agent_counts.sql`](../app/sql/agent_counts.sql). Both
`/stats` and the 07:00/19:00 digest read that file. Nothing else may count
agents with its own predicates.

## The four numbers

| Field | Rule | 2026-09-18 |
|---|---|---|
| `registered` | `revoked_at IS NULL` | 98 |
| `active` | distinct DIDs in `usage_daily_keys` inside the window | 10 |
| `test` | registered and (`platform='test'` or `agent_type='system'`) | 15 |
| `partner_test` | registered, not test, display name matches `probe\|test\|ambassador` | 19 |

`test` and `partner_test` are subsets of `registered`. They are shown next to it,
never subtracted from it.

`active_window_days` travels with `active`. Caller identity has only been written
since 2026-09-14 (#342), so the window is `LEAST(30, age of the oldest rollup)`
and grows on its own; it reaches 30 on 2026-10-14. Printing "30d" over four days
of data would have been the same class of error this file exists to end.

## Why the two sources disagreed

`/stats` reported 99 and the digest 72 on the same table at the same moment.
`/stats` ran `COUNT(*)` with no filter; the digest dropped every row whose
`display_name` contained `probe`, `test` or `ambassador`. The 27-row gap was
entirely that heuristic: 23 names with "test", 3 with "probe", 1 with
"ambassador".

The heuristic was wrong in both directions. It hid 19 real partner registrations
— six aeoess agents named `APS-Test-Agent-*`, `SDK_Test_Agent` on moltrust,
`test-slug` on ownify — while four agents actually sitting on `platform='test'`
(`tg-5adc4810`, `tg-7fbd4749`, `tg-cbd14755`, `smoke-decouple`) sailed through
because their names happened not to contain the word. Four of the five
`agent_type='system'` agents were counted as real for the same reason.

Hence the structural rule: a partner's own test agent is a real registration of
that partner, and our test surface is identified by `platform`/`agent_type`, not
by what someone typed in a name field.

## Schema facts worth keeping

`agents` has no `status` and no `deleted_at` column, and nothing in the system
deletes an agent. `revoked_at` (one row set) is the only lifecycle flag. Any
future "deleted"-shaped filter has to start by adding the column.

`usage_daily` carries only `distinct_dids` as a per-group aggregate, so it cannot
answer "which agents were active" — summing it double-counts. `usage_daily_keys`
carries the DID and survives the 30-day `request_log` pruning, which is why
`active` reads it. Same reasoning as #353.

## Backward compatibility

`/stats` still returns `agents`, `agents_total` and `agents_external` unchanged.
They are published and read by name elsewhere. The five labelled fields
(`agents_registered`, `agents_active`, `agents_active_window_days`,
`agents_test`, `agents_partner_test`) sit alongside them.
