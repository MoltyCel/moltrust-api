# AAE Constraint Taxonomy (normative reference)
**Source:** draft-kroehl-agentic-trust-aae-00 §2.2–2.4, the text this was written against; same sections and titles in -02 (checked 2026-10-23)
**Status:** Design reference for D3 MANDATE-Enforcement. NOT implementation. HARD GATE active (D1/PR#41, 3-reviewer consensus before code).
**Date:** 2026-05-30

## Zweck
Normative Grundlage für den D3-Evaluator. Definiert welche Constraint-Typen maschinell auswertbar sind und mit welcher Semantik. Bezugsdokument für die 3-Reviewer-Runde.

## MANDATE (§2.2) — was der Agent darf
- `scope` (OPTIONAL) — Einschränkung auf Vertical/Domain
- `action` allowlist + optionales `delegation`-Objekt

## CONSTRAINTS (§2.3) — Schranken auf Aktionen
Erweiterbar. Jede Constraint trägt ein `required`-Flag. **Default `required:true` wenn fehlend.**

| Type | Required fields | Evaluator-Semantik | Stateful? |
|---|---|---|---|
| `max_transaction_value` | value (number), currency (ISO 4217) | Wert-Schranke pro einzelner tx | nein |
| `allowed_domains` | value (array) | Domain-Allowlist | nein |
| `rate_limit` | value (int), window (ISO 8601 duration z.B. PT1H) | RP MUSS akzeptierte Aktionen im Fenster zählen | **ja — Counter** |

## VALIDITY (§2.4) — temporale Schranken, alle MUST-enforced
- `not_before` / `not_after` (REQUIRED, RFC 3339 UTC Z) — vor/nach ablehnen
- `revocation_check` (OPTIONAL, HTTPS URI-Template {id}/{did}) — MUSS abfragen; **fail-closed**: ablehnen bei revoked:true, unparsebar, oder indeterminate (5xx/Netz). Auditierbares fail-open nur für low-risk, governed actions.
- `single_use` (OPTIONAL bool, default false) — **stateful**, Replay per VC-id ablehnen; Multi-Node-State MUSS geteilt sein.

## KRITISCHE ENFORCEMENT-REGEL (treibt den gesamten Evaluator)
RP MUSS jede erkannte Constraint durchsetzen. RP MUSS die AAE ablehnen wenn eine als `required:true` markierte Constraint unbekannt oder unauswertbar ist. Unbekannte Constraints dürfen NUR ignoriert werden wenn explizit `required:false`.

## Delegation-Narrowing-Invarianten (5, refit-v2 §209)
Für Ketten: scope-Subset, spend<=, time<=, depth<=, keine Self-Issuance.

## Interop — aeoess ConstraintEvaluation-Schema
facet/limit/actual/delta ↔ unser type/threshold/current_value/delta. Fertige Verdict-Form + Cross-Engine-Testvektoren.

## Konsequenzen für die 3 D3-Gaps (PR #102)
1. **aae_envelopes-Store** MUSS typisierte Constraint-Objekte keyed by aae_ref(sha256) persistieren UND das per-Constraint `required`-Flag behalten (entscheidet reject-vs-ignore). Flat-Blob reicht NICHT.
2. **Evaluator** braucht Per-Type-Handler-Set. rate_limit + single_use erzwingen stateful Counter (neue State-Tabellen). revocation_check erzwingt outbound HTTPS mit fail-closed-Default.
3. **enforce-mode-Gate** — constraint_mode bekommt enforce-State (heute nur none/inherit).
