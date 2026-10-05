# AAE Spec Facts — Verified Reference

**Source:** draft-kroehl-agentic-trust-aae-00, IETF Datatracker, uploaded 2026-05-21
**Verified:** 2026-06-20 (Console verification against live Datatracker fetch)
**SHA-256 of draft text:** `2847f4daf3f0a088afb1bd1bd3b9c001947a9905426753673d9da8275a038b0a`
(source: `https://www.ietf.org/archive/id/draft-kroehl-agentic-trust-aae-00.txt`, 48500 bytes)
**Status:** -00, -01 and -02 public. **-02 is the current revision** (posted 2026-09-06,
expires 2027-03-10) — cite it, not -00/-01.

### -02 (current, published)

**Source:** draft-kroehl-agentic-trust-aae-02, IETF Datatracker, uploaded 2026-09-06T15:30Z
**Verified:** 2026-09-28 (Datatracker API `rev 02`; live archive fetch hash-identical to the local copy)
**SHA-256 of draft text:** `08e202ecc06d245b287e60591098c22d8af480fc88cb455defc9e7612a473b4c`
(source: `https://www.ietf.org/archive/id/draft-kroehl-agentic-trust-aae-02.txt`, 131860 bytes)
**Expires:** 2027-03-10 · 54 pages · stream: None (Individual Submission) · Intended status: Informational

Shipped in -02 (from the -02 candidates below; verified against 08e202ec, 28.09.2026):

- `action_binding` — §2.2.2 Action Binding; REQUIRED on every grant (§2.2.1). Digest
  `"sha256:" || LOWERHEX(SHA-256(TAG || JCS(A)))`, TAG = `aae:enforce-action:v1` + 0x00.
- Ratification / retroactive status change by append — §6 Verdicts and Ratification
  (§6.2 immutable records, chaining via `prev_core_digest`; §6.3 Ratification). The
  Witness-not-Ruler guard is normative in §6.4: authority MUST derive from the mandate
  (`principal` or `ratification_authorities`), key taken from the mandate, never from the
  proof; operator, registry or central supervising role MUST NOT be recognized.
- Closed constraint language + per-predicate trace — §2.5 Enforcement Constraint Language
  (§2.5.1 Closed Type Set, §2.5.2 Predicate Trace, §2.5.3 Recompute Determinism). Shipped
  narrower than the candidate: exactly three types (`exact`, `enum`, `range`); §2.5.1 states
  "no prefix matching"; no presence/absence constraint type. One-line explainability and
  deterministic evaluation are MUST.
- §6.5 SHOULD→MUST backlog item — now §7.5 Delegation Revocation. Issuer cascade: MUST.
  Relying party that already knows a parent is revoked: MUST treat descendants as invalid.
  Relying-party revocation *discovery* stays deferred (§5.2, §9).

### -01 (superseded)

**Source:** draft-kroehl-agentic-trust-aae-01, IETF Datatracker, uploaded 2026-08-11
**Verified:** 2026-08-11 (live fetch of the published text)
**SHA-256 of draft text:** `efc62096eedc4172e024d36269ac85125448c1799ab4df8749e03cdb9c7f9a2a`
(source: `https://www.ietf.org/archive/id/draft-kroehl-agentic-trust-aae-01.txt`, 51414 bytes)
**SHA-256 of XML:** `281ba7cd039f0a72c61fe909f96220d1dcfad91d05617b98d407bce4975f479a` (68534 bytes)
**Expires:** 2027-02-12 · 23 pages · stream: None (Individual Submission)
**Toolchain:** kramdown-rfc 1.7.39 + xml2rfc 3.31.0 — 1.7.39 is the version that
produced -00, so -01 is rendered by the same generator. The published .txt and .xml
are byte-identical to the locally built ones; the Datatracker took the XML unchanged.
**Carries:** three editorial precisions only - new §5.1 Verification Dependencies,
a proof-of-possession clarification at §5 step 4, and the note that "offline" is
inaccurate without qualification for an AAE with a delegation chain or
`revocation_check`. No new field, no new verification step, no new normative
requirement. -00 remains the historical reference above.
**NOT carried into -01:** the §6.5 SHOULD→MUST backlog item below; `action_binding`;
freshness. Those were -02 candidates; `action_binding` and §6.5 shipped in -02 (see
above).

Not shipped in -02 (verified against 08e202ec, 28.09.2026):

- Freshness — not normative; -02 §9 Future Work lists "Freshness and condition liveness"
  (Section 2.4), normative once cross-presentation state is deployed and reconciled with
  §2.5.3 recompute determinism.
- Selective hash commitments — see the entry below.

-02 candidates (added 15.08.2026, adopted from published prior art — Mandato, Racioppi, arXiv 2026).
The first two entries shipped in -02 (see above) and are kept as provenance record:

- Ratification / retroactive status change by append. A verdict record stays immutable;
  a signed, anchored follow-on record references it and re-sets its status (e.g. an open
  UNKNOWN-within-window verdict later ratified, or closed as breach). History corrected
  by append, never by edit.
  GUARD (Witness-not-Ruler): the ratifying authority MUST derive from the anchored mandate
  (issuing principal, or a role named in the mandate) and its signature MUST be independently
  recomputable — never a MolTrust instance or a central "supervising role".
  Serves: the (a) inactivity / NOT_EVALUATED open point (verdict post-processing without
  forcing premature ADHERENT/BREACHED).
  Provenance: Mandato ratification (mandato con rappresentanza) — published, not a novelty
  candidate, free to adopt.

- Constraint language as a normative property + per-predicate trace. CONSTRAINTS MUST be a
  closed, non-Turing-complete language (equality, enumerated sets, numeric ranges, string
  prefixes on validated identifier fields, structural presence/absence); each predicate
  explainable in one line; evaluation deterministic and constant-time. Add a per-predicate
  evaluation trace to verdict output — not only ADHERENT/BREACHED but predicate-by-predicate
  result + the value and the bound.
  Rationale: recompute determinism (two independent verifiers MUST reach the identical result)
  requires the constraint language not to be a program. Makes an implicit dependency explicit
  and enriches recompute output — the "what was recomputed, line by line" that distinguishes
  us from log-integrity-only approaches.
  GUARD: none (no anchor introduced).
  Provenance: Mandato closed-constraint-language + decidability requirement.

