# Architektur-Brief — Komponente 1: aae_envelopes Store
**Status:** DESIGN-BRIEF (kein Code). Vorschlag für Sign-off → danach Review-Pipeline (kein Single-LLM für security-critical) → DANN erst Migration-Code.
**Kontext:** ADR-D3-v3 (ACCEPTED, PR #107). Erste von vier Enforcement-Komponenten (Store → Evaluator → enforce-mode → revocation_check). HARD GATE erfüllt; dieser Brief ist der "architecture-brief-before-coding"-Schritt.
**Datum:** 2026-05-31 · **Autor:** Lars Kroehl
**Referenzen:** `docs/decisions/ADR-D3-mandate-enforcement-v3.md`, `docs/specs/aae-constraint-taxonomy.md` (normativ).

## Zweck
Persistenter, auflösbarer Store für AAE-Envelopes, gekeyed by Content-Hash. Schließt Gap 1 aus dem D3-Scope: der Evaluator kann Constraints nur prüfen, wenn das `mandate/constraints/validity`-JSON abfragbar ist (heute nur per Hash referenziert, nirgends materialisiert).

## Modellierungs-Entscheidung (getroffen)
**`aae_ref` ≠ `aae_id` — zwei verschiedene Konzepte, NICHT konflieren:**
- **`aae_ref`** = SHA-256-**Content-Hash** des kanonischen Envelopes. PRIMARY KEY. Format byte-identisch zur live `interaction_proof_records.aae_ref` → driftfreier Join an die Evidence-Schicht.
- **`aae_id`** = VC-**Identifier** (Ausstellungs-Identität, = `credentialSubject`/VC `id`). Separate Spalte, joint an `agent_delegations`-Tupel `(parent_did, child_did, aae_id)`.
- **single_use-Replay-Schutz operiert auf `aae_id`, NICHT `aae_ref`** (Replay = dieselbe ausgestellte Autorisierung erneut, nicht derselbe Inhalt). Unique-Constraint für single_use daher auf **`(aae_id, scope_canonical)`** — explizit, damit die Implementierung nicht die falsche Spalte nimmt.

> Merksatz: `aae_ref` = WAS (Inhalt), `aae_id` = WELCHE Ausstellung. Content kann identisch sein bei verschiedenen Ausstellungen; Replay-Schutz muss die Ausstellung treffen.

## DDL (VORSCHLAG — design-only, geht nach Sign-off in Review)
```sql
CREATE TABLE IF NOT EXISTS aae_envelopes (
    aae_ref          varchar(100) PRIMARY KEY
                     CHECK (aae_ref ~ '^sha256:[a-f0-9]{64}$'),   -- byte-identisch zu interaction_proof_records.aae_ref
    aae_id           varchar(255) NOT NULL,                       -- VC-Identifier, Join an agent_delegations
    mandate_scope    jsonb        NOT NULL,                       -- MANDATE-Block scope/actions
    constraints      jsonb        NOT NULL,                       -- jedes Objekt {type, value/window, required} — required INLINE (C5)
    validity         jsonb        NOT NULL,                       -- {not_before, not_after, revocation_check?, single_use?}
    scope_canonical  bytea        NOT NULL,                       -- JCS-canonical bytes von scope, app-seitig berechnet VOR INSERT
    aae_version      varchar(20)  NOT NULL,                       -- Version-Pinning
    taxonomy_version varchar(20)  NOT NULL,                       --   (3 Spalten gegen silent enforcement-downgrade)
    evaluator_version varchar(20),                                --   evaluator_version im Verdict mitgeführt; hier Soll-Pin
    raw_canonical    bytea        NOT NULL,                       -- kanonischer Envelope für Re-Verify (Hash-Recompute)
    created_at       timestamptz  NOT NULL DEFAULT now()
);
-- single_use Replay-Schutz (operiert auf aae_id, NICHT aae_ref):
CREATE UNIQUE INDEX IF NOT EXISTS uq_aae_single_use
    ON aae_envelopes (aae_id, scope_canonical);
-- Join-Beschleunigung an Evidence + Delegation:
CREATE INDEX IF NOT EXISTS idx_aae_envelopes_aae_id ON aae_envelopes (aae_id);
```
**Anmerkung Unique-Constraint:** der single_use-State (D-3, das *Verbrauchen*) ist konzeptionell eine eigene `consumption`-Tabelle (siehe ADR-v3 D-3). Der Unique-Index hier auf `(aae_id, scope_canonical)` modelliert die **Identitäts-Invariante des Envelopes**; die *Verbrauchs*-Invariante (INSERT-on-use → Unique-Violation = DENY) gehört zur enforce-mode/Evaluator-Komponente und wird dort spezifiziert. HIER nur benannt, nicht gelöst.

## Canonicalization-Hinweis
- JCS (RFC 8785) via **`jcs 0.2.1` (im venv vorhanden)** läuft in **Python VOR dem INSERT**. Postgres kann kein JCS — die DB speichert nur die fertigen canonical bytes (`scope_canonical`, `raw_canonical`) und erzwingt Uniqueness/Format.
- `aae_ref` = `'sha256:' || hex(sha256(raw_canonical))`; muss app-seitig recomputebar sein (Re-Verify-Pfad).

## Migration
- Datei: **`app/migrations/010_aae_envelopes.sql`** (nächste Nummer nach `009_agent_budget_caps.sql`).
- **Idempotent** (`IF NOT EXISTS` durchgängig), **additiv** (nur CREATE, kein ALTER an Bestandstabellen), **reversibel** (Down = `DROP TABLE IF EXISTS aae_envelopes` — keine Fremddaten betroffen).
- **fork-ci.yml:** neue Zeile nach Muster 008/009 (Workflow-Zeilen 156-157):
  `psql -h localhost -U moltstack -d moltstack -v ON_ERROR_STOP=1 -f app/migrations/010_aae_envelopes.sql`
- ⚠️ Legacy `~/moltstack/migrations/` ist **NICHT** der Pfad — die CI-applied Konvention ist `app/migrations/NNN_*.sql`.

## Mapping an Live-Tabellen (kein Drift)
| Bezug | Live-Spalte | Store-Spalte | Join |
|---|---|---|---|
| Evidence | `interaction_proof_records.aae_ref` (varchar 100, CHECK `^sha256:[a-f0-9]{64}$`) | `aae_envelopes.aae_ref` (PK, **gleiche CHECK**) | `ipr.aae_ref = env.aae_ref` |
| Delegation | `agent_delegations.aae_id` (varchar 255, Teil von `(parent_did,child_did,aae_id)`) | `aae_envelopes.aae_id` | `del.aae_id = env.aae_id` |
- Format-Identität bei `aae_ref` ist die Drift-Garantie: jede IPR mit gültigem `aae_ref` kann ihren Envelope auflösen, ohne Format-Übersetzung.
- `aae_id` bleibt varchar(255) konsistent zu `agent_delegations` (kein engerer Typ, sonst Join-Bruch bei Bestandsdaten).

## Implementation-Contract-Items, die HIER landen
- ✅ **JCS-Canonicalization** (`scope_canonical`, `raw_canonical`) — app-seitig, vor INSERT.
- ✅ **Version-Pinning** (`aae_version`, `taxonomy_version`, `evaluator_version`) — gegen silent enforcement-downgrade.

**NICHT hier (spätere Komponenten — nur genannt):** SSRF-Blocklist + Validation-Order (→ revocation_check), Circuit-Breaker fail-closed (→ revocation_check/Evaluator), M-of-N + No-Downgrade + Replay-Nonces (→ enforce-mode), Active-Cache-Invalidation (→ revocation_check), Anchoring/Salt-Store (→ enforce-mode/Anchoring).

## Offene Punkte für Sign-off
1. `evaluator_version` NULLable lassen (Soll-Pin optional) oder NOT NULL erzwingen?
2. `constraints jsonb` zusätzlich eine CHECK auf Array-of-Objects-Form, oder Validierung rein app-seitig (Evaluator-Komponente)?
3. Brauchen wir eine FK `aae_envelopes.aae_id` → eine kanonische Quelle, oder bleibt `aae_id` referenz-frei (da `agent_delegations.aae_id` nullable + nicht unique allein)?

## Nächster Schritt nach Sign-off
Brief → Review-Pipeline (Multi-Model, security-Modus) → bei Freigabe: `010_aae_envelopes.sql` + fork-ci-Zeile als **eigener PR** (eine Komponente = ein PR), mit Pre-Commit-Diff-Verify.
