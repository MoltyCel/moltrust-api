# Architektur-Brief — Komponente 2: AAE Evaluator (v2)
**Status:** DESIGN-BRIEF v2 (kein Code). Vorschlag für Sign-off → Re-Review (security) → DANN Code.
**Supersedes:** `docs/specs/aae-evaluator-design.md` (v1, PR #112) — v1 bleibt als **Audit-Trail** (NICHT löschen).
**Review-Basis:** Security-Runde 2026-06-01 (`~/moltstack/reviews/20260601_120003_C2-aae-evaluator_review.md`) — Verdikt v1 = **GRUNDLEGEND ÜBERDENKEN**. v2 foldet alle 4 Criticals + D-EVAL-3 als gelöste Spec.
**Datum:** 2026-06-01 · **Autor:** Lars Kroehl
**Referenzen:** ADR-D3-v3 (ACCEPTED), `docs/specs/aae-constraint-taxonomy.md`, `app/enforcement/envelope_store.py` (#110).

## Unveränderte Basis (aus v1, bestätigt)
- **Load-bearing:** dedizierter `POST /vc/aae/evaluate` mit action_context — IPR bleibt pure Provenance.
- **Verdict-Form** = WIEDERVERWENDUNG live ConstraintEvaluation-Mapping (`facet/limit/actual/delta` ↔ `type/threshold/current_value/delta`, 5/5 vectors, qntm/APS).
- **Default-DENY** auf unauswertbare `required:true`. **Immutable Store**. **Append-only `aae_evaluations`**.
- Per-Type-Handler mirror `anomaly.py` (`async _eval_<type>(...) -> verdict`), aber ALLOW/DENY.
- **Architektur-Guards (V1.15):** unabhängige Evaluation (kein Agent-self-check), kein Volllog.
- `revocation_check` bewusst DEFERRED (SSRF-egress-proxy-Subkomponente).

## RESOLVED — Security-Criticals (v1-Runde)

### C1 · rate_limit / single_use TOCTOU (KORREKTUR von v1-D-EVAL-2)
v1 war FALSCH: Count über IPR + bare `FOR UPDATE`.
- **Count über `aae_evaluations`, NICHT IPR.** IPR wird erst NACH der Aktion geloggt → ein PRE-Action-Gate sähe ~0 (100 parallele Requests umgehen das Limit). `aae_evaluations` wird IM Eval-Moment geschrieben → korrekte Pre-Action-Sicht.
- **`pg_advisory_xact_lock(hashtext(agent_did || aae_ref))`** zu Beginn der Eval-Transaktion — serialisiert konkurrierende Evals pro (agent, envelope). Schließt **Phantom-Reads**, die `SELECT ... FOR UPDATE` (sperrt nur existierende Rows) NICHT verhindert.
- **Count + Eval-INSERT in EINER Transaktion** → atomar; der Lock hält bis Commit, der nächste Eval sieht den committeten Row.
- Single-primary-Postgres-Annahme (ADR-v3 D-3) dokumentiert; Multi-Primary würde advisory-locks brechen → Follow-up.

### C2 · value-authenticity (ENTSCHEIDUNG: gestufte Quelle) — die neue load-bearing Festlegung
Der Kern-Trust-Fix: `action_context.value` ist **client-asserted** → allein NIE Basis für ein hartes ALLOW einer kritischen Constraint.
- **Zahlungen (verifizierbare Quelle):** `value` aus der **Payment-Rail** — x402 / AP2 *signed intent* / on-chain-Betrag — und gegen den **signierten Intent verifiziert**, NICHT aus `action_context.value`.
- **Ohne signierten Rail:** `action_context.value` ist erlaubt, aber das Verdict wird **`value_source: "self_asserted"`** markiert und die **Confidence gesenkt**. Eine `required:true`-Betrags-Constraint **ohne verifizierbare Quelle → Default-DENY im enforce-mode** (self-asserted ergibt NIE hartes ALLOW für eine kritische Constraint).
- **Server-enforced timestamp:** der Server setzt die Auswertungszeit; `action_context.timestamp` ist nur **Metadatum** (kein Backdating von `not_before`/`not_after`).
- **`agent_did` MUSS == auth-principal** (aus `verify_api_key_or_did`); Mismatch → DENY.
- **`vc_id` MUSS an den Envelope binden** (gehört zur `aae_id`-Kette); sonst DENY.

### C3 · numeric hardening (Input-Validierung VOR Eval)
`value`: **`>= 0`** + **finite** (kein NaN/Infinity) + **typed** (Number, kein string-vs-number) + **currency** ISO-4217-**normalisiert**. Verletzung → **422 reject** (nicht evaluieren — negatives/NaN value darf nie in die `<=`-Logik gelangen, `-1000000 <= 500` wäre sonst fälschlich ALLOW).

### C4 · audit integrity
- **Verdicts Ed25519-signiert** (registry-key, wie restlicher Stack — `app/signature.py`/`registry_keys`) und in `aae_evaluations` abgelegt → DB-Compromise kann das Verdict nicht forgen ohne den Key.
- **FK + CHECK-Constraints auf `aae_evaluations` soweit möglich** (z.B. `verdict IN ('ALLOW','DENY')`, `value_source IN (...)`, FK auf `aae_envelopes(aae_ref)` wo es die Immutability nicht bricht).

### D-EVAL-3 · enforce-mode als OBLIGATORISCHER Chokepoint (resolved gegen v1)
Trennung Urteil/Vollstreckung bleibt — **aber mit verbindlichem Integrationspfad:** die Aktion **MUSS** durch `evaluate`; **ohne ALLOW kein Vollzug**. Es gibt keinen Pfad an der Evaluation vorbei. `none`/`inherit` → DENY nur geloggt (Aktion läuft); `enforce` → DENY blockiert. Der Evaluator bleibt mode-unabhängig (urteilt gleich); nur die Vollstreckung in Komponente 3 unterscheidet. **Verbindlich**, nicht advisory-optional.

## `aae_evaluations` (zentral in v2)
Count-Quelle (C1) + signierter Audit-Trail (C4) + version-pin (D-EVAL-4 aus v1):
```
aae_evaluations(
  eval_id        PK,
  aae_ref        -> aae_envelopes(aae_ref) (FK wo möglich),
  agent_did      text,
  action_context jsonb,                 -- inkl. value_source
  verdict        text CHECK (verdict IN ('ALLOW','DENY')),
  value_source   text CHECK (value_source IN ('rail_verified','self_asserted','n/a')),
  evaluations    jsonb,                 -- per-constraint {type,threshold,current_value,delta,verdict,reason}
  evaluator_version text,
  verdict_signature text,               -- Ed25519 über kanonisches Verdict
  created_at     timestamptz NOT NULL DEFAULT now()
)
```
Append-only-Charakter analog Store. Für `rate_limit`-Count: `COUNT(*) WHERE agent_did=$ AND aae_ref=$ AND verdict='ALLOW' AND created_at >= now()-window`.

## action_context-Schema (final, C2/C3-gehärtet)
```
action_context = {
  aae_ref:   str (required),
  vc_id:     str (required, MUSS an Envelope binden),
  agent_did: str (required, MUSS == auth-principal),
  action:    str (required),
  value:     number >=0 finite | null,   # rail-verifiziert ODER self_asserted-markiert
  currency:  str(ISO4217) | null,
  domain:    str | null,
  timestamp: str (RFC3339, METADATA — Server-Zeit gilt)
}
```

## OFFEN (neu aufgetaucht, für Sign-off/Reviewer)
**D-EVAL-5 · Payment-Rail-Verifikation Detail.** Exakte Verifikations-Schnittstelle zu x402/AP2 signed intent (welcher Key, welches Intent-Format) ist eine eigene Integration — im v2 als Prinzip festgelegt (rail-verified vs self_asserted), das Verifikations-Detail = eigener Follow-up bei der Payment-Integration.

## Nächster Schritt nach Sign-off
Brief → **ai_review.py SECURITY-Modus** (Re-Review, kein Single-LLM) → bei Freigabe: Code komponentenweise (`aae_evaluations`-Migration + Verdict-Signing + Per-Type-Handler + advisory-lock-Eval-Tx + evaluate-Endpoint + violation-Write), je eigener PR, Pre-Commit-Diff-Verify.
