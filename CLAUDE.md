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

### Dritter Topf: Defekt-Bounty (Freigabe Lars, 21.09.2026)

**10 USDC pro Monat, Oktober bis Dezember 2026, eigener Deckel 30 USDC.**
Getrennt vom 21er-Deckel — der ist mit Rest 0,75 USDC **für Bounties
geschlossen**. Die beiden Töpfe werden nie gegeneinander verrechnet, auch nicht
wenn dieselbe Wallet zahlt.

- **Auszahlung aus `0xd8f5`**, wie die anderen Testwallet-Ausgaben. Adressen
  **nur programmatisch** — aus der Meldung gelesen, nie abgetippt. Dry-run
  zuerst, Regeln wie bei der Anerkennungszahlung.
- **Staffel 0,50–5 USDC je reproduzierbarem Fehler**, nach Schwere. Betroffen
  sind Doku, API und der Krypto-Pfad.
- **Ohne eigene Reproduktion kein Bonus.** Die Console stellt jeden Fund selbst
  nach, bevor gezahlt wird. Ein Bericht, der sich nicht nachstellen lässt, wird
  beantwortet, nicht bezahlt.
- **`pool_spend`-Zweck: `defect-bounty YYYY-MM`.** Sofort gebucht, im selben
  Arbeitsschritt (siehe Kontenabgleich oben).
- **Monatsrest verfällt.** Kein Übertrag in den Folgemonat. Wer im Oktober 3
  USDC ausschüttet, hat im November wieder 10, nicht 17.
- **Monatsreport:** Funde, Zahlungen, behobene PRs. Ein Fund ohne PR-Verweis ist
  ein offener Posten, kein erledigter.
- **Eingang 21.09.2026: 10 USDC auf `0xd8f5`** (Lars). Kontostand danach
  **10,750015 USDC**, live gelesen per `eth_call` — die 0,750015 des
  geschlossenen Topfs plus die 10 des Oktober-Budgets. Die Aufstockung hebt
  keinen Deckel; `scripts/wallet_reconcile.py` führt beide getrennt
  (`BUDGETS`) und ordnet jeden Abfluss über den `pool_spend`-Zweck zu.
  **Zweckpräfix ist exakt `defect-bounty`** — die deutschen Zwecke der
  Bounty-Runde 1 (`Defekt-Bonus …`) gehören in den alten Topf und dürfen dort
  nicht hineinrutschen.
- **Meldeweg und Regeln sind bis zur Freigabe Entwurf** — Seite
  `moltrust.ch/defects`, Abschnitt in `security.txt`, Hinweis in
  `developers.html`. Nichts davon geht ohne Lars' Freigabe live.

## Kontenabgleich (HART, ab 21.09.2026)

**Jede Transaktion aus einer verwalteten Wallet wird sofort in `pool_spend`
geschrieben, mit Zweck.** Sofort heißt im selben Arbeitsschritt, nicht am
Tagesende — zwei Konsolen arbeiten diese Wallets und sehen einander nicht.

Am 21.09.2026 sah ein Abfluss von 5 USDC stundenlang wie eine nicht zuzuordnende
Ausgabe aus, stark genug, um die Arbeit anzuhalten. Er stand die ganze Zeit in
`pool_spend`, gebucht von der anderen Session. Niemand hatte die beiden Seiten
verglichen.

- **Buchen ist keine Erinnerungssache.** `scripts/x402_self_payment.py` schreibt
  seine Zeile selbst. Wer ein neues Skript baut, das Geld bewegt, baut die
  Buchung mit ein.
- **Sonntags 06:30** läuft `scripts/wallet_reconcile.py`: Rekonstruktion aus der
  Kette gegen `pool_spend`. **Differenz = Alarm.**
- **Nur eine Richtung ist ein Vorfall.** Kette vorn = Ausgabe ohne Buchung, exit
  1. Buch vorn = Zahlung, die der Explorer noch nicht indexiert hat, löst sich
  von selbst.
- **Umbuchungen zwischen eigenen Wallets sind keine Ausgabe**, zählen aber gegen
  den Deckel der abgebenden Wallet.
- **`tx_hash` darf ein Relay-Hash sein**, wenn die Auszahlung über einen Vertrag
  lief. Der Abgleich vergleicht deshalb Beträge je Wallet, nicht nur Hashes.
- **Niemals einen Hash erfinden.** Eine erfundene Zeile in einer Abgleichstabelle
  ist schlimmer als eine fehlende.

## Vollständigkeit beim Lesen (HART, ab 21.09.2026)

**Jede Leseoperation gegen eine paginierte Quelle weist nach, dass sie alles
gesehen hat, oder sie liefert kein Ergebnis.** Eine Teilmenge, die als
Gesamtmenge gemeldet wird, ist eine Falschaussage, auch wenn jeder einzelne Wert
darin stimmt.

Am 20./21.09.2026 dreimal passiert:

