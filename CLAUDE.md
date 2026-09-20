# CLAUDE.md — moltrust-api

Repo-spezifische Instruktionen. Voller operativer Rahmen: `docs/WORKFLOW.md` (in **diesem** Repo). Backlog: `docs/BACKLOG.md`.

## Human/agent division of labour (binding)

**Default: die Console ermittelt und führt alles aus, was sie selbst ermitteln oder ausführen kann.** Lars wird nicht gebeten, etwas zu prüfen, zu recherchieren oder zu entscheiden, das die Console selbst feststellen kann (Deploy-Stand, ob etwas schon gebaut/läuft/geroutet ist, welcher PR gemergt ist, berechenbare Werte, …). „Verifizier mir mal X" ist ein Prozessfehler, außer X ist **nur** von Lars beobachtbar.

Lars bekommt genau **zwei** Arten von Handoff, sonst nichts:

1. **HUMAN-GATED: PRIVILEGED DEPLOY** — Befehle, die die Console unter ihrem nicht-interaktiven sudo nicht ausführen kann (systemd-Unit-Install, nginx-Edits, Service-Restarts außerhalb des erlaubten Sets). Als **ein** beschrifteter Copy-paste-Block.
2. **HUMAN-GATED: WALLET/KEYS** — Private-Key-Generierung, Wallet-Funding, Signieren/Broadcasten von On-Chain-Transaktionen. Per Design nie an die Console delegiert.

### Ausnahme: Testwallet (Freigabe Lars, 19.09.2026)

Genau **eine** Adresse ist von Punkt 2 ausgenommen:

```
0xd8f5bB747f7459BF3e1cc1aD041E2cA57B946C38   (= BASE_ANCHOR_KEY)
```

Die Console signiert und broadcastet daraus **selbständig**, ohne Rückfrage.

- **Zweck:** ausschließlich Funnel-/x402-Tests und Bounty-Auszahlungen.
- **Deckel:** kumuliert **16 USDC + 0,001 ETH** über alle Läufe zusammen, nicht
  pro Lauf (angehoben von 11 USDC, Freigabe Lars 20.09.2026). Deckel erreicht =
  Ausnahme verbraucht, Weiterarbeit nur nach neuer Freigabe. Eine drohende
  Überschreitung ist einer der zwei Fälle, in denen trotzdem gefragt wird (der
  andere: Bruch mit Geldverlust-Risiko).
- **Protokollpflicht:** jede Transaktion mit **Hash, Betrag und Zweck** im
  Report **und** per Telegram. Eine Tx ohne beides gilt als Regelbruch, auch
  wenn sie erfolgreich war.
### Zweite Ausnahme: taskmarket-Escrow (Freigabe Lars, 20.09.2026)

```
0xa175d51bfe0170738720DAAEc627A84d44dc9Eb9   (taskmarket-Escrow-Wallet)
```

Die Adresse gehört dem TaskMarket-CLI; der Schlüssel liegt verschlüsselt in
`~/.taskmarket/keystore.json` und wird über den Key-Server unter
`api.taskmarket.dev` mit dem dort hinterlegten Token benutzt. Zugehörige
ERC-8004-Identität: **agentId 95128**.

- **Erlaubt ohne Rückfrage:** Auszahlungen an Worker **bis zur Höhe des selbst
  eingezahlten Escrows**. Das ist keine neue Ausgabe, sondern die Freigabe von
  Geld, das für genau diesen Zweck hinterlegt wurde.
- **Nicht erlaubt:** Abhebungen über die Einzahlungshöhe hinaus, Transfers an
  eigene Adressen, alles andere.
- **Protokollpflicht wie bei 0xd8f5:** Hash, Betrag und Zweck im Report **und**
  per Telegram. Zusätzlich die Pool-Zuordnung in `pool_spend`.

- **Nicht ausgenommen:** jede andere Adresse, Testzwecke eingeschlossen — vor
  allem `BASE_WALLET_KEY` / `BASE_ADDR` (`0x3802…`, zugleich der x402-`payTo`).
  Für die gilt „kein Signieren" unverändert.
- **Auch hier human-gated:** Private-Key-Generierung und Wallet-Funding. Die
  Ausnahme deckt nur das Ausgeben vorhandenen Guthabens.

