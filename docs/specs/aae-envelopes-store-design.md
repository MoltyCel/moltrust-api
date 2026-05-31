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
    aae_id           varchar(255) NOT NULL,                       -- VC-Identifier, Join an agent_delegations; 255 = KNOWN LIMIT (von live geerbt, s.u.)
    issuer_did       varchar(255) NOT NULL,                       -- Origin-Authentizität: Aussteller-DID (nur Storage; VERIFY = Acceptance-Gate D-1)
    envelope_signature text       NOT NULL,                       -- AAE-Signatur (nur Storage; VERIFY deferred zu Acceptance-Gate D-1)
    mandate_scope    jsonb        NOT NULL,                       -- MANDATE-Block scope/actions
    constraints      jsonb        NOT NULL CHECK (jsonb_typeof(constraints) = 'array'),  -- required INLINE pro Objekt (C5); Shape app-seitig
    validity         jsonb        NOT NULL CHECK (jsonb_typeof(validity) = 'object'),    -- {not_before, not_after, revocation_check?, single_use?}
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

## Sign-off RESOLVED (2026-05-31)
Die drei offenen Punkte aus dem Brief sind entschieden:

1. **`evaluator_version` = NULLable.** Der Soll-Pin der Evaluator-Version steht erst beim ersten Enforcement fest; bis dahin NULL. → DDL bleibt `evaluator_version varchar(20)` (ohne NOT NULL).
2. **`constraints jsonb` = DB-CHECK nur auf Struktur, Shape app-seitig.** DB erzwingt lediglich Array-Form via `CHECK (jsonb_typeof(constraints) = 'array')`; die volle Per-Constraint-Shape-Validierung (typisierte `{type,value/window,required}`-Logik) ist DB-unfähig und gehört in die Evaluator-Komponente (app-seitig). → DDL ergänzt:
   ```sql
   constraints jsonb NOT NULL CHECK (jsonb_typeof(constraints) = 'array'),
   ```
3. **Kein FK auf `aae_id`.** `agent_delegations.aae_id` ist nullable und allein nicht unique (nur im Tupel `(parent_did,child_did,aae_id)`) → ein FK ist technisch nicht setzbar. `aae_id` bleibt **reference-free**; Join-Integrität wird app-seitig sichergestellt (kein DB-FK). → DDL unverändert (`aae_id varchar(255) NOT NULL`, kein REFERENCES).

**Konsequenz für die DDL oben:** einzige Änderung ggü. dem Vorschlag = die `jsonb_typeof='array'`-CHECK auf `constraints`. `evaluator_version` (NULLable) und `aae_id` (kein FK) entsprechen bereits dem DDL-Vorschlag. Damit ist die DDL sign-off-fertig für den Review-Pipeline-Pass.

## Review-Härtung (aus Security-Review `20260531_214349_C1-aae-envelopes-store`)
Verdikt 1. Pass = ÜBERARBEITEN. Folgende 4 Punkte in die DDL/Impl eingearbeitet (keine Architektur-Änderung, reine Härtung):

**1. DB-level Hash-Binding (Critical) + Immutability (Critical, Runde 2).** `aae_ref` darf nicht frei mit beliebigem `raw_canonical` kombinierbar sein → Hash-Bindung wird **DB-Invariante**. Trigger ist **INSERT-only** (NICHT `OR UPDATE` — Re-Review zeigte: `OR UPDATE` erlaubte silent rewrite mutierbarer Crypto-Envelopes). Envelopes sind **immutable**: UPDATE/DELETE werden geblockt.
```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- Hash-Binding nur bei INSERT:
CREATE OR REPLACE FUNCTION aae_envelopes_bind_ref() RETURNS trigger AS $$
BEGIN
  NEW.aae_ref := 'sha256:' || encode(digest(NEW.raw_canonical, 'sha256'), 'hex');
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_aae_bind_ref BEFORE INSERT ON aae_envelopes
  FOR EACH ROW EXECUTE FUNCTION aae_envelopes_bind_ref();
-- Immutability: UPDATE/DELETE hart verbieten (append-only Store):
CREATE OR REPLACE FUNCTION aae_envelopes_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'aae_envelopes is append-only: % verboten', TG_OP;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_aae_immutable BEFORE UPDATE OR DELETE ON aae_envelopes
  FOR EACH ROW EXECUTE FUNCTION aae_envelopes_immutable();
```
(Trigger berechnet `aae_ref` server-seitig → Format-CHECK bleibt Defense-in-Depth. Grant-level zusätzlich: `REVOKE UPDATE, DELETE ON aae_envelopes FROM moltstack` als zweite Schranke.)

