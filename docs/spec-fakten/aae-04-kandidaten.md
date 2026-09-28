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