**Diese Adresse hat eine Zweitfunktion.** `anchor_publication.py` verankert damit
Publikationen und nennt sie dort „a DEDICATED wallet … never the [productive
one]". Sie läuft nicht im Cron und hat bisher genau 2 Transaktionen gesendet, der
Konflikt ist also klein — aber wer sie leerfährt, nimmt dem Publication-Anchoring
das Gas. Der Deckel ist auch dafür da.

Stand bei Einrichtung (live gelesen 19.09.2026): 10,85 USDC, 0,0000993 ETH. Das
ETH ist der knappe Posten und reicht absehbar nicht für K4 plus zwei Bounties.

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
- [ ] Agent-Card (`/.well-known/agent-card.json`) — neuer Skill / Capability eingetragen, A2A v1.0-konform. **Quelle ist `.well-known/agent-card.json` in diesem Repo, nie die Datei im Webroot.** Ändern heißt: Repo-Datei bearbeiten → `python -m scripts.resign_agent_card --in .well-known/agent-card.json --in-place` (signiert neu und weigert sich, eine unverifizierbare Card zu schreiben) → committen → deployen. Ein Handgriff direkt in `/var/www/html/.well-known/agent-card.json` macht die Signatur ungültig; am 20.09.2026 lief die Card so einen Tag lang mit einer Signatur, die zu ihrem Rumpf nicht mehr passte.
- [ ] **MCP-Katalog / Smithery:** Neues MCP-Tool im Server? → Smithery-Listing `@moltrust/moltrust-mcp-server` **re-publishen** (Server exponiert sonst mehr Tools als gelistet). Bei Skill-Änderung `EXPECTED_AGENT_CARD_SKILLS` in `agents/watchdog.py` bumpen. Erzwungen durch täglichen Abgleich: `watchdog.py::check_discovery_drift` (MCP↔Smithery + Card) → Telegram bei Drift.
- [ ] Falls authentifizierte Erweiterung: Extended Agent Card (`/extendedAgentCard`) gepflegt
- [ ] OpenAPI-Contract (`/docs`-Spec) — Pfad, Schema, Beispiele konsistent
- [ ] `api.moltrust.ch/llms.txt` — Endpoint-Referenz für Agent-Konsumenten aktualisiert
- [ ] Weitere `.well-known/`-Surfaces (`agent-registration.json` ERC-8004, `jwks.json`, …) falls Auswirkung — konsistent halten
- [ ] **Cross-repo:** Falls aus dem Endpoint eine HTML-Seite unter `moltrust.ch` entsteht (Marketing-Landing, Dev-Docs, Blog), Discovery-Schritte parallel im `MoltyCel/moltrust-web` Repo nachziehen (dort: `sitemap.xml` + GSC-Re-Submit, siehe `CLAUDE.md` dort).

**Begründung:** „Entdeckbarkeit = Definition of Done" — Lesson aus GROUP-5-Nachzug Mai 2026 (`MoltyCel/moltrust-web`): 5 Seiten waren live, aber wochenlang nicht in Sitemap → für Crawler unsichtbar trotz vorhandenem Inhalt. Analog für die API: ein Endpoint, der nicht in Agent-Card / OpenAPI / `llms.txt` referenziert ist, wird von Verbraucher-Agents nicht gefunden — auch bei HTTP-200.

Volltext + Begriffsdefinitionen: `docs/WORKFLOW.md` §11.

## Python-Package-Standard (pyproject.toml)