- Selective hash commitments of sensitive argument values in anchored records. Commit-now,
  disclose-later: anchor that an agent stayed within a value/whitelist constraint without
  writing the plaintext value on-chain (GDPR data-minimisation; underpins the "Standard-Config
  no PII" / "supports Article 12 logging" claim).
  GUARD: the commitment MUST bind to the anchored chain reality, not to caller-supplied input
  (same separation as today's offline empty-input guard: a commitment over a caller claim is
  not a commitment over recomputable reality).
  Status: implementation principle for when the live chain reader is wired — NOT draft-normative
  in -02 (verified against 08e202ec, 28.09.2026: §7.7 "This document defines no such
  mechanism"; §9 Future Work, normative once a reader of the anchoring ledger is wired).
  Provenance: Mandato selective hash commitments.

## Section Map

Numbering of -02 (verified against 08e202ec, 28.09.2026, from the draft's Table of Contents).
-00/-01 numbered Security Considerations §6, Privacy §7, IANA §8, References §9 — a
-00/-01 citation of §6.x is §7.x in -02.

- §1 Introduction
  - §1.1 Regulatory Convergence
  - §1.2 Terminology
- §2 The Agent Authorization Envelope
  - §2.1 Structure
  - §2.2 The MANDATE Block
    - §2.2.1 Grants
    - §2.2.2 Action Binding
    - §2.2.3 Grant Evaluation
  - §2.3 The CONSTRAINTS Block
  - §2.4 The VALIDITY Block — `not_before` (REQUIRED), `not_after` (REQUIRED), `revocation_check` (OPTIONAL), `single_use` (OPTIONAL, Boolean, default false)
  - §2.5 Enforcement Constraint Language
    - §2.5.1 Closed Type Set
    - §2.5.2 Predicate Trace
    - §2.5.3 Recompute Determinism
- §3 Delegation Chains (mechanics, structure, `delegator_aae_hash` — OPTIONAL)
- §4 Action Vocabulary Schemas
- §5 Verification Algorithm (9 steps; step 9 = delegation chain walk)
  - §5.1 Verification Dependencies — added in -01; DID documents, ancestor AAEs,
    revocation endpoint named as retrieval dependencies
  - §5.2 Deferred Verification Capabilities
- §6 Verdicts and Ratification
  - §6.1 Verdict Vocabulary
  - §6.2 Verdict Records and Chaining
  - §6.3 Ratification
  - §6.4 Authority (Witness, not Ruler)
- §7 Security Considerations
  - §7.1 Replay Attacks
  - §7.2 Constraint Bypass
  - §7.3 Key Compromise
  - §7.4 Delegation Amplification
  - §7.5 Delegation Revocation (-00/-01 §6.5 SHOULD; -02 splits issuer MUST / relying-party
    consequence MUST / discovery deferred, see "Shipped in -02")
  - §7.6 Clock Skew and Time Synchronization
  - §7.7 On-Chain Anchoring
  - §7.8 Predicate Trace Disclosure
  - §7.9 Resource Exhaustion and Input Limits
- §8 Privacy Considerations
- §9 Future Work
- §10 Test Vectors
- §11 IANA Considerations
  - §11.1 Media Type Registration
  - §11.2 No Further IANA Actions
- §12 References
  - §12.1 Normative References
  - §12.2 Informative References

## Hash Mechanics

-02 defines several digests under two constructions (verified against 08e202ec,
28.09.2026). -00/-01 defined only `delegator_aae_hash`.

§2.2.2, verbatim: "This document now defines two digest constructions, and they
deliberately differ. The delegator_aae_hash of Section 3 takes the exact ASCII octets of
a parent AAE's JWS compact serialization as retrieved — no domain tag, no
canonicalization, encoded base64url [RFC4648]. The action_binding defined here takes a
JSON value, canonicalizes it under [RFC8785], prefixes a domain tag, and encodes the
result as lowercase hexadecimal. [...] Neither construction substitutes for the other,
and a value produced by one MUST NOT be compared against a value produced by the other."

**Construction 1 — `delegator_aae_hash` (§3, OPTIONAL).** §3, verbatim: "If present, the
value MUST have the form sha-256:<base64url-encoded-digest>. The digest input MUST be the
exact ASCII octet sequence of the parent AAE JWS compact serialization as retrieved,
without additional whitespace, decoding, re-encoding, or JSON canonicalization. SHA-256
is as defined in [RFC6234]. If the computed digest does not match the value in
delegator_aae_hash, the relying party MUST reject the delegated AAE."

**Construction 2 — domain-tagged JCS digests.** §2.2.2, verbatim: "JCS is the JSON
Canonicalization Scheme of [RFC8785], and SHA-256 is as specified in [RFC6234]. The same
two apply to every digest defined in this document."

| Digest | Section | Domain tag (verbatim, each followed by a single 0x00 octet) |
|---|---|---|
| `action_binding` | §2.2.2 | `aae:enforce-action:v1` |
| `mandate_digest` | §2.5.3 | `aae:enforce-mandate:v1` |
| `transaction_digest` | §2.5.3 | `aae:enforce-transaction:v1` |
| verdict core digest | §2.5.3 | `aae:enforce-core:v1` |
| ratification core digest | §6.3 | `aae:enforce-ratify-core:v1` |

- §2.2.2, verbatim: `action_binding = "sha256:" || LOWERHEX( SHA-256( TAG || JCS(A) ) )`
- §2.5.3, verbatim: `core_digest = "sha256:" || LOWERHEX( SHA-256( TAG || JCS(core) ) )`
- §2.5.3, verbatim: "mandate_digest and transaction_digest are computed as in Section 2.2.2
  with the domain tags aae:enforce-mandate:v1 and aae:enforce-transaction:v1 respectively,
  each followed by a 0x00 octet."
- `action_digest` is a member of the verdict core (§2.5.3).
- `prev_core_digest` (§6.2) is not a construction of its own: verbatim, "A record's
  prev_core_digest carries the core digest of the record it follows, or null at the start
  of a chain." For a ratification record it "MUST be present and MUST equal the core
  digest of the record it ratifies."

(Note: the JWS compact serialization itself — `BASE64URL(header).BASE64URL(payload).BASE64URL(signature)` — is the envelope encoding, not a separate content-hash definition.)

## NOT in AAE (common attribution errors)

- No `receipt_id`. That belongs to receipt-format specs, not AAE. Still absent in -02
  (verified against 08e202ec, 28.09.2026). *(Attribution to a specific spec such as an "APS ActionReceipt with sha256(jcs(payload))" is UNVERIFIED here — confirm against the APS spec / a future `aps.md` before citing it externally.)*
- ~~No JCS canonicalization (RFC 8785) — explicitly excluded.~~ ~~No content-canonicalization
  of any kind.~~ **Both withdrawn for -02 (28.09.2026).** True for -00/-01; -02 uses JCS
  (RFC 8785) for every Construction-2 digest (see Hash Mechanics). The exclusion still
  holds for `delegator_aae_hash` alone (§3).
- ~~"Cycle detection" — NOT specified in the -00 draft.~~ **This entry was wrong
  and is withdrawn (2026-08-11).** Cycle detection *is* normative in -00, §5
  step 9: "To detect cycles, the relying party MUST maintain the set of AAE id
  values already visited in the current verification path and MUST reject the
  chain immediately if any id appears more than once", plus a recursion limit
  no greater than the smallest `max_depth` observed. Verified against the
  published -00 text (line 812 of `draft-kroehl-agentic-trust-aae-00.txt`, pin
  `2847f4da...`). The 2026-06-20 "correction" turned a true statement false.
  In -02, §5 step 9, verbatim: "It MUST apply a recursion limit equal to the smaller
  of 8 and the smallest max_depth observed anywhere in the chain."

## Update Triggers

- AAE -01 revision release → update Section Map + Hash Mechanics. **DONE for -02
  (28.09.2026):** Section Map, Hash Mechanics and the NOT-in-AAE list verified against
  08e202ec.
- Any section renumbering or hash-algorithm change → immediate update + citation-rule reminder.
