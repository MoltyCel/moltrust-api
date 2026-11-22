# ADR D3 — MANDATE Runtime Enforcement
**Status:** PROPOSAL — für 3-Reviewer-Runde (C1-Konsens). NICHT akzeptiert. HARD GATE (D1/PR#41) bleibt: kein Production-Code vor Konsens.
**Datum:** 2026-05-30 · **Autor:** Lars Kroehl
**Referenzen:** docs/specs/aae-constraint-taxonomy.md (normativ), PR #102 (D3-Scope), interne Bewertung, nicht im Repo

## Kontext
Datenmodell ist ~70% vorverdrahtet: agent_delegations.aae_id + IPR.aae_ref (sha256) = Hash-Links; interaction_proof_records = Behavioral-Evidence (AAE-verknüpft); violation_records = Outcome-Sink; anchor_to_base wiederverwendbar. Es fehlen: (1) auflösbarer Constraint-Store, (2) Evaluator, (3) enforce-mode-Gate. Dieser ADR schlägt die drei Komponenten vor und markiert die offenen Entscheidungen für die Reviewer.

## Architektur-Guards (nicht verhandelbar, aus V1.15)
- KEINE zirkuläre Agent-Selbstprüfung — MolTrust evaluiert unabhängig.
- KEIN DSGVO-Volllog — Hash/Attestation-Anchoring statt Inhalt; Inhalt bleibt beim Unternehmen.

## Komponente 1 — aae_envelopes Store
Persistiert typisierte Constraint-Objekte keyed by aae_ref (sha256). MUSS das per-Constraint `required`-Flag behalten (entscheidet reject-vs-ignore). Flat-Blob unzureichend.
Vorschlag Schema (Skizze): aae_envelopes(aae_ref PK sha256, mandate_scope jsonb, actions jsonb, constraints jsonb[typed], validity jsonb, required_flags jsonb, created_at, raw_canonical bytea für Re-Verify).

## Komponente 2 — Evaluator-Contract
Per-Type-Handler-Set. Verdict-Form (kompatibel zu aeoess ConstraintEvaluation): {type, threshold, current_value, delta, verdict: ALLOW|DENY, reason}.
- max_transaction_value — stateless, Wert pro tx vs. threshold.
- allowed_domains — stateless, Domain in Allowlist.
- rate_limit — STATEFUL, Counter akzeptierter Aktionen im ISO-8601-Fenster.
- not_before/not_after — stateless, RFC-3339-Vergleich.
- revocation_check — outbound HTTPS, fail-closed-Default.
- single_use — STATEFUL, Replay-Schutz per VC-id.

## Komponente 3 — enforce-mode State-Machine
constraint_mode heute: none | inherit. Vorschlag neuer State: enforce.
Übergänge + Semantik (none→inherit→enforce) als Reviewer-Entscheidung. enforce schreibt bei DENY in violation_records (violation_type aus Constraint-Typ, interaction_proof_id-Link, reversible-Flag).

## OFFENE ENTSCHEIDUNGEN (für die 3 Reviewer)
**D-1 · Ein oder zwei Gates.** Empfehlung: ZWEI. (a) Acceptance-Gate bei AAE-Registrierung — statische Prüfung ob alle required:true-Typen bekannt/auswertbar; sonst AAE ablehnen (normative Regel verlangt "ablehnen" = Annahmemoment = Registrierung). (b) Runtime-Gate pro Interaction — dynamische Schranken-Prüfung. Alternative (ein Gate, rein Runtime) riskiert dass eine unauswertbare AAE faktisch akzeptiert wird bevor sie abgelehnt wird → Regelverstoß. → Reviewer entscheiden.
**D-2 · revocation_check fail-open-Ausnahme.** Spec erlaubt auditierbares fail-open nur für "low-risk, governed actions". Wer definiert low-risk? Vorschlag: explizite Allowlist-Aktionen, sonst fail-closed. → Reviewer.
**D-3 · Multi-Node-State-Sharing.** rate_limit + single_use erfordern geteilten State über Prozesse/Nodes. Heute single-node. Vorschlag: PostgreSQL als shared counter-store (kein Redis-Neudependency). Race-Conditions bei rate_limit? → Reviewer.
**D-4 · Anchoring-Trigger.** Welche Verdicts werden on-chain geankert? Alle DENY (= violation, MUST per TechSpec §6)? Auch ALLOW-Samples? Kostenfrage Base-Gas. → Reviewer.
**D-5 · Performance.** Runtime-Gate pro Interaction + outbound revocation_check = Latenz. Akzeptables Budget? Caching von revocation-Status (TTL)? → Reviewer.

## Konsequenzen
- Positiv: schließt die deklariert-vs-enforced-Lücke (AAE-Card-Claim wird einlösbar); Felix/Harald-Verhaltens-Nachweis-Produkt wird real; baut auf live IPR-Infra.
- Risiko: Latenz (D-5), State-Komplexität (D-3), Gas-Kosten (D-4).
- HARD GATE: kein Code bis C1-Konsens über D-1..D-5.