Jedes MolTrust-Python-Package (hatchling) MUSS im `[project]` **`license = { text = "MIT" }` UND `license-files = []`** setzen — das liefert klassische, versionsunabhängig von PyPI/twine akzeptierte License-Metadaten (`License: MIT`, **kein** `License-Expression`/`License-File`); hatchlings Metadata-2.4-Defaults (`License-Expression` aus `license = "MIT"` + auto-`License-File`) haben PyPI-Uploads abgelehnt (u. a. 400 „License-Expression ↔ License-Klassifikator", und alte `twine` ↔ Metadata 2.4). Verifiziert an moltrust-crewai/-langchain 0.1.1.


---
## Console Operating Rules

### COMPACT / NO-REASONING-PATH
Direktes Ergebnis zuerst — keine Schritt-für-Schritt-Begründung der eigenen
Vorgehensweise. Reasoning nur bei strategischen Lars-only-Entscheidungen.

**DEFAULT NO-EXPLAIN:** explanations, rationale, background ONLY on explicit `Explain!`. Otherwise deliver result/answer/action directly — as short as possible, as long as strictly necessary. No reasoning path, no meta-sentences, no volunteered justification. Global, all output.

### CONSOLE-AUTONOMIE & KB-FIRST
- Fehlende Datei/Info: zuerst in der KB suchen; sonst Console-Command der nach
  `~/Downloads` lädt (nie nur `/tmp`).
- Console arbeitet autonom mit minimalen Rückfragen; führt GH push/squash/merge
  selbständig durch für **operative** Doku/Code.
- NICHT für global/strategische Änderungen (→ erst Lars).
- Mechanische Arbeitsteilung (was Eigenarbeit ist, was Human-Gated): siehe **§ Human/agent division of labour (binding)** oben — nur *privileged deploy* und *wallet/keys* gehen an Lars.

## Fremde Repos: erst nachsehen, dann einreichen (HART)

Vor **jedem** PR, Issue oder Kommentar in einem fremden Repo:

```bash
gh pr list   --repo <owner>/<repo> --author MoltyCel --state all
gh issue list --repo <owner>/<repo> --author MoltyCel --state all
```

Zwei Konsolen arbeiten parallel und sehen einander nicht. Am 20.09.2026 sind so
zwei PRs auf dieselbe Datei entstanden (`aeoess/agent-governance-vocabulary`
#170 und #171, drei Minuten auseinander, inhaltlich widersprüchlich), und am
selben Tag wäre beinahe ein `awesome-erc8004`-Eintrag ein zweites Mal
eingereicht worden — MolTrust stand dort seit März, aus zwei gemergten PRs.

Der Duplikat-PR wird geschlossen, nicht der ältere: wer zuerst eingereicht hat,
hat die Review-Zeit des Maintainers schon gebunden. Schließen mit Verweis auf
den anderen und einer Zeile, was er besser macht.

**Gleiches gilt vor dem Anlegen eines Listings.** Erst prüfen, ob wir schon
gelistet sind — `moltrust-vet` galt als nicht gelistet, weil die Domain falsch
geraten war (`clawhub.dev` statt `clawhub.ai`).

## Anti-Drift-Quickref

Vor Eskalations-Berichten Cross-Check gegen WORKFLOW.md §11.5:
- Server `/var/www/html/.git`-Anomaly = kein Vorfall
- Web-Root-Sync NIE komplettes main-Repo (Info-Leak)
- "Live gefixt" → sofort Repo-Commit nachziehen

GitHub-API: unauth 60/h shared session — niemals pollen, siehe WORKFLOW.md §6.4.

## Web-Deploy Quickref (→ WORKFLOW.md §15)

**Autoritatives Volltext-Runbook: `moltrust-web/docs/website-deploy.md`** (https://github.com/MoltyCel/moltrust-web/blob/main/docs/website-deploy.md) — dorthin *defers* diese Quickref und WORKFLOW.md §15. Merke insb.: `/blog/index.html` ist Cron-generiert (§3 dort) → nie deployen.

Servierte Website (`moltrust.ch`): Host **`moltstack@api.moltrust.ch`** (= `46.225.175.218` = `moltrust.ch`, EIN Server) — **nicht** `vcone` (`178.104.48.73`, gleicher geklonter Hostname `ubuntu-4gb-nbg1-1`, kein NOPASSWD, serviert die Site nicht; Host an IP/`sudo -n -l` festmachen, „Permission denied" → erst User prüfen). Webroot `/var/www/html` (+ `/blog`). Ablauf: PR → `origin/main` mergen (§11.1, `post-sha==repo-sha`) → `scp … :/home/moltstack/blog-deploy-stage/` → `sudo /usr/bin/install -m 644 …/blog-deploy-stage/<f> /var/www/html/<f>` (NOPASSWD aktiv, bestätigt 30.06.26) → Live-curl-Probe. VOR `install`: Live gegen `origin/main` diffen (nie stale local). Volltext: `docs/WORKFLOW.md` §15.

## Verify-before-Recommend (HART — WORKFLOW.md §14)

Vor jeder Empfehlung/Eskalation/Status-Aussage: tragende Fakten klassifizieren — (a) live gefetcht, (b) Memory/Doku, (c) abgeleitet. Nur (a) trägt Empfehlungen. (b)/(c) → erst read-only verifizieren oder explizit als ungeprüft markieren.
"Status 200" ≠ gültig · "nicht gefunden" ≠ existiert nicht · Memory/PDF ≠ Primärquelle.

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
