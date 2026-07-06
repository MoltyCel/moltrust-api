# WORKFLOW.md — MolTrust Operational Discipline

**Status:** V1.5, lebendiges Dokument
**Letzte Aktualisierung:** 2026-06-27
**Eigentümer:** Lars (Entscheidungen) + Claude/Claude Code (Ausführung gemäß diesem Dokument)
**Geltungsbereich:** Alle MolTrust-Repos (moltstack, moltguard, moltrust-protocol) plus die Bot-/Agent-Infrastruktur

---

## 0. Warum dieses Dokument existiert

Am 12.05.2026 wurde während des Auto-Probe-Sprints klar, dass operative Disziplin im MolTrust-Setup über die letzten Monate gedriftet ist. Sieben separate Recovery-Operationen an einem Tag — Sprint-Rollback, Working-Tree-Mess in drei Repos, PAT-Leak in `.git/config`, Memory-Lüge über TrustScout-Cron, Hook-Bug, CONFORMANCE-Drift, Telegram-Token im Log. Keine dieser Sachen war an dem Tag neu entstanden. Alle waren akkumulierte schlafende Fehler, die niemand systematisch geprüft hatte.

Dieses Dokument definiert die Routinen, die solche Akkumulation verhindern. Es ersetzt mündliche Vereinbarungen und Memory-Einträge mit Disziplinen, die nachvollziehbar im git versioniert sind.

**Was dieses Dokument nicht ist:** kein Spec-Dokument für Features, kein Compliance-Dokument für Externe, kein Marketing. Internes Operating Manual.

## 0.1 Identity Kontext (Drift-Hotspot)

**MoltyCel ist Lars Kroehls GitHub-Identität**, nicht ein separater Bot-Account. Email `lars@moltrust.ch`, Display "Lars Kroehl". Es gibt KEINEN separaten privaten Lars-Kroehl-Account auf GitHub.

**Konsequenz für Console-Diagnose:**
- Posts vom MoltyCel-Account ≠ "Bot-Aktivität". Lars postet manuell via diesen Account. Autonomes Bot-Posting ist seit 12.04.26 deaktiviert.
- Claims wie "der Bot hat heute X gepostet" (autonom) sind Memory-Drift, nicht Vorfall — direkt bei Lars verifizieren bevor eskalieren.

**Legal/Corporate-Identität ist getrennt:** `kersten.kroehl@cryptokri.ch` + "Lars Kersten Kroehl" für Stripe, npm/PyPI-Publish, Verträge, Domain-Registrierung. Niemals global ersetzen — Kontext entscheidet pro Aktion.

## 1. State-of-Truth Architektur

Das größte heutige Problem war, dass kein einzelner Ort definitiv den Zustand des Systems beschrieb. Memory hatte Lügen, Server-State war undokumentiert, Specs lebten teilweise nur in Konversationen.

Ab jetzt: **eine klare Aufteilung mit dokumentierten Zuständigkeiten.**

### 1.1 Was in Claude-Memory lebt

Nur das hier:

- Identitäts- und Kontextinfo über Lars und das Team (Namen, Rollen, Background)
- Verhaltensregeln und Kommunikations-Präferenzen (Compact Mode, Sprache, Ton)
- Strategie-Prinzipien und Positionierung (mach-statt-erklär, IMDA-Framework, etc.)
- Session-Rituale (Health-Check beim Start, Halluzinations-Guards)
- Kurze Pointer zu wichtigen aktuellen Sprints mit Verweis auf `docs/sprints/`

Was NICHT in Memory gehört:
- Operativer System-State (was läuft auf welchem Port)
- Cron-Job-Listen (drift permanent)
- Branch-Namen und Commit-SHAs außer den durabel-relevanten
- Tabellen-Schema-Details
- Token-Werte oder Credentials

**Regel:** Wenn ein Memory-Eintrag älter als 14 Tage operative Detail-Info enthält, wird er gegen die Realität geprüft oder gelöscht. Drift wird aktiv erkannt und behoben.

### 1.2 STATUS.md — laufender System-Zustand

Datei: `docs/STATUS.md` (repo-relativ, `MoltyCel/moltrust-api`)

**Inhalt:** automatisch täglich aktualisierter Operating-State des Systems. Mensch-lesbar, kurz.

Sections:
- Running Services (systemctl-aktive Services mit Ports)
- Active Cron Jobs (mit Schedule, Script-Pfad, was sie schreiben)
- Active Branches (lokal + remote, mit last-commit-date)
- Open PRs (über alle moltrust-relevanten Repos)
- Working-Tree-Status (dirty/clean pro Repo)
- Stashed Work (mit Beschreibung warum es deferred ist)
- Known Drift (zwischen verschiedenen Locations)
- DB Schema Drift (Tables nicht in migrations vs Tables in migrations)

**Update-Mechanismus:** cron-Job `0 7 * * *` läuft `scripts/generate_status.py`, schreibt STATUS.md, commitet auto auf einen `chore/status-auto`-Branch (nicht auf main). Lars reviewt einmal pro Woche und mergt manuell.

**Wenn STATUS.md älter als 24h ist:** Warnung in Telegram-Watchdog-Output.

### 1.3 docs/specs/ — Feature-Spezifikationen

Pfad: `docs/specs/` (repo-relativ, `MoltyCel/moltrust-api`)

**Inhalt:** alle Specs für aktuelle und kommende Features, mit explizitem Layer-Scope.

Format pro Spec: ein Markdown-File `YYYY-MM-DD_<feature-name>.md` mit Sections:

1. Goal (was wir erreichen)
2. Non-Goals (was wir explizit nicht tun)
3. Architecture-Layer-Scope (welche Code-Layer betroffen sind — Pflichtfeld nach Auto-Probe-Lesson)
4. Data-Model-Changes (welche DB-Tables/Columns dazukommen oder sich ändern)
5. API-Contract-Changes (welche Endpoints neu oder modifiziert)
6. Migration-Path (für existierende User/Data)
7. Rollback-Plan
8. Success-Criteria
9. Open Decisions

**Regel:** kein Sprint startet ohne Spec mit allen 9 Sections gefüllt. Architecture-Layer-Scope ist nach Auto-Probe-Drama 12.05.26 verpflichtend explizit.

### 1.4 docs/decisions/ — Architecture Decision Records

Pfad: `docs/decisions/` (repo-relativ, `MoltyCel/moltrust-api`)

**Inhalt:** kurze 1-Pager pro durabel-wichtige Entscheidung. Format: `NNNN-<slug>.md` (sequenzielle Nummerierung).

Pro Decision:
- Datum
- Status: Proposed / Accepted / Superseded / Deprecated
- Context (warum stand diese Entscheidung an)
- Decision (was wir entschieden haben)
- Consequences (was das nach sich zieht)
- Alternatives considered

Beispiele für Decision-Worthy:
- "Auto-Probe als mounted sub-app statt globale Middleware" (V2-Architektur-Entscheidung)
- "Pattern B credential-helper statt SSH für moltrust-protocol Repo" (Token-Recovery)
- "Memory-Caps und Alerting-Thresholds für Probe-Spawn"

Nicht decision-worthy: Implementations-Details, Bugfixes, kosmetische Änderungen.

