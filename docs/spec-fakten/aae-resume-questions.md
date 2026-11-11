# AAE -02 and the three resume questions — Spec Facts

**Question source:** D. Marla, K. Bhatnagar (Arya.ai), "Permission to Retry: A Study of
Authorization and Recovery in AI Agent Workflows", arXiv:2610.31916v1 (submitted
2026-10-22), §3 (model) and §8 (implications).
`https://arxiv.org/pdf/2610.31916v1`, 413,248 bytes, SHA-256
`d7651b65c1fcbbbba0f57440ec43de30c4a1f3548e81972a08792ba1971b2a1d`. Retrieved 2026-10-27.

**Spec:** draft-kroehl-agentic-trust-aae-02, IETF Datatracker, rev 02 posted
2026-09-06T15:30:19Z (Datatracker API, fetched 2026-10-27).
`https://www.ietf.org/archive/id/draft-kroehl-agentic-trust-aae-02.txt`, SHA-256
`08e202ecc06d245b287e60591098c22d8af480fc88cb455defc9e7612a473b4c` (fetched 2026-10-27;
identical to the hash in `aae.md`). Line numbers below refer to that text.

**Repository state when written:** `MoltyCel/moltrust-api` main `881cb1f7`.

**Scenario matrix and gap list:** `MoltyCel/aae-conformance-vectors`,
`scenarios/permission-to-retry/README.md` and `LUECKEN.md`, merged in `08387306` (#25).
Gap ids L1–L10 below refer to that `LUECKEN.md`.

**Verified:** 2026-10-27. **Status:** facts only. This file records what -02 says; it
proposes no text.

## The three questions

The paper (§8) names three questions that have to stay explicit when an agent resumes: is a
new dispatch currently authorized; can a previous delivery still commit; and is there
durable, participant-visible evidence that permits settlement. Its model (§3) treats the
authorization instance, the transport key, the worker lease and the checkpoint as distinct,
with an allowed effect count bound to the authorization instance.

### Q1 — Is a new dispatch authorized now?

**Partly regulated.**

- Each presentation is verified again: time (§5 step 3), revocation (§5 step 8).
- §2.4 `single_use`, l. 757–766: the relying party "MUST reject any subsequent presentation
  of an AAE bearing the same id after the first successful authorization" (l. 759–760), and
  the state "MUST be shared across all nodes that can accept the AAE" (l. 763–764).
  §5 step 5 repeats this as an atomic check-and-record (l. 1467–1473).
- §7.1, l. 2149: "A PERMIT is not a single-use authorization." A relying party "MUST
  establish freshness and consumption by its own means" — a nonce, a spent identifier or an
  idempotency key (l. 2162–2165); one that does none of these "MUST NOT rely on the verdict
  for either property" (l. 2165–2166).
- §2.4, l. 789–797: a verdict "says nothing about whether a condition that held when the
  mandate was issued still holds"; the validity members "bound the authorization in time;
  they do not make it fresh" (l. 797). §9, l. 2476: "Freshness and condition liveness" is
  future work.

**Not regulated:** whether a repeat of an already admitted action is the same action or a
new one (L2); a policy change that is not expressed as revocation (L7); the horizon of an
idempotency key (L8).

### Q2 — Can an earlier dispatch still take effect?

**Not regulated.** -02 governs whether a relying party accepts an envelope when it is
presented. It says nothing about a delivery that was admitted and is still in flight.

- §2.4, l. 765–766: single-use invalidation "denotes relying-party-local state; it does not
  invalidate the AAE globally."
- §2.4, l. 748–749: "The relying party MUST reject the AAE if the endpoint indicates
  revoked: true" — a rule about acceptance, not about effects already under way.
- §7.5 (l. 2215–2271) states the consequence of a known revocation for descendants and
  defers its discovery at evaluation time: "A relying party SHOULD NOT dereference a
  revocation endpoint at evaluation time in this revision" (l. 2265–2266).

Gap ids: L3 (in-flight delivery after revocation), L10 (revocation discovery, declared in
§9).

### Q3 — Which evidence permits completion?

**Not regulated.**

- §6.2, l. 1766–1768: "A verdict record consists of the core of Section 2.5.3 and its
  digest. A record is immutable." The core (§2.5.3, l. 918–934) holds mandate,
  transaction and action digests, verdict, grant index, trace and predecessor digest. It
  "MUST NOT include server time, random values, stored counters, or accumulated budget"
  (l. 920–921). No member records an effect, a provider receipt or a settlement.
- §6.1 (l. 1743–1762): the verdict vocabulary is PERMIT, DENY, PENDING. No state exists for
  an action whose external outcome is unknown (L6).

Gap ids: L5 (completion evidence), L6 (unresolved outcome).

## Checked points

| Point | -02 | Where |
|---|---|---|
| Effect count per mandate | Not regulated. `single_use` counts successful authorizations of one envelope id at a relying party (l. 757–766); `rate_limit` counts "accepted actions" per window (§2.3, l. 702–710); a `range` constraint "bounds each transfer, never their sum" (§7.1, l. 2155–2156). No member states an effect count. | L1 |
| Rule for a repeated dispatch | Not regulated as a rule of its own. §7.1 requires the relying party to establish consumption by a nonce, a spent identifier or an idempotency key (l. 2160–2166) without choosing one; with `single_use: true` any second presentation is rejected (§5 step 5), a legitimate retry included. | L2, L8 |
| Read permission for reconciliation after revocation | Not regulated. `read` is an action of the same mandate (§4, Table 1, l. 1154: "Retrieve information without side effects"); a revoked envelope is rejected as a whole (l. 748–751). No reconciliation capability separate from write authority. | L4 |
| AER (Agent Execution Record) as completion evidence | Not regulated. The term does not occur in the -02 text (full-text search of the 3,024-line `.txt`). In this repository AER material appears in the leak-canary implementation (`app/leak_canary.py`, `docs/leak-canary.md`), which is implementation, not -02. | L5 |

## Common attribution errors to avoid

- Reading `single_use` as an effect count. It counts successful authorizations at one
  relying party; it does not know whether the provider committed.
- Reading a PERMIT as consumption. §7.1 says it is not.
- Citing -02 for in-flight effects, settlement evidence, unresolved outcomes or an AER. -02
  contains none of these.
- Reading LUECKEN.md as proposed text. It lists open questions against -02 only.
