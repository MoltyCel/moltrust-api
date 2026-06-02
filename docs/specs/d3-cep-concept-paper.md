# Runtime Enforcement of Agent Authorization, and Its Governance
### A Concept Paper on the MANDATE-Enforcement Architecture (D3) and the Combined Enforcement Protocol (CEP)

**Status:** CONCEPT — design-only, positioning draft (arXiv 2.0 preparation). No implementation internals.
**Date:** 2026-06-02 · **Author:** Lars Kroehl (MolTrust / CryptoKRI GmbH)
**Scope:** Mechanism and governance. This paper deliberately omits implementation interna (storage schemas, locking, transport paths); those live in the D3 ADRs and the AAE draft.

---

## 1. Problem

Autonomous AI agents increasingly initiate actions on behalf of principals — payments, data access, downstream delegations — without a human in the loop at the moment of action. The prevailing answer to "what is this agent allowed to do?" is a **declarative** one: a credential, scope list, or policy document that *states* the agent's authority.

Declaration is necessary but not sufficient. A declared permission that is merely *stored* and presented — never independently checked against the agent's actual runtime behaviour — leaves an open gap between **what was declared** and **what is enforced**. An agent can hold a perfectly valid authorization envelope and still act outside its declared bounds; nothing at the point of action verifies the two against each other. We call this the **declarative-vs-enforced gap**.

Closing it requires *runtime enforcement*: an independent, verifiable check at the moment of action that the planned action falls within the declared authority — and, critically, a non-repudiable record that the check happened and what it decided. This paper describes a four-layer architecture that does so, and then turns to the harder question the architecture raises: **who is allowed to switch enforcement on, and how does that authority survive the disappearance of any person, chain, or instance?**

---

## 2. Layer 1 — The Authorization Envelope (AAE)

The base layer is the **Agent Authorization Envelope (AAE)** — a cryptographically signed, verifier-independent statement of an agent's authority, structured into three mandatory blocks (per the AAE Internet-Draft taxonomy):

- **MANDATE** — *what the agent is authorized to do* (scope, action allowlist, optional delegation).
- **CONSTRAINTS** — *the bounds on those actions* (e.g. value ceilings, domain allowlists, rate limits), each carrying an explicit `required` flag.
- **VALIDITY** — *the temporal envelope* (not-before / not-after, optional revocation, single-use).

The AAE is the declarative substrate. By itself it is Layer 1 only — storage and presentation. The remaining layers turn the declaration into something enforced.

## 3. Layer 2 — The Evaluator

The **Evaluator** performs the independent runtime check. Given a planned action and the relevant envelope, it evaluates each constraint and the validity window and returns an **ALLOW/DENY verdict** with a per-constraint breakdown.

Three properties make the verdict trustworthy beyond a single operator's word:

1. **Independence.** The Evaluator is not the agent and not the agent's principal. It evaluates against the declared constraints from a position the actor does not control — eliminating circular self-attestation.
2. **Signed, anchorable verdicts.** Each verdict is **Ed25519-signed** over its full canonical content (the action context and the per-constraint evaluation, not merely metadata) with a key identifier for rotation, and is **chain-agnostically anchorable**. Any third party can later verify a verdict against the published verification key — the audit trail is cryptographic, not a claim.
3. **Value authenticity.** A constraint is only as strong as the truth of the value it is checked against. The Evaluator distinguishes **rail-verified** facts (e.g. an amount taken from a signed payment intent) from **self-asserted** ones (a value the actor merely claims). A `required` value-constraint that cannot be checked against a verifiable source yields **Default-DENY** — a self-asserted figure never grants a hard ALLOW for a critical bound.

The Evaluator answers *whether* an action is within authority. It does **not**, by itself, stop the action — judgment and enforcement are deliberately separated.

## 4. Layer 3 — The Enforcement Chokepoint

Separating judgment from enforcement is only safe if there is a **mandatory** path: an action must pass through evaluation, and **no ALLOW means no action**. The enforcement chokepoint is that mandatory gate, with a mode:

- **none / inherit (advisory):** a DENY is *recorded* (signed verdict + violation record) but the action proceeds. This is the conservative bootstrap state — the system observes and proves what *would* have been blocked before it blocks anything.
- **enforce (blocking):** a DENY *blocks* the action. There is no path around the chokepoint.

Advisory-first is a feature, not a limitation: it accumulates a verifiable record of enforcement decisions, building the evidence base — and the trust — needed before flipping the switch to blocking. But flipping that switch is an act of authority. Who holds it?

## 5. Layer 4 — Governance of Enforcement (CEP)

