# Architektur-Brief — Komponente 2: AAE Evaluator (v3)
**Status:** DESIGN-BRIEF v3 (kein Code). Vorschlag für Sign-off → Re-Review (security) → DANN Code.
**Supersedes:** `docs/specs/aae-evaluator-design-v2.md` (v2, PR #113) — v2 bleibt als **Audit-Trail** (Kette v1→v2→v3 erhalten).
**Review-Basis:** Security-Runde 2 (`~/moltstack/reviews/20260601_120850_C2-aae-evaluator-v2_review.md`) — Verdikt v2 = ÜBERARBEITEN, v1-Criticals GESCHLOSSEN, 4 neue Criticals. v3 foldet diese 4 + vc_id-Binding.
**Datum:** 2026-06-01 · **Autor:** Lars Kroehl
**Referenzen:** ADR-D3-v3 (ACCEPTED), `docs/specs/aae-constraint-taxonomy.md`, `docs/decisions/ADR-D3-mandate-enforcement-v3.md` (D1-Baseline-B-Block), `app/signature.py`.

## Unveränderte Basis (v1/v2, bestätigt GESCHLOSSEN)
- Dedizierter `POST /vc/aae/evaluate` mit action_context; IPR pure Provenance.
- Verdict-Form = live ConstraintEvaluation-Mapping (`type/threshold/current_value/delta`, 5/5 vectors).
- **C1 TOCTOU GESCHLOSSEN:** Count über `aae_evaluations` (nicht IPR) + advisory-lock + Count&INSERT in einer Tx.
- **C2 value-authenticity GESCHLOSSEN:** gestuft `rail_verified` vs `self_asserted`, Server-Zeit, `agent_did==principal`.
- Default-DENY, immutable store, append-only `aae_evaluations`, Architektur-Guards (kein self-check, kein Volllog).
- `revocation_check` DEFERRED (SSRF-egress-proxy).

## RESOLVED — Security-Criticals (v2-Runde)

### 1 · Advisory-Lock: zwei-int64-Variante (statt 32-bit hashtext)
`hashtext()` ist 32-bit → Birthday-Kollision bei ~65k Locks → unrelated `(agent, envelope)` teilen einen Lock (DoS/False-Serialization).
- **`pg_advisory_xact_lock(key1 int4, key2 int4)`** (zwei-Integer-Variante = 64-bit Entropie) mit zwei Hälften eines **stärkeren** Hash: `h = sha256(agent_did || ':' || aae_ref)`; `key1 = h[0:4] als int4`, `key2 = h[4:8] als int4`.
- 64-bit-Schlüssel → Kollisionswahrscheinlichkeit praktisch eliminiert; transactional lock wird bei COMMIT automatisch freigegeben.

### 2 · Verdict-Signatur-Schema (präzise — kein Splicing/Replay)
- **JCS (RFC 8785) über ein EXPLIZIT definiertes Feld-Set** (geschlossene Liste, keine offenen Felder):
  `{ eval_id, aae_ref, agent_did, verdict, value_source, evaluator_version, timestamp }`.
- **Domain-Separation-Tag:** vorangestelltes Kontext-Präfix `"moltrust:aae-verdict:v1"` vor dem JCS-Payload → eine AAE-Verdict-Signatur kann nicht als anderer Signatur-Typ wiederverwendet werden.
- **`eval_id` + `timestamp` sind im signierten Set** → Replay/Splicing erkennbar (jede Eval eindeutig gebunden).
- **Ed25519 registry-key**, `app/signature.py`-Bausteine wiederverwenden (`canonicalize` / `build_registry_jws`-Pattern). Signatur in `aae_evaluations.verdict_signature`.

### 3 · Numeric (D1-Baseline-B-Block VERBATIM)
Nicht nur `value >= 0`, sondern die bereits normative D1-Regel übernehmen:
- **Integer-minor-units** (kein Float für Beträge) + **`currencyScale`** (ISO-4217-Nachkommastellen) + **overflow=reject**.
- **Upper bound** (definiertes Maximum) zusätzlich zu `>= 0`; **finite/typed** (kein NaN/Infinity, kein string-vs-number).
- Verletzung (negativ, NaN, Overflow, falscher Typ) → **422 reject**, nie evaluieren.

### 4 · `rail_verified` Minimum-Security-Contract (ENTSCHEIDUNG: jetzt definieren)
`rail_verified`-Status (= Voraussetzung für hartes ALLOW einer Betrags-Constraint) wird NUR vergeben, wenn **alle drei** erfüllt:
- **(a) Signatur gegen konfigurierten Trust-Anchor verifiziert** (Rail-Issuer-Key aus konfigurierter Allowlist, nicht beliebig).
- **(b) Replay-protected** (Nonce ODER frisches Timestamp-Fenster im signierten Intent).
- **(c) Intent-bound:** Betrag + Empfänger im signierten Intent **== action** (action_context-Werte müssen mit dem signierten Intent übereinstimmen, nicht nur "ein gültiger Intent existiert").
Fehlt eines der drei → **kein `rail_verified`** → `self_asserted`-Pfad (required Betrags-Constraint → Default-DENY im enforce-mode).
**Bar steht jetzt; die konkrete x402/AP2-Anbindung bleibt Follow-up (D-EVAL-5).**

### 5 · vc_id ↔ Envelope-Binding (formal — Substitution-Schutz)
`action_context.vc_id` **MUSS** an den geladenen Envelope binden, sonst DENY:
- **Methode:** `vc_id == aae_envelopes.aae_id` (direkte Bindung) **ODER** `vc_id` liegt in der **Delegation-Chain** des Envelopes (`agent_delegations` mit passender `aae_id`, geprüft über die `(parent_did,child_did,aae_id)`-Kette).
- Kein Match → DENY (`reason=vc_envelope_binding_failed`). Verhindert, dass ein gültiger Envelope mit einer fremden `vc_id` kombiniert wird (Substitution-Attack).

## Bestätigte Stärken (behalten)
TOCTOU-Mitigation (aae_evaluations + advisory-lock), Default-DENY + value_source-Differenzierung, append-only Audit-Trail.

## `aae_evaluations` (final, v3)
```
aae_evaluations(
  eval_id        PK,
  aae_ref        -> aae_envelopes(aae_ref) (FK wo möglich),
  agent_did      text,
  action_context jsonb,
  verdict        text CHECK (verdict IN ('ALLOW','DENY')),
  value_source   text CHECK (value_source IN ('rail_verified','self_asserted','n/a')),
  evaluations    jsonb,
  evaluator_version text,
  verdict_signature text,   -- Ed25519 über JCS(domain-tag || {eval_id,aae_ref,agent_did,verdict,value_source,evaluator_version,timestamp})
  created_at     timestamptz NOT NULL DEFAULT now()
)
```

## OFFEN (Deferrals, NICHT Blocker)
- **D-EVAL-5:** x402/AP2-Intent-Format + Trust-Anchor-Konfiguration (volle Payment-Rail-Anbindung) — Prinzip/Bar in #4 fixiert, Anbindung = Follow-up.
- `revocation_check` = eigene SSRF-egress-proxy-Subkomponente.

## Nächster Schritt nach Sign-off
Brief → **ai_review.py SECURITY-Modus** (Re-Review, kein Single-LLM) → bei Freigabe: Code komponentenweise (`aae_evaluations`-Migration + Verdict-Signing + advisory-lock-Eval-Tx + Per-Type-Handler + evaluate-Endpoint + violation-Write), je eigener PR, Pre-Commit-Diff-Verify.