### 1.5 docs/sprints/ — Sprint-Plans und Reports

Pfad: `docs/sprints/` (repo-relativ, `MoltyCel/moltrust-api`)

**Inhalt:** Pre-Deploy-Reports, Post-Deploy-Reports, Workflow-Pläne für laufende Sprints (z.B. `2026-05-12_smithery-v2-workflow.md`).

Lebenszyklus pro Sprint:
1. Sprint-Plan vorher (geht in `docs/specs/`)
2. Pre-Deploy-Report vor Cutover (geht in `docs/sprints/`)
3. Post-Deploy-Report nach Cutover (geht in `docs/sprints/`)
4. Bei Failure: Post-Mortem in `docs/sprints/`

### 1.6 audits/ — Audit-Outputs und Diagnose-Artefakte

Pfad: `audits/` (repo-relativ, `MoltyCel/moltrust-api`)

**Inhalt:** Static-Analysis-Outputs, GPT-5-Verification-Bundles, Header-Captures, andere Diagnose-Outputs.

Pro Datei: `YYYY-MM-DD_<topic>.md`. Diese Dateien sind "Forensik-Material", werden nicht gelöscht, dienen als historischer Beweis was zu welchem Zeitpunkt geprüft wurde.

### 1.7 Backlog

Pfad: `docs/BACKLOG.md` (repo-relativ, `MoltyCel/moltrust-api`)

**Inhalt:** Liste aller offenen Items mit Severity und letztem Touch-Date. Sortiert nach Severity.

Format:
```
## High
- [WORKFLOW.md fehlt] (added 2026-05-12, owner Lars)
- [TrustScout reanimate or decommission] (added 2026-05-12)

## Medium
- ...

## Low
- ...
```

**Regel:** jeder Sprint-Start beginnt mit Backlog-Review. Items älter als 30 Tage ohne Bewegung werden hinterfragt: ist es noch relevant, oder gestrichen?

## 2. Pre-Sprint-Checklist

Vor jedem produktiven Code-Sprint geht durch:

### 2.1 State-Check

- [ ] `cat docs/STATUS.md` — wann zuletzt aktualisiert? Wenn älter als 24h: refresh
- [ ] `git status` in allen relevanten Repos — alle clean? Sonst erst Working-Tree-Triage
- [ ] `cat docs/BACKLOG.md` — gibt es höhere-Prio-Items als das geplante?
- [ ] Memory-Realitäts-Check: stimmt Memory zur betroffenen Komponente noch? Wenn unsicher: live curl/grep/psql

### 2.2 Spec-Check

- [ ] Spec in `docs/specs/` existiert für diesen Sprint
- [ ] Alle 9 Sections gefüllt, insbesondere **Architecture-Layer-Scope**
- [ ] Open Decisions sind beantwortet
- [ ] Rollback-Plan ist explizit

### 2.3 Cross-Review

Für Sprints in folgenden Kategorien ist Cross-Review verpflichtend:
- Security-kritisch (Auth, Tokens, Permissions, Encryption)
- Architektur-kritisch (Middleware, Service-Topologie, API-Contracts)
- Multi-Layer-Operationen (DB-Migration + API + Cron in einem Sprint)
- Datenmodell-Migrationen mit existing-data-Impact

Cross-Review-Format: Spec wird vor Implementation an ein zweites LLM (GPT-5, DeepSeek, oder Kimi) geschickt mit explizitem Auftrag "find architecture gaps and missing risk surface". Mindestens eine Antwort-Iteration bevor Code geschrieben wird.

**Wenn Cross-Review skipped wird:** Begründung im Sprint-Spec dokumentieren. Skip ohne Begründung ist Disziplinverstoß.

### 2.4 Bundle-Size-Check

Wenn die Spec impliziert dass mehr als ~50KB Code geschrieben wird, splitten in:
- REST-Bundle (HTTP-Endpoints, Middleware, Auth)
- MCP-Bundle (MCP-Tools, FastMCP-Integration)
- DB-Bundle (Migrations, Schema-Drift-Checks)
- Test-Bundle

Grund: `ai_review.py` truncated bei ~60KB. Ein bundle größer als 50KB hat kein zuverlässiges Cross-Review.

## 3. In-Sprint-Disziplin

### 3.1 Pre-Commit-Diff-Verifikation

Vor jedem Commit, ohne Ausnahme:

```bash
git diff --cached --stat        # Anzahl Files + Zeilen-Anzahl
git diff --cached --name-only   # Welche Files
git diff --cached | head -60    # Erste 60 Zeilen content review
```

Wenn der Diff unexpected größer ist als erwartet, oder Files enthält die nicht erwartet wurden: STOP, untersuchen.

**Verboten:** `git commit -a` (commitet alles unstaged, ohne explizite Selektion).

### 3.2 Pre-Push-Hygiene

Pre-Push-Hook scannt für Secret-Patterns. Aber: Hook scannt nur Diffs, nicht `.git/config`. Manuell zusätzlich:

```bash
grep -E "https://ghp_|https://github_pat_|@github\.com" .git/config
```

Sollte leer sein. Wenn nicht: Token-in-URL Antipattern, fixen vor push.

### 3.3 Architecture-Briefing-Approval

Wenn ein Sprint Multi-Layer-Code-Änderungen impliziert (z.B. Middleware + Cron + API + DB), gilt:

1. Architecture-Brief schreiben (1-Pager, was wird wo geändert)
2. Lars approved den Brief
3. Erst dann Implementation
4. Bei Spec-Abweichung während Implementation: stop, neuen Brief, neue Approval

**Verboten:** Implementation ohne Brief bei Multi-Layer-Sprints. Solo-LLM-Coding für security/architecture-critical Code.

### 3.4 Cross-LLM-Verification bei kritischen Entscheidungen

Wenn während des Sprints eine architektonische Mid-Course-Korrektur nötig ist (z.B. "Skip-Liste zu eng, soll ich erweitern oder umstrukturieren?"): Cross-Review mit zweitem LLM vor Entscheidung. Nicht Solo entscheiden.

## 4. Post-Sprint-Hygiene

### 4.1 Sofort nach Deploy

- [ ] Smoke-Tests gegen Production-Endpoints
- [ ] Watchdog-Logs scannen auf neue Errors
- [ ] STATUS.md update triggern
- [ ] Sprint-Post-Deploy-Report in `docs/sprints/` schreiben (Stand, was funktioniert hat, was Backlog wurde)

### 4.2 Working-Tree-Hygiene

Nach Sprint-Ende:

```bash
git status
```

Erwartung: clean (außer audits/ untracked, falls bewusst).

Wenn modified Files oder unexpected untracked Files: nicht in nächsten Sprint mitschleppen. Entweder committen oder explizit stashen mit erklärendem Stash-Namen.

Stash-Namen-Konvention: `pre-<sprint-name>-WIP-<short-description>`. Beispiel: `pre-auto-probe-deploy-2026-05-12-WIP-incl-prediction-accuracy`.

Stashes älter als 30 Tage werden aktiv aufgelöst — entweder committen, dropen, oder in eine eigene branch. Nicht ewig stashen.

### 4.3 Memory-Realitäts-Sync

