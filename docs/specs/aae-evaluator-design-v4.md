# Architektur-Brief — Komponente 2: AAE Evaluator (v4, FINAL)
**Status:** **FINAL für Implementierung** (design-only Brief abgeschlossen). Verbleibende Krypto-Präzision wird **am CODE** verifiziert (Tests + Security-Code-Review des fertigen Evaluators), **NICHT** in weiterer Brief-Review-Runde.
**Supersedes:** `docs/specs/aae-evaluator-design-v3.md` (v3, PR #114) — v3 bleibt Audit-Trail (Kette v1→v2→v3→v4 erhalten).
**Review-Basis:** Security-Runde 3 (`~/moltstack/reviews/20260601_122947_C2-aae-evaluator-v3_review.md`) — Verdikt v3 = ÜBERARBEITEN, v2-Criticals geschlossen, 3 Krypto-Criticals + 2 Refinements. v4 foldet diese.
**Datum:** 2026-06-01 · **Autor:** Lars Kroehl
**Referenzen:** ADR-D3-v3 (ACCEPTED), `docs/specs/aae-constraint-taxonomy.md`, D1-Baseline-B-Block, `app/signature.py`.

## Bestätigt GESCHLOSSEN (v1–v3, nicht erneut öffnen)
- Dedizierter `POST /vc/aae/evaluate` + action_context; IPR pure Provenance. Verdict-Form = live ConstraintEvaluation (5/5 vectors).
- **C1 TOCTOU:** Count über `aae_evaluations` (nicht IPR) + advisory-lock + Count&INSERT in einer Tx.
- **C2 value-authenticity:** `rail_verified` vs `self_asserted`; Server-Zeit; `agent_did==principal`.
- **Advisory-Lock 64-bit, integer-minor-units, rail_verified-3-Kriterien-Contract, vc_id↔Envelope-Binding** — alle bestätigt.
- Default-DENY, immutable store, append-only `aae_evaluations`, Architektur-Guards. `revocation_check` DEFERRED.

## RESOLVED — Krypto-Criticals (v3-Runde), FINAL

### 1 · Signatur-Payload deckt den vollen Record (kein Audit-Forge)
v3 signierte nur Metadata → `action_context`/`evaluations` waren nachträglich manipulierbar.
- **Signiert wird der Hash des VOLLEN kanonischen Eval-Records:**
  `record = { eval_id, aae_ref, agent_did, action_context, evaluations, verdict, value_source, evaluator_version, timestamp, nonce }`
  `verdict_signature = Ed25519( DOMAIN_TAG_BYTES || JCS(record) )`.
- Damit sind `action_context` (inkl. value) **und** die per-constraint `evaluations` **mit-signiert** — eine nachträgliche Änderung an einem gespeicherten ALLOW bricht die Signatur.

### 2 · Advisory-Lock: single-bigint (umgeht int4-signedness)
```sql
-- key aus den ersten 64 bit von sha256(agent_did || ':' || aae_ref):
pg_advisory_xact_lock( ('x' || substr(encode(digest(agent_did||':'||aae_ref,'sha256'),'hex'),1,16))::bit(64)::bigint )
```
`bit(64)::bigint` mappt korrekt auf signed bigint (kein int4-MSB-Overflow). Transactional → Release bei COMMIT.

### 3 · Replay-Schutz (normativ)
- **Nonce-Store:** `nonce` ist Teil des signierten Records; **Uniqueness via `aae_evaluations`** — UNIQUE-Constraint auf `nonce` (bzw. `(agent_did, nonce)`); Wiedervorlage → Insert scheitert → DENY (Replay erkannt).
- **Clock-Skew-Window:** Server-Zeit gilt; ein Intent/Request-`timestamp` wird nur innerhalb der **D1-B-Block-Clock-Drift-Toleranz** akzeptiert (gleiche Toleranz wie VALIDITY-Prüfung, nicht neu erfinden). Außerhalb → DENY.
- **`eval_id`-Uniqueness:** PK/UNIQUE auf `eval_id` (app-generiert, z.B. `eval_` + uuid4) — jede Eval eindeutig.

### 4 · JCS-Byte-Ordering (Domain-Separation korrekt)
Domain-Tag wird auf **Byte-Ebene NACH der Serialisierung** vorangestellt, NICHT in das JSON gemischt:
`signing_input = DOMAIN_TAG_BYTES || JCS(record)` wobei `DOMAIN_TAG_BYTES = b"moltrust:aae-verdict:v1\x00"` (fester Präfix-String + Trenner). JCS(record) ist gültiges RFC-8785-JSON; die Konkatenation erfolgt auf den serialisierten Bytes.

### 5 · `kid` auf der Verdict-Signatur (Rotation)
`aae_evaluations.verdict_kid` speichert die Key-ID des signierenden registry-keys (z.B. `moltrust-registry-2026-v1`, vgl. CAEP `kid`). Verifier wählt den Public-Key per `kid` → Key-Rotation ohne Bruch alter Signaturen.

## `aae_evaluations` (FINAL)
```
aae_evaluations(
  eval_id        text PRIMARY KEY,                 -- app-generiert, unique
  aae_ref        text REFERENCES aae_envelopes(aae_ref),  -- FK wo möglich
  agent_did      text NOT NULL,
  action_context jsonb NOT NULL,
  evaluations    jsonb NOT NULL,                   -- per-constraint {type,threshold,current_value,delta,verdict,reason}
  verdict        text NOT NULL CHECK (verdict IN ('ALLOW','DENY')),
  value_source   text NOT NULL CHECK (value_source IN ('rail_verified','self_asserted','n/a')),
  evaluator_version text NOT NULL,
  nonce          text NOT NULL,
  verdict_signature text NOT NULL,                 -- Ed25519(DOMAIN_TAG_BYTES || JCS(full record))
  verdict_kid    text NOT NULL,                    -- Key-ID für Rotation
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (agent_did, nonce)                         -- Replay-Schutz
)
```

## OFFEN (Deferrals, NICHT Blocker — am Code/Follow-up)
- **D-EVAL-5:** x402/AP2-Intent-Format + Trust-Anchor-Konfiguration (volle Payment-Rail-Anbindung). Prinzip/Bar fixiert (v3 #4).
- `revocation_check` = eigene SSRF-egress-proxy-Subkomponente.
- Verbleibende Krypto-Implementierungs-Präzision (exakte Byte-Encodings, Testvektoren) → **am Code** (Unit-Tests + Security-Code-Review), nicht weitere Brief-Review.

## Nächster Schritt (Code-Phase)
Brief ist FINAL → Code komponentenweise, je eigener PR, Pre-Commit-Diff-Verify + Security-Code-Review des fertigen Evaluators (kein Single-LLM):
1. `aae_evaluations`-Migration (FK/CHECK/UNIQUE).
2. Verdict-Signing (`app/signature.py`-Bausteine, Domain-Tag, kid) + Verify-Pfad.
3. Per-Type-Handler + advisory-lock-Eval-Transaktion (Count über aae_evaluations).
4. `POST /vc/aae/evaluate`-Endpoint + numeric-hardening (D1-B-Block) + vc_id-Binding.
5. violation_records-Write bei DENY.
