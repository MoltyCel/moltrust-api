# AAE -04 Candidates — Input Log

Candidate material for a future `draft-kroehl-agentic-trust-aae-04`. One entry per
input, newest appended at the bottom. Each entry carries its date, source, and a
caveat line saying what the source does and does not establish.

This file is an **input log, not a spec fact file**. Nothing here is verified against
a published draft unless the entry says so. For citable AAE facts see
[`aae.md`](aae.md); memory holds only a one-line pointer to this file.

---

## 2026-09-23 — Consumption vs. Issuance-Eligibility (Thread-Gate)

**Source:** IRIS/MAPACHE, Patiele. E-Mail exchange 22./23.09.2026.
**Nature:** production log of an external operator running an outbound agent under
human campaign authorization. Operator report, not a verified AAE implementation.

### Finding

`single_use` operates at the **envelope** level. It is keyed on the Verifiable
Credential id (§2.4, §5 Step 5) and stops a spent envelope from being presented a
second time, enforced relying-party side through an atomic shared consumed-id store.

At the **enforcement** level there is no consumption, replay or budget bound at all.
§7.1 states it outright: "Section 2.5 provides no replay protection, no cumulative
budget across presentations, and no rate limit", and the nonce challenge of §5 step 4
"provides exactly this for the envelope […] Nothing equivalent exists at the
enforcement layer in this revision."

What it does not cover: whether a thread is still eligible to receive a **new**
mandate at all. A standing authorization can keep issuing a fresh `single_use`
mandate for the same thread after a human has replied, and every one of those
mandates is individually well-formed.

### Consequence

The human-reply gate has to bite **at issuance**, not at evaluation. The issuer
holds the thread state. An envelope cannot carry that state without breaking
recompute determinism (§2.5.3), because the state changes after the envelope is
built.

### Where AAE would contribute

The auditable binding. A mandate carries a thread identifier plus a reference to
the standing authorization it descends from. The issuer keys its state machine on
that pair, and the recomputable record then shows, per send, which thread it
belonged to, which authorization it came from, and whether a human decision stood
behind it.

### External state model

From the operator's own runbook, recorded as-is — **not** a verified AAE
implementation:

```
NEW -> OUTBOUND_SENT -> HUMAN_REPLY -> HUMAN_DECISION_REQUIRED
```

### Source caveat

The operator corrected themselves during the exchange: their single-flight
execution-and-recording is not a shared atomic consumption store. Do not cite this
entry as an implementation proof for `single_use`.

### Related

`stale-mandate` (Jiayu Li) — mandate freshness is not the same property as
validity. Both belong to the same -04 axis: freshness and state across
presentations, the open point at §9.

---

## 2026-09-28 — String prefix + presence/absence constraint types

**Source:** aae-02 §2.5.1, verified against sha256
`08e202ecc06d245b287e60591098c22d8af480fc88cb455defc9e7612a473b4c` (28.09.2026).
**Nature:** remainder of the -02 constraint-language candidate in [`aae.md`](aae.md),
excluded from -02.

String prefix constraints and presence/absence constraint types — deliberately excluded
in -02 (§2.5.1: exact/enum/range only, "no prefix matching"). -04 candidate.

### Source caveat

Records what -02 leaves out, not a decision to add it. §2.5.1 excludes prefix matching
on purpose; any -04 proposal has to answer why.

---

## 2026-09-28 — WHO axis

**Source:** -02 candidate list in [`aae.md`](aae.md) (2026-08-11); term search against
aae-02, sha256 `08e202ecc06d245b287e60591098c22d8af480fc88cb455defc9e7612a473b4c`
(28.09.2026).
**Nature:** carried-over candidate, not shipped in -02.

WHO axis — not in -02 (the term does not occur in the -02 text). Possible relation to §9
"Principal identity across identifier spaces", but UNCONFIRMED (class c, not verified
against -02) — check against the draft text before use. -04 candidate.

### Source caveat

Only the absence of the term is verified. The relation to §9 is an inference.

---

## 2026-10-05 — Grant attenuation per hop: kernel aligned, purpose stays informative