- Der Bazaar-Katalog von CDP wurde mit 100 von 14 950 Zeilen gelesen und das
  Ergebnis als „nicht gelistet" gemeldet. Der `payTo`-Filter wird von der API
  angenommen und ignoriert.
- Derselbe Katalog wurde danach vollständig geholt, aber über `r.resource.url`
  ausgewertet. Bei CDP ist `resource` ein String; alle 15 141 Zeilen ergaben
  `undefined`, und das Ergebnis lautete wieder „nicht gelistet".
- `wallet_reconcile.py` las eine Blockscout-Seite mit 50 Transfers und nannte das
  die Kette. Nach 71 Auszahlungen fehlten der Kettenseite 6,45 USDC, was am
  Sonntag einen Alarm ausgelöst hätte. Entschieden hat es der Kontostand:
  0,750015 tatsächlich gegen 0,75 gebucht.

Was ein Leser erfüllen muss:

- **Bis zum Ende blättern.** Solange `next_page_params`, `has_more` oder ein
  Cursor gesetzt ist, wird weitergelesen.
- **Der Seitendeckel wirft.** Ein Lauf mit Obergrenze endet beim Erreichen der
  Grenze mit einem Fehler; ein `break` mit Teilergebnis ist die Bauform, die die
  drei Fälle oben erzeugt hat. In `wallet_reconcile.py` liegt die Grenze bei 40
  Seiten.
- **Gesamtzahl gegenprüfen, wo die Quelle eine nennt.** Gelesene Zeilen gegen
  `total` beziehungsweise `COUNT(*)`, Abweichung ist ein Fehler.
- **Lesbarkeit gehört zur Vollständigkeit.** 15 141 Zeilen geholt und 0 davon
  ausgewertet heißt, dass der Leser defekt ist. Wer scannt und nichts versteht,
  meldet Exit 2 statt eines Nullbefunds.
- **Bis ans Ende geblättert heißt nicht alles gesehen.** Am 21.09.2026 endete
  Blockscouts Index für `0xd8f5` bei Block 51 606 562, während die Kette 500
  Blöcke weiter war, und lieferte trotzdem `next_page_params: null`. 16 von 68
  Auszahlungen und eine Aufstockung über 10 USDC fehlten, ohne ein Feld, das
  das gesagt hätte. Wo eine unabhängige Kennzahl existiert, wird gegen sie
  geprüft: bei Wallets ist es der Kontostand, den der Node aus dem State
  beantwortet statt aus einem Index. Zufluss minus Abfluss muss ihn treffen;
  trifft er ihn nicht, ist die Sicht veraltet und keine daraus abgeleitete Zahl
  darf gemeldet werden.
- **Ohne Nachweis kein Ergebnis.** Ein Leser, der seine Vollständigkeit nicht
  belegen kann, gibt einen Fehler zurück und keine Zahl.

Gilt für Explorer (Blockscout, Basescan), CDP-Endpunkte, fremde Kataloge,
taskmarket-Listings und jede SQL-Abfrage mit `LIMIT`.

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
- [ ] **MCP-Katalog / Smithery:** Neues MCP-Tool im Server? → Smithery-Listing `@moltrust/moltrust-mcp-server` **re-publishen** (Server exponiert sonst mehr Tools als gelistet). Bei Skill-Änderung `EXPECTED_AGENT_CARD_SKILLS` in `agents/watchdog.py` bumpen. Erzwungen durch täglichen Abgleich: `watchdog.py::check_discovery_drift` (MCP↔Smithery + Card) → Telegram bei Drift. **Smithery und Glama messen Verschiedenes:** Smithery listet den Remote am Origin (53 Tools), Glama indexiert das GitHub-Repo, also das Paket (48). Die Differenz sind die fünf `moltproof_*`-Tools, die `services/mcp_http.py` nur am gehosteten Endpunkt hinzufügt. Kein Drift — wer die beiden Zahlen gegeneinander hält, alarmiert auf eine Architekturentscheidung.
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

## Inaktive DIDs: Listen ist Default, Revoke braucht Scharfschaltung (ab 21.09.2026)

`scripts/revoke_inactive.py` läuft **sonntags 05:00 UTC im Cron und listet nur**.
Die Kandidatenliste (DID, Plattform, zuletzt gesehen, fällig seit) geht an den
**STATS**-Kanal — eine Zahl, auf die niemand reagieren muss, bis jemand entscheidet.

Scharf läuft es **nur** mit beidem:

```
REVOKE_INACTIVE_ARMED=1 python3 scripts/revoke_inactive.py --apply
```

Fehlt das Flag, verweigert `--apply` den Dienst und meldet das an ALERTS. Das ist
Absicht: ein Schalter, der nur im Argument lebt, ist eine editierte Crontab-Zeile
vom versehentlichen Feuern entfernt.

**Kriterium:** nie ein authentifizierter Aufruf **und** älter als 90 Tage **und**
registriert nach dem Telemetrie-Stichtag 2026-09-14 — vor diesem Datum heißt
„keine Usage-Zeile" unmessbar, nicht inaktiv. Ein Agent, der einmal aufgerufen
hat und seitdem schweigt, ist angekommen und fällt nicht darunter; das ist eine
Retention-Frage, keine Zählfrage.

