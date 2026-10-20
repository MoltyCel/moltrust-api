# Deposit constraints: mapping to Verifiable Intent and AP2

Status: draft, implemented in `moltrust_enforce.deposit`, not released.

This document maps the five `ch.moltrust.deposit.*` constraint types onto the two mandate
formats that savings agents are likely to carry, and lists every decision the
implementation makes beyond the public draft schema.

## Sources

| Source | Pin |
| --- | --- |
| Verifiable Intent (VI), `spec/constraints.md` | `github.com/agent-intent/verifiable-intent`, commit `356c296` (Version 0.1-draft) |
| AP2, open payment mandate schema | `github.com/google-agentic-commerce/AP2`, commit `e1ea56d`, `code/sdk/schemas/ap2/open_payment_mandate.json` |
| AP2, payment mandate and specification | same commit, `docs/ap2/payment_mandate.md`, `docs/ap2/specification.md` (v0.2) |
| Draft schema and check sketch | "Deposit constraints for Verifiable Intent mandates, a draft schema" (dev.to), canonical at `moltrust.ch/blog/a-bank-run-without-the-queue.html` |

Section numbers below refer to VI `spec/constraints.md` at `356c296`. AP2 field and type
names are quoted from the files above; where AP2 has nothing comparable, the table says
"no counterpart identified".

## Rules that apply to all five types

**VI §3.1, common schema.** Every type is a JSON object with the required string field
`type`. A constraint without `type` is malformed and the check returns `forbid`.

**VI §3.2, unknown fields.** Parsers must preserve fields they do not recognize.
`parse_deposit_constraints` returns the original objects, and `check_transfer` neither
copies nor rewrites them (`tests/test_deposit.py::test_parsing_preserves_the_constraint_objects`).
A deposit constraint with extra fields is accepted. Only the fields the check reads are
validated.

**VI §3.3, constraints array.** Several constraints of the same type in one mandate are
each checked independently. All of them must pass.

**VI §6.1, Model B.** The names use reverse-DNS notation under `ch.moltrust.deposit.`
(Model B: private or organisational types). That keeps them clear of the `mandate.checkout.*`
and `mandate.payment.*` namespaces, which belong to Model A and to future registered types.
Within the `ch.moltrust.deposit.` namespace every type is known, so a misspelled type such as
`ch.moltrust.deposit.min_hold_dayz` gets `forbid` instead of being skipped.

**VI §6.2, registry.** None of the five is registered. For each type, the table names the
nearest registered type. If the types are adopted, the disclosure form we propose is
"property (full constraint)", the same form as every `mandate.payment.*` entry in §6.2.

**VI §5.4, open mandates with unknown types are rejected.** "Regardless of strictness
mode, verifiers MUST reject open mandates containing unknown constraint types." A savings
agent's mandate is an open mandate (`vct: "mandate.payment.open.1"`). Until a verifier
implements these five types, it has to reject every open mandate that carries one of them,
in PERMISSIVE mode as well as in STRICT mode, and per-constraint policy overrides do not
change that (§5.4, "Per-Constraint Policy Overrides"). **So every verifier on the path
(credential provider, network, receiving bank) must implement these types before a mandate
carrying them is accepted anywhere.** Model B names only bridge the time until the types
are registered under `mandate.payment.*` or the verifiers on a given path implement them.

**Amounts and periods.** Amounts are integers in ISO 4217 minor units with an ISO 4217
alphabetic currency code, as in VI §4.4 and §7.1. Periods are the ISO 20022 frequency codes
listed in VI §4.6 (`frequency` of `mandate.payment.recurrence`).

**AP2.** AP2 type names have no `mandate.` prefix (`payment.amount_range`,
`payment.agent_recurrence`, ...). Its extension point "Mandate Constraints"
(`specification.md`) requires "a uniquely defined `type`", "a Schema, including which
fields are selectively disclosable" and "the evaluation algorithm". This document and
`deposit.py` provide all three. However, in the published JSON schema of the AP2 open payment
mandate, `constraints.items` is a closed `oneOf` over the eight AP2 payment constraint
definitions. A verifier that validates against that schema as published rejects any of the
five types until the schema admits them. That is the AP2 equivalent of the VI §5.4 effect.

## Per type

