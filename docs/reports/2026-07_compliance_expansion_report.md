# Compliance-Expansion — Abschluss-Report (Sprints 1–3)

**Datum:** 2026-07-11 · **Auftrag:** Console-Auftrag „Compliance-Erweiterung, autonomer Durchlauf Sprints 1–3" ·
**Referenz:** compliance_expansion_roadmap_20260711.md, Harald PBA 2026-07-06 · **Ausführung:** vollautonom.

Alle acht Endpoints sind **live und smoke-getestet auf `api.moltrust.ch`** (API-Version **2.5**).
Deploy-Disziplin nach WORKFLOW.md §11 (merge-first, `post-sha==repo-sha`), additive/idempotente Migrationen,
Restart nur nach grünem Sandbox-Suite-Lauf.

## Phase 0 — Verification Gate (EUR-Lex 2024/1689, CELEX 32024R1689, abgerufen 2026-07-11)

Ablage: `docs/spec-fakten/eu-ai-act-2024-1689.md` (+ `docs/spec-fakten/ucan-0.10.0.md` für Sprint 2).

| Fundstelle | Pin | Ergebnis |
|---|---|---|
| Klassifikation | Art 6(1)/(2)/(3), Art 7(2), Annex III (8 Bereiche) | Vollständig verbatim; Profiling-Override Art 6(3) letzter Unterabsatz |
| Prohibited | Art 5(1)(a)–(h) | 8 Kategorien verbatim |
| Declaration | Annex V(1)–(8) | 8 Pflichtfelder → VC-Schema |
| **Incident-Fristen** | Art 73(2)/(3)/(4) + Art 3(49) | **15 Tage generell, 2 Tage kritische Infrastruktur (Art 3(49)(b)), 10 Tage Tod** |
| Anwendung | Art 113(a)–(c) | 2 Aug 2026 generell; Prohibitions 2 Feb 2025; **Art 6(1) High-Risk erst 2 Aug 2027** |
| Logging | Art 12 | Formulierung bleibt „supports Article 12 logging" |

**Verifikations-Hinweis:** Art 12/113-Artikeltexte per EUR-Lex-Single-Page abgeschnitten → gegen die
**offizielle EU-Kommissions-AI-Act-Service-Desk** (`ai-act-service-desk.ec.europa.eu`) gegengeprüft, identisch.
Direkter EUR-Lex-Verbatim-Spot-Check der beiden Artikelkörper = Backlog (kein Blocker; EC-Quelle ist autoritativ).
Die „15 Tage" der PBA sind damit **bestätigt** (als generelle Frist) — plus die zwei kürzeren gestaffelten Fristen.

## Sprint 1 — Compliance-Kern · PR #240 (`e2b6730`) + Fixes #241 (`6992716`), #242 (`db7b82e`)

Endpoints (live, smoke gegen Produktion):
- `POST /compliance/assess` → 200, `risk_tier=high`, 14 Obligations (Annex-III-Fall).
- `POST /compliance/declaration` → 200, VC-Typ `[VerifiableCredential, MolTrustConformityDeclaration]`, signierter Ed25519-Proof, Annex V vollständig.
- `GET /compliance/report/{did}` → 200, `text/html`.