The authority to switch enforcement to *blocking* must not depend on a **person** (a founder is not a ten-year institution), a **single chain** (which can be censored, forked, or abandoned), or a **single instance** (which can be shut down or compromised). The **Combined Enforcement Protocol (CEP)** is the governance layer that distributes this authority across objective, publicly verifiable conditions rather than any one locus of trust.

CEP deliberately rejects three tempting tools as wrong-for-the-problem: zero-knowledge proofs (they solve privacy, not authority), MPC / node-bound schemes (they re-bind authority to an operator set), and single-chain governance (which re-introduces the chain dependency). Instead it **combines** three primitives, each already native to the trust substrate:

- **(a) Chain-agnostic anchoring of rule versions** — enforcement-rule versions are anchored with a quorum across multiple chains, surviving the disappearance of any single chain.
- **(b) Trust-weighted veto** — voting/veto weight is bound to a **behavioural trust score** that is Sybil-resistant (mutual-endorsement clustering + cross-vertical diversity), so weight reflects demonstrated, diverse standing rather than capital — avoiding plutocracy.
- **(c) Time-lock and public veto** — changes take effect after a long delay unless a reasoned objection is raised (objection rather than active assent), bridging a small early-phase base in a technology-independent way.

**Ramp-up.** Enforcement rules are initially set by the founder. The transition to CEP is **automatic** and triggers only when **four conditions hold simultaneously** (a conjunction, *not* a disjunction — any single threshold is gameable or arbitrary):

1. a **minimum time** has elapsed (anti-rush);
2. at least **N** behaviourally qualified, Sybil-checked relying parties exist;
3. distributed across at least **M** verticals (diversity);
4. **no actor or cluster exceeds X%** of trust-weighted voting power (anti-concentration).

The numbers **N / M / X / time are fixed and anchored in advance**, before ramp-up begins — there is no opportunistic re-tuning, not even by the founder.

---

## 6. Primary Thesis (a)

> **Runtime enforcement for autonomous agents — via independently verifiable, signed, chain-agnostically anchored verdicts — provably closes the declarative-vs-enforced gap.**

The contribution is not any single mechanism but their composition: an *independent* evaluator (not self-attestation), producing *cryptographically signed* verdicts over full action content (not just metadata, so the audit cannot be silently rewritten), *anchored chain-agnostically* (so the record outlives any one chain), with *value-authenticity gating* (so a constraint is enforced against verified facts, not claims). Together these make "the action was within authority" a checkable, non-repudiable proposition rather than an assertion of good faith.

## 7. Secondary Thesis (b) — flagged for evaluation as possibly the stronger core

> **Governance transition for non-stationary threat spaces: adaptability over static correctness — an automatic ramp-up from founder-set rules to trust-weighted veto, gated on pre-committed, publicly verifiable conditions.**

We mark this thesis **explicitly and separately** because we are uncertain it is secondary. The threat space for autonomous-agent authorization is **non-stationary**: attack patterns, Sybil strategies, and the agent population itself shift over time. A statically "correct" governance design optimizes for a fixed adversary; what may matter more is a governance *transition* that is itself **adaptive yet not capturable** — one that hands authority from a single founder to a diverse, trust-weighted, Sybil-resistant body **only** when objectively measured conditions are met, with the bar fixed in advance so it cannot be moved when convenient.

The novel claim in (b) is the **conjunctive, pre-committed, publicly-verifiable trigger** for that handover — adaptability bounded by commitment. **We invite the review to assess directly whether (b), not (a), is the stronger and more original core of this work.**

---

## 8. Architecture Guards (non-negotiable)

- **Independent evaluation** — no circular self-checking; the actor cannot grade its own homework.
- **No bulk content logging (GDPR)** — only hashes / attestations / verdicts are anchored; the action content stays with the enterprise. Auditability is achieved by anchoring proofs, not data.
- **Default-DENY** — any unevaluable required constraint, parse failure, or unknown-but-required condition resolves to DENY, never to a silent ALLOW.

## 9. Open Research Question

The hardest open problem is the **independent measurability of the CEP trigger** — an *oracle problem of condition satisfaction*. If the four ramp-up conditions are measured by the system itself, the trigger becomes a new single point of failure: authority would, after all, still rest on one instance's claim that "the conditions are now met." For CEP to deliver on its premise, the relying-party count, cluster analysis, vertical distribution, and concentration measure must be **independently recomputable by any party from publicly anchored data** — not an internal assertion. This closes the circle back to the transparency/anchoring principle that underlies the whole architecture: the same primitives that make enforcement auditable must also make the governance trigger auditable. How to construct that oracle without re-introducing a trusted measurer is, in our view, the central unsolved question — and a candidate for the paper's sharpest contribution if resolved.