### `ch.moltrust.deposit.min_hold_days`

```json
{ "type": "ch.moltrust.deposit.min_hold_days", "days": 90 }
```

| Aspect | Mapping |
| --- | --- |
| Fields | `days`: non-negative integer. |
| Check | Last arrival at the transfer's source (from the mandate's history). If `(exec_time - arrival) // 86400 < days`, the result is `forbid` with "holding period not elapsed". No arrival means nothing to hold, so the check passes. |
| VI §3.1 / §3.2 | `type` required; other fields preserved. |
| VI §6.1 | Model B, reverse-DNS. |
| Nearest VI §6.2 type | None. The registered types bound what is bought, from whom and how much. None of them bounds how long funds stay. |
| AP2 | No counterpart identified. |
| Enforced by | The agent. The receiving bank cannot stop money from leaving early. It sees the breach when the money leaves, and the evidence record names the agent and mandate. |

### `ch.moltrust.deposit.sweep_threshold_bp`

```json
{ "type": "ch.moltrust.deposit.sweep_threshold_bp", "min_rate_diff_bp": 50, "rate_basis": "APY" }
```

| Aspect | Mapping |
| --- | --- |
| Fields | `min_rate_diff_bp`: non-negative integer, basis points. `rate_basis`: `"APY"` or `"NOMINAL"`. |
| Check | `rate(target) - rate(source) < min_rate_diff_bp` gives `forbid` with "rate difference below threshold". Both rates are read from the AER evidence bundle, for the mandate's `rate_basis`, valid at `trigger_time`. |
| Evidence | Query `{"kind": "ch.moltrust.deposit.rate", "institution", "rate_basis"}`. The value is the rate as an integer in basis points, and negative rates are allowed. Because the evidence names its basis in the query, an observation of the wrong basis never answers the mandate's question. |
| VI §3.1 / §3.2 | `type` required; other fields preserved. |
| VI §6.1 | Model B. |
| Nearest VI §6.2 type | None. No registered type reads external market data. |
| AP2 | No counterpart identified. |

### `ch.moltrust.deposit.max_sweeps_per_period`

```json
{ "type": "ch.moltrust.deposit.max_sweeps_per_period", "max": 2, "period": "QUTR" }
```

| Aspect | Mapping |
| --- | --- |
| Fields | `max`: non-negative integer. `period`: ISO 20022 code from VI §4.6 except `INDA`. |
| Check | Earlier transfers under the mandate whose `exec_time` falls in the same calendar period (UTC) as this transfer's `exec_time`. If that count is `>= max`, the result is `forbid` with "sweep limit reached". |
| VI §3.1 / §3.2 | `type` required; other fields preserved. |
| VI §6.1 | Model B. |
| Nearest VI §6.2 type | `mandate.payment.agent_recurrence` (§4.7). Its `max_occurrences` caps the total over the whole mandate, not per period, and its `frequency` "guides timing but does not strictly enforce it". This type is a hard cap per period. |
| AP2 | Nearest: `payment.agent_recurrence` with `frequency` (`ON_DEMAND`, `DAILY`, `WEEKLY`, `BIWEEKLY`, `MONTHLY`, `QUARTERLY`, `ANNUALLY`) and `max_occurrences`. AP2 evaluates `frequency` as minimum spacing between presentations and `max_occurrences` as a total. It has no count per period, and its frequency words are not ISO 20022 codes. |

### `ch.moltrust.deposit.insured_counterparty_only`

```json
{ "type": "ch.moltrust.deposit.insured_counterparty_only",
  "schemes": ["US-FDIC", "EU-DGSD", "CH-esisuisse"],
  "max_insured_balance": { "currency": "USD", "amount": 25000000 } }
```