**2. Size/DoS-Limits (Critical).** `octet_length()`-CHECKs gegen Resource-Exhaustion; Unique-Index auf **gehashtem** `scope_canonical` (fixed-size) statt rohem `bytea` → umgeht das Postgres-B-Tree-Limit (~2712 Bytes/Index-Eintrag), das große legitime Scopes sonst am INSERT scheitern lässt:
```sql
-- CHECKs (Beispielwerte, Reviewer-bestätigbar):
ALTER TABLE aae_envelopes
  ADD CONSTRAINT chk_scope_canon_size CHECK (octet_length(scope_canonical) <= 8192),
  ADD CONSTRAINT chk_raw_canon_size   CHECK (octet_length(raw_canonical)   <= 1048576);
-- Unique-Index auf Hash des canonical scope (fixed-size 32 Byte):
DROP INDEX IF EXISTS uq_aae_single_use;
CREATE UNIQUE INDEX IF NOT EXISTS uq_aae_single_use
  ON aae_envelopes (aae_id, digest(scope_canonical, 'sha256'));
```

**3. Single-use-Index — durch #2 mitgelöst.** Der gehashte Index `(aae_id, digest(scope_canonical,'sha256'))` ist gleichzeitig der single_use-Replay-Schutz (fixed-size, kein B-Tree-Limit). JCS sorgt dafür, dass semantisch gleiche Scopes byte-identisch kanonisieren → gleicher Hash → Unique-Violation = DENY. Keine separate Maßnahme nötig.

**4. Transaction-Bracketing + JSON-Depth (Hoch).** Schreiboperationen auf `aae_envelopes` + `agent_delegations` laufen **atomar in einer Transaktion** (BEGIN/COMMIT) — das ist die korrekte Mitigation der no-FK-Entscheidung (Race-Conditions ohne FK), NICHT ein nachträglicher FK. Zusätzlich app-seitige **JSON-Depth-Limits** auf `mandate_scope`/`constraints`/`validity` vor INSERT (DB kann Nesting-Tiefe nicht begrenzen).

> Diese 4 Punkte ändern KEINE Architektur und keine der 3 Sign-off-Resolutions — der gehashte Index lässt die `(aae_id, scope_canonical)`-Invariante semantisch unverändert (nur fixed-size); no-FK bleibt (Mitigation via Transaktion). Re-Review-tauglich.

## Final-Härtung (Runde-2-Review `20260531_215029`, RESOLVED)
Re-Review bestätigte die 3 Original-Criticals als GESCHLOSSEN (Hash-Binding, Size/DoS, single_use). 3 neue/eskalierte Punkte entschieden:
1. **Immutability (Critical, selbst-verursacht):** Trigger jetzt **INSERT-only** + UPDATE/DELETE-Blocker (siehe Härtung #1 oben). Crypto-Envelopes append-only.
2. **`aae_id`-Länge (Hoch) — ENTSCHEIDUNG: 255 inherit.** W3C-VC-IDs/DID-URLs können >255 sein, aber live `agent_delegations.aae_id` ist `varchar(255)`. Um die Migration **additiv** zu halten, erbt der Store den **255-Bound als KNOWN LIMIT** (dokumentiert). Widening beider Tabellen = **separate spätere Migration** (berührt Bestandstabelle), NICHT diese Iteration.
3. **Origin-Authentizität (Critical) — ENTSCHEIDUNG: Storage hier, Verify später.** `issuer_did` + `envelope_signature` als Spalten im Store (s. DDL). **Signatur-VERIFIKATION gehört zum Acceptance-Gate (D-1)**, nicht zum Store — hier nur Persistenz, damit die Gate-Komponente sie konsumieren kann.
4. **`validity`-CHECK** `jsonb_typeof='object'` gefoldet (s. DDL). Kardinalitäts-/Quota-DoS = **app-seitig** (DB kann Row-Count-Quotas pro issuer nicht sinnvoll erzwingen).

### Future Considerations (NICHT v1-Scope)
- **Post-Quantum (NIST PQC 2024):** `aae_ref` trägt `sha256:`-Prefix → Crypto-Agility-Pfad existiert bereits (späterer Hash-Algo = neuer Prefix, kein Schema-Bruch). Kein v1-Blocker.
- **JSON-Schema Draft 2020-12** für `constraints`/`validity`-Shape: app-seitige Validierung in der Evaluator-Komponente, nicht DB. Future.
- **Widening `aae_id` → TEXT** (beide Tabellen) wenn DID-URLs >255 real auftreten.

## Nächster Schritt nach Sign-off
Brief → Review-Pipeline (Multi-Model, security-Modus) → bei Freigabe: `010_aae_envelopes.sql` (inkl. pgcrypto-Trigger + Size-CHECKs + gehashter Index) + fork-ci-Zeile als **eigener PR** (eine Komponente = ein PR), mit Pre-Commit-Diff-Verify.