`agent_type='system'` ist ausgenommen — unsere fünf Service-Agents rufen sich
nicht selbst authentifiziert auf.

**Erledigt 21.09.2026:** `moltrust-vet` (`did:moltrust:157224190be24072`,
platform `clawhub`) trug `agent_type='external'`, obwohl es unser eigener, auf
ClawHub veröffentlichter Skill ist — seine DID steht als `author` im öffentlichen
Manifest. Umklassifiziert auf `agent_type='system'`, damit greift die
System-Ausnahme. Folge für die Organic-Zählung: **57 → 56** (`is_organic` gibt
für `agent_type='system'` False zurück). Eine Zeile, eine Registrierung weniger
im Ziel — richtig so, es war nie eine fremde. Protokoll: `docs/infra-notes.md`.

Revoke ist umkehrbar (`revoked_at`, `revocation_reason`), das Scharfschalten
trotzdem eine Lars-Entscheidung.

## 90-Tage-Ziel: Zählregel  (ab 21.09.2026)

**Aktiviert = registriert + mindestens ein authentifizierter Aufruf auf einen
Endpoint, den kein Task-Text genannt hat.** Nur diese Zahl zählt aufs Ziel.

Festgelegt nach dem Abgleich der taskmarket-Runde 1 (`~/Downloads/taskmarket-abgleich.md`,
114 DIDs). Jedes schwächere Kriterium fiel an den Daten durch:

| Kriterium | Ergebnis | warum es nichts belegt |
|---|---:|---|
| registriert | 114 | der Task-Text schreibt `platform=taskmarket` wörtlich vor |
| öffentlicher Call | 111 | *„Registration is free and needs no API key"* — so war die Aufgabe gestellt |
| API-Key gebunden | 68 | Schritt 2 der verify-Aufgabe; **54 haben den Key nie benutzt** |
| authentifizierter Call | 14 | 12 davon riefen nur den einen Endpoint auf, den die Aufgabe nannte |
| **außerhalb des Task-Skripts** | **2** | die Einzigen, die aus eigenem Antrieb weitergesucht haben |

Geskriptete Endpoints (aus den Task-Texten, nicht geschätzt): `/identity/verify/`,
`/skill/trust-score/`, `/identity/erc8004/register`. Die Liste steht als
`SCRIPTED_ENDPOINTS` in `agents/proof_post.py` und wächst mit jeder künftigen Bounty.

**Die Zahlen kommen aus `app/sql/taskmarket_cohort.sql`, aus keiner zweiten
Abfrage.** Blog, Weekly Proof und Telegram ziehen dieselbe Datei, damit nicht
zwei Reports unter demselben Wort zwei Fragen beantworten. Genau das war der
Widerspruch „112 DIDs / 99 ohne Call" gegen „114 DIDs / 100 mit Call": die 112
waren der Stand um 13:42 und zählten authentifizierte Calls, die 114 der Stand um
15:03 mit öffentlichen trust-score-Abrufen. Beide reproduzieren sich aus der
Datei, wenn man den Cutoff verschiebt. Falsch war das Etikett der 99 — „nie eine
Anfrage gestellt" trifft auf **3** zu. Die Datei prüft am Ende mit, ob
`request_log` die Lebensdauer der Kohorte überhaupt abdeckt; steht dort `f`, ist
jede Null-Zahl darüber ein Artefakt der Aufbewahrungsfrist.

**Bounty-Kohorte bleibt getrennt ausgewiesen**, auch wenn ein Agent daraus
aktiviert — sonst kauft sich das Ziel selbst.

## Social-Posting: kein Füller-Zweittweet (ab 21.09.2026)

Die Regel heißt **kein Füller-Zweittweet**, nicht „genau ein Tweet". Ein zweiter
Tweet ist erlaubt, wenn er etwas trägt, das im ersten schadet:

- **Erlaubt:** der Link als Reply. X drosselt die Reichweite eines Posts mit
  Auslink, und die URL kostet 42 der 280 Zeichen im Hook. Der Hook entscheidet,
  ob die Timeline den Post zeigt; der Link gehört in die Antwort darunter
  (so gebaut in #401).
- **Verboten:** ein zweiter Tweet, der nur wiederholt, ankündigt oder auffüllt.
  Der alte Herald-Pfad produzierte genau das („Check it: <link>") — die
  Ist-Aufnahme vom 20.09. maß für solche Zweittweets 0–15 Impressionen.

Prüffrage vor jedem Thread-Teil: Trägt dieser Tweet einen Inhalt, den der
vorherige nicht tragen kann, ohne selbst schlechter zu werden? Nein → streichen.

Durchgesetzt wird das über `agents/voice_gate.py`, Gate 2 (e): genau ein Link im
Thread, und der steht im letzten Teil. Ein Hook, der die URL zurückschmuggelt,
fällt durch den Scan statt rauszugehen.

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