| Aspect | Mapping |
| --- | --- |
| Fields | `schemes`: array of distinct non-empty strings. `max_insured_balance`: `{currency, amount}` in minor units. |
| Check | The scheme that covers the target, read from evidence valid at `trigger_time`, is compared exactly against `schemes`. If it is not listed, the result is `forbid` with "target not covered". If `balance_at(target) + amount > max_insured_balance.amount`, the result is `forbid` with "above insured limit". |
| Evidence | Query `{"kind": "ch.moltrust.deposit.insurance", "institution"}`. The value is a scheme code string, and any string not in `schemes` (for example `"none"`) means not covered. |
| VI §3.1 / §3.2 | `type` required; other fields preserved. |
| VI §6.1 | Model B. |
| Nearest VI §6.2 type | `mandate.payment.allowed_payees` (§4.3) for the target restriction, but that is a static allowlist of payees, and this check is a property of the payee taken from evidence at the trigger. `mandate.payment.budget` (§4.5) for the limit, but `budget` caps cumulative spend under the mandate, and this check caps the balance held at one counterparty. |
| AP2 | Nearest for the target restriction: `payment.allowed_payees` (a static set of `Merchant` objects). No counterpart identified for deposit insurance coverage or for a per-counterparty balance limit. |

### `ch.moltrust.deposit.execution_window_hours`

```json
{ "type": "ch.moltrust.deposit.execution_window_hours", "hours": 24 }
```

| Aspect | Mapping |
| --- | --- |
| Fields | `hours`: integer `1..8760`. |
| Check | `offset = int.from_bytes(sha256(f"{mandate_id}\|{trigger_id}")[:8], "big") % (hours * 3600)`. If `abs(exec_time - (trigger_time + offset)) > TOLERANCE_SECONDS`, the result is `hold` with "outside execution window". This is the only check that holds instead of forbidding. The transfer is admissible, only not at this time, and the result's `detail` names the expected time. |
| Recomputation | `window_offset_seconds(mandate_id, trigger_id, hours)` is public. Example: `("mandate-savings-0001", "rate-change-0042", 24)` gives `25937` s, pinned in `tests/deposit-vectors.json` and in `test_offset_pinned_literal`. |
| VI §3.1 / §3.2 | `type` required; other fields preserved. |
| VI §6.1 | Model B. |
| Nearest VI §6.2 type | None. `mandate.payment.agent_recurrence` (§4.7) has a `frequency` that is not enforced, and nothing in VI spreads agents across a time window. |
| AP2 | Nearest: `payment.execution_date` (`not_before`, `not_after`), a fixed window set by the user. This type instead derives the window from the trigger and gives each agent its own offset, which a fixed window cannot do. |

## Evidence, not live lookups

Rates and coverage status are inputs to the check, never fetched by it. They arrive as AER
evidence items (DSSE envelopes over a signed statement with `source_id`, `query`, `value`,
`valid_from`, `valid_until`, `nonce`) in a bundle that `build_bundle` binds to the mandate
(`mandate_ref`) and to the planned transfer (`transaction_ref`). `check_transfer` requires:

- a structurally valid bundle (`bundle_problem`) whenever the mandate carries a sweep
  threshold or an insured-counterparty constraint, and whenever a bundle is passed at all;
- `mandate_ref` and `transaction_ref` binding exactly the mandate and transfer being checked;
- `decision_timestamp == trigger_time`, so the inputs are judged as they stood at the trigger;
- an item for each query it needs, whose window covers `trigger_time`.

Signatures are not checked in the kernel, just as `f_ext` does not check them.
`verify_deposit_evidence(bundle, trust_list)` runs the V2 (signature against a pinned trust
list) and V3 (freshness) checks of `verify_record` over the same bundle. A rate signed by the
wrong source passes the kernel and fails there
(`test_forged_rate_fails_signature_check_but_kernel_does_not_verify_signatures`). Live data
sources (FDIC BankFind, EU deposit guarantee schemes) are out of scope here. They become AER
sources that sign statements in this format.

## Decisions beyond the draft schema

1. **Check order and first failure decides.** The order is the order of the sketch:
   holding period, rate, sweep count, coverage, insured limit, execution window. Within a
   type, constraints are checked in mandate order. `hold` is returned only if every other
   check passed.
2. **Sketch reasons kept verbatim.** The six substantive outcomes use the sketch's reason
   strings. Malformed input and evidence problems use their own reasons, always with
   `forbid`.
3. **Malformed is `forbid`, never an exception.** Malformed covers non-integer or negative
   amounts, days, thresholds and counts, booleans in integer fields, an unknown `rate_basis`,
   a currency that is not three upper-case ASCII letters, and an unknown period code. Integers
   are bounded by `MAX_ABS_INT` (10^15), as in the rest of the SDK.