Engine `app/compliance.py` (deterministisch, first-match-wins, eigene Feldnamen, kein Fremd-Vokabular).
Pricing analog VC-Issuance (assess=2, declaration=2, report=1). Additive Migration `compliance_assessments`
+ `ensure_compliance_tables()`-Startup-Hook. **Testabdeckung: 25 Tests** (Klassifikations-Matrix, Annex-V-Builder,
Report-Render/Escaping, Pricing + Happy/Validation/Auth pro Endpoint) — grün.
Zwei Fixes nach Sandbox-Lauf: Report-Query gegen fehlende Migrations-Spalten robust (#241) + ungenutzte
`agents.reputation_score` entfernt (#242, Live-Schema führt die Spalte nicht mehr).

## Sprint 2 — Delegation UCAN + Batch · PR #244 (`177bda7`) + Fix #245 (`8c61e57`)

Gate: `docs/spec-fakten/ucan-0.10.0.md` (Pin UCAN **0.10.0** JWT-Modell; 1.0.0 = DAG-CBOR-Rewrite, nicht anwendbar).

- `POST /delegation/create` → 200, 2-Segment-Punkt-JWT (`ucv 0.10.0`), attenuation-geprüft, durch `agent_delegation_config` begrenzt.
- `POST /delegation/verify` → 200, `valid=true`, 7 Checks (Signatur, Zeitfenster, iss/aud-Chain, Attenuation-Monotonie, Revocation).
- `POST /reputation/batch-sync` → 200, `count=2` (eine `GROUP BY`-Query, ≤500 DIDs, Flat-Preis 2 statt N×).

Engine `app/delegation.py`; Registry-Ed25519-Signatur (`iss=did:web:api.moltrust.ch`, verifizierbar via JWKS),
Delegator in `fct`. Proofs als **eingebettete UCAN-JWTs** (dokumentierte Abweichung von `[CID]`).
**AAE-Wechselwirkung (DoD §3):** `/delegation/*` ist eine Authz-Token-Schicht **oberhalb** des Enforcement-Evaluators
und ruft `evaluate_envelope` nicht auf — kein Konflikt, dokumentiert (nicht stillschweigend aufgelöst).
Fix #245: `ucan`-Feld in `_KNOWN_PUBLIC_CREDENTIAL_FIELDS` aufgenommen (der Secret-Scrubber hatte das JWT aus der
Response maskiert). **Testabdeckung: 18 Tests** (Attenuation-Matrix, Mint/Verify-Roundtrip, Chain valid/Eskalation/
Alignment, Tamper, Time-Bounds + Integration) — grün.

## Sprint 3 — Infrastruktur · PR #246 (`1bc9757`)

- `POST /anchors/batch` → 200, 64-hex Merkle-Root, Per-Leaf-Proof-Pfade, `anchor_status=computed`.
  **Wallet-Regel eingehalten:** reine Merkle-Berechnung ohne Wallet per Default; optionaler On-Chain-Submit nutzt
  **ausschließlich den bestehenden Anchoring-Key (`BASE_KEY`)** via `anchor_single_calldata` — die gesperrte
  ERC-8004-Wallet `0x9068…` wird nie berührt, keine neue Wallet-Konfiguration.
- `POST /compliance/incident` → 200, `deadline_days=10` (Tod → Art 73(4)), `deadline_status=on_track`.
  Verifizierte gestaffelte Fristen (2/10/15 Tage); reines Recording, **kein automatischer Behörden-Versand**.

Additive Migration `compliance_incidents` + Startup-Hook. Pricing anchors/batch=2, incident=2.
**Testabdeckung: 10 Tests** (Art-73-Deadline-Mapping, Deadline-Status, Merkle-Root/Proof-Rekonstruktion, Pricing +
Happy/Validation/Auth) — grün.

## Regressions & CI

- Voller Sandbox-Suite-Lauf: **265 passed**, 2 pre-existing Failures in `test_pqc_security.py::TestVerifyCredentialWrapper`
  (auf dem Basis-Commit `ae446e6` **vor** dieser Arbeit reproduziert → keine Regression dieses Auftrags; PQC-Pfad
  bewusst nicht angefasst, `PQC_ENFORCE` unverändert default OFF). → Backlog.
- Fork-CI (`fork-ci.yml`) auf jedem PR grün: compileall, `from app.main import app`-Import-Smoke, `pytest --collect-only`,
  Credit-Middleware-DB-Job.
- Review-Checkliste pro Merge: Token-Audit (`ghp_`/`sk_live_`/`sk-ant-`/…) clean, keine Attestix/VibeTensor-Begriffe,
  Migrationen additiv+idempotent, Squash-Merge.

## Peer-Reviews

Keine Multi-Modell-Peer-Reviews nötig — alle offenen Fragen aus Primärquellen (EUR-Lex, UCAN-Spec) und Server-Ist-Stand
beantwortbar. Die einzige inhaltliche Unsicherheit (Art-73-Fristen: PBA „15 Tage" vs. gestaffelt) ist durch den
verbatim EUR-Lex-Abruf entschieden (15/2/10). Ablage der Fundstellen: `docs/spec-fakten/`.

## Website · moltrust-web PR #121 (`fb65e40`), deployed 2026-07-11

- `compliance.html`: neuer Abschnitt „Eight endpoints against Regulation (EU) 2024/1689" — 8 Live-Endpoints mit
  Kurzbeschreibung, **echtes signiertes Annex-V-VC-Beispiel** (aus Produktion generiert), Art-6/7-Klassifikations-Notiz,
  Art-113-Timeline (2 Aug 2026 generell / 2 Aug 2027 Art 6(1)). Stale „GET /compliance/export Q3 2026" ersetzt.
  Voice geprüft (my-voice-en Compliance-Register + anti-KI-Sprech §1–§5). **Kein Attestix/VibeTensor.**
- `sitemap.xml` lastmod → 2026-07-11; `llms.txt` um die Live-Endpoints + korrigierte Art-113-Daten erweitert.
- Deploy: `install -m 644` aus `blog-deploy-stage` nach `/var/www/html` (NOPASSWD), `served == repo` verifiziert.
  Live-Probe: `https://moltrust.ch/compliance.html` 200 mit neuem Abschnitt; sitemap/llms.txt live.
- **Offen (human-gated):** GSC-Sitemap-Re-Submit (Login `clipperati2015@gmail.com`) — von der Console nicht ausführbar.

## Blogpost · Review-Queue-ID 179 (draft-only, `state=pending_review`)

„Compliance as an API" (Analysis-Register). In `content_review_queue` eingestellt, **Telegram-Ping an Lars gesendet (200)**.
**Kein Auto-Publish** (v0-Regel). Verification-Status-Block:
- ✅ Art 5(1)(f), Art 73-Fristen, Art 6(3)-Override, Art 113-Daten, 8 Endpoints live — jeweils mit Pin+Quelle+Datum.
- ⚠️ **UNVERIFIED: LARA-Studie „13 Frontier-Models 0 % auf Art 5(1)(f)"** — keine Primärquelle gefunden; vor Publish
  Titel/Autoren/Venue/Datum/URL pinnen oder Claim ersetzen.
- ⚠️ **UNVERIFIED: MolTrust-Prioritäts-Marker (AAE-Draft, arXiv v2)** — Datatracker-Link + arXiv-v2-ID vor Publish anhängen.

## Offene Punkte / Backlog

- **LARA-Zitat pinnen** (Blogpost-Blocker vor Publish).
- **GSC-Re-Submit** (human-gated, Lars).
- **Discovery-Rest (§8):** `/.well-known/agent-card.json` um die konsumenten-relevanten Endpoints ergänzen (llms.txt web
  ist erledigt; API-`llms.txt` + Agent-Card offen). Gate prüfen, welche der 8 Endpoints external-consumer vs. internal sind.
- **PDF-Report-Rendering:** aktuell HTML v1 (`format=html|json`); PDF nur bei bereits vorhandener Server-Lib — pandoc/typst
  vorhanden, aber bewusst nicht als Endpoint-Dependency verdrahtet → Backlog.
- **PQC-Verify-Wrapper-Tests** (2 pre-existing Failures) — separater Fix, PQC-Pfad.
- **On-Chain-Anchoring** von `/anchors/batch` ist opt-in und ungetestet gegen echte Chain (Wallet-Rotation abwarten).
- **Pricing-Feintuning:** aktuell assess=2, declaration=2, report=1, incident=2, delegation create=2/verify=1,
  batch-sync=2, anchors/batch=2 — analog VC-Issuance; bei Bedarf nachjustieren.

---

## Anhang — Ownify-Removal-Checkliste für Harald (Versand macht Lars)

Grundlage: die acht Endpoints + Engines sind jetzt **upstream in `MoltyCel/moltrust-api` main** live. Fork-lokale
(Ownify-/Harald-Fork) Parallel-Implementierungen können durch Calls gegen die Upstream-Endpoints ersetzt und entfernt
werden. **Genaue Fork-Dateipfade gegen PBA §7 / Haralds Fork-Baum gegenprüfen** — unten die kanonischen Upstream-Ersätze:

| Fork-lokale Funktion (falls vorhanden) | Upstream-Ersatz (kann Fork-Kopie ablösen) |
|---|---|
| Eigene AI-Act-Risk-Klassifikation | `app/compliance.py::classify` / `POST /compliance/assess` |
| Eigener Annex-V-Declaration-Builder | `app/compliance.py::build_declaration_claims` / `POST /compliance/declaration` |
| Eigene Compliance-Report-Generierung | `GET /compliance/report/{did}` |
| Eigenes Incident/Deadline-Tracking | `app/compliance.py::incident_deadline` / `POST /compliance/incident` |
| Eigene UCAN-Mint/Verify-Logik | `app/delegation.py` / `POST /delegation/create` + `/verify` |
| Eigene Batch-Reputation-Query | `POST /reputation/batch-sync` |
| Eigener Merkle-Batch-Anchor-Layer | `POST /anchors/batch` (nutzt `app/provenance/anchor.py`) |

Nach Entfernen: Fork-CI (`fork-ci.yml`) erneut grün prüfen; Discovery-Surfaces (Agent-Card/llms.txt) im Fork auf die
Upstream-Endpoints umbiegen.
