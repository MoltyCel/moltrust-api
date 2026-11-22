# ADR D3 — MANDATE Runtime Enforcement (v2)
**Status:** PROPOSAL — für 2. 3-Reviewer-Runde (C1-Konsens). NICHT akzeptiert. HARD GATE (D1/PR#41) bleibt: kein Production-Code vor Konsens. **Design-only.**
**Supersedes:** `docs/decisions/ADR-D3-mandate-enforcement.md` (v1) — v1 bleibt als **Audit-Trail** erhalten, NICHT löschen.
**Datum:** 2026-05-31 · **Autor:** Lars Kroehl
**Review-Basis:** Security-Runde 2026-05-31 (GPT-5 + Gemini 3.1 Pro Preview + Perplexity Sonar Pro → Claude-Synthese), Output `~/moltstack/reviews/20260531_151001_D3-ADR-MANDATE-Enforcement_review.md` (gitignored). Verdikt v1 = "grundlegend überdenken". Diese v2 foldet alle 5 Konsens-Criticals + 2 Cross-Cutting als **gelöste** Design-Entscheidungen ein.
**Referenzen:** `docs/specs/aae-constraint-taxonomy.md` (normativ), PR #102 (Scope), interne Bewertung, nicht im Repo.

## Änderungen gegenüber v1
v1 stellte D-1..D-5 als offene Fragen. Die Security-Runde hat D-1/D-2/D-3 + Datenmodell + enforce-mode-AuthZ entschieden. v2 schreibt diese als Design fest. **Weiterhin offen:** nur D-4 (Anchoring-Trigger) und D-5 (Performance-Budget).

## Architektur-Guards (unverändert, nicht verhandelbar)
- KEINE zirkuläre Agent-Selbstprüfung — MolTrust evaluiert unabhängig.
- KEIN DSGVO-Volllog — Hash/Attestation-Anchoring statt Inhalt; Inhalt bleibt beim Unternehmen.

## Komponente 1 — aae_envelopes Store (gehärtet)
Persistiert typisierte Constraint-Objekte keyed by aae_ref (sha256).
**RESOLVED (Critical 5 — Data-Model):** Das `required`-Flag lebt ZWINGEND INNERHALB jedes typisierten Constraint-Objekts — KEIN separates `required_flags`-Array. Verhindert Index-Mismatch-/Desync-Angriffe.
Skizze: `aae_envelopes(aae_ref PK sha256, mandate_scope jsonb, actions jsonb, constraints jsonb /* jedes Objekt: {type, value/window/..., required} */, validity jsonb, aae_version, taxonomy_version, raw_canonical bytea, created_at)`.
**RESOLVED (Cross-Cutting — Version-Pinning):** `aae_version` + `taxonomy_version` persistiert; `evaluator_version` im Verdict mitgeführt. Beide Gates pinnen Versionen → kein silent enforcement-downgrade.

## Komponente 2 — Evaluator-Contract (gehärtet)
Verdict: `{type, threshold, current_value, delta, verdict: ALLOW|DENY, reason, checked_at, evaluator_version}`.
- Stateless: max_transaction_value, allowed_domains, not_before/not_after.
- Stateful: rate_limit, single_use (siehe D-3).
- revocation_check: siehe D-2.
**RESOLVED (Cross-Cutting — Default-DENY):** Schlägt die Auswertung eines `required:true`-Constraints fehl (Parse-Fehler, unbekannter Typ, unauflösbar) → ZWINGEND DENY. Unbekannte Constraints NUR ignorierbar wenn explizit `required:false`.

## Komponente 3 — enforce-mode State-Machine (gehärtet)
`constraint_mode`: none | inherit | **enforce** (neu). enforce schreibt bei DENY in `violation_records` (violation_type aus Constraint-Typ, interaction_proof_id-Link, reversible-Flag).
**RESOLVED (Critical 4 — AuthZ):** Mode-Transitions sind NICHT von einem einzelnen Agent/Key schaltbar. Erforderlich: **M-of-N Multi-Party-Control + Write-Audit.** Konkret: Telegram-Approval (Muster wie MoltyCel-Review-Flow) + zweiter unabhängiger Key. Jeder Wechsel auditiert (wer, wann, von→zu).

## RESOLVED Decisions (durch Security-Runde entschieden)
**D-1 · Gates — RESOLVED: ZWEI Gates.**
- (a) Acceptance-Gate bei Registrierung: statisch prüfen, dass alle `required:true`-Typen bekannt/auswertbar UND aae/taxonomy/evaluator-Version kompatibel; sonst hart ablehnen (fail-closed).
- (b) Runtime-Gate pro Interaction: dynamische Schranken-Prüfung mit identischer/nachweislich kompatibler Evaluator-Version. Inkompatibilität → fail-closed + Governance-Eskalation, NIE still ALLOW.
**D-2 · revocation_check — RESOLVED: fail-closed + SSRF-/DoS-Härtung.**
- SSRF (Critical 2): revocation_check NUR über dedizierten **Egress-Proxy** mit RFC1918- + 169.254.169.254-Blocking + DNS-Rebinding-Schutz. ⚠️ **INFRA-ABHÄNGIGKEIT → Harald** (mögliche Überschneidung mit Cloud-Run-Proxy) — vor Implementierung abstimmen.
- DoS (Critical 3): Timeout + Circuit-Breaker + kurze Cache-TTL; **multi-layered Failover** (sekundäre Revocation-Quelle / on-chain-Attestation für Hochkritikalität), NICHT plain TTL. `checked_at` + `revocation_source` im Verdict.
- fail-open NUR für signierte, versionierte Allowlist rein-lesender/unkritischer Aktionen mit Betrags-/Häufigkeitsgrenzen; sonst fail-closed.
**D-3 · Multi-Node-State — RESOLVED: DB-Invarianten statt App-Logik.**
- single_use (Critical 1): unique-constraint INSERT auf `(vc_id, scope)` in „consumption"-Tabelle; Unique-Violation → DENY (Replay). Replay-Schutz wird DB-Invariante.
- rate_limit (Critical 1): atomic upsert (`INSERT ... ON CONFLICT DO UPDATE`) mit window_start; SERIALIZABLE bzw. `SELECT ... FOR UPDATE` gegen lost updates/TOCTOU. Windowing-Algorithmus + Clock-Source explizit dokumentieren.

## WEITERHIN OFFEN (für 2. Reviewer-Runde)
**D-4 · Anchoring-Trigger.** Welche Verdicts on-chain? Vorschlag: alle DENY (= violation, MUST per TechSpec §6); ALLOW zwingend wenn max_transaction_value involviert, sonst Sampling. Offen: Gas-Kosten + Linkability/Deanonymisierung (salted hashing / Merkle / Batching). → Reviewer.
**D-5 · Performance-Budget.** Runtime-Gate + outbound revocation_check = Latenz. Harte SLOs nötig, sonst Druck zu ad-hoc fail-open. Offen: Caching-TTL-Wert, akzeptables Latenz-Budget. → Reviewer.

## Konsequenzen
- Positiv: schließt deklariert-vs-enforced-Lücke; alle 5 Bypass-Vektoren der v1-Runde adressiert.
- Risiko/Offen: D-4 (Gas/Privacy), D-5 (Latenz/SLO), Harald-Infra-Abhängigkeit (Egress-Proxy).
- HARD GATE bleibt: kein Code bis C1-Konsens über v2 (inkl. D-4/D-5).
