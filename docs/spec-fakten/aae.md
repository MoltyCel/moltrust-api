# AAE Spec Facts — Verified Reference

**Source:** draft-kroehl-agentic-trust-aae-00, IETF Datatracker, uploaded 2026-05-21
**Verified:** 2026-06-20 (Console verification against live Datatracker fetch)
**SHA-256 of draft text:** `2847f4daf3f0a088afb1bd1bd3b9c001947a9905426753673d9da8275a038b0a`
(source: `https://www.ietf.org/archive/id/draft-kroehl-agentic-trust-aae-00.txt`, 48500 bytes)
**Status:** -00 and -01 both public. **-01 is the current revision** — cite it, not -00.

### -01 (current, published)

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
**NOT carried into -01:** the §6.5 SHOULD→MUST backlog item below; WHO axis;
`action_binding`; freshness. Those are -02 candidates.

## Section Map

- §1 Introduction
- §2 The Agent Authorization Envelope
  - §2.1 Structure
  - §2.2 MANDATE
  - §2.3 CONSTRAINTS
  - §2.4 VALIDITY — `not_before` (REQUIRED), `not_after` (REQUIRED), `revocation_check` (OPTIONAL), `single_use` (OPTIONAL, Boolean, default false)
- §3 Delegation Chains (mechanics, structure, `delegator_aae_hash` — OPTIONAL)
- §4 Action Vocabulary Schemas
- §5 Verification Algorithm (9 steps; step 9 = delegation chain walk)
  - §5.1 Verification Dependencies — **added in -01** (published); DID documents, ancestor
    AAEs, revocation endpoint named as retrieval dependencies
- §6 Security Considerations
  - §6.1 Replay Attacks
  - §6.2 Constraint Bypass
  - §6.3 Key Compromise
  - §6.4 Delegation Amplification
  - §6.5 Delegation Revocation (normative level: SHOULD; AAE-01 Backlog: SHOULD→MUST)
  - §6.6 Clock Skew and Time Synchronization
  - §6.7 On-Chain Anchoring
- §7 Privacy Considerations
- §8 IANA Considerations
- §9 References

## Hash Mechanics

AAE defines exactly ONE content hash: `delegator_aae_hash`

- **Location:** §3, OPTIONAL field
- **Form:** `sha-256:<base64url-digest>`
- **Input:** the exact ASCII octet sequence of the parent AAE JWS-compact-serialization as retrieved
- **Explicit exclusions (§3, lines 544–545):** no additional whitespace, no decode/re-encode, no JSON canonicalization
- **Algorithm:** SHA-256 per RFC 6234
- **Mismatch handling:** relying party MUST reject the delegated AAE

(Note: the JWS compact serialization itself — `BASE64URL(header).BASE64URL(payload).BASE64URL(signature)` — is the envelope encoding, not a separate content-hash definition.)

## NOT in AAE (common attribution errors)

- No `receipt_id`. That belongs to receipt-format specs, not AAE. *(Attribution to a specific spec such as an "APS ActionReceipt with sha256(jcs(payload))" is UNVERIFIED here — confirm against the APS spec / a future `aps.md` before citing it externally.)*
- No JCS canonicalization (RFC 8785) — explicitly excluded.
- No content-canonicalization of any kind.
- ~~"Cycle detection" — NOT specified in the -00 draft.~~ **This entry was wrong
  and is withdrawn (2026-08-11).** Cycle detection *is* normative in -00, §5
  step 9: "To detect cycles, the relying party MUST maintain the set of AAE id
  values already visited in the current verification path and MUST reject the
  chain immediately if any id appears more than once", plus a recursion limit
  no greater than the smallest `max_depth` observed. Verified against the
  published -00 text (line 812 of `draft-kroehl-agentic-trust-aae-00.txt`, pin
  `2847f4da...`). The 2026-06-20 "correction" turned a true statement false.

## Update Triggers

- AAE -01 revision release → update Section Map + Hash Mechanics.
- Any section renumbering or hash-algorithm change → immediate update + citation-rule reminder.
