# Architektur-Brief — Komponente 2: AAE Evaluator
**Status:** DESIGN-BRIEF (kein Code). Vorschlag für Sign-off → danach Review-Pipeline (security-Modus, höchste Kritikalität) → DANN erst Code.
**Kontext:** ADR-D3-v3 (ACCEPTED, #107). Zweite von vier Enforcement-Komponenten (Store ✅ #109/#110/#111 → **Evaluator** → enforce-mode → revocation_check). Der Evaluator ist die **ALLOW/DENY-Komponente** — höchste Kritikalität.
**Datum:** 2026-06-01 · **Autor:** Lars Kroehl
**Referenzen:** `docs/decisions/ADR-D3-mandate-enforcement-v3.md`, `docs/specs/aae-constraint-taxonomy.md` (normativ), `app/enforcement/envelope_store.py` (#110).

## Zweck
Wertet eine geplante Aktion gegen die CONSTRAINTS + VALIDITY eines gespeicherten AAE-Envelopes aus und liefert ein ALLOW/DENY-Verdict pro Constraint + aggregiert. Schließt Gap 2 aus dem D3-Scope (Evaluator joint Envelope × Aktion → Verdict).

## Load-bearing Entscheidung (GETROFFEN): dedizierter evaluate-Endpoint, IPR NICHT erweitern
- **`POST /vc/aae/evaluate`** nimmt einen **action_context** (geplanter Wert/Domain VOR der Aktion). IPR bleibt **pure Provenance** (geloggter Wert NACH der Aktion).
- **Begründung:** Gate-Zeitpunkt (vor Aktion, *geplanter* Wert) ≠ IPR-Logging (nach Aktion, *geloggter* Wert) — verschiedene Zeitpunkte und Payloads. IPR um Gate-Felder zu erweitern würde zwei Semantiken vermischen.
- IPR wird weiterhin **gelesen** (für stateful Checks: rate_limit-Count, single_use), aber nicht um action-context-Felder erweitert.

## Endpoint
`POST /vc/aae/evaluate` (tags: AAE Enforcement) — Auth erforderlich (wie `/vc/aae/submit`, `verify_api_key_or_did`).
- **Input:** `{aae_ref, action_context: {value, currency, domain, vc_id, agent_did, ...}}`.
- Lädt Envelope aus `aae_envelopes` per `aae_ref`. Existiert nicht → DENY (`reason=envelope_not_found`).
- Evaluiert jede Constraint + VALIDITY. Aggregiert: **ALLOW nur wenn alle auswertbaren required-Constraints ALLOW** und keine VALIDITY-Verletzung.
- **Output:** aggregiertes `{verdict: ALLOW|DENY, evaluations: [<per-constraint>], reason}`.

## Verdict-Form (WIEDERVERWENDEN, nicht neu erfinden)
Live **ConstraintEvaluation-Mapping** (qntm/APS-interop, 5/5 test-vectors live — `docs/specs/moltrust-v08-patch-notes.md`):
```
facet   ↔ type            (Constraint-Typ)
limit   ↔ threshold       (aus Envelope-Constraint)
actual  ↔ current_value   (aus action_context / IPR)
delta   ↔ delta           (threshold − actual, explizit für cross-engine)
```
Pro-Constraint-Verdict: `{type, threshold, current_value, delta, verdict: ALLOW|DENY, reason}`.

## Per-Type-Handler (mirror `anomaly.py compute_flags`-Struktur, aber ALLOW/DENY statt advisory)
Signatur: `async def _eval_<type>(constraint: dict, action_context: dict, conn) -> dict` (gibt Per-Constraint-Verdict).

| Typ | Logik | State |
|---|---|---|
| `max_transaction_value` | `action_context.value` vs `threshold`; **currency-match prüfen** (Mismatch → DENY) | stateless |
| `allowed_domains` | `action_context.domain` ∈ allowlist | stateless |
| `rate_limit` | `COUNT` IPR-rows für `agent_did`/`aae_ref` über ISO-8601-window via `produced_at` vs `value` | **STATEFUL** |
| `not_before`/`not_after` (VALIDITY) | `now()` vs window (RFC 3339) | stateless |
| `single_use` (VALIDITY) | `aae_id` schon in akzeptierter Interaction genutzt? (DB-invariant `uq_aae_single_use` + IPR-count über `aae_ref`) | **STATEFUL** |
| `revocation_check` (VALIDITY) | **DEFERRED** → eigene Sub-Komponente (outbound HTTPS braucht SSRF-Egress-Proxy, ADR-v3 C2/D-2). Hier nur benannt, NICHT gebaut. Bis dahin: required revocation_check **nicht auswertbar → Default-DENY** (siehe kritische Regel). |

**Stateful-Locking (ADR-v3 D-3):** `rate_limit`- + `single_use`-Counts unter `SERIALIZABLE` bzw. `SELECT ... FOR UPDATE`, damit parallele evaluate-Calls den Count nicht gleichzeitig lesen und das Limit umgehen (TOCTOU).

## KRITISCHE REGEL (Taxonomie / AAE draft-04 §2.3)
- Jede `required:true`-Constraint **MUSS auswertbar** sein — sonst **DENY** (Default-DENY).
- **Unbekannter** Typ mit `required:true` → **DENY**.
- Unbekannter Typ mit `required:false` → **ignore** (kein Verdict, kein Block).
- Auch Auswertungs-Fehler (fehlendes `action_context`-Feld, Parse-Fehler) bei `required:true` → **DENY**, nie still ALLOW.

## violation_records — Write-Contract bei DENY
Bei aggregiertem DENY (oder pro verletzter required-Constraint) ein `violation_records`-Insert. **Alle Spalten TEXT, kein FK, `id` ohne Default → app-generiert:**
```
id                    = app-generiert (z.B. 'viol_' + uuid4 hex)
agent_did             = action_context.agent_did
principal_did         = envelope issuer_did (oder mandate-principal)
violation_type        = aus Constraint-type (z.B. 'max_transaction_value_exceeded')
interaction_proof_id  = action_context.vc_id / verknüpfte IPR-id (falls vorhanden)
description            = reason + delta
adjudicator_type      = 'evaluator'     (NICHT default 'external' — Evaluator ist automated adjudicator)
confirmed_at          = now() als TEXT (Spalte ist text)
reversed              = false (reversible-flag; spätere Reversal via reversal_date/reference)
created_at            = now()::text
```

## Version-Pinning
Beim ersten Eval eines Envelopes `aae_envelopes.evaluator_version` setzen (war NULLable bis hier). ⚠️ Konflikt-Hinweis: die Tabelle ist **immutable** (UPDATE geblockt!) → `evaluator_version` kann NICHT per UPDATE nachgetragen werden. **Design-Konsequenz (offen, siehe D-EVAL):** entweder evaluator_version schon beim `submit` setzen, oder Pinning in einer separaten `aae_evaluations`-Tabelle führen statt im immutable Store. → Reviewer-Entscheidung.

## Architektur-Guards (V1.15, nicht verhandelbar)
- **Unabhängige Evaluation** — MolTrust evaluiert; kein Agent-Self-Check.
- **Kein Volllog** — nur Hash/Attestation/Verdict, kein Inhaltslog (DSGVO).

## OFFENE Designpunkte für Sign-off
**D-EVAL-1 · action_context-Schema final.** Welche Felder pro Constraint-Typ zwingend: `max_transaction_value`→`{value, currency}`; `allowed_domains`→`{domain}`; `rate_limit`/`single_use`→`{agent_did, aae_id/vc_id}`; immer `{aae_ref}`. Fehlt ein für eine required-Constraint nötiges Feld → Default-DENY. Exakte Schema-Festlegung = Sign-off.
**D-EVAL-2 · rate_limit window-count Query + Locking.** COUNT über `interaction_proof_records` (Index `idx_ipr_agent_did`) gefiltert auf `produced_at >= now() - window`. Window-Parsing (ISO-8601-Duration). Locking-Granularität: SERIALIZABLE-Transaktion vs. advisory-lock pro `(agent_did, constraint)`. → Reviewer.
**D-EVAL-3 · Inline-gate vs. advisory-return.** ADR-v3 sagt **runtime-gate pro Interaction** — aber **wer enforced das DENY operativ?** (a) evaluate-Endpoint ist rein beratend (gibt Verdict, Caller MUSS selbst blocken) vs. (b) Evaluator hängt sich in einen verpflichtenden Pfad (z.B. IPR-submit lehnt bei DENY ab). Empfehlung-Tendenz: (a) für v1 (beratend + violation_records-Audit), (b) als enforce-mode-Komponente (D3 Komponente 3). → Reviewer.
**(Bonus) D-EVAL-4 · evaluator_version-Pinning trotz Immutability** (siehe Version-Pinning oben).

## Nächster Schritt nach Sign-off
Brief → **ai_review.py SECURITY-Modus** (Evaluator = ALLOW/DENY-Komponente, höchste Kritikalität; kein Single-LLM) → bei Freigabe: Code komponentenweise (Verdict-Schema + Per-Type-Handler + evaluate-Endpoint + violation-Write), je eigener PR, Pre-Commit-Diff-Verify.
