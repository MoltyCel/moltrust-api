# CLAUDE.md — moltrust-api

Repo-spezifische Instruktionen. Voller operativer Rahmen: `docs/WORKFLOW.md` (in **diesem** Repo). Backlog: `docs/BACKLOG.md`.

## Identity Kontext

**MoltyCel = Lars Kroehls GitHub-Identität** (lars@moltrust.ch, "Lars Kroehl"). Kein separater Bot, kein separater privater Account. Manuelle Posts via MoltyCel-Account sind normal. Autonomes Bot-Posting ist seit 12.04.26 deaktiviert — Claims über aktuelles Auto-Posting = Drift, gegen WORKFLOW.md §0.1 prüfen.

## Repo-as-Source-of-Truth (HART — WORKFLOW.md §11 V1.2)

- **11.1** Kein Server-Deploy ohne vorherigen gemergten Commit im zuständigen produktiven GitHub-Repo. `post-sha == repo-sha`.
- **11.2** Jede Arbeitsiteration sofort committen, sobald ein Artefakt-Kandidat existiert — Chat-Scratch zählt nicht.
- **11.3** Pro Console ein eigener `git worktree`. Server-schreibende Arbeit seriell — „Server frei" erst nach protokollierter Anfrage+Bestätigung.
- **11.4** Session-Start: `git fetch`, `git worktree list`, `git status`, `origin/main` — frischer Branch ab `origin/main` (0 behind), nie von stale local `main`.

**Geltungsbereich:** repo-verwaltete Dateien. Server-Infra (nginx/systemd/cron) ist **NICHT** repo-verwaltet → bis zur Backlog-Überführung manuelle Sorgfalt + Audit-Eintrag.

## Discovery-Checklist (HART — nichts gilt als "fertig" bevor entdeckbar)

Nach jedem neuen Endpoint, jedem neuen Skill, jeder neuen API-Capability:

- [ ] **Gate:** Ist der Endpoint internal-only / admin-only (nicht für externe Konsumenten-Agents gedacht)? Wenn ja: **nicht** in Agent-Card / öffentlicher OpenAPI-Spec eintragen, restliche Discovery-Schritte überspringen — internal-Entscheidung in `docs/BACKLOG.md` oder Audit-Eintrag dokumentieren.
- [ ] Agent-Card (`/.well-known/agent-card.json`) — neuer Skill / Capability eingetragen, A2A v1.0-konform
- [ ] Falls authentifizierte Erweiterung: Extended Agent Card (`/extendedAgentCard`) gepflegt
- [ ] OpenAPI-Contract (`/docs`-Spec) — Pfad, Schema, Beispiele konsistent
- [ ] `api.moltrust.ch/llms.txt` — Endpoint-Referenz für Agent-Konsumenten aktualisiert
- [ ] Weitere `.well-known/`-Surfaces (`agent-registration.json` ERC-8004, `jwks.json`, …) falls Auswirkung — konsistent halten
- [ ] **Cross-repo:** Falls aus dem Endpoint eine HTML-Seite unter `moltrust.ch` entsteht (Marketing-Landing, Dev-Docs, Blog), Discovery-Schritte parallel im `MoltyCel/moltrust-web` Repo nachziehen (dort: `sitemap.xml` + GSC-Re-Submit, siehe `CLAUDE.md` dort).

**Begründung:** „Entdeckbarkeit = Definition of Done" — Lesson aus GROUP-5-Nachzug Mai 2026 (`MoltyCel/moltrust-web`): 5 Seiten waren live, aber wochenlang nicht in Sitemap → für Crawler unsichtbar trotz vorhandenem Inhalt. Analog für die API: ein Endpoint, der nicht in Agent-Card / OpenAPI / `llms.txt` referenziert ist, wird von Verbraucher-Agents nicht gefunden — auch bei HTTP-200.

Volltext + Begriffsdefinitionen: `docs/WORKFLOW.md` §11.


---
## Console Operating Rules

### COMPACT / NO-REASONING-PATH
Direktes Ergebnis zuerst — keine Schritt-für-Schritt-Begründung der eigenen
Vorgehensweise. Reasoning nur bei strategischen Lars-only-Entscheidungen.

### CONSOLE-AUTONOMIE & KB-FIRST
- Fehlende Datei/Info: zuerst in der KB suchen; sonst Console-Command der nach
  `~/Downloads` lädt (nie nur `/tmp`).
- Console arbeitet autonom mit minimalen Rückfragen; führt GH push/squash/merge
  selbständig durch für **operative** Doku/Code.
- NICHT für global/strategische Änderungen (→ erst Lars).

## Anti-Drift-Quickref

Vor Eskalations-Berichten Cross-Check gegen WORKFLOW.md §11.5:
- Server `/var/www/html/.git`-Anomaly = kein Vorfall
- Web-Root-Sync NIE komplettes main-Repo (Info-Leak)
- "Live gefixt" → sofort Repo-Commit nachziehen

GitHub-API: unauth 60/h shared session — niemals pollen, siehe WORKFLOW.md §6.4.

## SPEC-FAKTEN-PIN (aae)

- **Zitier-Primärquelle** = die **publizierte** `draft-kroehl-agentic-trust-aae-00`, sha256 `2847f4da`,
  **live verifizierbar** via Datatracker
  (`https://www.ietf.org/archive/id/draft-kroehl-agentic-trust-aae-00.txt`, 48500 bytes). Inhalt:
  9-Step-Verifikation, `delegator_aae_hash` §3, §6.5 Cascade Revocation, §6.6 Clock Skew. Lokale
  Arbeitsrevision „-04" == **inhaltsgleich** zur publizierten -00. Citations IMMER gegen diesen Draft.
- **KB-Derivat** = `~/moltstack/docs/spec-fakten/aae.md` trägt denselben `2847f4da` als Inhalts-Pin
  (Integritäts-Index, **KEINE Zitierquelle**).
- **`b619d163` = veraltete lokale `.md`** (7-Step, **kein** `delegator_aae_hash`, kein §6.5/§6.6) —
  **NIE Quelle.** Falsch-Pin aus #185 entfernt (pinte auf b619d163 + erklärte `2847f4da` „entfernt").
  Fehlergrund: Suche **nur auf lokalen Hosts** ohne Live-Datatracker-Fetch — „nicht lokal gefunden"
  wurde fälschlich als „Artefakt existiert nicht" gelesen.
- **Strukturregel (verhindert Wiederholung):** Spec-Primärquelle IMMER per Live-Datatracker/Repo-Fetch
  verifizieren, nie nur gegen lokale Hosts. **„Nicht lokal gefunden" ≠ „existiert nicht".**