Wenn der Sprint Memory-Inhalte betraf (z.B. neue Services hinzugefügt, alte entfernt): Memory-Eintrag aktualisieren. Wenn Memory-Eintrag durch den Sprint outdated wurde: aktualisieren oder löschen.

Beispiel von heute: Memory #25 sagte "TrustScout crontab 4x/day". Reality: scout.py läuft 2x/day, trustscout.py wird gar nicht getriggert. Memory-Eintrag wäre nach Sprint-Cleanup geupdated worden.

## 5. Periodic Routines (automatisch)

### 5.1 Daily — STATUS.md auto-refresh

Cron: `0 7 * * * cd ~/moltstack && python3 scripts/generate_status.py`

Output: `docs/STATUS.md` mit aktuellem System-State, commited auto auf `chore/status-auto`-Branch. Bei drift gegen Memory: Telegram-Alert.

### 5.2 Weekly — Multi-Repo-Health-Check

Cron: `0 8 * * 1` (Montag früh)

Script `scripts/weekly_health_check.sh` läuft durch:
- Alle Repos `git status` — clean?
- Alle Repos `git fetch && git log --oneline origin/main..main` — lokale unpushed?
- Alle `.git/config` Files — Token-URLs?
- Alle Stashes älter 30 Tage
- Memory-Drift-Check gegen STATUS.md

Output: Telegram-Report. Wenn Issues gefunden: explizite Tasks für Lars.

### 5.3 Monthly — Audit-Recap

Cron: `0 9 1 * *` (1. des Monats)

Script läuft durch:
- Token-Rotation-Status (welche PATs/API-Keys laufen in <60 Tagen ab?)
- Memory-Hygiene-Status (Edit-Count, Realitäts-Drift)
- Backlog-Aging (Items älter als 30 Tage ohne Bewegung)
- Audit-Trail-Vollständigkeit (alle Sprints haben Post-Deploy-Report?)

Output: Markdown-Report im audits/-Folder + Telegram-Summary.

### 5.4 On-Demand — Pre-Sprint-Health-Check

Manuell vor jedem Sprint:

```bash
bash scripts/pre_sprint_check.sh
```

Output: STATUS.md-refresh, Working-Tree-Check pro Repo, Memory-Spot-Check, Backlog-Top-5.

## 6. Notfall-Routinen

### 6.1 Production-Regression-Detected

Schritt für Schritt, nicht ad-hoc:

1. **Halt all destructive operations.** Kein Force-push, kein `git stash pop`, kein Service-Restart.
2. **Static-Analysis erstellen** — vollständiger Snapshot des aktuellen State (Service-Status, Route-Inventory, Branch-State, Stash-State). Heute morgen: `audits/2026-05-12_static-analysis.md`.
3. **Cross-LLM-Validation** — Static-Analysis an GPT-5/DeepSeek/Kimi schicken mit Frage "validate findings, propose recovery path".
4. **Recovery-Plan dokumentieren** vor Ausführung — als File in `docs/sprints/`.
5. **Lars approved Recovery-Plan** vor irgendwelchen destruktiven Schritten.
6. **Recovery sequenziell ausführen** mit Verifikation zwischen Schritten.
7. **Post-Recovery-Report** in `docs/sprints/` schreiben.

Nichts überspringen. Auch wenn es schnell gehen soll. Heute morgen war Recovery-Zeit-Insgesamt ~6h, das ist akzeptabel bei einem Production-Issue.

### 6.2 Secret-Leak-Detected

Sofortmaßnahmen in Reihenfolge:

1. **Token rotieren** auf Service-Seite (GitHub, npm, Anthropic, etc.) — Token wird sofort tot, egal wo er noch im Logging steht.
2. **Multi-Storage-Audit — Pflicht-Checkliste:** Token kann an mehreren Orten gleichzeitig gespeichert sein. Vor Token-als-Done-Markierung systematisch durch alle bekannten Speicherorte gehen:
   - `~/.moltrust_secrets` (Standard env-Secrets)
   - `~/<bot-name>/secrets/` Folder pro Bot (z.B. `~/moltycelbot/secrets/GITHUB_PAT`)
   - `.git/config` aller relevanten Repos (URL-embedded Token Antipattern)
   - `~/.bashrc`, `~/.profile`, `~/.zshrc` (shell exports)
   - `crontab -l` (inline-exports in cron-Lines)
   - systemd-Unit `Environment=` oder `EnvironmentFile=` Statements
   - `~/.env` oder Project-spezifische `.env` Files
   - Docker-secrets falls Docker-Container im Einsatz
   - Logs (httpx default-Logging schreibt URL-embedded Tokens in plain text)
   
   Standard-Audit-Befehl: `grep -rE "ghp_|sk_live_|whsec_|github_pat_|sk-ant-" ~/.moltrust_secrets ~/*/secrets/ ~/.bashrc ~/.profile 2>/dev/null && crontab -l | grep -E "ghp_|sk_"`
   
