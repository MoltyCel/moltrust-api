# ADR D3 — MANDATE Runtime Enforcement (v3)
**Status:** **ACCEPTED** (2026-05-31). Status-Flip PROPOSAL → ACCEPTED nach einstimmigem Konsens.
**Konsens:** 3-Runden-Review (rethink → revise → approve), einstimmig **FREIGEBEN** 2026-05-31. Erfüllt **D1-HARD-GATE** (C1 / 3-Reviewer-Konsens). Review-Dateien: `20260531_151001`, `20260531_164522`, `20260531_180419`. **Enforcement-Code ab Merge dieses Addendums unblocked** — Implementierung gemäß Implementation-Contract (unten) + Sicherheits-Sprint-Regeln.
**Supersedes:** `docs/decisions/ADR-D3-mandate-enforcement-v2.md` (v2) — v2 bleibt als **Audit-Trail** erhalten, NICHT löschen. (Kette: v1 → v2 → v3, alle erhalten.)
**Datum:** 2026-05-31 · **Autor:** Lars Kroehl
**Review-Basis:**
- Runde 1 (v1): `~/moltstack/reviews/20260531_151001_D3-ADR-MANDATE-Enforcement_review.md` — Verdikt "grundlegend überdenken", 5 Criticals.
- Runde 2 (v2): `~/moltstack/reviews/20260531_164522_D3-ADR-v2-MANDATE-Enforcement_review.md` — Verdikt "überarbeiten", Critical 5 GESCHLOSSEN, 4 Restlücken.
v3 foldet die 4 Restlücken aus Runde 2 + D-4 als gelöste Spec ein.
**Referenzen:** `docs/specs/aae-constraint-taxonomy.md` (normativ), PR #102/#104/#105, interne Bewertung, nicht im Repo.

## Änderungen gegenüber v2
v2 schloss Critical 5 (Data-Model) endgültig. Runde 2 ließ 4 Restlücken offen (SSRF-Blocklist unvollständig, AuthZ-Telegram-Schwäche, scope-Normalisierung, DoS-SLOs) + D-4 Privacy. v3 füllt diese als konkrete Spec.

## Architektur-Guards (unverändert, nicht verhandelbar)
- KEINE zirkuläre Agent-Selbstprüfung — MolTrust evaluiert unabhängig.
- KEIN DSGVO-Volllog — Hash/Attestation-Anchoring statt Inhalt.

## Komponente 1 — aae_envelopes Store (unverändert ggü v2)
keyed by aae_ref (sha256). `required`-Flag ZWINGEND INNERHALB jedes typisierten Constraint-Objekts (Critical 5, GESCHLOSSEN). aae_version + taxonomy_version persistiert; evaluator_version im Verdict (Version-Pinning).
Skizze: `aae_envelopes(aae_ref PK sha256, mandate_scope jsonb, actions jsonb, constraints jsonb /* {type,value/window,required} */, validity jsonb, aae_version, taxonomy_version, raw_canonical bytea, created_at)`.

## Komponente 2 — Evaluator-Contract (unverändert ggü v2)
Verdict: `{type, threshold, current_value, delta, verdict: ALLOW|DENY, reason, checked_at, evaluator_version}`. Default-DENY bei unauswertbarem required:true. Unbekannt nur ignorierbar wenn explizit required:false.

## Komponente 3 — enforce-mode State-Machine (C4 GEHÄRTET)
`constraint_mode`: none | inherit | enforce. enforce schreibt bei DENY in `violation_records`.
**RESOLVED (Critical 4 — AuthZ, Runde-2-Restlücke geschlossen):**
- Mode-Transitions via **M-of-N KRYPTOGRAPHISCHE Signaturen** — 2 unabhängige Keys: **Governance-Key + zweiter unabhängiger Key**. Telegram autorisiert NICHTS.
- **Telegram = NOTIFICATION-ONLY** (reiner Transparenz-Alert, kein Faktor in der Vertrauenskette — adressiert SIM-Swap/Session-Hijack-Kritik).
- **No-Downgrade-Guard:** `enforce → none/inherit` erfordert HÖHERE Schwelle (mehr Signaturen / strengere Policy) als `none → enforce`. Verhindert leichtes Abschalten des Schutzes.
- **M-of-N-Parteien + Key-Rotation-Policy** explizit zu spezifizieren (wer hält die N Keys; Rotations-/Recovery-Verfahren) — als benannter Implementierungs-Vertrag.

## RESOLVED Decisions
**D-1 · Gates — RESOLVED (Runde 1):** ZWEI Gates (Acceptance statisch fail-closed + Version-kompatibel; Runtime pro Interaction, Inkompatibilität → fail-closed + Eskalation, NIE still ALLOW).

**D-2 · revocation_check — RESOLVED (Runde-2-Restlücke geschlossen): vollständige SSRF-Blocklist + fail-closed.**
- **IPv4-Blocklist:** RFC1918 (10/8, 172.16/12, 192.168/16) + `0.0.0.0/8` + `127.0.0.0/8` + `169.254.0.0/16`.
- **IPv6-Blocklist:** `::1/128` (loopback) + `fc00::/7` (ULA) + `fe80::/10` (link-local) + `::ffff:0:0/96` (IPv4-mapped).
- **Cloud-Metadata:** `169.254.169.254` (AWS/GCP) + `fd00:ec2::254` (AWS IPv6) explizit blocken.
- **Scheme-Allowlist:** NUR `https://`. `file://`, `gopher://`, `ftp://`, `data://` explizit verboten.
- **DNS-Rebinding-Schutz:** Resolve + Pin der IP VOR Connect (keine Re-Resolution zwischen Check und Connect).
- Transport via dediziertem Egress-Proxy. ⚠️ **INFRA-DEP → Harald** (mögl. Cloud-Run-Proxy-Überschneidung).
- DoS (siehe D-5): Timeout + Circuit-Breaker + Cache-TTL + multi-layered Failover. fail-open nur signierte versionierte Allowlist rein-lesender Aktionen.

