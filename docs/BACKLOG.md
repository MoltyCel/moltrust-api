# BACKLOG.md — MolTrust Open Items

**Status:** V1.20, lebendiges Dokument
**Letzte Aktualisierung:** 2026-06-02
**Geltungsbereich:** Alle MolTrust-Repos (moltstack, moltguard, moltrust-protocol)
**Definiert durch:** WORKFLOW.md Sektion 1.7

---

## D3 MANDATE-Enforcement — Implementierungsstand (2026-06-01)
- **Komponente 1 (aae_envelopes Store): LIVE.** Migration 010+011+012 deployed. INSERT-only immutability-trigger, hash-binding (aae_ref=sha256(raw_canonical)), FK-frei (wie violation_records), single_use unique auf (aae_id, scope_canonical). App-Layer #110, HTTP POST /vc/aae/submit #111. JCS canonicalize app-seitig.
- **Komponente 2 (Evaluator): LIVE im ADVISORY-Modus.** Loggt DENYs (signierte eval-rows in aae_evaluations + atomare violation_records), blockiert NICHT (scharfes enforce = Komponente 3). PRs #116-#121. Per-type-handler (max_transaction_value/allowed_domains/rate_limit/validity/single_use), revocation_check deferred (SSRF-proxy). Verdict Ed25519-signiert (DOMAIN_TAG||JCS, verdict_kid), extern gegen registry-key verifizierbar. advisory-lock auf aae_ref allein (cross-agent single_use TOCTOU geschlossen). rate_limit per-(agent,aae_ref). nonce client-required→fail-closed. value-authenticity: rail_verified vs self_asserted, self_asserted required:true Betrag→DENY. 4 Brief-Review-Runden + 3 Code-Review-Runden. POST /vc/aae/evaluate #120 live (401 auth, 422 nonce-missing, vc_id-binding, agent_did==principal).
- **Komponente 3 (enforce-mode-Chokepoint): GATED auf CEP.** none/inherit→nur loggen (= aktueller advisory-Zustand), enforce→blockiert (kein ALLOW→keine Aktion, mandatory chokepoint). **CEP-Governance ACCEPTED 2026-06-04** (`ADR-CEP-governance-v8` Governance-Flip, PR #143; Design-Loop nach 8 Runden geschlossen). Komponente-3-CODE bleibt durch 2 eigene Gates gesperrt: **Gate-C3-1** (Schwellen festgeschrieben+verankert) — **Schwellen-Festschreibung ERFÜLLT 2026-06-05** via `docs/specs/cep-3-thresholds.md` (N=101/K=4/Y=33%/X=10%/T=31d, Invarianten ✓; Verankerung an V3 gekoppelt; T_min/T_endorse_min offen); **Gate-C3-2** (V1-V3 gebaut: RP-Registry IP→DID, enforce-State, Multi-Chain-Anchoring) — OFFEN. Bis beide Gates erfüllt: Evaluator bleibt ADVISORY. M-of-N kryptographisch + no-downgrade-guard (Ramp-up-Mechanik).
- **Acceptance-Gate D-1: PENDING, NICHT CEP-gated.** Verifiziert issuer_did + envelope_signature bei AAE-Registrierung (heute nur GESPEICHERT in Komponente 1, nie geprüft). Reine Krypto-Verifikation, unabhängig baubar. = nächster logischer Schritt.
- **NÄCHSTE SESSION (Empfehlung):** (a) Acceptance-Gate D-1 bauen (unabhängig, abgeschlossen) — Brief→Review→Code wie Evaluator. PARALLEL (b) CEP-ADR als Denk-/Design-Arbeit (Recon→Proposal→Review, KEIN Code) + Geschäftsentscheidung N/M/X/Zeit-Schwellen vorab festschreiben. Komponente 3 erst wenn beide stehen.
- **Deploy-Stand:** Migrationen 010/011/012 live. Code bis #121 in main + deployed (HEAD 4f864781). Live-serving pid läuft advisory.

### Konzeptpapier + arXiv (PR #126, Review 2026-06-02)
- **Review-Ergebnis (whitepaper-mode):** **These (b) Governance-Transition = stärkerer/originellerer Kern als (a)** (Reviewer-Konsens, Autor-Hunch bestätigt). Paper-v2 um (b) restrukturieren, (a) als enabling-platform. (a) allein inkrementell (XACML/OPA trennen schon decision/enforcement); echte (a)-Novelty = chain-agnostisches Anchoring + full-record-signing für Agenten + value-authenticity-gating.
- **Paper-v2-TODO (NACH D3-Abschluss, nicht jetzt):** (1) (b) zur Hauptthese, Sybil-Kriterien + Literatur konkretisieren; (2) **Related-Work-Sektion = größte Lücke** (NIST AI RMF, EU AI Act, NIST SP 800-207, W3C VC, ERC-8004, MS Entra Agent ID, Sybil-lit SybilGuard/SybilLimit/BrightID); (3) **Oracle-Problem (CEP-Auslöser-Messbarkeit) frontal angehen** — decentralized-measurability-Pfad (EAS+ZK / attestation-networks) = kritische Schwäche UND potenziell schärfster Beitrag wenn gelöst; (4) "provably" abschwächen oder formales Angreifermodell.
- **Stärken (zum Ausbauen):** declarative-vs-enforced-gap-framing, value-authenticity-gating, RegTech/Compliance-Winkel (signed verdicts + Default-DENY = EU-AI-Act/NIST-Audit-Bedarf).
- **Status:** Aufschlag arXiv 1.9→2.0; finalisiert NACH D3+CEP+TechSpec-Update.

---

## Lese-Anleitung

- **Severity:** High / Medium / Low — Priorität in der Bearbeitungs-Reihenfolge
- **Status:** Open / In-Progress / Blocked / Deferred — aktueller Bearbeitungs-Zustand
- **Aufwand:** S (<30 Min) / M (30 Min - 2h) / L (>2h) — Zeitschätzung
- **Added:** Datum der Erstaufnahme
- **Source:** Wo das Item herkommt (Memory-Eintrag, Sprint-Doc, Audit-Output, Konversation)

**Hygiene-Regel (WORKFLOW Sektion 1.7):** Items älter als 30 Tage ohne Bewegung werden hinterfragt — ist es noch relevant, oder gestrichen?

---

## High

### V1.4-1: D3 = kritischer Pfad — keine Produktion ohne formales Delegations-Enforcement-Modell
- **Status:** Open
- **Aufwand:** L
- **Added:** 2026-05-18
- **Source:** AAE-D1-Kanonisierung §2.3-Security-Cross-Review (PR #41, Verdikt GRUNDLEGEND ÜBERDENKEN, Punkt C1)
- **Details:** Das D1-AAE-Schema (PR #41) ist als **strukturelle** Baseline freigegeben (Lars, Option a), aber die `delegation`-**Semantik ist NICHT enforce-bar**. Drei-Reviewer-Konsens (GPT-4o + Gemini + Perplexity, security mode): das Verschieben der Reconciliation zwischen Schema-`Delegation` (`attenuationOnly`/`maxSubAgents`/`maxDepth`) und der Live-`agent_delegation_config` (`constraint_mode ∈ {inherit,restrict,none}`) auf V1.4-1 D3 ist ein **Privilege-Escalation-Risiko** (zwei konkurrierende Delegationsmodelle ohne formales Kompositions-/Attenuationsmodell). **Konsequenz: V1.4-1 darf NICHT in Produktion, bevor D3 ein formales Delegations-Enforcement-Modell (Attenuation/Komposition + Mapping zur Live-`agent_delegation_config`) geliefert hat.** D3 ist damit **kritischer Pfad** für V1.4-1. Quelle/Detail: PR #41 NORMATIV-Block + Appendix B.

### AAE ins Credential einbauen (Phase-1-Analyse §8 Punkt 1)
- **Status:** Open
- **Aufwand:** L
- **Added:** 2026-05-15
- **Source:** moltrust-web Phase-1-Analyse v4 (UNC-07 Lars-Entscheidung), API-Sprint-Übergabe §8
- **Details:** `POST /identity/register` liefert aktuell ein vollständiges signiertes `AgentTrustCredential` **ohne** AAE-Envelope. AAE läuft separat über `POST /delegation/configure` und ist in `agent-card.json` als eigene Extension deklariert. Die Developer-Seite behauptet aber "Every MolTrust credential embeds an Agent Authorization Envelope" — im Ist-Zustand eine Falschaussage. Lars-Entscheidung: API erweitern, damit das Credential die AAE tatsächlich trägt. Voller WORKFLOW-Pfad (Spec mit 9 Sections, Cross-Review, Tests, PR). **Sequenzierung mit Credit-Middleware-Idempotency koordinieren** — beide ändern Schema, beide berühren `/identity/register`-Pfad, sollten nicht parallel laufen. moltrust-web kann die "embedded"-Darstellung erst nach Merge auf "embedded" heben; bis dahin entschärft PR1 die Falschaussage zu "separater delegation/configure-Schritt".

### Credit-Middleware Idempotency-Mechanismus
- **Status:** Open
- **Aufwand:** L
- **Added:** 2026-05-15
- **Source:** GPT-5 Cross-Review der Credit-Middleware-Spec V2 (CRITICAL F), bewusst Out-of-Scope des heutigen Schema-Alignment-Sprints
- **Details:** Retries und Duplicate-Deliveries können einen Agent doppelt charged werden, weil `reference = resolve_endpoint_key(method, path)` nicht pro Request eindeutig ist. Vollständige Lösung: Idempotency-Key pro Request (`X-Idempotency-Key`-Header oder serverseitig generierte UUID), Unique-Index auf `credit_transactions.idempotency_key`, INSERT mit `ON CONFLICT DO NOTHING`, bei Konflikt das vorherige Ergebnis replayen. Schema-Change. Eigener Spec mit voller 9-Section-Disziplin, eigener Cross-Review.

### cron.service OOM-kill investigieren
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-15
- **Source:** Session-Start Health-Check 2026-05-14 06:36 UTC
- **Details:** cron.service wurde am 14.05. 02:01 UTC vom OOM-Killer beendet. Memory-Pressure auf dem 4GB-Server. Mindestens ein 02:00-cron-Tick ist verloren gegangen. Investigation: welche Prozesse zogen zu dem Zeitpunkt Memory? Ist das ein einmaliger Vorfall oder ein Muster? Mögliche Mitigationen: systemd-Memory-Limits, Swap erhöhen, Memory-fressende Background-Jobs zeitlich entzerren.

### Telegram-Bot httpx-Logging-Leak fix
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-12
- **Source:** Claude Code diagnostic output 12.05.26 abends
- **Details:** Token-Rotation am 12.05. erledigt (Lars, server-side direkt). Verbleibendes Issue: httpx schreibt aktuellen Bot-Token weiterhin in plain text in `logs/watchdog.log` bei jedem Telegram-API-Call (175 Treffer im Log). Fix: httpx-Logger auf WARNING-Level setzen ODER Token via HTTP-Header statt URL-Pfad (httpx redacts Header by default). Plus: alte logs mit dem alten Token-Wert auditen und ggf rotieren/löschen.

### moltguard-Repo Working-Tree-Triage
- **Status:** Open
- **Aufwand:** L
- **Added:** 2026-05-12
- **Source:** Konversation 12.05.26 (während CONFORMANCE-Drift-Fix)
- **Details:** 9 modified + 14 untracked Files inkl. mehrerer .bak-Files und neuer Routen (events.ts, wallet.ts, aeoess-verify.ts). Separate Triage-Session analog zu moltstack PR #18. master-branch (nicht main).

### moltguard-Repo nach GitHub bringen (`MoltyCel/moltguard`, §11.1-Konformität)
- **Status:** **DONE 2026-05-20**
- **Aufwand:** L
- **Added:** 2026-05-20
- **Done-URL:** <https://github.com/MoltyCel/moltguard> (public, Apache-2.0, branch-protected on `main`)
- **Source:** MoltGuard-Discovery-Phase-1 SPEC §9.5 Drift-Forensik (`/guard/events/feed`-Pricing-Diskrepanz, PR #48)
- **Sprint-Outcome (2026-05-20):** 9-Phasen-Sprint per SPEC `docs/specs/2026-05-20_moltguard-remote-migration-SPEC.md` (PR #52, merged `68e27d1`). P2 Working-Tree-Triage: 11 audit-sync-Commits + 11 .bak in server-only `~/moltguard/.attic/`. P3 LICENSE Apache-2.0 + `package.json.license` Update + README-Polish + `.gitignore`-Erweiterung. P4 `master → main` rename. P5 Final Secret-Audit cleared (0 echte Hits, 3 SPEC-noted false-positives in `src/services/skill.ts` regex-strings korrekt excluded). P6 `gh repo create --private`. P7 First-push via neuem deploy-key `github_moltguard_deploy` + `~/.ssh/config` Alias `github-moltguard`. P8 CI-PR #1 (`tsc` + `vitest`, SHA-pinned actions) + §2.3 Cross-Review (Verdikt ÜBERARBEITEN → P0/P1 Findings adressiert in `e1e6056`) — CI grün auf main `6e8a90b`. P9-Vor-Check: 3 README-Issues (Lizenz-Duplikat, Build-Duplikat, Pricing-Drift 5-10× off) — fixed in PR #2, gemerged als `e99765f`. P9 visibility-toggle `public` + branch-protection auf `main` (`required_status_checks: Build & Test`, `allow_force_pushes: false`, `allow_deletions: false`, PR-required). **§11.1 jetzt vollständig erfüllt** für moltguard.

### WORKFLOW.md Bootstrap-Items (Scripts)
- **Status:** Open
- **Aufwand:** L (gesamt, sequenziell)
- **Added:** 2026-05-13
- **Source:** WORKFLOW.md Sektion 10
- **Details:** Vier Scripts schreiben:
  - `scripts/generate_status.py` (für daily STATUS.md auto-refresh)
  - `scripts/weekly_health_check.sh` (Multi-Repo-Health + Token-Audit + Stash-Aging)
  - `scripts/pre_sprint_check.sh` (manueller pre-sprint state check)
  - cron-jobs für 5.1, 5.2, 5.3 in WORKFLOW.md installieren
  Plus: `docs/STATUS.md` erste manuelle Version, dann auto-refresh aktivieren.

---

## Medium

### CLAUDE.md TechSpec-Versionsdrift (v0.3 gelistet, v0.8.1 live)
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-28
- **Source:** Opus-4.8-Session 2026-05-28 (Webroot-vs-CLAUDE.md-Abgleich)
- **Details:** Global `~/.claude/CLAUDE.md` Whitepaper-Tabelle listet „TechSpec v0.3“ als live; `/var/www/html/` führt bis `MolTrust_Protocol_TechSpec_v0.8.1.pdf` (2026-05-25) — 7 Minor-Versionen stale. Zu tun: (1) Public-Download-Link + `sitemap.xml`/`llms.txt` gegen v0.8.1 prüfen, (2) CLAUDE.md-Zeile aktualisieren. Hinweis: CLAUDE.md ist nicht repo-verwaltet (§11 N/A), reine Memory/Config-Korrektur.

### Dirty Working Tree auf Server-main (§4.2-Verstoß)
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-28
- **Source:** Opus-4.8-Session 2026-05-28 (`git status ~/moltstack` während BACKLOG-Drift-Sprint)
- **Details:** Der live-serving Checkout `~/moltstack` steht auf `main` mit uncommitteten Änderungen: modified `scripts/threadwatch.py` + `scripts/threadwatch_config.yaml`; untracked `audits/2026-05-14_onboarding-verification.md`, `audits/2026-05-15_api-versioning.md`, `audits/2026-05-15_webroot-reconcile.md`, `ietf-submission/`, `migrations/add_outcome_tracker.sql`, `scripts/blog_index_selfheal.sh`. §4.2-Working-Tree-Hygiene-Verstoß auf dem shared Anchor. Klären: committen, in eigenen Branch auslagern oder verwerfen — **wem gehören die `threadwatch`-Änderungen?** Bis zur Klärung nicht in andere Sprints mitschleppen.

### openclaw-plugin Rate-Limit-Strategie für parallelisierte Counterparty-Lookups
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-28
- **Source:** `@moltrust/openclaw-plugin@2.0.0-alpha.2` §12-Re-Re-Review (Run 2026-05-28 17:41 UTC, `~/moltstack/reviews/20260528_174139_openclaw-plugin-v2.0.0-alpha.2_review.md`), Aktionsliste #5
- **Details:** Mit der alpha.2-Umstellung auf `Promise.allSettled` in `moltrust-openclaw-v2/src/hooks/before-tool-call.ts` werden alle Counterparty-Lookups parallel gefeuert. Bei vielen gleichzeitig aktiven Agents mit vielen Counterparties pro Tool-Call kann das Burst-Last gegen die MolTrust-API erzeugen (Rate-Limit-Triggering). Bei typischen N (1-4 DIDs/Call) unkritisch; bei großen Counterparty-Listen → Strategie-Entscheidung: Soft-Cap (z.B. `maxConcurrentLookups: 10` Config), Batch-Queue oder Server-Side-Rate-Limit-Aware-Backoff. Erst nach Beobachtung echter Last priorisieren.

### §11-Härtung & Infra-Repo-Überführung (aus §11-Cross-Reviews, bewusst aus V4 ausgeklammert)
- **Status:** Open
- **Aufwand:** L (mehrere Teil-Sprints, teils produktionskritisch)
- **Added:** 2026-05-19
- **Source:** §11 §2.3-Cross-Review + Re-Cross-Review 2026-05-19 (GPT-4o+Gemini+Perplexity); Lars-Entscheidung §11 V4 (radikale Vereinfachung, Härtung zeitlich entkoppelt)
- **Details:** §11 V4 schließt bewusst nur die 3 real passierten Drift-Ursachen (Server-Datei ohne Commit / Doku-Iteration im Chat / Console-Kollision). Die von den Cross-Reviews als Härtung empfohlenen Punkte sind **NICHT verloren**, sondern hier als **zeitlich entkoppelte** Backlog-Items festgehalten — **kein §11-V4-Merge-Blocker, kein vorgelagerter Sprint**:
  1. **Infra-Config-Repo-Überführung** — nginx (4 sites/~479 Z.), systemd (9 Units), cron (43 user-Zeilen + 5 `/etc/cron.d`), Secrets-**Inventar** (61 Keys — nur Namen, **nie Werte**) deklarativ unter Versionskontrolle + non-disruptives, verifiziertes Apply pro Kategorie. Eigener mehrtägiger Infra-Sprint (eigenes Spec, §2.3, gestaffelt; Outage-Risiko nginx/systemd). Bis dahin gilt die §11-V4-Intro-Bereichsgrenze (manuelle Sorgfalt + Audit-Eintrag).
  2. **Build-/Supply-Chain-Integrität** — deterministische/isolierte Build-Pipeline, CI-Workspace-Mutation = Drift; Alignment **SLSA v1.0 / NIST SP 800-218 (SSDF)**. Eigener Spec `docs/specs/build-pipeline-integrity`.
  3. **WORM-/Append-only-Audit-Repo** — dediziertes externes Audit-Repo (branch-protected, no-rewrite, signiert, restriktiver Write) für Deploy-/Notfall-/Lock-Events.
  4. **Atomarer Deploy-Lock** — Lock-File mit Inhaber/UTC-Timestamp/PID, `kill -0`-Liveness + Max-Alter + expliziter Stale-Reclaim-mit-Audit (härtet die schlanke serielle V4-Regel 11.3 gegen OOM/HW-Ausfall/Race).
  5. **Formaler Notfall-/Hotfix-Pfad** — P0/P1-only, Rolle + 4-Augen, Rate-Limit, 24-h-Nachbearbeitung, Eskalation (V4 nutzt stattdessen Bereichsgrenze + manuelle Sorgfalt).
- **Kopplung:** keine harte Kopplung an den §11-V4-Merge (entkoppelt). Punkt 1 ist der größte/produktionskritischste; alle einzeln priorisierbar. Cross-Review-Reports: `~/moltstack/moltstack/reviews/20260519_082456_*` + `20260519_084218_*`.

### Weg B — `/identity/register` keyless machen (A2A-first / OD-3)
- **Status:** Open
- **Aufwand:** L (Auth-Pfad → voller WORKFLOW; + moltrust-web Folge-Deploy)
- **Added:** 2026-05-18
- **Source:** moltrust-web Phase-1-Analyse OD-3 ("verifiziert werden = gratis") + A2A-first-Positionierung; Lars-Entscheidung Weg B; Code-Verifikation 2026-05-18 (read-only)
- **Details:** `POST /identity/register` (`app/main.py:971`) hat aktuell hartes `Depends(verify_api_key)` (`:973`, Def `:621`) — gültiger `X-API-Key` ist Pflicht. **Verifiziert: seit Initial-Commit so, keine Regression, beabsichtigtes Design** — widerspricht aber A2A-first und OD-3 ("verifiziert werden = gratis"); Pflicht-Key auf dem allerersten Schritt ist eine Hürde genau dort, wo die Strategie keine wollte. **Sprint-Inhalt:** striktes `verify_api_key` auf `/identity/register` durch keyless-Pfad ersetzen (z.B. existierendes `verify_api_key_or_did` `:629`, oder Register ganz öffnen). **Security-kritisch (Auth-Pfad) → voller WORKFLOW: 9-Sektionen-Spec + §3.3-Brief + §2.3-Cross-Review, KEIN Solo-LLM.** **Sybil-Schutz (verifiziert, materiell):** register hat *zwei* Limiter — per-IP `@limiter.limit("10/minute")` (`:972`, key = x-real-ip/x-forwarded-for, OD-7) **und** per-API-Key `check_registration_rate(api_key)` 5/Key/h (`:974`/Def `:702`). Keyless **nullifiziert den per-Key-Limiter**; übrig bleiben nur per-IP + die 24h-`display_name`/`platform`-Dup-Erkennung (`:984`) + anonymisierte `registration_ip`. Spec MUSS bewerten, ob per-IP allein als Spam-Schutz ausreicht. **Credit-Link-Abhängigkeit (verifiziert):** register nutzt `api_key` aktuell, um den Key zu verknüpfen und 100 Gratis-Credits zu vergeben (`:~1010`) — keyless muss Credit-Grant/Linking ohne Key neu definieren (hängt an FOLGE 2). **FOLGE 1 — Website-Rück-Korrektur:** sobald keyless, muss derselbe `developers.html`-Abschnitt, den Weg A auf "braucht X-API-Key" korrigiert hat, wieder zurück (Hero-Curl, A2A-Schritt 0 + Schritt 2, Why-Register-Bullets) — eigener moltrust-web-Surgical-Deploy, in den Weg-B-Sprint eingeplant; sonst ist die Seite danach in die andere Richtung falsch. **FOLGE 2 — Credit-Verifikation nachholen:** Live-Seite behauptet "zero credits to register"; aktuell nur code-verifiziert (`credit_middleware`-Bypass `:530-531`, konditional auf `not caller_did`), NICHT mit echtem Key live gegengeprüft (war read-only verboten). Der Weg-B-Sprint fasst `/identity/register` ohnehin an → dabei mit echtem Call verifizieren, dass Register die Credit-Balance nicht verändert; Test-Agent-Anlage bewusst in Kauf nehmen. **Sequenzierung:** berührt denselben `/identity/register`-Pfad wie "AAE ins Credential einbauen" (High) und der Credit-Idempotency-Sprint — koordinieren, **nicht parallel** (konsistent mit bestehendem Sequenzierungs-Hinweis). **Nach V1.4-1 oder parallel, wie es passt — kein Blocker für andere Stränge.**

### AAE `tool_allowlist`-Inhaltsmodell (C2, Follow-up — KEIN D1-Blocker)
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-18
- **Source:** AAE-D1-Kanonisierung §2.3-Cross-Review (PR #41, Punkt C2)
- **Details:** Das D1-Schema führt `tool_allowlist` als `Obligation`-Flag, aber **ohne Inhaltsmodell** (welche Tools konkret erlaubt sind). In v1 daher **deklarativ/inert** — DARF nicht für Enforcement herangezogen werden, bis ein Tool-Constraint-Inhaltsmodell entschieden ist. Reviewer-Optionen (nicht entschieden, **nicht erfunden**): `ToolConstraint[]` direkt im Schema vs. externer Referenz-Hash. **Ausdrücklich KEIN D1-Blocker** (Lars-Entscheidung) — eigenes Follow-up. Relevant erst, sobald `obligations` tatsächlich enforced werden.

### §9.2 global: Debit-vor-call_next für alle 12 paid Routes (sauberer Endzustand)
- **Status:** Open
- **Aufwand:** L
- **Added:** 2026-05-18
- **Source:** Idempotency-Spec D1 (PR #34) — Lars: Option A (keyed-only vor call_next) bestätigt
- **Details:** D1-Option-A ist eine **bewusste, dokumentierte Risiko-Akzeptanz**: Aufrufer **ohne** `Idempotency-Key` behalten das alte Verhalten (Legacy-Pfad unverändert, Debit **nach** `call_next` → Doppel-Belastung bei Retry weiterhin möglich). Nur der Opt-in-keyed-Pfad bekommt Debit-vor-`call_next`. Sauberer Endzustand: §9.2 **global** — Debit-vor-`call_next` für **alle 12 paid `ENDPOINT_COSTS`-Routes**. Eigener Sprint: eigene 9-Sektionen-Spec, eigener §2.3-Cross-Review, **gestuft ausrollen — NICHT Big-Bang über alle 12 Routes** (Auto-Probe-Lesson: prozessweite Middleware-Änderung ist dieselbe Architektur-Klasse wie die Auto-Probe-Regression; schema-alignment §9.2 hat die Process-wide-Middleware-Frage bereits markiert). **Unabhängig von / nach** der Idempotency-Foundation (PR #34) und V1.4-1.

### ai_review.py — Synthese-400 war Billing (Credits falsche Org); Silent-Success-Defekt
- **Status:** Primärursache RESOLVED (2026-05-18); sekundärer Code-Defekt Open → eigener Fix
- **Aufwand:** S
- **Added:** 2026-05-18
- **Source:** `/review`-Lauf 2026-05-18 (credit-idempotency-brief); Root-Cause-Verifikation 2026-05-18 (read-only API-Diagnose)
- **Details:** **Korrektur der ursprünglichen Fehldiagnose.** Primärursache war **nicht** der Code: die Anthropic-API-Credits waren erschöpft bzw. in der **falschen Organisation** aufgeladen — `POST /v1/messages` lieferte für **jeden** Model-String identisch `invalid_request_error: "Your credit balance is too low"`. ~4 Tage Totalausfall aller Anthropic-Consumer (erste Beobachtung `moltbook.log 2026-05-14T09:00`, behoben `2026-05-18T10:38Z` nach Top-up in der korrekten Org `5f4b3dfb-…`). **Model-ID-Verdacht widerlegt:** `claude-sonnet-4-20250514` ist gültig und gelistet (`GET /v1/models` → HTTP 200), war nie das Problem; ein Modellwechsel hätte nichts behoben. **Sekundärer, echter Code-Defekt:** `ai_review.py` meldet `Synthesis : ✅` / `✅ Review abgeschlossen` und exitet 0 **auch wenn** der Synthese-Schritt einen Error-String zurückgibt — dieser Silent-Success hat die Fehldiagnose (Model-ID statt Billing) erst ermöglicht. Fix dieses Defekts: separater Code-PR (`fix/ai-review-silent-success`), nicht hier. **Unabhängig von V1.4** — eigenes Fix-Item.
### API-Versionierung — Single-Source + v1-Contract klären (Phase-1-Analyse §8 Punkt 5)
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-15
- **Source:** moltrust-web Phase-1-Analyse v4 OD-8, plus Versionierungs-Audit 2026-05-15 (`~/moltstack/audits/2026-05-15_api-versioning.md`)
- **Details:** Der Audit hat drei zusammenhängende Probleme aufgedeckt: (a) **Drei-Stellen-Duplikation ohne Single-Source** — `FastAPI(version="2.4")` in `app/main.py:50`, zusätzlich "2.4" als Literal in `:1519` (`/health`-Body) und `:5855` (zweiter Handler-Body). Kein zentraler Versions-String in `pyproject.toml`/`setup.cfg`/`app/__init__.py`. Ein Bump heute muss drei Stellen einzeln anfassen. (b) **Rückwärts-Dekrement 2.6 → 2.4** in der Repo-Historie — Initial-Commit `6c6a892` (2026-03-10) setzte `version="2.6"`, HEAD ist "2.4". Diagnostisches Signal: irgendwann hat jemand den Wert manuell editiert ohne sauberen Sprint-Pfad. (c) **Null versionierte Pfade** — 0 von 136 OpenAPI-paths haben `/v1/`, `/v2/`, `/api/v*`. Konvention ist domänen-präfixiert (`/identity/`, `/credits/`), nicht versions-präfixiert. Die OAS-`info.version` ist damit die einzige öffentliche Versions-Aussage des Systems — und sie ist unzuverlässig. Fix: Single-Source-of-Truth für die Versionsangabe (eine Konstante, z.B. `app.version.API_VERSION`, drei Stellen lesen sie), v1-Contract-Deklaration ("MolTrust API v2.4" im OAS vs. öffentliche v1-Aussage synchronisieren), Versionierungs-Schema (Breaking Changes über `/v2` oder Version-Header), Deprecation-Policy (6-Monats-Fenster, RFC 8594 `Deprecation`/`Sunset`-Header).

### Trust-Score-Reads Rate-Limiting (Phase-1-Analyse §8 Punkt 2)
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-15
- **Source:** moltrust-web Phase-1-Analyse v4 (OD-7 / V-9 partial), API-Sprint-Übergabe §8
- **Details:** Handler `/skill/trust-score/{did:path}` ist un-rate-limited und konstruktiv mit slowapi nicht nachrüstbar — der Handler hat keinen `request: Request`-Parameter. Signatur-Refactor nötig: `request: Request` als Parameter aufnehmen, slowapi-Decorator anwenden (Vorschlag: `60/minute/IP` analog zu anderen Read-Endpoints). Strukturarbeit, kein Config-Fix.

### CAEP als Extension in agent-card.json deklarieren (Phase-1-Analyse §8 Punkt 3)
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-15
- **Source:** moltrust-web Phase-1-Analyse v4 (UNC-11 / V-7), API-Sprint-Übergabe §8
- **Details:** CAEP-Profil ist live (`@moltrust/agent-firewall@1.0.0`, PROFILE.md sauber, 4 Endpoints live), aber **nicht** als sechste Extension in `agent-card.json` deklariert. Aktuell dort nur fünf: trust-score, aae, erc8004, x402-payment, discovery-surfaces. CAEP-Extension-Eintrag ergänzen mit korrektem Schema-URI und Endpoint-Liste. Reine Doku-Auslieferungs-Asymmetrie, kein Funktions-Bug. Beim Gelegenheits-Cleanup auch den Doku-Drift in `agent-firewall` PROFILE.md angleichen: nennt CAEP-Default-Limit 100, Server-Code nutzt 50 — Server-Wert übernehmen, PROFILE.md korrigieren.

### `x402-prices.ts` `/api/market/feed`-Doppelpfad konsolidieren (moltguard)
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-20
- **Source:** MoltGuard-Discovery-Phase-1 SPEC §9.5 Drift-Forensik (PR #48)
- **Sequenzierung:** **Jetzt startbar** (post moltguard-Remote-Migration 2026-05-20 — siehe entsprechendes HIGH-Item, DONE). Code-Change = 1 Zeile in `x402-prices.ts`, via regulärem PR-Workflow auf `MoltyCel/moltguard`.
- **Details:** `~/moltguard/src/middleware/x402-prices.ts` enthält `/api/market/feed` in BEIDEN Listen — `X402_PRICES` (Line 10, $0.10) **und** `X402_FREE_PATHS` (Line 50). Middleware-Reihenfolge in `x402.ts` (`isFree()` zuerst → `getPrice()` danach) bedeutet: **FREE gewinnt** → Live-Verhalten ist free (200 OK ohne Payment, verifiziert 2026-05-20). Discovery-Inventory (`/guard/api/info`) listet es trotzdem als paid → klassische Drift-Klasse. **Entscheidung Lars (2026-05-20):** free legitimieren, d.h. Eintrag aus `X402_PRICES` streichen — Live-Konsumenten verlassen sich seit Monaten auf das free-Verhalten. Plus: hand-curated `src/openapi/spec.ts` (Discovery-P2) sollte den Pricing-Eintrag dafür auch entfernen, damit `/guard/openapi.json` konsistent wird.

### MoltGuard CI Actions v4 → v5 upgrade (Node 24 readiness)
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-20
- **Source:** GitHub Actions annotation auf moltguard CI-Runs 2026-05-20: „Node.js 20 actions are deprecated. Node.js 24 default ab 2026-06-02, Node 20 removed 2026-09-16."
- **Sequenzierung:** Hard-Deadline **2026-09-16** (Node 20 entfernt aus Runners). Komfort-Deadline **2026-06-02** (Default switch zu Node 24). Niedrige Friktion.
- **Details:** `MoltyCel/moltguard/.github/workflows/ci.yml` SHA-pinnt heute `actions/checkout@v4` und `actions/setup-node@v4` — beide laufen auf Node.js 20 in der Action-Runtime. GitHub deprecation: 2026-06-02 wird Default Node 24, 2026-09-16 wird Node 20 entfernt. Upgrade-Path: SHA-pin auf `actions/checkout@v5` + `actions/setup-node@v5` (latest commits). Live SHAs fetchen analog zum P8-Pattern (`gh api repos/actions/checkout/git/refs/tags/v5`). Vermutlich 5-Minuten-PR.

### MoltGuard Validation Hardening — Zod-Schemas + Validator-Middleware + zod-openapi-Codegen
- **Status:** Open
- **Aufwand:** L (1–2 Tage)
- **Added:** 2026-05-20
- **Source:** MoltGuard-Discovery-Phase-2 Generator-Choice-Entscheidung (P2.1 §9.1-Prämissen-Check) — die SPEC-Tendenz „zod-openapi weil moltguard schon Zod-Schemas hat" wurde durch read-only Check widerlegt: `src/schemas/*.ts` sind reine TS-Interfaces + `as const`-Literale (`grep -c "zod"` = 0 in allen 6 Files), keine Zod-Validatoren. Routes machen ad-hoc Parsing (`c.req.json().catch(()=>({}))`, `c.req.param()`, `c.req.query()`) ohne strukturierte Validierung.
- **Sequenzierung:** **Jetzt startbar.** Discovery-P4 ist live, `MoltyCel/moltguard` ist seit 2026-05-20 mit branch-protected `main` + PR-Workflow + CI auf GitHub (siehe DONE-Item oben). Der Validierungs-Refactor mit 6 Schema-Konvertierungen + ~22 Route-File-Anschlüssen hat damit den nötigen PR-Review-Schutz.
- **Details:** Aktueller Stand (post-P2): `src/openapi/spec.ts` ist hand-curated TS-Modul, Variante (III) aus dem SPEC §9.1. Dieser Sprint upgradet auf Variante (I): (1) 6 Schema-Files in echte Zod-Schemas konvertieren (z.B. `z.object({...})` statt `as const`-Literale), (2) `@hono/zod-openapi` als Dep installieren, (3) `OpenAPIHono` statt vanilla `Hono` als App-Konstruktion in `src/index.ts`, (4) pro Route: `app.openapi(createRoute({...}), handler)` mit Zod-Schema-Refs für Request/Response, (5) `src/openapi/spec.ts` durch Codegen-Output ersetzen — Single Source of Truth ist dann der Route-Code. **Risiko-Mitigation:** bestehende toleranten Defaults (`c.req.json().catch(()=>({}))`) werden zu Strict-Validierung — pro Route prüfen ob Konsumenten 400er-Antworten verkraften (Agent-Konsumenten: vermutlich ja; legacy Frontend-Clients: ggf. Migration nötig). **Akzeptanz:** `/guard/openapi.json` muss byte-äquivalent oder eng-äquivalent zur heutigen hand-curated Version sein (Drift-Schutz gegen versehentliche Discovery-Regression).

### .well-known-Mirror-Generierung + Deprecation-Header (Phase-1-Analyse §8 Punkt 4)
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-15
- **Source:** moltrust-web Phase-1-Analyse v4 (OD-8 / INC-08), API-Sprint-Übergabe §8
- **Details:** `agent-card.json` ist aktuell identisch unter `api.moltrust.ch/.well-known/` und `moltrust.ch/.well-known/` ausgeliefert, ohne kanonische Quelle. Entscheidung (OD-8): `api.moltrust.ch/.well-known/...` = kanonisch, `moltrust.ch/.well-known/...` = generierter Mirror. Mirror-Generierungs-Pipeline aufsetzen (cron, post-merge-hook, oder build-time). Plus RFC 8594 `Deprecation`/`Sunset`-Header für deprecated Endpoints implementieren. Hängt nicht von "API-Versionierung Single-Source" ab, aber thematisch verwandt — sinnvoll im selben Sprint oder direkt danach.

### Credit-Middleware process-wide-Scope + Inversion debit-vor-call_next
- **Status:** Open
- **Aufwand:** L
- **Added:** 2026-05-15
- **Source:** GPT-5 Cross-Review der Credit-Middleware-Spec V2 (CRITICAL A "Preferred"-Variante), bewusst Out-of-Scope des heutigen Sprints (Spec V2.1 Section 9.2)
- **Details:** `credit_middleware` läuft via `@app.middleware("http")` auf jedem Request — dieselbe Architektur-Klasse wie die Auto-Probe-Regression. Zwei verbundene offene Fragen: (a) soll Credit-Deduction wirklich für jeden Request laufen, oder nur für explizit als "paid" markierte Routen? (b) soll der Debit **vor** `call_next` passieren statt danach, damit der Handler im Race-Fall gar nicht erst aufgerufen wird? Aktueller Zustand (Spec V2.1) nutzt die Minimal-Variante: Debit nach `call_next`, bei UPDATE=0 → HTTP-402-Mutation. Funktioniert, aber im Race-Fenster lief der Handler bereits. Eigener Spec mit voller 9-Section-Disziplin, eigener Cross-Review.

### WORKFLOW.md V1.2 — heutige Lessons einarbeiten
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-15
- **Source:** Credit-Middleware-Sprint + Working-Tree-Cleanup 2026-05-14/15
- **Details:** Drei Lessons aus den letzten zwei Tagen gehören in WORKFLOW.md:
  - **SQL-Validation niemals gegen Live-DB:** "Dry-Run gegen Live-DB" ist ein Widerspruch — `psql --single-transaction -f` committed bei `-f`-Ende. SQL-Validierung gehört offline (`pg_format --check`, `sqlparse`) oder gegen Wegwerf-DB. Aufgedeckt durch den unbeabsichtigten Live-DB-Touch im Step B des Credit-Sprints (drei Indizes wurden ungewollt auf Production angelegt; netto harmlos, aber Prozessfehler).
  - **Vor Branch-Creation `git fetch && git log origin/main`:** stale-local-main verhindern. Aufgedeckt durch die Working-Tree-Cleanup-PR-Erstellung am 14.05. die zunächst gegen stale-local-main lief.
  - **Reviewbedürftige Outputs immer in `/tmp/`-Datei, nicht inline:** Transport-Verlust beim Kopieren aus der Console — lange Diffs und Schema-Dumps kommen unvollständig beim Reviewer an. Standardmuster ab jetzt: Claude Code schreibt Diff/Schema/Log in `/tmp/<file>`, Lars lädt hoch.

### tx_type CHECK-Constraint + FK credit_transactions → credit_balances
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-15
- **Source:** Credit-Middleware-Spec V2.1 (Open Decisions, beide bewusst Out-of-Scope des heutigen Sprints)
- **Details:** Zwei kleine Schema-Migrationen die zusammen passen: (a) `credit_transactions.tx_type` bekommt einen CHECK-Constraint auf die Convention-Werte `('grant', 'api_call', 'transfer')` statt Convention-only durch `credits.py` enforced. (b) Foreign-Key von `credit_transactions.from_did` und `credit_transactions.to_did` auf `credit_balances.did`, `DEFERRABLE INITIALLY DEFERRED` damit Transaktions-Reihenfolgen nicht brechen. Sinnvoll als Eine-Migration-Item, low-risk.

### FastAPI on_event → lifespan Migration
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-15
- **Source:** Credit-Middleware-Sprint Test-Output — 4 DeprecationWarnings im pytest run
- **Details:** `@app.on_event("startup")` und `@app.on_event("shutdown")` in `app/main.py` (mehrere Stellen) sind seit FastAPI 0.110+ deprecated. Migration auf `@asynccontextmanager` mit `lifespan=`-Parameter beim FastAPI-Konstruktor. Mehrere Handler zusammenführen. Test-conftest's manueller Startup-Trigger muss entsprechend angepasst werden (`async with LifespanManager(app):` aus asgi-lifespan statt manueller Handler-Loop).

### traffic_monitor.py in Watchdog AGENTS-Liste aufnehmen
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-15
- **Source:** Working-Tree-Cleanup 2026-05-14 — der 3-Tage-Bug war unsichtbar genau weil traffic_monitor nicht in der AGENTS-Liste war
- **Details:** `agents/traffic_monitor.py` läuft stündlich via cron, ist aber nicht in `agents/watchdog.py`'s AGENTS-Liste. Folge: SyntaxError-Crash über 3+ Tage (11.05.–14.05.) blieb unbemerkt, ~72 fehlgeschlagene cron-Runs. AGENTS-Liste ergänzen, damit broken-state künftig im Watchdog-Alert auftaucht.

### migrations/add_outcome_tracker.sql Altlast klären
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-15
- **Source:** Working-Tree-Realität 2026-05-14 (Datei existiert in `migrations/`, ist aber nie committet — wurde von altem `*.sql` in `.gitignore` geblockt, jetzt durch `.gitignore`-Negation sichtbar)
- **Details:** `migrations/add_outcome_tracker.sql` liegt seit 2.04. im Ordner, ist untracked. Prüfen: ist die Migration noch relevant (welche Tabelle/Spalte würde sie anlegen, existiert sie schon)? Wenn relevant + nicht applied: committen + applien. Wenn relevant + bereits applied: committen als historisches Artefakt mit Kommentar. Wenn irrelevant: löschen.

### Auto-Probe-Migrations Repo-Status verifizieren
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-15
- **Source:** `.gitignore`-Realität-Check 2026-05-14 — `*.sql` blockte alle Migrations bis zur heutigen Negation
- **Details:** Es gibt mehrere `.sql`-Files in `migrations/` und `app/migrations/`. Vier sind getrackt (siehe `git ls-files | grep .sql$`). Verifizieren: welche Auto-Probe-relevanten Migrations existieren im Working-Tree der Server-Installation, und sind sie alle im Repo? Falls eine Migration nur auf dem Server liegt und nirgends versioniert ist, ist sie de facto ein loses File — committen oder dokumentieren.

### Stash-Hygiene Post-Credit-Sprint
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-15
- **Source:** Working-Tree-Cleanup 2026-05-14
- **Details:** Aktuell sind mindestens zwei Stashes im moltstack-Repo: `pre-auto-probe-deploy-2026-05-12-WIP-incl-prediction-accuracy` (alt, 12.05.) und `pre-2026-05-14-WIP-xmtp-v3-migration` (heute angelegt). Review: was ist tatsächlich im alten Stash noch unique vs. inzwischen anderswo gemerged? Alten Stash sauber droppen sobald nichts uniques mehr drin ist. Konsolidiert das frühere V1.2-Item "Stash@{0} Post-Triage Review", inkl. heutiger neuer Stash-Information.

### flag_records.anomaly_score integer → numeric(10,4) migration
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-12
- **Source:** Commit 2 Pre-Commit-Verifikation 12.05.26
- **Details:** `anomaly_score integer` vs Code übergibt potenziell float aus MoltGuard API. Silent-rounding-Risiko bei Outcome-Tracking. Migration ist backward-compatible (existing integers parsen als numeric).

### CAEP Profile v2 — neuer Sprint mit Cross-LLM-Review
- **Status:** Open
- **Aufwand:** L
- **Added:** 2026-05-12
- **Source:** Konversation 13.05.26 (Harald Rückfrage)
- **Details:** CAEP v2 envelope-sync war ursprünglich als "wait for Harald slot" markiert. Korrektur per Lars 13.05.: Harald hat aktuell keine Items offen, V2 muss als kompletter neuer Sprint aufgesetzt werden — mit Cross-LLM-Architecture-Review im Sinne von WORKFLOW Sektion 2.3 + Memory #28 Lesson. Spec mit allen 9 Sections schreiben, Layer-Scope explizit, vor Implementation Cross-Review durch GPT-5/DeepSeek/Kimi.

### Re-Deploy V2 Auto-Probe sprint
- **Status:** In-Progress (Workflow-Doc fertig, Code noch nicht)
- **Aufwand:** L
- **Added:** 2026-05-12
- **Source:** Memory #25, audits/2026-05-12_gpt5-verification-bundle.md, docs/sprints/2026-05-12_smithery-v2-workflow.md
- **Details:** GPT-5 D3 Architektur (mounted sub-app statt globale Middleware) + composite client-instance token + decorator pattern. Sprint-Code preserved auf feature/auto-probe-token. Spec mit allen 9 Sections schreiben bevor Implementation startet.

### moltstack/CONFORMANCE.md Namespace-Konflikt
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-12
- **Source:** CONFORMANCE-Drift-Diagnose 12.05.26
- **Details:** `moltstack/CONFORMANCE.md` ist "AIP Conformance Report", `moltrust-protocol/CONFORMANCE.md` ist "Skill Audit Conformance" — zwei verschiedene Docs mit identischem Filename. Rename ersteres zu `moltstack/AIP_CONFORMANCE.md` + Referenzen im Code updaten.

### XMTP v3 Migration testen + committen, dann Sandbox-Architektur entscheiden
- **Status:** Deferred (Reactivation-Bedingung: dedizierter Test-Sprint)
- **Aufwand:** M
- **Added:** 2026-05-12 (Sandbox-Architektur), 2026-05-15 update (Migration-Code seit heute als named-stash)
- **Source:** Commit 4b skip-Entscheidung 12.05.26 + heutiger Working-Tree-Cleanup (Stash `pre-2026-05-14-WIP-xmtp-v3-migration`)
- **Details:** Zwei verbundene Threads, in dieser Reihenfolge zu lösen: (1) Den seit 2026-05-14 named-stashten XMTP-v3-Migrations-Code (`scripts/outreach_xmtp.js`, ~80 Zeilen, Library-Swap `@xmtp/xmtp-js` → `@xmtp/node-sdk`, neue Signer-API mit `IdentifierKind.Ethereum`, encryption-key via sha256(privateKey)) zuerst gegen v3-API testen — unklar ob die Migration tatsächlich funktioniert, ist 35+ Tage alte uncommittete Arbeit. Sobald getestet: regulär committen. (2) Sandbox-Architektur entscheiden: aktuell kann das Script `node_modules` in `experiments/xmtp/` nicht erreichen. Drei Optionen: (α) viem zu `experiments/xmtp/package.json` + npm install, (β) Script relocaten nach `experiments/xmtp/`, (γ) eigenes `node_modules` für `scripts/`. Reihenfolge wichtig — ohne Test ist die Architektur-Entscheidung vorzeitig.

### Multi-Repo Branch-Naming Vereinheitlichung
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-12
- **Source:** Working-Tree-Triage 12.05.26
- **Details:** moltguard nutzt `master`, moltstack nutzt `main`. WORKFLOW Sektion 7.2 listet inkonsistente Namen als Backlog-Item. Migration moltguard `master` → `main` über GitHub-Settings.

### Auto-update-Hook für /var/www/html/CONFORMANCE.md
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-12
- **Source:** CONFORMANCE-Drift-Resolution 12.05.26
- **Details:** Aktuell muss man nach jedem `gen_conformance.py`-Run manuell `sudo cp` zum Web-Path. Anti-Pattern. Fix: post-merge-hook in moltrust-protocol/.git/hooks/ ODER systemd-path-watcher auf docs/CONFORMANCE.md.

### gh CLI installieren auf moltstack-server
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-12
- **Source:** PR-Creation-Friction 12.05.26
- **Details:** `gh` fehlt auf api.moltrust.ch-Server, PR-Creation läuft manuell via Browser. `sudo apt install gh && gh auth login` löst es. Plus: Bot-Account-Authentication damit gh-Calls als MoltyCel funktionieren.

### SSH-Migration für MoltyCel-Bot
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-12
- **Source:** PAT-Rotation 12.05.26
- **Details:** Aktuell Pattern B (credential-helper aus env) für moltrust-protocol. Langfristig sauberer: SSH-Key für MoltyCel-Bot auf GitHub registriert, kein Token-in-env mehr nötig. Setup: neuer ed25519 für MoltyCel-personal, GitHub key + ssh config alias, dann Pattern A für alle bot-getriebenen Repos.

### Pre-push Secret-Audit-Hook
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-22
- **Source:** Incident INC-2026-05-22-test-key-exposure (`docs/incidents/2026-05-22_test-key-exposure.md` §7.3 / §9) — struktureller Schutz gegen die im Incident aufgedeckte Leak-Klasse (Secret im Public-Repo)
- **Priority-Begründung:** Medium — kein akuter Bug, aber der einzige strukturelle Schutz, der das ursprüngliche Leck (Key im Initial-Commit-README) **vor** der Veröffentlichung gestoppt hätte.
- **Details:** Pre-push git-Hook für alle `MoltyCel`-Repos, der jeden Push auf bekannte Secret-Patterns scannt — `ghp_`, `sk_live_`, `whsec_`, `github_pat_`, `sk-ant-`, `mt_<32-hex>`, `AKIA`, `xoxb-` — und bei Treffer den Push mit klarer Fehlermeldung abbricht. **Installations-Vorschlag:** versioniertes `.githooks/`-Verzeichnis pro Repo + `git config core.hooksPath .githooks` — gegenüber einem globalen `~/.git/hooks` / globalem `core.hooksPath` zu bevorzugen, weil der Hook so (a) im Repo versioniert + reviewbar ist, (b) mit dem Repo mitwandert, (c) auf MolTrust-Repos beschränkt bleibt (ein globaler Hook griffe auf alle lokalen Repos und wäre nicht reproduzierbar für andere Klone/CI). Einschränkung: `core.hooksPath` ist lokale Config, muss pro Klon einmalig gesetzt werden (Setup-Zeile ins README) — und ein Pre-push-Hook ist client-seitig mit `--no-verify` umgehbar; der nicht-umgehbare Gegenpart ist ein CI-/server-seitiger Secret-Scan bzw. GitHub Secret Scanning + Push Protection, den dieses Item mit abdecken sollte. Bei Umsetzung: §7.3 der Incident-Doc von „noch anzulegen" auf den Verweis hierauf ändern.

---

## Low

### Memory-Pfaddrift: WORKFLOW.md-Speicherort
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-28
- **Source:** Opus-4.8-Session 2026-05-28
- **Details:** Annahme/Memory referenziert `~/moltstack/moltrust-api/docs/WORKFLOW.md`; kanonischer Pfad ist `~/moltstack/docs/WORKFLOW.md` (kein `moltrust-api/`-Unterverzeichnis — `~/moltstack` IST der moltrust-api-Checkout, siehe WORKFLOW.md §1.2.1-Changelog). Memory-Eintrag korrigieren.

### openclaw-plugin Test #1 — Own-DID Early-Exit Proof
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-28
- **Source:** `@moltrust/openclaw-plugin@2.0.0-alpha.2` §12-Re-Re-Review, Aktionsliste #1 — für v2.0.0-beta/RC
- **Details:** Test in `moltrust-openclaw-v2/tests/before-tool-call.test.ts`: wenn `cfg.agentDid` Score < `minTrustScore` UND `failOpen=false`, dann werden Counterparty-Lookups NICHT mehr aufgerufen (early-exit-Semantik). Verifikation: Spy/Mock auf `client.getTrustScore` mit Aufruf-Count=1 (nur own-DID). Formale Test-Absicherung der Optimierung, die heute nur durch Code-Inspection gestützt ist.

### openclaw-plugin Test #2 — Mixed-State Counterparties + failOpen=true
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-28
- **Source:** `@moltrust/openclaw-plugin@2.0.0-alpha.2` §12-Re-Re-Review, Aktionsliste #2 — für v2.0.0-beta/RC
- **Details:** Test in `moltrust-openclaw-v2/tests/before-tool-call.test.ts`: Counterparty-Array `[OK, FAIL, OK]` + `failOpen=true` → erwartet ALLOW (Single-Failure ist mit failOpen=true transitiv nicht blockierend). Regression-Schutz für die `Promise.allSettled`-Umstellung in alpha.2.

### openclaw-plugin Block-Priority README-Dokumentation
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-28
- **Source:** `@moltrust/openclaw-plugin@2.0.0-alpha.2` §12-Re-Re-Review, Aktionsliste #3 — für v2.0.0-beta/RC
- **Details:** Inline-Kommentar in `before-tool-call.ts` dokumentiert „Block-priority is deterministic: first counterparty in array order whose result triggers a block wins" — Operator-facing README sollte das explizit aufnehmen (eigener Abschnitt „Operator Semantics" oder unter „Security Posture & Roadmap"). Operator-Erwartung: identischer Input → identischer Block-Output, auch bei mehreren parallel-blockenden Counterparties.

### openclaw-plugin Multi-Counterparty-Block Logging-Enhancement
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-28
- **Source:** `@moltrust/openclaw-plugin@2.0.0-alpha.2` §12-Re-Re-Review, Aktionsliste #4 — für v2.0.0-beta/RC
- **Details:** Bei Multi-Counterparty-Block (mehrere fails parallel via `Promise.allSettled`) loggt aktuell nur die erste blockierende Counterparty (die per array-order-Priority „gewinnt"). Für Operator-Debugging hilfreich: zusätzliche Warn-Log-Einträge mit Index für alle anderen problematischen Counterparties, damit Operator das volle Bild sieht statt nur den ersten Block.

### openclaw-plugin „Silent Enforcer"-Pattern in README dokumentieren
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-28
- **Source:** `@moltrust/openclaw-plugin@2.0.0-alpha.2` §12-Re-Re-Review, Aktionsliste #6
- **Details:** Die Config-Kombination `registerMoltrustTools: false` + `minTrustScore > 0` ist ein legitimes „Silent Enforcer / Defense-in-Depth"-Pattern: LLM kann keine MolTrust-Tools triggern, aber Lifecycle-Hooks blocken trotzdem unzulässige Tool-Calls anhand vorhandener Trust-Daten/Caches. Reviewer-Konsens (GPT-5 + Perplexity, alpha.2-Review) bestätigte das Pattern explizit als sinnvoll. Sollte in README als „Best Practice"-Snippet aufgenommen werden (eigener kleiner Abschnitt unter Privacy oder Security Posture).

### MoltGuard CONFORMANCE.md-Drift-Check via CI
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-20
- **Source:** Post-moltguard-Migration-Audit 2026-05-20 — `scripts/gen_conformance.py` ist server-local (audit-sync committed in `cc736e9`), läuft aber heute nicht automatisch im CI-Pfad.
- **Details:** `MoltyCel/moltguard/scripts/gen_conformance.py` generiert `CONFORMANCE.md` aus dem Live-Stand. Heute manueller Aufruf erforderlich, sodass Drift zwischen Code und CONFORMANCE.md unbemerkt entstehen kann. CI-Step in `.github/workflows/ci.yml`: nach build den Generator laufen lassen + `git diff --exit-status CONFORMANCE.md` prüfen — failed run signalisiert „CONFORMANCE.md ist stale". Niedrig-priorität weil heute keine externen Konsumenten von CONFORMANCE.md, aber gute Hygiene-Maßnahme analog zum Discovery-Drift-Pattern. Verbunden mit moltguard-PR im normalen Workflow.

### Separate Test-DB für Credit-Tests
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-15
- **Source:** Credit-Middleware-Sprint Test-Architektur-Entscheidung (Spec V2.1 Section 9.3)
- **Details:** Die heutige Test-Architektur testet `credit_middleware` gegen die Live-DB mit klar markierten Test-DIDs (`did:moltrust:<16hex>`, `display_name='tc-...'`, `platform='test'`). `credit_balances`/`agents`/`api_keys` werden aufgeräumt; `credit_transactions`-Einträge bleiben wegen append-only Trigger als markierte Audit-Spur in der Live-DB. Über Zeit sammelt sich Test-Müll an (filterbar, aber unschön). Saubere Lösung: separate Test-DB `moltstack_test` mit eigenem Schema-Sync, in Test-Env-Vars verdrahtet, Tests laufen dagegen. Eigene Infrastruktur-Arbeit (DB-Setup, env-Handling, Schema-Sync), deshalb low-priority.

### known_callers Tabelle DROP oder Migration
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-15
- **Source:** Working-Tree-Cleanup 2026-05-14 — bei der traffic_monitor.py-Restore aufgedeckt
- **Details:** Postgres-Tabelle `known_callers` (45 rows, oldest Jan 2026, last write 2026-04-18 — 4 Wochen stale). Wurde von der nie-committeten DB-based v2 von `traffic_monitor.py` befüllt; die heutige Entscheidung war file-based v2 zu restoren, damit wird `known_callers` dauerhaft nicht mehr beschrieben. Nur von `traffic_monitor.py` referenziert (verifiziert via grep). Entscheidung: `DROP TABLE` oder Migration des State-Files zur Tabelle (falls man später doch DB-based will). Pragmatisch: DROP, da file-based jetzt der bewusste Stand ist.

### logrotate permissions fix (moltguard.log)
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-15
- **Source:** Session-Start Health-Check 2026-05-14 06:36 UTC
- **Details:** logrotate hat heute morgen `moltguard.log` (19.8 MB) abgelehnt — Permission-Fehler. Log wächst ungebremst weiter. Fix: logrotate-Config für moltguard prüfen, User/Group anpassen, manuell einmal rotieren.

### app/settlement.py defensive coding cleanup
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-12
- **Source:** Commit 3 Pre-Commit-Diff-Review 12.05.26
- **Details:** Defensive `isinstance`-Pattern in settlement.py prüft Type für asyncpg-Return-Values. Audit zeigt: aktueller Code hat `isinstance(prediction, str)` (line 221), nicht der vermutete `isinstance(row, dict)`. Code-Review nötig ob das aktuelle Pattern semantisch korrekt ist.

### Audit-Endpoints konsolidieren (404er)
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-12
- **Source:** CONFORMANCE-Drift-Diagnose 12.05.26
- **Details:** `/guard/audit/version` funktioniert, aber `/audit/version` 404, `/guard/audit` 404. Inkonsistente Convention. Fix: alle Audit-Endpoints unter `/guard/audit/*` konsolidieren ODER 301-redirects einrichten.

### gen_conformance.py als täglicher Cron
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-12
- **Source:** CONFORMANCE-Drift-Resolution 12.05.26
- **Details:** Script läuft aktuell nur manuell. Bei jedem MoltGuard-Update sollte CONFORMANCE.md automatisch nachgezogen werden. Cron 1x täglich, idempotent.

### Pre-commit-hook conflict-marker-check auf alle Repos
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-12
- **Source:** Commit 5 Conflict-Resolution 12.05.26 (Gemini-Migration)
- **Details:** `git diff --check` als pre-commit-hook in moltstack + moltguard + moltrust-protocol. Findet `<<<<<<<`/`=======`/`>>>>>>>` Marker bevor sie committed werden.

### `./mcp_server.py` Legacy stdio cleanup
- **Status:** Open
- **Aufwand:** S
- **Added:** Pre-2026-05-12
- **Source:** Memory (legacy MCP transition)
- **Details:** stdio-File obsolet seit MCP-HTTP-Migration. Audit bestätigt: existiert weiter als 2495 B (Mar 29). Löschen.

### PR7 Post-Quantum ML-DSA Diskussion mit Harald
- **Status:** Blocked (waiting for Harald)
- **Aufwand:** M
- **Added:** Pre-2026-05-12
- **Source:** Memory (laufende Diskussion)
- **Details:** Dual-Signature-Approach für VC-Issuance. Harald hat PR auf moltrust-protocol mit Implementation-Vorschlag.

### PR14 CI workflow (alt 30.04)
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-04-30
- **Source:** Memory
- **Details:** GitHub Actions workflow für agent-firewall provenance + tests. Vor V1-Publish gestern noch nicht aktiv — jetzt Backlog für v1.0.1.

### sys.path.insert services/ → proper Python-Package
- **Status:** Open
- **Aufwand:** M
- **Added:** Pre-2026-05-12
- **Source:** Memory (technical-debt)
- **Details:** services/ wird via `sys.path.insert` importiert. Sauber wäre __init__.py + proper package-structure.

### withheld-200 documentation (Harald-Finding)
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-12
- **Source:** Harald-Mail
- **Details:** `/skill/trust-score` returns 200 mit signed `withheld:true` für bogus DIDs — kann als "registration proof" missverstanden werden. Doku-Klarstellung + Empfehlung `/identity/verify/{did}` als registration-gate.

### Harald's PROFILE.md als authoritative wire-format
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-12
- **Source:** Harald-Mail
- **Details:** Harald hat eigene PROFILE.md die das tatsächliche CAEP-Wire-Format dokumentiert (consistency_level, evaluation_context, registry_signature Fields nicht in offizieller PR16-Description). Übernehmen als authoritative docs für CAEP v1.x.

### trustscout.py + 2 systemd-Service-Files Investigation
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-13
- **Source:** TrustScout-Diagnose 13.05.26 (PR #22)
- **Details:** Diagnose ergab: `agents/trustscout.py` (514 Zeilen) hat 2 systemd-Service-Files (`moltrust-trustscout.service` heartbeat, `moltrust-trustscout-daily.service` daily). Nicht orphaned wie initial vermutet. Schreibt parallel mit `agents/moltguard.py` post-edu/post-deep das `data/trustscout_state.json`. Multi-Writer-Pattern für `last_post_time` Field. Funktional läuft alles (Posts kommen auf Moltbook an, verifiziert via Telegram-Stats), aber Architektur ist unklar: warum 2 Code-Pfade parallel? Soll konsolidiert werden? Reines Investigation-Item, kein akuter Fix nötig.

### 5 stale lokale Branches Cleanup
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-13
- **Source:** Side-Beobachtung 13.05.26 (PR #22 prep)
- **Details:** Nach git branch -vv mit gone upstream: chore/smithery-v2-workflow-doc (PR#19), chore/workflow-doc (PR#20), chore/working-tree-rescue-2026-05-12 (PR#18), chore/backlog-init (PR#21), feature/caep-registry-endpoints (orphaned probe-sprint base). Cleanup via `git branch -d <name>` für jeweils. Vermutlich nach heutigem Sprint zusätzliche Branches dazu (fix/credit-middleware-schema-alignment wurde von GitHub automatisch gelöscht, aber lokal noch zu prüfen).

### Voll-Secret-Scan moltrust-api Full-History (Vorbedingung für etwaigen History-Rewrite)
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-22
- **Source:** Test-Key-Incident Phase-2-SPEC (`docs/specs/2026-05-22_test-key-history-scrub-SPEC.md`, §1 / §10.3 Pkt 5)
- **Details:** Der Test-Key-History-Scrub-Sprint hat für moltrust-api **Option C** gewählt — kein History-Rewrite, nur Working-Tree-Redact von `pentest.sh`. Falls je ein echter Full-History-Rewrite von moltrust-api erwogen wird, ist dieser Scan **zwingende Vorbedingung**: `mt_test_key_2026` steckt in 9 History-Dateien, und Commit `e51c05a` (`fix(security): CRITICAL-1,2,5 — hardcoded key … CLI private key`) deutet auf **weitere historische Secrets**. Vor einem Rewrite die Full History mit gitleaks/trufflehog scannen und **alle** Funde in **einem** `git-filter-repo --replace-text`-Lauf entfernen — nicht nur das `mt_test_key_2026`-Pattern. Ohne diesen Scan ließe ein Rewrite andere Alt-Secrets in der History zurück und müsste später wiederholt werden.

### compute_phase2_score Härtung (depth-Doku, Timeout-Bound, Exception-Handling)
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-22
- **Source:** §2.3 Cross-Review `s121-a2a-card-fix` (2026-05-22), Punkte #3/#4 — bewusst out-of-scope des Sprint-1.2.1-Handler-Fix
- **Details:** Die geteilte Funktion `compute_phase2_score` (`app/swarm/trust_score.py`) wird von `/skill/trust-score/{did}` **und** seit Sprint 1.2.1 auch von `/a2a/agent-card/{did}` aufgerufen — beide öffentliche, unauth GET-Endpoints. Härtung des Graph-Traversals: (1) `depth`-Parameter explizit dokumentieren + harte Obergrenze für die Endorsement-Propagation; (2) Timeout-Bound auf die Berechnung; (3) Exception-Handling mit definiertem Fallback (z.B. Score 0 + Log-Error), damit ein Traversal-Fehler keinen 500 auf der öffentlichen Card-Surface erzeugt. Betrifft **beide** Endpoints zugleich → eigener Sprint, nicht im Handler-Fix mitgemacht.

---

## Deferred (Decision Required Before Activation)

### ERC-8004 ValidationRegistry nicht implementiert (nur Identity + Reputation)
- **Status:** Deferred (Decision Required — ERC-8004-Validation-Support überhaupt gewünscht?)
- **Aufwand:** M
- **Added:** 2026-05-28
- **Source:** Opus-4.8 Code/Spec-Audit 2026-05-28 (`app/erc8004.py` + TechSpec v0.8.1 §6)
- **Details:** `app/erc8004.py` verdrahtet nur 2 der 3 ERC-8004-Registries: Identity (`0x8004A169…a432`, on-chain write `register()`) + Reputation (`0x8004BAa1…9b63`, write `giveFeedback()`). **Kein ValidationRegistry** (grep `ValidationRegistry`/`validation_response`/`VALIDATION_REGISTRY` leer). `/identity/erc8004/validate` ist read-only Resolve (`ownerOf`/`tokenURI`/`getAgentWallet`/`getSummary`, alle `.call()`), kein on-chain Write. TechSpec §6 On-Chain-Anchoring ist bewusst **chain-agnostisch** (eigenes `MolTrust/<event>/<v> SHA256:<hash>`-Calldata-Format), NICHT ERC-8004-Validation. **Decision offen:** ERC-8004-Validation bauen oder bewusst nicht. **Guard bis dahin:** weder agent-card noch A2A-Thread noch Spec-Pitch dürfen ERC-8004-*Validation* behaupten (Proof-of-Work-Disziplin) — Identity + Reputation sind belegt, Validation nicht.
- **Update 2026-05-29 (On-Chain-Provenienz, Base mainnet — `eth_getStorageAt` EIP-1967 + Blockscout):** Die MolTrust-Proxies fahren die **echten offiziellen ERC-8004-Referenz-Impls**: Identity `0x8004A169…` → Impl `0x7274e874…` (`IdentityRegistryUpgradeable`), Reputation `0x8004BAa1…` → Impl `0x16e0FA7f…` (`ReputationRegistryUpgradeable`). Für Identity+Reputation damit **faktisch ERC-8004-konform** (Referenz-Logik hinter eigenen Proxies an nicht-kanonischen Adressen; kein Fork, keine Custom-Logik; `erc8004.py` referenziert die kanonischen Singletons bewusst nicht). Die **kanonischen Vanity-Singletons** (`0x8004A818`/`0x8004B663`/`0x8004Cb1B`) zeigen auf Base mainnet noch auf den **MinimalUUPS-Platzhalter** (`0xd53de688…`) — nicht auf echte Logik upgegradet. **Validation:** weiterhin komplett abwesend (kein eigener Proxy, keine Referenz); selbst der kanonische Validation-Proxy ist noch MinimalUUPS, „sich darauf berufen“ wäre hohl → **Proof-of-Work-Guard bleibt nur hier scharf**. **Konsistenter Build-Weg falls gewünscht:** eigener Proxy → offizielle Validation-Impl `0xDB31f5d9167f8ebc8B30FbBF814c4d297c2D7F99` (gleiche Mechanik wie Identity/Reputation).
- **KORREKTUR (2026-05-29, V1.11 — ersetzt #91-Aussage):** Proxies 0x8004A169…/0x8004BAa1… sind FREMD-OWNED — owner() = 0x547289…062603 (offizieller erc-8004-Deployer, hardcoded im Public-Repo), NICHT MolTrust. MolTrust ist reiner KONSUMENT dieser fremd-deployten, offiziell-geownten Registries (ruft register()/giveFeedback() mit eigenem BASE_WRITE_KEY). Frühere Aussage „eigene Proxies" war falsch.
- **Validation-Weg dadurch neu gefasst:** MolTrust kann NICHTS „unter derselben Ownership" ergänzen, da es Identity/Reputation nicht ownt. Zwei Optionen: (1) abhängig von 0x5472 warten/anfragen bis Owner eine Validation-Registry bereitstellt (out of hand), oder (2) eigene MolTrust-geownte Validation-Registry deployen → dann bewusst eigenständig/nicht-kanonisch. Decision-Required.
  - **Validation final geklärt (2026-05-29):** Modell ist PERMISSIONLESS (kein Whitelist/Owner-Hebel — jeder Agent kann requesten, jede Adresse validieren) — ABER es existiert keine scharfe ValidationRegistry auf Base: kanonische 0x8004Cb1B… steht auf MinimalUUPS-Platzhalter (0xd53de688…), Upgrade-Key bei 0x5472. Validator-Build wäre Arbeit ins Leere bis 0x5472 auf echte Impl (0xdb31f5d9…) upgraded. → ERC-8004-Validation-Sprint VOM TISCH bis Registry scharf.
  - **Ökosystem-Realität (Report #73, Laplace, 2026-04-02):** 119.675 Agents / 6 Chains, davon ~50 aktiv. Top-Agents (Toppa/Clawdia/Agentic Eye) ranken über Feedback-Volumen + live Endpoint + x402 — NICHT Validation. MolTrust (33553) registriert aber dormant (kein Feedback-Volumen, kein 8004scan-Rank). → Echter ERC-8004-Sichtbarkeits-Hebel = Reputation-Feedback + x402, nicht Validation.

### B2C Prediction-Market Edge-Tool (Polymarket+Kalshi)
- **Status:** Deferred (separater Geschäftsmodell-Discovery-Chat)
- **Aufwand:** L
- **Added:** 2026-05-10
- **Source:** Memory #26
- **Details:** Anomaly-Spotting für Counter-Bet-Opportunities + Investigative-Stories. SEC/Peirce-Validation 08.05. Klärungen pending: Naming, Single/Multi-Platform, Free/Paid, Channel-Account, Brand-Verhältnis zu MolTrust.

### Bernd Plugin-Idee (A2A-Card für Drittsites)
- **Status:** Deferred (Reactivation-Bedingung definiert)
- **Aufwand:** L
- **Added:** 2026-05-11
- **Source:** Memory #27
- **Details:** WP/Shopify-Plugin als MolTrust-Distribution. Nicht bauen ohne Pilot-Pipeline. Reactivation-Bedingung: 5 konkrete Sites die installieren würden. Implementer wäre Lars/Harald, nicht Bernd. Fallback Mini-Tool moltrust.ch/card-generator (8-16h).

---

## Bootstrap (WORKFLOW.md V1 fordert, keine Spec nötig)

### docs/STATUS.md erste Version
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-13
- **Source:** WORKFLOW.md Sektion 1.2 + 10
- **Details:** Manuell erste Version mit aktuellem System-State. Danach via `scripts/generate_status.py` auto-refreshed.

### docs/decisions/ initialisieren mit 3-5 ADRs
- **Status:** Open
- **Aufwand:** M
- **Added:** 2026-05-13
- **Source:** WORKFLOW.md Sektion 1.4 + 10
- **Details:** ADRs für (1) Auto-Probe V2-Architektur mounted-sub-app, (2) Pattern B credential-helper (Token-Rotation), (3) MCP-Tool-Convention beibehalten, (4) Sequential A/B-Test bei Smithery, (5) Memory-Reality-Sync-Pflicht. Plus neu: (6) Credit-Middleware Minimal-Variante (statt Inversion) für 402-Mutation — bewusste Scope-Entscheidung des 15.05.-Sprints.

### Multi-Repo-Inventory-File
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-13
- **Source:** WORKFLOW.md Sektion 7.1
- **Details:** `docs/repos.md` mit Liste aller MolTrust-Repos, Branch-Naming-Status, Cross-Dependencies, Verantwortung.

### docs/incidents/ Folder als Konzept einführen
- **Status:** Open
- **Aufwand:** S
- **Added:** 2026-05-15
- **Source:** Working-Tree-Cleanup-Lesson 14.05.: Incident-Post-Mortems sind anderes Format als ADRs (in `docs/decisions/`) — Auto-Probe-Drama + Credit-Middleware-Sprint-Validate-Touch wären beides Kandidaten
- **Details:** ADRs dokumentieren *Entscheidungen* mit Kontext, Optionen, Begründung. Incidents dokumentieren *Vorfälle* mit Hergang, Root-Cause, Lessons-Learned, Mitigationen. Separater Folder `docs/incidents/` mit eigenem Template. Initial: 2-3 retrospektive Incidents als Beispiele (Auto-Probe 12.05., SQL-Validate-Live-DB-Touch 14.05., MoltyCel-PAT-Cascade-Failure 12.05.).

---

## ERC-8004 / Standardisierung — Einbringungs-Kanal (Decision Required, 2026-05-29)
- **Befund:** ERC-8004 On-Chain-Visibility kein lohnender Hebel jetzt — aktives Feld ~50 Agents, MT-Käufer (Compliance) suchen nicht dort, Feedback-Volumen-Aufbau = Flywheel-Henne-Ei. Self-Feedback bei Trust-Anbieter gefährlich.
- **Wertvoller Hebel = eigener fachlicher Beitrag in ethereum-magicians ERC-8004-Thread (25098):** Offene Kernfrage dort = Reputation-AGGREGATION (single score „dangerous/monopolistic" — daniel-ospina/spengrah; Marco-MetaMask fragt AKTIV: „let's challenge this rationale, welche Use-Cases, welche Daten on-chain"). v2-Spec mit „enhanced validation" in Entwicklung (Mainnet seit 29.01.2026). MT-Bauraum exakt: laufendes gewichtetes Anti-Collusion-Modell (Sybil-Penalty, Jaccard-Cluster) in Produktion auf Base = Praxis-Evidenz wo Thread nur Theorie hat.
- **KERN-THESE (von Lars, das fehlende Element im Thread):** Es gibt keine statisch „richtige" Trust-Aggregation — Angriffs-/Fehlervektoren verändern sich DYNAMISCH, jede heute bewiesene Formel ist morgen umgangen. Die A2A-Community braucht JETZT eine praktikable, zuverlässige Lösung mit genug Flexibilität für schnelle Anpassung an neue Vektoren OHNE Spec-Amendment/Konsens. Chain-agnostische off-chain Attestation IST dieser Anpassungs-Mechanismus (Logik im Validator → Reaktion in Stunden statt Monaten). Adressiert direkt das Spec-Eigenrisiko „manipulation/adversarial coordination over time" das die Spec heute nur an Entwickler delegiert. Enterprise-Bedarf (GF will schnell/einfach/günstig/zuverlässig, nicht theoretisch final) = WARUM es jetzt zählt — aber FACHLICH verpacken (Adaptierbarkeit in nicht-stationären Bedrohungsräumen), nicht als Vertrieb (Forum = Protokoll-Idealisten, Anti-Intellektualismus-Falle vermeiden).
  - **NEXT (eigene Session):** (1) vollständige spätere Thread-Seiten lesen (Fetch griff nur S.1, JS-Pagination — v2-Validation-Stand seit Mainnet-Launch); (2) Beitrag ausformulieren: Praxis-Evidenz (Swarm-Modell-Trade-offs) + These (Adaptierbarkeit schlägt Eleganz im dynamischen Vektorraum); (3) durch Review-Engine §12 (gpt-5+gemini-3.1-pro-preview+sonar-pro) gegen „fachlich wasserdicht + NICHT als Werbung lesbar"; (4) posten mit validem Rückkanal. Forum-Form, MT-Substanz.

## D3 — MANDATE-Runtime-Enforcement Scope (DESIGN ONLY, HARD GATE aktiv)
- **HARD GATE (aus D1/PR #41):** Kein Production-Code vor formalem Delegation-Enforcement-Modell mit 3-Reviewer-Konsens. "STOP before code." Aktueller Stand = Design-Territorium.
- **Soll-Struktur (patent_evaluation.md:66-78):** 3-Layer — (1) AAE bindet deklarierte Constraints kryptographisch → (2) Behavioral Anomaly Scoring prüft Runtime-Verhalten dagegen → (3) On-Chain-Anchor als non-repudiable Compliance-Proof. Envelope: {mandate:{scope:[...]}, constraints:{...}, validity:{...}}.
- **Datenmodell ~70% vorverdrahtet:** agent_delegations.aae_id + IPR.aae_ref (sha256-CHECK) = Hash-Link da; interaction_proof_records (aae_ref, outcome_hash, outcome_correct, confidence, anchor) = Behavioral-Evidence reich + AAE-verknüpft; violation_records (violation_type, interaction_proof_id, principal_did, adjudicator_type, reversible) = Outcome-Sink da; Anchoring (anchor_to_base) wiederverwendbar.
- **3 konkrete Gaps:**
  1. KEIN materialisierter Constraint-Set — AAE nur per sha256-Hash referenziert, mandate/constraints/validity-JSON in keiner abfragbaren Tabelle. Evaluator kann nicht gegen unauflösbare Constraints prüfen. → braucht aae_envelopes-Store keyed by Hash.
  2. KEIN Evaluator — nichts joint interaction_proof_records × constraints → verdict. anomaly.py ist nächstes Scaffold aber explizit advisory (trust-score-Signale, NICHT mandate-scope).
  3. KEIN Write-Path verdict → violation_records; constraint_mode hat keinen enforce-State (nur none/inherit).
- **Architektur-Guards (aus GCP-Call-Strang, V1.15):** keine zirkuläre Agent-Selbstprüfung; kein DSGVO-Volllog → Hash/Attestation-Anchoring statt Inhalt (Inhalt bleibt beim Unternehmen).
- **NEXT (eigene Session, Reihenfolge zwingend):** (a) Constraint-Taxonomie aus patent_evaluation.md + moltycel_refit_phase_a_v2 (~187-245) extrahieren — exakte Constraint-Typen, damit Evaluator gegen Spec statt Vermutung baut. DANN (b) D3-ADR: aae_envelopes-Tabelle + Evaluator-Contract + enforce-mode State-Machine, für 3-Reviewer-Runde. Design only, kein Code.

## Changelog

- **2026-06-02 — V1.20**: D3+CEP-Konzeptpapier (PR #126) + Positionierungs-Review (whitepaper-mode) dokumentiert (Notiz im D3-Strang oben). Kernbefund: These (b) Governance-Transition = stärkerer/originellerer Kern als (a); Paper-v2 um (b) restrukturieren. Paper-v2-TODO (nach D3-Abschluss): (b)-Hauptthese + Related-Work-Sektion (NIST AI RMF/EU AI Act/SP800-207/W3C VC/ERC-8004/MS Entra/Sybil-lit) + Oracle-Problem frontal (EAS+ZK/attestation-net) + "provably" abschwächen. = Aufschlag arXiv 1.9→2.0.
- **2026-06-02 — V1.19**: D3-MANDATE-Enforcement-Implementierungsstand dokumentiert (Status-Sektion oben). Komponente 1 (Store) + Komponente 2 (Evaluator) LIVE, Evaluator im ADVISORY-Modus (loggt signierte DENYs + violation_records, blockiert nicht). PRs #110/#111 + #116-#121, deployed HEAD 4f864781. Komponente 3 (enforce-Chokepoint) GATED auf CEP (#122); Acceptance-Gate D-1 PENDING (NICHT CEP-gated, reine Krypto-Verifikation issuer_did/envelope_signature). Empfehlung nächste Session: (a) D-1 bauen + (b) CEP-ADR-Designarbeit parallel.
- **2026-06-02 — V1.18**: CEP-Governance-Strang eröffnet (`docs/decisions/ADR-CEP-governance-DRAFT.md`, Status KONZEPT/design-only). CEP = Combined Enforcement Protocol: enforce-mode-Autorität personen-/chain-/instanz-unabhängig (10-Jahres-Horizont). Richtung = objektive Bedingungen (NICHT ZK/MPC/Single-Chain). 3 Bausteine: (a) Regel-Versionen chain-agnostisch verankert (TechSpec §6, Multi-Chain-Quorum), (b) Stimmgewicht an behavioral trust score (Sybil-resistent), (c) Zeitschloss + öffentliches Veto. Ramp-up Gründer→CEP bei 4 GLEICHZEITIGEN Bedingungen (AND): Mindestzeit + >=N Sybil-geprüfte RPs + >=M Verticals + kein Cluster >X% Stimmgewicht; N/M/X/Zeit VORAB verankert. OFFENE KERNFRAGE: wer misst die 4 Bedingungen → MUSS unabhängig aus verankerten Daten nachrechenbar sein (kein interner SPOF). Blockiert Komponente 3 NICHT für advisory/none, aber scharfes enforce-Umschalten hängt an CEP. NEXT: eigenes ADR (Recon→Proposal→Review wie ADR-D3).
- **2026-05-30 — V1.17**: D3-MANDATE-Enforcement-Scope dokumentiert (DESIGN ONLY, HARD GATE aktiv) — Soll-3-Layer, Datenmodell ~70% vorverdrahtet, 3 Gaps (Constraint-Store/Evaluator/enforce-Gate); NEXT (a) Constraint-Taxonomie dann (b) D3-ADR für 3-Reviewer-Runde.
- **2026-05-29 — V1.14**: ERC-8004-magicians-Beitrag als lebender Strang (ersetzt aeoess-Kanal) — offene Aggregations-/Kollusionsfrage = MT-Bauraum (laufendes Anti-Collusion-Modell als Praxis-Evidenz); Kern-These Adaptierbarkeit>Eleganz im dynamischen Vektorraum + Enterprise-Bedarf; NEXT eigene Session via Review-Engine vor Posting.
- **2026-05-29 — V1.13**: ERC-8004-Einbringungs-Kanal dokumentiert — On-Chain-Visibility kein Hebel (Feld ~50 aktiv, Self-Feedback gefährlich); wertvoller Weg = eigene fachliche Präsenz in der ethereum-magicians-Diskussion (bei den 4 Spec-Autoren bekannt werden vor Validation-Finalisierung). NEXT eigene BD-Session.
- **2026-05-29 — V1.12**: ERC-8004-Validation final — permissionless aber keine scharfe Registry auf Base (kanonische auf MinimalUUPS, Key bei 0x5472); Validation-Sprint vom Tisch bis Upgrade. Ökosystem-Report #73: echter Sichtbarkeits-Hebel = Feedback+x402, nicht Validation.
- **2026-05-29 — V1.11**: Korrektur zu #91 — ERC-8004-Proxies (0x8004A169/0x8004BAa1) sind fremd-owned (owner()=0x547289…, offizieller erc-8004-Deployer), MolTrust ist Konsument, nicht Owner. Validation-Weg neu gefasst (Ownership-Entscheidung statt nur Deploy).
- **2026-05-29 — V1.10**: ERC-8004-Deferred-Item um On-Chain-Provenienz präzisiert (Base mainnet, `eth_getStorageAt` EIP-1967 + Blockscout): MolTrust-Identity/Reputation-Proxies fahren die echten offiziellen Referenz-Impls (`0x7274e874…`/`0x16e0FA7f…`) → faktisch konform; kanonische Singletons noch MinimalUUPS; nur Validation fehlt → Guard bleibt dort scharf; konsistenter Build-Weg = eigener Proxy → offizielle Validation-Impl `0xDB31f5d9…`.
- **2026-05-28 — V1.9**: Vier Drift-Findings aus Opus-4.8-Audit-Session aufgenommen. **Neu Deferred (1):** ERC-8004 ValidationRegistry nicht implementiert (nur Identity+Reputation belegt) — Decision offen + Proof-of-Work-Guard gegen Validation-Claims. **Neu Medium (2):** CLAUDE.md TechSpec-Versionsdrift (v0.3 gelistet vs v0.8.1 live), Dirty Working Tree auf Server-main (§4.2). **Neu Low (1):** Memory-Pfaddrift WORKFLOW.md-Speicherort. Quelle: Code/Spec-Audit + `git status ~/moltstack` 2026-05-28.
- **2026-05-28 — V1.8**: Sechs Items aufgenommen aus dem §12-Re-Re-Review von `@moltrust/openclaw-plugin@2.0.0-alpha.2` (Synthesis-Votum FREIGEBEN; alle 6 Items von den Reviewern explizit als „nicht release-blockierend" klassifiziert, für v2.0.0-beta/RC bzw. unbounded Backlog). Quelle: `~/moltstack/reviews/20260528_174139_openclaw-plugin-v2.0.0-alpha.2_review.md`.
  - **Neu Medium (1):** openclaw-plugin Rate-Limit-Strategie für parallelisierte Counterparty-Lookups (Aktion #5) — erst nach Beobachtung echter Last priorisieren.
  - **Neu Low (5):** Test-Coverage Own-DID Early-Exit Proof (#1), Test-Coverage Mixed-State Counterparties + failOpen=true (#2), Block-Priority README-Dokumentation (#3), Multi-Counterparty-Block Logging-Enhancement (#4), „Silent Enforcer"-Pattern in README dokumentieren (#6).
  - **Kontext:** Drei-Iterations-Sprint (alpha.0 → alpha.1 → alpha.2) mit jeweiligem 3-Modell-§12-Review (gpt-5 + gemini-3.1-pro-preview + sonar-pro). Alpha.2 explizit zur npm-Publikation freigegeben; diese 6 Items adressieren was Reviewer als „polish/regression protection für beta/RC" markiert haben.
- **2026-05-22 — V1.7**: Ein Low-Item aufgenommen — `compute_phase2_score`-Härtung (depth-Doku, Timeout-Bound, Exception-Handling der geteilten Graph-Traversal-Funktion). Ausgelagert aus dem §2.3-Cross-Review `s121-a2a-card-fix` (#3/#4), out-of-scope des Sprint-1.2.1-Handler-Fix.
- **2026-05-22 — V1.6**: Ein Medium-Item aufgenommen — Pre-push Secret-Audit-Hook, struktureller Schutz gegen die im Incident INC-2026-05-22-test-key-exposure aufgedeckte Leak-Klasse (Secret im Public-Repo). Quelle: `docs/incidents/2026-05-22_test-key-exposure.md` §9. (`**Status:**`-Zeile auf V1.6 nachgezogen — war seit V1.5 stale.)
- **2026-05-22 — V1.5**: Ein Low-Item aufgenommen — Voll-Secret-Scan moltrust-api Full-History, als Vorbedingung für einen etwaigen späteren History-Rewrite. Ausgelagert aus dem Test-Key-Incident Phase-2-SPEC (`docs/specs/2026-05-22_test-key-history-scrub-SPEC.md`); dort Option C gewählt (kein Rewrite, nur Working-Tree-Redact von `pentest.sh`).
- **2026-05-15 — V1.4**: API-Sprint-Übergabe aus moltrust-web Phase-1-Analyse §8 als verfolgbare Items aufgenommen, ausgelöst durch den Versionierungs-Audit am 2026-05-15 (~/moltstack/audits/2026-05-15_api-versioning.md) und die Conversion-Chat-Nachfrage zur §8-Kommunikation.
  - **Neu High (1):** AAE ins Credential einbauen (Phase-1 UNC-07 + Lars-Entscheidung) — koordinieren mit Credit-Middleware-Idempotency-Sprint (beide Schema-Change auf /identity/register, nicht parallel).
  - **Neu Medium (4):** API-Versionierung Single-Source + v1-Contract klären (Audit-Befunde: 3 Stellen ohne zentrale Quelle, Rückwärts-Dekrement 2.6→2.4, 0/136 Pfade versioniert), Trust-Score-Reads Rate-Limiting (Handler-Signatur-Refactor), CAEP als Extension in agent-card.json deklarieren, .well-known-Mirror-Generierung + Deprecation-Header.
  - **Sequenzierungs-Hinweis:** AAE-Sprint und Credit-Idempotency-Sprint berühren beide /identity/register — Reihenfolge ist Lars-Entscheidung, aber nicht parallel laufen lassen.
  - moltrust-web kann die "embedded AAE"-Darstellung erst nach Merge des AAE-Sprints zeigen; bis dahin entschärft PR1 die Falschaussage zu "separater delegation/configure-Schritt".
- **2026-05-15 — V1.3**: Credit-Middleware-Sprint 14./15.05. abgeschlossen (PR #27 merged), Out-of-Scope-Items + Health-Check-Findings nachgezogen.
  - **Resolved (raus):** `agents/traffic_monitor.py File-vs-DB Architektur` — heutige Entscheidung file-based v2 wiederhergestellt + cron wieder funktional, Item geschlossen.
  - **Neu High (2):** Credit-Middleware Idempotency-Mechanismus (GPT-5 Cross-Review CRITICAL F, eigenes Feature mit Schema-Change), cron.service OOM-kill investigieren (Health-Check 14.05. 02:01 UTC).
  - **Neu Medium (7):** Credit-Middleware process-wide-Scope + Inversion debit-vor-call_next (Spec V2.1 Section 9.2), WORKFLOW.md V1.2 — heutige Lessons einarbeiten (SQL-Validation, fetch-vor-Branch, /tmp-Dateien), tx_type CHECK + FK credit_transactions→credit_balances, FastAPI on_event → lifespan Migration (4 DeprecationWarnings im Test-Output), traffic_monitor.py in Watchdog AGENTS-Liste, migrations/add_outcome_tracker.sql Altlast klären, Auto-Probe-Migrations Repo-Status verifizieren, Stash-Hygiene Post-Credit-Sprint (konsolidiert mit altem "Stash@{0} Post-Triage Review").
  - **Neu Low (3):** Separate Test-DB für Credit-Tests, known_callers Tabelle DROP/Migration (45 rows, seit 18.04. stale), logrotate permissions fix (moltguard.log 19.8 MB).
  - **Neu Bootstrap (1):** docs/incidents/ Folder als Konzept einführen.
  - **Umformuliert:** `experiments/xmtp/ Sandbox-Architektur-Entscheidung` → zusammengezogen mit dem neuen XMTP-v3-Stash zu einem Item "XMTP v3 Migration testen + committen, dann Sandbox-Architektur entscheiden" mit Reihenfolge-Hinweis.
  - **ADR-Liste in `docs/decisions/`-Item ergänzt** um ADR (6) Credit-Middleware Minimal-Variante als bewusste Scope-Entscheidung des 15.05.-Sprints.
- **2026-05-13 — V1.2**: Drei resolved-by-action Items entfernt: TrustScout reanimate/decommission (resolved durch Diagnose 13.05. → PR #22 permanently removed Watchdog-Eintrag), TrustScout-Silencing-Commit (resolved durch PR #21 + #22), Memory #25 TrustScout-crontab-Lüge (resolved durch Memory-Replace 12.05.). Zwei neue Low-Items hinzu: trustscout.py + 2 systemd-Service-Files Investigation (offene Architektur-Frage, kein akuter Fix nötig), 5 stale lokale Branches Cleanup.
- **2026-05-13 — V1.1**: BACKLOG-Audit gegen Server-State durchgeführt (4 von 30 Items stale: stash-Claims falsch, herald_v3.py uuid-pattern nicht im File, settlement.py isinstance-Pattern anders als vermutet, KNOWN_FAILURES-Tests nicht im File). CAEP v2 umformuliert per Lars-Korrektur (nicht blocked auf Harald, sondern neuer Sprint mit Cross-LLM-Review). Telegram-Token-Item präzisiert (Rotation done by Lars server-side, verbleibendes Issue ist httpx-Log-Leak). Stash@{0} Post-Triage Review als neues Medium-Item hinzu.
- **2026-05-13 — V1**: Initial. Konsolidiert offene Items aus 12.05.26 (Auto-Probe-Drama) + 13.05.26 (WORKFLOW.md V1-Merge).