4. **`TOLERANCE_SECONDS = 300`.** This matches the gate's clock-skew allowance. A tolerance
   of a few minutes leaves the spread of a window of one hour or more intact.
5. **`INDA` refused for counting.** VI §4.6 lists it, but "intraday" has no defined length
   and cannot delimit a count. `ON_DEMAND` (VI §4.7 only) is not a period and is refused too.
6. **Periods are calendar periods in UTC, not rolling windows.** `WEEK` is the ISO week
   (Monday 00:00 UTC). `TOWK` uses 14-day blocks from Monday 1969-12-29. `TWMN` splits the
   month into days 1–15 and 16–end. `TOMN`, `QUTR`, `FOMN` and `SEMI` start in January.
   `TYEA` starts in even years. `period_key` is public.
7. **`rate_basis` values are `APY` and `NOMINAL`.** These are the two bases the draft
   discusses. The evidence names the basis it observed in its query.
8. **Rates are integers in basis points.** Negative rates are allowed. The comparison is
   integer arithmetic only, with no floats.
9. **Same currency required.** If the transfer amount, an opening balance or any history
   amount touching the target is in a different currency from `max_insured_balance`, the
   result is `forbid`. There is no conversion.
10. **Balance at the target** = opening balance (optional, per institution, default 0)
    + arrivals − departures in the mandate's history. A negative result means the history is
    inconsistent, and the result is `forbid`.
11. **Holding period counts arrivals under this mandate only.** Opening balances have no
    arrival time and are not held. Elapsed days are whole days, `(exec - arrival) // 86400`,
    so exactly 90 days passes and 89 days and 23:59:59 does not.
12. **Empty `schemes` is unsatisfiable.** It is well-formed, but no target matches it. This
    follows the empty-allowlist principle VI §5 sets for its own allowlists.
13. **History must not contain transfers after the planned `exec_time`.** If it does, the
    result is `forbid`. The history is the caller's ledger of earlier transfers under the
    same mandate, and the caller scopes it.
14. **Same institution as source and target is malformed.** Such a transfer is not an
    inter-bank sweep.
15. **`mandate_id` must not contain `|`.** Otherwise `"a|b" + "c"` and `"a" + "b|c"` would
    give the same offset. `mandate_id` is an opaque string. We recommend the base64url
    SHA-256 of the L2 payment mandate disclosure, built the way VI §4.8 builds
    `conditional_transaction_id`. The transfer carries the same `mandate_id`, and a mismatch
    gives `forbid`.
16. **Bundle `decision_timestamp` equals `trigger_time`.** The evidence states the facts at
    the trigger, as the draft requires.
17. **Time format.** RFC 3339 UTC to the second (`YYYY-MM-DDTHH:MM:SSZ`), read by the same
    strict parser as AER. Offsets other than `Z`, fractions and leap seconds give `forbid`.
18. **Bounds.** `hours` must be at most 8760, `schemes` may hold at most 64 entries, history
    at most 10,000 transfers, at most 64 deposit constraints per mandate, and identifiers at
    most 512 characters.

## Open questions

- **Sub-basis-point rates.** Rates published with more precision than one basis point
  (for example 4.125 %) have to be rounded by the source. A scaled rate (like
  `rate_scale` in `evidence_scaled_range`) would remove that, at the cost of a further
  field in the evidence format.
- **Rolling periods.** Some banks may expect "90 days" rather than "calendar quarter". The
  draft fixes ISO 20022 codes, which read most naturally as calendar periods. A rolling
  variant would need a separate field.
- **Scheme identifiers.** `US-FDIC`, `EU-DGSD` and `CH-esisuisse` come from the draft.
  There is no registry for them yet, and EU coverage is per member-state scheme, so
  `EU-DGSD` may have to split per scheme once real sources sign coverage statements.
- **Per-depositor vs per-account limits.** Statutory limits apply per depositor and
  institution. The check sees only balances under this mandate. Funds the saver holds at
  the same bank outside the mandate are invisible unless the caller supplies them as an
  opening balance.
- **Registration.** As long as the types are not registered under `mandate.payment.*`
  (VI) and not admitted by the AP2 schema, VI §5.4 and the closed AP2 `oneOf` make every
  mandate carrying them unusable at any verifier that has not implemented them.