3. **Token in allen gefundenen Orten ersetzen** mit gleichem neuen Wert. File-Permissions prüfen (0600 für secret-Files). Bei jedem Ort: Backup mit `.bak-<datum>`-Suffix anlegen.
4. **Migration auf sichereres Pattern** (SSH-Keys, credential-helper from env, Secrets-Manager).
5. **Post-Rotation-Verifikation:** für jeden Service der den Token nutzt: 1 manueller Test-Call mit dem neuen Token. Bei Bot-Services: nächsten cron-Tick abwarten ODER manuell triggern.
6. **Memory-Update** im Secret-Hygiene-Entry (#21).
7. **Post-Mortem-Eintrag** in `docs/decisions/` mit Lesson-Learned wenn die Rotation Cascade-Failure verursacht hat.

**Lesson 13.05.26:** MoltyCel-PAT-Rotation am 12.05. hatte nur `.moltrust_secrets` aktualisiert, aber `~/moltycelbot/secrets/GITHUB_PAT` wurde übersehen → 24h+ 401-Storm in MoltyCelBot, alle Drafts verloren. Multi-Storage-Audit (Punkt 2 oben) hätte das verhindert.

### 6.3 Watchdog-Alert-Storm

Wenn Watchdog viele Alerts in kurzer Zeit sendet:

1. **Diagnose-First** — was sagt der Watchdog konkret, ist es ein echtes Issue oder false-positive?
2. **Silencing nur wenn klar false-positive** — temp Edit mit `# TEMP DISABLED <date> — <reason>` Kommentar.
3. **Diagnostik-Session schedulen** für die echte Ursache, nicht das Symptom.
4. **Re-Enable** nach Diagnose mit echtem Fix, nicht Silence-permanent.

### 6.4 GitHub-API Rate-Limit-Hygiene

Unauthentifizierte GitHub-API-Calls teilen sich ein Limit von 60 Calls/Stunde pro IP — über ALLE Calls der Session hinweg. Konsequenz:

- **Verboten:** Polling-Schleifen gegen GitHub (CI-Status, Issue-Comments-Refresh, PR-Mergeable-Check via curl oder unauth gh CLI in Loops).
- **Pflicht:** Authentifizieren mit PAT für alle programmatischen Reads (raises auf 5000/h) ODER Web-UI nutzen (nicht rate-limited).
- **Diagnose-Falle:** Leergelaufene Quota antwortet mit HTTP 403 und einer rate-limit-Message — das wird regelmäßig als "CI-Fehler" oder "Endpoint down" fehlinterpretiert. Vor jedem solchen Schluss erst `gh api rate_limit` checken.

## 7. Cross-Repo-Disziplin

### 7.1 Repo-Inventory

Aktive MolTrust-Repos:
- `~/moltstack` — Haupt-API + Agents
- `~/moltguard` — MoltGuard-Service
- `~/moltrust-protocol` — Specs + Conformance-Docs
- `~/trouvart` — separates Projekt (außerhalb MolTrust-Scope)

Jedes Repo hat eigene Hygiene-Verantwortung. Multi-Repo-Operationen (z.B. moltstack-Code referenziert moltguard-Script) brauchen explizite Cross-Repo-Dependency-Documentation.

### 7.2 Branch-Naming

Konvention pro Repo:
- `main` oder `master` — produktiver Stand
- `feature/<name>` — neue Features in Entwicklung
- `chore/<name>` — Hygiene, Refactoring, Doku-Updates
- `fix/<name>` — Bugfixes
- `audit/<name>` — Audit-Branches, oft lokal-only

Branch-Naming inkonsistent zwischen Repos (moltguard nutzt `master`, moltstack `main`) ist Backlog-Item für Vereinheitlichung.

### 7.3 Cross-Repo-Dependencies dokumentieren

Wenn Code in Repo A auf Files in Repo B referenziert: explizit im Commit-Body. Beispiel:

```
feat(watchdog): conformance drift cron + remove dead Moltbook poster

Dependency: ../moltguard/scripts/check_drift.sh (committed in separate repo as 41159b0)
```

So findet man bei Disaster-Recovery die richtige Reihenfolge.

## 8. Verboten

Diese Patterns sind verboten, ohne Ausnahme:

- `git commit -a` (commitet alles unselektiert)
- `git stash pop` ohne explizite Selektion welche Files
- `git push --no-verify` ohne dokumentierte Begründung im Commit
- `git push --force` auf shared Branches
- HTTPS Git-Remote mit URL-embedded Token (`https://ghp_xxx@github.com/...`)
- Bash-Scripts mit `set -x` wenn sie Secrets handhaben
- API-Keys oder Tokens hardcoded in Code
- Solo-LLM-Implementation für Security oder Architecture-kritischen Code
- Sprint-Start ohne Spec mit allen 9 Sections
- Memory-Eintrag mit operativen Details ohne 14-Tage-Drift-Check

## 9. Mess-Punkte für WORKFLOW.md selbst

Dieses Dokument muss wirken. Mess-Punkte:

- **Anzahl Recovery-Operationen pro Monat:** Ziel: <2/Monat. Auto-Probe-Drama-Tag war Anomalie, sollte nicht Norm sein.
- **Working-Tree-Mess-Detected-Rate:** Wenn weekly_health_check.sh dirty Trees findet: kein Erfolg.
- **Memory-Drift-Vorfälle:** Wenn Memory-Realitäts-Sync (5.1) regelmäßig Drift findet: Memory-Hygiene-Disziplin nicht aktiv.
- **Stale Stashes >30 Tage:** Ziel: 0.
- **Cross-Review-Skip-Rate bei kritischen Sprints:** Ziel: 0% bei Security/Architecture.

Quartalsweise (3-Monats-Rhythmus): Review von WORKFLOW.md selbst. Was funktioniert? Was nicht? Updates oder Vereinfachungen.

## 10. Aktuelle offene Items aus WORKFLOW-Implementierung

**Bootstrap-Hinweis:** Diese Sektion-10-Items folgen direkt aus diesem Dokument und sind nicht-Sprint-Items in dem Sinne dass sie selbst keine separaten Spec-Dokumente in `docs/specs/` benötigen. Sie sind die Erst-Implementierung des Workflow-Frameworks. Ab der V2 von WORKFLOW.md gilt die Spec-Pflicht für alle weiteren Changes.

Items die direkt aus diesem Dokument folgen, aber noch nicht existieren:

- [ ] `scripts/generate_status.py` schreiben (Sektion 5.1)
- [ ] `scripts/weekly_health_check.sh` schreiben (Sektion 5.2)
- [ ] `scripts/pre_sprint_check.sh` schreiben (Sektion 5.4)
- [x] `docs/BACKLOG.md` initialisieren mit aktuellen Items (✓ V1.1 + V1.2, 13.05.26)
- [ ] `docs/STATUS.md` erste Version manuell schreiben, dann auto-refresh aktivieren
- [ ] `docs/decisions/` mit ersten 3-5 ADRs befüllen (Auto-Probe V2-Architektur, Pattern B credential-helper, etc.)
- [ ] Pre-commit-hook für conflict-marker-detection (`git diff --check`)
- [ ] Multi-Repo-Inventory-File mit Branch-Naming-Status
- [x] Telegram-Bot-Token rotation (✓ 12.05.26, Lars server-side. Verbleibendes Backlog-Item: httpx-Log-Leak fix)
- [x] Memory #25 TrustScout-Crontab-Lüge korrigieren (✓ via Memory-Replace 12.05.26 abends)

Diese Items werden in `docs/BACKLOG.md` mit aufgenommen.

## 11. Repo-as-Source-of-Truth & Deploy-Disziplin

Diese Sektion schliesst die **drei real passierten** Drift-Ursachen des moltrust-web-Reconcile (Mai 2026): (a) server-führender Datei-Inhalt ohne Repo-Commit, (b) nur-im-Chat lebende Doku-Iterationen, (c) Zwei-Console-Kollision im selben Worktree. Nicht jeden theoretischen Pfad — bewusst schlank.

**Geltungsbereich (ehrliche Bereichsgrenze, kein Schlupfloch):** §11 deckt **repo-verwaltete Dateien** (Code, Content, App-Config). **Server-Infrastruktur (nginx, systemd, cron) ist DERZEIT nicht repo-verwaltet** — Änderungen daran erfordern bis zu einer künftigen Überführung **manuelle Sorgfalt + Audit-Eintrag**; die Überführung ist ein eigenes, zeitlich entkoppeltes Backlog-Item (kein vorgelagerter Sprint). Das ist eine **deklarierte Bereichsgrenze**, kein legalisiertes Loch *innerhalb* des Bereichs. Weitergehende Härtung (Build-/Supply-Chain-Integrität, WORM-Audit, atomarer Lock, formaler Notfallpfad) ist bewusst **Backlog**, nicht §11.

### 11.1 Repo-first für versionierte Dateien

Jede Änderung an einer repo-verwalteten Datei (Code/Content/App-Config) MUSS vorher als **gemergter Commit im zuständigen Repo** liegen; ein Deploy rollt **ausschliesslich Repo-Inhalt** aus. Nach jedem Deploy gilt pro Datei `post-sha == repo-sha`, dokumentiert; ein Deploy ohne diese verifizierte Gleichheit gilt als **nicht abgeschlossen**. **Verboten:** server-führenden Datei-Inhalt erzeugen/ändern, der nicht aus einem Repo-Commit stammt. — *Repo-verwaltete Datei* = Datei, deren autoritative Quelle ein Commit im *zuständigen Repo* ist; *zuständiges Repo* = das produktive GitHub-Repo des Artefakts (Code/App-Config/Docs → `MoltyCel/moltrust-api`, Web-Root → `MoltyCel/moltrust-web`) — **nicht** ein lokales/temporäres/persönliches Verzeichnis oder Fork.

### 11.2 Iteration = Commit

Jede Arbeitsiteration an einem versionierten Artefakt wird committet, **bevor** die nächste beginnt. *Arbeitsiteration* = ab dem Moment, in dem ein **Artefakt-Kandidat** existiert (eine Änderung, die committet werden *könnte*) — Chat-/Console-Inhalt ist **nicht** von 11.2 ausgenommen, sobald er Artefakt-Kandidat ist; er ist **kein** gültiger Speicherort dafür. Eine Versionsangabe („Dokument ist v7") gilt nur, wenn diese Version im zuständigen Repo liegt.

### 11.3 Worktree-Isolation + serieller Server-Zugriff

**Pro Console ein eigenes `git worktree`-Verzeichnis** (getrennte HEADs/Working-Trees, gemeinsamer Objektstore); der shared Anchor wird nie aktiv editiert. Server-/Live-schreibende Arbeit läuft **seriell — eine Console am Server zur Zeit**. *Server frei* = definierter Ablauf: die deployende Console fragt explizit an; die andere bestätigt ausdrücklich (erkennbar daran, dass sie selbst **keine** offene Server-schreibende Operation hält); Anfrage **und** Bestätigung werden mit Zeitstempel festgehalten (Commit-/Ticket-/Chat-Protokoll). Echte atomare Lock-Härtung gegen Race/OOM ist das bewusst entkoppelte Backlog-Item — 11.3 macht den Ablauf nur so eindeutig wie ohne Lock möglich.

### 11.4 Session-Start-Frischecheck

Als erste Handlung an einem Repo: `git worktree list`, `git status` je Worktree, `git fetch origin` + `origin/main`-Hash (ahead/behind). Neue Arbeit startet in einem **frischen Branch** im dedizierten Console-Worktree. *Frischer Branch* = abgezweigt von `origin/main` **nach** `git fetch`, **0 Commits behind** `origin/main` — **nicht** von lokalem/stale `main`.

### 11.5 Anti-Drift-Guards

Drei beobachtete Drift-Muster, die als "Vorfall" fehldiagnostiziert werden oder Info-Leaks erzeugen:

**(a) Server-vestigial-git-Checkout ist KEIN Vorfall.**
Der Web-Root `/var/www/html/` enthält einen alten `.git`-Ordner aus dem cp-basierten Deploy-Modell, mit Owner www-data und ohne SSH-Key. Befund-Audits, die das als Sicherheitsanomalie melden (z.B. "fehlender SSH-Key", "falscher Owner"), sind Drift — nicht eskalieren, der HEAD ist bewusst nicht in main-Historie integriert.

**(b) Web-Root-Sync NIE per rsync/cp vom kompletten main-Repo.**
Das moltrust-web-Repo enthält Repo-Meta (CLAUDE.md, docs/BACKLOG.md, ADRs, docs/incidents/, docs/specs/). Ein full-tree-Sync auf /var/www/html/ würde diese Files auf moltrust.ch öffentlich exponieren. Sync nur servierte Files (*.html, *.json, *.txt, *.xml, *.css, *.js) — niemals docs/**, niemals .github/**, niemals Repo-Meta.

**(c) "Live gefixt" ≠ "im Repo".**
Bei Hotfixes an **repo-verwalteten** Files (z.B. security-floor in requirements.txt) gilt §11.1: nach dem Live-Fix sofort einen Repo-Commit nachziehen — sonst regressiert der nächste Deploy (PR #159, pip-audit-Floors waren live, nie im Repo). Für **Server-Infra** (nginx/systemd/cron) gilt die §11-Bereichsgrenze: kein zuständiges Repo, daher **Audit-Eintrag** statt Repo-Commit.

## 12. External Publish Review

Lessons-Reaktion auf den moltrust-openclaw-v2-Sprint (Mai 2026): lokale Tests + `npm publish --dry-run` sind notwendig, aber **nicht hinreichend**, bevor ein Artefakt ausserhalb der eigenen Repo-/Org-Grenze publik wird. §12 macht den 3-Modell-Review zur **Vorbedingung**, nicht zur optionalen Hygiene.

**Geltungsbereich** — §12 gilt für jede Aktion, die einen Artefakt-Stand **ausserhalb der MolTrust-Repos sichtbar** macht:

- **(a)** `npm publish` an eine öffentliche Registry (inkl. Prerelease-Tags via `--tag`; `--dry-run` ist ausgenommen).
- **(b)** `gh pr create` gegen ein Repo **ausserhalb** der `MoltyCel/*`-Org (Outreach-PRs, Awesome-Listen, Spec-Repos, Upstream-Fixes).
- **(c)** Outreach-Mails an externe Empfänger (Standardisierungs-Gremien, Vendor/Partner, Pitch-Drafts) — sobald der Empfängerkreis ausserhalb von CryptoKRI GmbH liegt.

**Nicht abgedeckt (keine §12-Pflicht):** interne MolTrust-Repos/Channels, Code-Reviews innerhalb des eigenen Repos, Server-Deploys (§11 gilt dort), `npm publish --dry-run`.

### 12.1 3-Modell-Review als Vorbedingung

Vor jeder §12-Aktion MUSS `~/moltstack/agents/ai_review.py` mit den drei Reviewern gelaufen sein:

- **gpt-5** (OpenAI) — semantische Kohärenz, Edge-Case-Logik
- **gemini-3.1-pro-preview** (Google) — technische Analyse, Spec-Konformität
- **sonar-pro** (Perplexity Sonar Pro) — web-grounded Faktenprüfung (Modell-IDs, externe Referenzen, Cross-Repo-Konvention)

Synthese läuft via Claude im selben Skript. Output landet in `~/moltstack/reviews/YYYYMMDD_<label>_review.md` (gitignored — siehe globales `CLAUDE.md`, Sektion „Security — AI Review Pipeline"). Schweigender Skripterfolg ist **kein** Pass: die Synthese MUSS Befunde nach Schweregrad ausweisen oder explizit „keine Blocker" konstatieren.

### 12.2 Briefing-Template

Jeder Review-Run wird durch ein **Briefing-Markdown** angestossen, kein roher Code-/Diff-Dump. Master-Template liegt im Review-Ordner (`~/moltstack/reviews/_templates/review-briefing.md`). Das Briefing benennt mindestens: Artefakt-ID (Paket+Version / PR-URL / Mail-Subject), Geltungsbereich-Kategorie (a/b/c), zu prüfender Inhalt, gezielte Review-Fragen, sowie bewusst getroffene Entscheidungen (verhindert Wiederholung in der Synthese).

### 12.3 Blocker-Handling

Synthese markiert Findings nach Schweregrad (Blocker / Major / Minor / Note). Bei **Blocker oder Major: §12-Aktion wird gestoppt**, Fix → erneuter Review-Lauf. Minor/Note werden vor der Aktion adressiert oder als Follow-up in `docs/BACKLOG.md` festgehalten — Schliessen ohne Eintrag ist verboten.

### 12.4 Konsens-Kriterium: wörtliches „FREIGEBEN" vs. Substanz (Governance-ADRs)

Präzisierung des 3-Reviewer-Konsens-Gates (gilt für die Multi-Modell-Review-Konsens-Logik — sowohl §12-Publish-Gates als auch ADR-Design-Gates / D1-HARD-GATE):

- **Wörtliches „FREIGEBEN" bleibt das Kriterium für abgrenzbare technische Mechanismen** (z. B. ADR-D3 MANDATE-Enforcement): klar umrissener Scope → einstimmiges wörtliches FREIGEBEN ist erreichbar.
- **Tiefe, parameter-reiche Governance-Designs** (z. B. CEP) erreichen wörtliches einstimmiges FREIGEBEN **strukturell nicht**: es gibt immer eine feinere Parameter-Stufe (Mess-Methodik, Schwellen-Einheiten) **plus** ein weiteres Legal-Doc zu fordern. **Empirisch belegt (CEP v8):** beide Review-Linsen attestierten explizit „keine Design-Blocker" / „Grundarchitektur konform", gaben aber **kein** FREIGEBEN-Label — die Eskalation lief auf Bau-Parameter-Mess-Methodik (technical) bzw. Legal-Deliverables (eu), beides strukturell ausserhalb dessen, was ein Design-Dokument schliessen kann.
- **Erreichbares + ausreichendes SUBSTANZ-Kriterium** für solche ADRs (ACCEPTED-Flip zulässig, wenn **alle drei** erfüllt):
  1. **beide Linsen** (technical + eu-compliance, soweit einschlägig) bestätigen **explizit „keine Design-Blocker";**
  2. die **Fundamentalkonflikte sind gelöst** (nicht nur benannt);
  3. die **verbleibenden Punkte sind strukturell Implementation-Contract (Bau-Phase) oder Legal-Process (extern) — NICHT Design.**
- Der **ACCEPTED-Flip** ist dann mit **dokumentierter Substanz-Begründung** legitim (Konsens-Block im ADR: Runden/Reviews, Zitat der „keine-Design-Blocker"-Befunde, IC/LP-Aufschlüsselung). **Präzedenz:** CEP-ADR-ACCEPTED (PR #143, Governance-Ebene); ADR-D3 → PR #107 (approve-with-nits, Mechanismus-Ebene).
- **Zweck:** verhindert den **Infinite-Review-Loop** bei Governance-ADRs (jede Version, die Impl-Detail einbringt, lädt mehr Impl-Detail-Forderung ein), **ohne das Gate aufzuweichen** — der HARD-GATE-*Zweck* (unabhängige Multi-Modell-Design-Bestätigung **vor** scharfem Code) muss substanziell erfüllt sein; das **wörtliche** Label ist nicht der Zweck.
- **Abgrenzung (kein Schlupfloch):** Das Substanz-Kriterium greift **nur**, wenn (3) nachweislich zutrifft (verbleibende Punkte sind Bau-/Legal-, nicht Design-Fragen). Ein **echter Design-Blocker** (Architektur-Eigenschaft, ohne Design-Änderung nicht reparierbar) bleibt ein **wörtlicher Stopp**, unabhängig von der ADR-Tiefe. Die Substanz-Begründung MUSS benennen, warum jeder Restpunkt Bau oder Legal ist.

---

## 13. Console Operating Rules

### 13.1 COMPACT / NO-REASONING-PATH
Direktes Ergebnis zuerst; keine Schritt-für-Schritt-Begründung des eigenen
Vorgehens. Reasoning nur bei strategischen Lars-only-Entscheidungen.

### 13.2 Console-Autonomie & KB-First
- Fehlende Datei/Info zuerst in der KB suchen; sonst Console-Command der nach
  `~/Downloads` lädt (nie nur `/tmp`).
- Console arbeitet autonom mit minimalen Rückfragen; führt GH push/squash/merge
  selbständig durch für operative Doku/Code.
- NICHT für global/strategische Änderungen — erst an Lars.

## 14. Verify-before-Recommend Gate

Jede Handlungsempfehlung, Eskalation oder Status-Aussage ("X ist registriert / tot / kompromittiert / erledigt / gültig") MUSS auf live-verifizierten Fakten stehen. Vor der Ausgabe jeden tragenden Fakt KLASSIFIZIEREN:

- **(a) LIVE verifiziert** — diesen Lauf gefetcht (DB / Datatracker / GitHub-API / Code / curl).
- **(b) Memory / Doku / früherer Bericht** — UNVERIFIZIERT für diesen Zweck.
- **(c) Abgeleitet / angenommen** — aus HTTP-Status, Plausibilität, Naming, "nicht gefunden".

REGEL: Empfehlungen nur auf (a). Trägt eine Empfehlung (b) oder (c):

- → zuerst read-only verifizieren, DANN empfehlen; ODER
- → explizit markieren "abhängig von ungeprüftem <X> — vor Handlung verifizieren".

ANTI-PATTERNS (real aufgetreten Juni 2026):

- "Status 200/202" als "Key gültig/akzeptiert" → erst Auth-Pfad/DB prüfen (header- vs query-auth).
- "nicht lokal gefunden" als "existiert nicht" → Live-Quelle (Datatracker/Repo) fetchen (#185-Pin-Inversion).
- Memory-Hash/Count als Fakt in eine Empfehlung → gegen Quelle prüfen.
- Sekundärquelle (PDF/Artikel) als Primärquelle behandelt → Original fetchen (Estonia-PDF).

Spezialfälle dieses Gates: §6.4 (Rate-Limit-403 ≠ CI-Fehler), §11.5 (Drift), §12 (External Publish), External-Post-Standard, Spec-Citation-Rule. Gilt symmetrisch für Console UND Chat-Claude.

---

## 15. Web-Deploy (moltrust.ch / Blog)

**→ Autoritatives Runbook: `moltrust-web/docs/website-deploy.md`** (https://github.com/MoltyCel/moltrust-web/blob/main/docs/website-deploy.md). Seit 2026-07-06 lebt das vollständige Deploy-Runbook dort (Single Source of Truth): Green-Path-Autonomie, Site-/Blog-Invarianten, der **generierte-Index-Self-Heal-Contract** (`/blog/index.html` wird per Cron alle 15 min aus den Post-Tags regeneriert — nie index.html deployen), Content-Diff-Gate und STOP-Bedingungen. Dieser §15 ist die server-seitige Kurz-Referenz und *defers* zu jenem File, wo beide sich unterscheiden.

Kanonisches Runbook für jeden Schreibzugriff auf die servierte Website (`moltrust.ch`-Pages + Blog). Ergänzt §11 (Repo-first, Diff-Gate) um die **host-konkreten** Deploy-Fakten; ändert keine §11-Regel.

### 15.1 Host-Mapping (Drift-Falle)

- **Deploy-Host = `moltstack@api.moltrust.ch` = `46.225.175.218` = `moltrust.ch`.** EIN Server, zwei Hostnamen (nginx `server_name moltrust.ch api.moltrust.ch`); `api.` und Apex lösen auf dieselbe IP auf.
- **NICHT `vcone` (`178.104.48.73`)** — andere VM, aber **gleicher geklonter Hostname `ubuntu-4gb-nbg1-1`**. `hostname` allein unterscheidet die Boxen NICHT; `vcone` hat KEIN NOPASSWD, kein `blog-deploy-stage`, serviert die Site nicht. Host-Identität an **IP / `sudo -n -l`-Scope** festmachen, nie am Hostnamen (30.06.26-Fehldiagnose: `vcone` fälschlich für den Web-Host gehalten).
- **Falscher User ≠ falscher Host:** `kerstenkroehl@.218` → `Permission denied (publickey)`, `moltstack@.218` trägt. Bei „Permission denied" zuerst den **User** prüfen, bevor auf „anderer Server" geschlossen wird.

### 15.2 Webroot + NOPASSWD-Scope (aktiv, bestätigt 30.06.26)

- Webroot: `/var/www/html` (Pages), `/var/www/html/blog` (Blog-Posts); nginx `root /var/www/html`.
- Web-Root gehört `root`/`www-data` → Schreiben braucht `sudo` (`moltstack` uid 1000 ≠ `www-data`; direktes scp = `Permission denied`).
- NOPASSWD-Regel **aktiv** (`sudo -n -l` als `moltstack@api.moltrust.ch`, bestätigt 30.06.26 — ersetzt die frühere „NOPASSWD nur vorgeschlagen"-Notiz in `moltrust-web/CLAUDE.md`):
  ```
  /usr/bin/install -m 644 /home/moltstack/blog-deploy-stage/* /var/www/html/*
  /usr/bin/install -m 644 /home/moltstack/blog-deploy-stage/* /var/www/html/blog/*
  ```
  Staging-Quelle ist fix `/home/moltstack/blog-deploy-stage/`; Ziel-Glob deckt Pages (`/var/www/html/*`) und Blog (`/var/www/html/blog/*`).

### 15.3 Ablauf

1. **PR gegen `origin/main` mergen** — §11.1, `post-sha == repo-sha` ist PFLICHT vor jedem Deploy. Kein Live-`install` aus einem ungemergten Branch.
2. `scp <datei> moltstack@api.moltrust.ch:/home/moltstack/blog-deploy-stage/`
3. `ssh moltstack@api.moltrust.ch sudo /usr/bin/install -m 644 /home/moltstack/blog-deploy-stage/<datei> /var/www/html/<datei>` (Blog-Ziel: `/var/www/html/blog/<datei>`).
4. **Live-curl-Probe** gegen `https://moltrust.ch/<datei>` — Inhalt prüfen, nicht nur Status (§14: HTTP 200 ≠ Inhalt korrekt).

- Blog-Pages bevorzugt via `scripts/deploy_page.sh --prebuilt <repo-dir> <slug>` (Staging + install ohne Nav-Doppelinjektion — siehe `moltrust-web/CLAUDE.md`).
- Nur **servierte** Files (§11.5): `*.html`, `*.json`, `*.txt`, `*.xml`, `*.css`, `*.js`. Nie Repo-Meta (`docs/**`, `.github/**`, `CLAUDE.md`, ADRs), kein Full-Repo, kein `git pull` im Web-Root (`/var/www/html/.git` = vestigial).

### 15.4 Diff-Gate VOR jedem `install` (Pflicht)

Live-Datei gegen den **gemergten `origin/main`-Stand** diffen — NICHT gegen lokalen/stale `main` (§11.4):

```
ssh moltstack@api.moltrust.ch "cat /var/www/html/<datei>" | diff - <(git show origin/main:<datei>)
```

- Diff leer → Deploy trägt nur die beabsichtigte PR-Delta. OK.
- Diff zeigt Live-only-Inhalt → echter Server-Drift: erst in den Repo zurückführen (Commit), DANN deployen (§11.5: „Live gefixt" → sofort Repo-Commit). Kein blindes `install` über unversionierten Live-Stand.

**30.06.26-Lehre (Baseline-Falle):** Ein gemeldeter `transparency.html`-„Drift" (Live 18.06. ≠ vermeintlicher Repo-HEAD 14.06.) war **kein** Server-Drift — verglichen wurde versehentlich gegen **stale local `main`** (19 Commits behind `origin/main`). Gegen `origin/main` war Live byte-identisch. Drift-Checks immer gegen `origin/main`, sonst erzeugt stale-local einen Falsch-Alarm.

---

## Changelog

- **2026-07-06 — V1.7**: **§15 defers an das kanonische Deploy-Runbook `moltrust-web/docs/website-deploy.md`** (neu adoptiert; Single Source of Truth). Dorthin gefaltet: §15.1 Host-Pinning-per-IP, exakter NOPASSWD-`install`-Scope, `deploy_page.sh --prebuilt`, §15.4 Content-Diff-Gate gegen `origin/main`, Per-Repo-PR-Mechanik (moltrust-web via gh; moltrust-api via GH_TOKEN/SSH). Neu dokumentiert: der **generierte-Index-Self-Heal-Contract** (Cron `/etc/cron.d/moltrust-blog-index` → `generate_blog_index.py`, */15 als root, regeneriert `blog/index.html` aus Post-Tags — `index.html` nie deployen, repo↔live-Index-Divergenz ist erwartet, kein Drift). GSC-Sitemap-Re-Submit = nicht-blockierender Report-Eintrag, kein Green-Path-Schritt. §15-Adoptionsnotiz korrigierte Ref §6.2→§15. Rein additiv/Pointer; keine bestehende §15-Regel entfernt.

- **2026-06-30 — V1.6**: **§15 Web-Deploy (moltrust.ch / Blog)** — kanonisches Deploy-Runbook für die servierte Website. §15.1 Host-Mapping-Drift-Falle (`api.moltrust.ch` = `46.225.175.218` = `moltrust.ch`, EIN Server; `vcone` `178.104.48.73` ist eine andere VM mit **gleichem geklonten Hostname** `ubuntu-4gb-nbg1-1` — Host an IP/`sudo -n -l` festmachen, nie am Hostnamen; „Permission denied" → erst User prüfen). §15.2 Webroot `/var/www/html` (+ `/blog`) + **aktiver** NOPASSWD-`install`-Scope (bestätigt 30.06.26 — korrigiert die stale „nur vorgeschlagen"-Notiz). §15.3 4-Schritt-Ablauf (PR-Merge §11.1 → scp-Stage → install → Live-curl-Probe). §15.4 Diff-Gate gegen `origin/main` (nicht stale local). 30.06.26-Lehre: gemeldeter transparency.html-„Drift" war ein stale-local-main-Vergleichsartefakt, kein Server-Drift. Rein additiv; keine bestehende Regel geändert.
- **2026-06-27 — V1.5**: **§14 Verify-before-Recommend Gate** — generalisiert das Verifikations-Gate über External-Posts (§12) / Specs hinaus auf JEDE Empfehlung, Eskalation oder Status-Aussage. Tragende Fakten klassifizieren: (a) live verifiziert / (b) Memory/Doku / (c) abgeleitet — Empfehlungen nur auf (a), sonst erst read-only verifizieren oder explizit als ungeprüft markieren. Anti-Patterns (Juni 2026): "Status 200/202" ≠ Key gültig, "nicht gefunden" ≠ existiert nicht, Memory/PDF ≠ Primärquelle. Rein additiv; keine bestehende Regel geändert.
- **2026-06-17 — V1.4**: Drei additive Drift-Guard-Sektionen. **§0.1 Identity Kontext** — MoltyCel = Lars' GitHub-Identität (lars@moltrust.ch), kein separater Bot; autonomes Bot-Posting seit 12.04.26 deaktiviert; Legal-Identität (kersten.kroehl@cryptokri.ch) getrennt. **§6.4 GitHub-API Rate-Limit-Hygiene** — unauth 60/h pro IP shared session; kein Polling; PAT (5000/h) oder Web-UI; HTTP 403 = leere Quota ≠ "CI-Fehler", erst `gh api rate_limit` checken. **§11.5 Anti-Drift-Guards** — (a) vestigialer `/var/www/html/.git`-Checkout = kein Vorfall, (b) Web-Root-Sync nur servierte Files, nie Repo-Meta (Info-Leak), (c) Live-Fix an repo-verwalteten Files → sofort Repo-Commit (PR #159-Lehre), Server-Infra (nginx/systemd/cron) bleibt §11-out-of-scope mit Audit-Eintrag. Rein additiv; keine bestehende Regel geändert.
- **2026-06-04 — V1.3.1 (Patch)**: **§12.4** ergänzt — Konsens-Kriterium des Multi-Modell-Review-Gates präzisiert. Wörtliches einstimmiges FREIGEBEN bleibt für abgrenzbare technische Mechanismen (ADR-D3); für tiefe parameter-reiche Governance-Designs (CEP) ist es strukturell unerreichbar (immer feinere Parameter-Stufe + weiteres Legal-Doc). Erreichbares + ausreichendes SUBSTANZ-Kriterium: beide Linsen „keine Design-Blocker" + Fundamentalkonflikte gelöst + Rest strukturell Implementation-Contract (Bau) / Legal-Process (extern), nicht Design → ACCEPTED-Flip mit dokumentierter Substanz-Begründung legitim. Verhindert Infinite-Review-Loop ohne Gate-Aufweichung; echter Design-Blocker bleibt wörtlicher Stopp. Präzedenz: CEP-ADR-ACCEPTED #143 + ADR-D3 #107. Empirisch belegt CEP v8 (beide Linsen „keine Design-Blocker", kein FREIGEBEN-Label).
- **2026-05-28 — V1.3**: Sektion 12 (External Publish Review) ergänzt — macht den 3-Modell-Review (`gpt-5` + `gemini-3.1-pro-preview` + `sonar-pro` → Synthese via Claude über `~/moltstack/agents/ai_review.py`) zur Vorbedingung vor jeder Aktion, die ein Artefakt ausserhalb der MolTrust-Repos publik macht: (a) `npm publish` öffentlich, (b) PRs gegen Non-MolTrust-Repos, (c) Outreach-Mails an externe Empfänger. Lessons-Reaktion auf den moltrust-openclaw-v2-Sprint (Mai 2026): lokale Tests + `--dry-run` sind notwendig, aber nicht hinreichend. Geltungsbereich a/b/c explizit; interne Channels und Server-Deploys (§11) ausgenommen. Briefing-Template-Pflicht (Master in `~/moltstack/reviews/_templates/`), Blocker-/Major-Findings stoppen die Aktion.
- **2026-05-19 — V1.2.1 (Patch)**: **§1.2–1.7** interne Pfade durchgängig von `~/moltstack/docs|audits/…` auf **repo-relativ** (`MoltyCel/moltrust-api`) korrigiert — gesamtes Kapitel §1 hat jetzt eine **konsistente** Pfadkonvention (kein Halb-Drift, den §11 verhindern soll). Selbstverortungs-Drift: WORKFLOW.md lebt in moltrust-api; das Server-Arbeitsverzeichnis `~/moltstack` ist verifiziert ein Checkout ebendieses Repos, kein eigenes Repo. Reine Pfad-Textkorrektur, keine §11-/Regeländerung.
- **2026-05-19 — V1.2**: Sektion 11 (Repo-as-Source-of-Truth & Deploy-Disziplin) ergänzt — schliesst die **drei real passierten** moltrust-web-Reconcile-Drift-Ursachen (Server-Datei ohne Repo-Commit / Doku-Iteration nur im Chat / Zwei-Console-Worktree-Kollision) in **4 schlanken Regeln**: 11.1 Repo-first für versionierte Dateien (+ `post-sha==repo-sha`), 11.2 Iteration=Commit, 11.3 Worktree-Isolation + serieller Server-Zugriff, 11.4 Session-Start-Frischecheck — Schlüsselbegriffe je inline definiert. **Ehrliche Bereichsgrenze** im Intro: §11 regiert repo-verwaltete Dateien; Server-Infra (nginx/systemd/cron) bewusst out-of-scope (deklarierte Grenze, kein Schlupfloch). Weitere Härtung (Infra-Repo-Überführung, Build-/Supply-Chain-Integrität SLSA/NIST-SSDF, WORM-Audit-Repo, atomarer Lock, formaler Notfallpfad) als entkoppeltes `docs/BACKLOG.md`-Item festgehalten — kein §11-Blocker. Zusätzlich Selbstverortungs-Korrektur (Schlusszeile → kanonisches `MoltyCel/moltrust-api`). Durchlief 5 Entwurfs-Iterationen + 2 §2.3-Cross-Review-Runden (GPT-4o+Gemini+Perplexity): „GRUNDLEGEND ÜBERDENKEN" → „ÜBERARBEITEN" → Kernfragen (3 Ursachen geschlossen, Bereichsgrenze ehrlich) zweireviewer-bestätigt, Begriffs-Präzision final eingearbeitet.
- **2026-05-13 — V1.1**: Sektion 6.2 (Secret-Leak-Detected) substantiell erweitert mit Multi-Storage-Audit-Checkliste (8 Speicherorte) und Post-Rotation-Verifikations-Step. Lesson 13.05.26 dokumentiert (MoltyCel-PAT-Rotation übersah `moltycelbot/secrets/GITHUB_PAT` → 24h 401-Storm). Sektion 10 Bootstrap-Items: completed-Markers für BACKLOG.md (V1.1+V1.2 fertig), Telegram-Token-Rotation, Memory #25 Korrektur. Bootstrap-Hinweis-Paragraph aus V1 bleibt erhalten (ungewollt im Initial-V1.1-Edit entfernt, via Follow-up-Commit restored).
- **2026-05-12 — V1**: Initial. Definiert State-of-Truth Architektur, Pre/In/Post-Sprint-Disziplinen, Periodic Routines, Notfall-Routinen, 10 verbotene Anti-Patterns. Bootstrap-Hinweis in Sektion 10: Bootstrap-Items brauchen keine eigene Spec.

---

**Ende WORKFLOW.md V1.6. Dies ist ein lebendiges Dokument. Updates via PR auf das kanonische Repo `MoltyCel/moltrust-api` (Pfad `docs/WORKFLOW.md`) mit Changelog-Eintrag. Hinweis: „moltstack" bezeichnet anderswo im Dokument die Plattform/den Server-Arbeitsbereich (`~/moltstack/…`), NICHT den Repo-Ort dieses Dokuments.**