**Source:** aae-02 §5 step 9 ("Grant attenuation"), §2.2 (`mandate.purpose`), §3 closing
rule, verified against sha256
`08e202ecc06d245b287e60591098c22d8af480fc88cb455defc9e7612a473b4c` (05.10.2026). Kernel
change: moltrust-api #598, merged and deployed as `f1e327b`. Vectors: aae-conformance-vectors
#14 (draft, enforce 27–29, prepared as 1.5.0, untagged).
**Nature:** implementation finding plus three -04 drafting points. Nothing here is in a
published revision.

### (a) Kernel deviation from step 9 — fixed

Up to `15fbc5f` the chain walk (`app/enforcement/delegation_chain.py`) compared actions,
constraints and validity only. Grants were not attenuated, so a delegated mandate could
widen a grant (larger `range`, wider `enum`, `hold` to `allow`) while naming only actions
its parent allows. The verdict trace carried no chain entry. Since #598 the same rule runs
in two places: the VC-level walk rejects the hop, and `enforce_check(..., ancestors=[…])`
records each hop as a `grant_attenuation` predicate in the digested core, with the child
and parent mandate digests as `value` and `bound`. Without `ancestors` the core is unchanged,
and vectors 01–26 keep their digests.

### (b) `mandate.purpose` is informative

-02 defines it as "RECOMMENDED. Human-readable description of the authorization context.
Used for audit logs." (§2.2). The kernel does not evaluate it, and does not read the §3
closing rule ("cannot determine … MUST be rejected") as covering a free-text field. -04
should say so in one sentence at §2.2, so that a verifier does not reject a chain over a
reworded audit string.

### (c) Narrowing a purpose with the means -02 already has

A purpose that has to narrow per hop is an `enum` constraint on a transaction field inside
a grant, for example `{"type": "enum", "field": "purpose_code", "values": ["travel.booking"]}`.
Grant attenuation then requires the child's set to be a subset of the parent's, and a
missing constraint is a widening. Enforce vector 29 shows the DENY. -04 could carry this as
a non-normative example under §5 step 9. A dedicated purpose field is not needed for it.

### (d) Gaps in the literal coverage test

The kernel closes these from the general clause of step 9 ("the child MUST be no broader
than its parent"). -04 should state them in the text:

1. A parent `forbid` outranks every grant for its action (§2.2.3 step 5). A child that drops
   the forbid and keeps an `allow` has every grant "covered" and still permits what the
   parent denies.
2. The parent stops at its first grant whose constraints hold. A child `allow` covered by a
   later parent `allow` is broader when an earlier parent `hold` for the same action can
   hold on the same transactions. It is safe only if the two are disjoint.
3. A child with grants under a parent that has none: the literal text rejects it, because
   no parent grant covers the child's. The kernel follows the text. Adding grants under an
   actions-only parent narrows, so -04 should decide whether to allow it.
4. Implication across constraint types (`exact` inside an `enum`) is not defined. The kernel
   compares what each constraint admits, as sets.

### Source caveat

(a) is verified against the code and the deployed SHA. (b)–(d) are drafting proposals, not
decisions. The reference verifier and the kernel attenuation share an author, so for vectors
27–29 the second-implementation bar of -02 §10 is not met.

---

## Console notes (2026-09-23, not part of the entry)

- The section references in this entry (§2.4, §2.5.3, §5 Step 5, §7.1, §9) are
  **verified against aae-02, sha256 `08e202ecc06d245b287e60591098c22d8af480fc88cb455defc9e7612a473b4c`
  (checked 2026-09-23)**. §2.4 is the VALIDITY block and carries `single_use`;
  §5 step 5 is the *Single-use check*; §2.5.3 is *Recompute Determinism*;
  §7.1 is *Replay Attacks*; §9 is *Future Work*, where the third item
  ("Freshness and condition liveness") names the condition this entry describes.
- The finding itself is unchanged by that check. What moved is the layer the
  gap sits on: the original wording placed `single_use` at the action level,
  which understates the gap — the enforcement layer has no such control at all.
- [`aae.md`](aae.md) still names **-01** as the current revision. Memory records
  -02 as published on 2026-09-06. That file is stale on this point and needs its
  own pass before anything here is cited publicly.
  **Resolved 2026-09-28:** `aae.md` now names -02 as current, verified against
  `08e202ec` (branch `docs/aae-md-rev02`).