**D-3 · Multi-Node-State — RESOLVED (Runde-2-Restlücke geschlossen): DB-Invarianten + scope-Kanonisierung.**
- single_use: unique-constraint INSERT auf `(vc_id, scope_canonical)`; Unique-Violation → DENY (Replay).
- **scope-Canonicalization:** **JCS (RFC 8785)** auf `scope` VOR dem INSERT (sortierte Keys, kein Whitespace) — sonst via Key-Reorder umgehbar. RFC 8785 ist bereits im Stack (B-Block D1-Baseline).
- rate_limit: atomic upsert (`ON CONFLICT DO UPDATE`) + window_start; SERIALIZABLE bzw. `SELECT FOR UPDATE` gegen TOCTOU.
- **Multi-DB/Sharding:** v3 nimmt **single-primary-Postgres** an (dokumentiert als explizite Annahme). Sharding/Multi-Primary → eigener Follow-up (shared-counter-Strategie), NICHT in dieser Iteration.

## D-4 · Anchoring-Trigger — RESOLVED-Vorschlag (Runde-2-Privacy-Restlücke)
NICHT alle DENY raw on-chain (Linkability/Deanonymisierungs-Risiko, Pattern-Frequenz). Stattdessen:
- **Salted Merkle-Tree** über DENY-Batch; nur Root on-chain.
- **Batch-Anchoring** (zeit-/größenbasiert), off-chain **Salt-Store**.
- Konsistent mit TechSpec §6 + dem bereits LIVE IPR-Merkle-Anchoring-Pattern (`merkle_proof` in `interaction_proof_records`).
ALLOW: zwingend verankern wenn `max_transaction_value` involviert (im Batch), sonst Sampling.

## D-5 · Performance-Budget — RESOLVED-Vorschlag (= C3-SLOs)
- revocation_check: **200ms p99 Timeout**.
- Cache-TTL: **5 Minuten** (mit freshness-Indikator).
- Circuit-Breaker: open nach **5 Fails / 30s**, half-open-Retry nach **60s**.
- Werte als Vorschlag — Reviewer-bestätigbar / justierbar.

## Konsequenzen
- Positiv: alle 5 v1-Criticals + 4 v2-Restlücken + D-4 adressiert; nur noch konkrete Wert-Bestätigung offen.
- Risiko/Offen: M-of-N-Parteien-Benennung + Key-Rotation (Implementierungs-Vertrag); Sharding als Follow-up; Harald-Infra-Dep (Egress-Proxy).
- HARD GATE **ERFÜLLT** (3-Reviewer-Konsens, einstimmig FREIGEBEN 2026-05-31). Enforcement-Code ab Merge unblocked.

## Implementation-Contract (approve-with-nits, vor/während Code zu lösen)
Von den Reviewern als nicht-design-blockierende Follow-ups bestätigt; verbindliche Checkliste für die Implementierung:
- [ ] **SSRF-Blocklist ergänzen:** `100.64.0.0/10` (CGNAT, RFC 6598) + `255.255.255.255/32` (Broadcast) zur IPv4-Blocklist.
- [ ] **SSRF-Validation-Order fixieren:** `resolve → check (aufgelöste IP vs. Blocklist) → connect zu exakt geprüfter IP`. KEIN Re-Resolve nach dem Check (sonst DNS-Rebinding).
- [ ] **Circuit-Breaker open → MUSS DENY (fail-closed)** — explizit; niemals fail-open/ALLOW bei offenem CB.
- [ ] **Active Cache-Invalidation** (Redis Pub/Sub o.ä.) zum sofortigen Override der 5min-TTL bei Revocation von **high-value** Mandates (insb. `max_transaction_value`).
- [ ] **Replay-Schutz** (Nonces/Timestamps) im **M-of-N-Signatur-Payload** der Mode-Transitions — gültige `enforce → none`-Signatur darf nicht wiederholt einspielbar sein.
- [ ] **Monitor/Alert auf failed mode-transitions** (abgelehnte M-of-N-Signaturen = Kompromittierungs-Indikator); **Salt-Store ACL + Mindest-Batch-Größe** für D-4.
- [ ] **M-of-N-Party-Assignment + Key-Rotation-Policy** — Governance-Entscheidung (Lars / Bernd / Harald): wer hält die N Keys, Rotations-/Recovery-Verfahren.
- [ ] **Harald-Egress-Proxy-Infra** abstimmen (mögliche Cloud-Run-Proxy-Überschneidung) bevor revocation_check live geht.
- [ ] **Sharding/Multi-Primary** bleibt deferred (single-primary-Postgres-Annahme gilt) — eigener Follow-up wenn nötig.

## Sicherheits-Sprint-Regeln für die Implementierung (verbindlich)
- **KEIN Single-LLM-Session** für diesen security-kritischen Code (Multi-Model bzw. unabhängiger Review-Pass).
- **Pre-Commit-Diff-Verify** vor jedem Commit (Diff manuell gegen Intent prüfen).
- **Jede Komponente eigener PR** (aae_envelopes-Store / Evaluator / enforce-mode-State-Machine / revocation_check getrennt) — kein Big-Bang-Merge.
