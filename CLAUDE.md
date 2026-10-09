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
- **Eingang 21.09.2026: 10 USDC auf `0xd8f5`** (Lars), Tx
  `0xfc76a125aec906a5fc855f90dbf9ffe7e17306e214032939a2e73638ac6bceb2`,
  Block **51607047**, von `0x825c87f3…` — gegen die Kette verifiziert, nicht
  aus dem Explorer. Kontostand danach **10,750015 USDC**, live per `eth_call`
  gelesen: die 0,750015 des geschlossenen Topfs plus die 10 des
  Oktober-Budgets. Die Aufstockung hebt
  keinen Deckel; `scripts/wallet_reconcile.py` führt beide getrennt
  (`BUDGETS`) und ordnet jeden Abfluss über den `pool_spend`-Zweck zu.
  **Zweckpräfix ist exakt `defect-bounty`** — die deutschen Zwecke der
  Bounty-Runde 1 (`Defekt-Bonus …`) gehören in den alten Topf und dürfen dort
  nicht hineinrutschen.
- **Meldeweg und Regeln sind bis zur Freigabe Entwurf** — Seite
  `moltrust.ch/defects`, Abschnitt in `security.txt`, Hinweis in
  `developers.html`. Nichts davon geht ohne Lars' Freigabe live.

### Releases: der Tag ist die Freigabe, das Veröffentlichen ist Eigenarbeit

**Ein Tag wird nur nach Lars' Go gesetzt.** Er benennt den Commit, und mit ihm
geht das Paket nach draußen — PyPI lässt eine Version zurückziehen, aber nicht
ersetzen.

**Den Publish-Workflow startet die Console selbst.** Der PAT
`moltycel-console-2026-09` hat seit dem 22.09.2026, 19:10 Uhr
`Actions: Read and write`, für `moltrust-mcp-server` wie für `moltrust-api`:

```bash
curl -X POST -H "Authorization: Bearer $MOLTYCEL_GH_TOKEN" \
  https://api.github.com/repos/MoltyCel/moltrust-mcp-server/actions/workflows/publish.yml/dispatches \
  -d '{"ref":"main"}'
```

Vorher war das ein Handgriff für Lars, und er kam an der schlechtestmöglichen
Stelle: am 22.09.2026 veröffentlichte `v1.2.3` das Wheel nach PyPI und fiel
danach an der Beschreibungsgrenze der MCP-Registry. Der Workflow war binnen
Minuten repariert, starten konnte ihn niemand außer Lars, und die Registry stand
zwei Stunden fünf Versionen zurück.

**Prüfen, ohne etwas auszulösen:** ein Dispatch auf einen Ref, den es nicht gibt.
`422 No ref found` heißt, die Rechte stimmen; `403` heißt, sie fehlen.
`GET /actions/permissions` beantwortet eine andere Frage — es prüft
`administration` und antwortet auch mit gültigem `actions: write` mit 403.

**Nach dem Publish liegen zwei Caches im Weg, und beide haben schon gelogen.**
Am 22.09.2026 meldete `pypi.org/pypi/.../json` noch 1.2.3, während 1.2.4 seit
zwei Minuten auf dem Index lag, und `pip install -U` blieb aus demselben Grund
auf der alten Version stehen. Ein Abgleich, der das nicht einrechnet, meldet
einen Bruch, den es nicht gibt — oder, schlimmer, einen Erfolg, den es nicht
gibt.

- **Origin-Update immer mit `--no-cache-dir`:**
  ```bash
  /home/moltstack/moltstack/venv/bin/pip install -U --no-cache-dir moltrust-mcp-server
  sudo systemctl restart moltrust-mcp-http
  ```
- **PyPI-Version am Cache vorbei lesen.** `Cache-Control: no-cache` plus ein
  Cache-Buster in der Query, und wenn die Antwort die erwartete Version nicht
  trägt: 60 Sekunden warten und einmal wiederholen, bevor daraus ein Befund
  wird.
  ```bash
  curl -sS -H "Cache-Control: no-cache" \
    "https://pypi.org/pypi/moltrust-mcp-server/json?_cb=$(date +%s)"
  ```
  Verlässlicher als `info.version` ist die Frage nach der Version selbst:
  `/pypi/<paket>/<version>/json` antwortet 200 oder 404 und kennt kein
  veraltetes „latest". Dasselbe gilt für `pypi.org/simple/<paket>/`.
- **„Registry == PyPI" scheitert nie an einem Cache-Treffer**, sondern nur an
  einem echten Unterschied. Der Watchdog liest montags beide Indizes; ein
  Cache-Treffer dort wäre ein Fehlalarm, den niemand nachstellen kann.

**Was ein Release vorher besteht:** `pyproject.toml`, `server.json` und der Tag
nennen dieselbe Version, und die Felder der Registry bleiben unter ihren
Grenzen (Beschreibung 100 Zeichen). Beides prüft
`moltrust-mcp-server/tests/test_version_consistency.py` im `test`-Job, also vor
dem Upload. Der Watchdog vergleicht montags nach, ob beide Indizes dieselbe
Version führen.

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

## Ein Task-Text ist nach der Anlage unveränderlich (HART, ab 03.10.2026)

**`taskmarket task update` kennt Reward, Ablauf, Bid- und Pitch-Frist sowie
Auktionspreise — die Beschreibung nicht.** Was im Aufgabentext steht, steht dort
bis zum Ablauf, für jeden Worker lesbar, und lässt sich nicht nachbessern.

Festgestellt am 03.10.2026: der Text von TSK-E49N4V7T sagt „poll until it
appears" über einen Endpunkt, der bei jedem Aufruf ein neues Credential prägte.
Drei Agents lasen das als Aufforderung und holten 75 Credentials. Der Satz
bleibt bis zum 05.10. stehen; reparierbar war nur das System dahinter.

- **Der Scan und das Gegenlesen vor der Anlage sind die einzige Gelegenheit.**
  Danach gibt es keine zweite.
- **Jede Anweisung im Text auf ihre naheliegende Fehllesung prüfen**, nicht nur
  auf Richtigkeit. „Poll until it appears" war wahr und wurde trotzdem falsch
  befolgt, weil daneben stand, welcher Endpunkt gemeint war, und nicht, welcher
  nicht.
- **Was der Text verspricht, muss der Vertrag einlösen können.** Ein
  100-Gewinner-Versprechen auf einem Vertrag, der zehn auszahlt, ist ein Fehler,
  den keine Korrektur mehr einholt.

## Kein Fix ohne Wächter (HART, ab 03.10.2026)

**Jeder gefundene Defekt bekommt eine Invariante, die ihn künftig fängt — und
zwar bevor der Fix gemergt wird.** Die Invariante steht als Datei in
`docs/invariants/`, trägt ihre Herkunft im Klartext, und `scripts/selftest.py`
fährt sie stündlich oder täglich.

Grund: wir haben Lebenszeichen geprüft und das Monitoring genannt. Der
Track-Record-Defekt lief vom 1. bis 3.10. fünf Tage lang, während jeder
Watchdog grün meldete — drei Agents holten 75 Credentials, jedes verankert und
aus `BASE_ANCHOR_KEY` bezahlt. Der Watchdog fragt, ob der Dienst antwortet.
Niemand fragte, ob die Verteilung stimmt.

- **Die Herkunft steht in der Invariante.** Datum und Vorfall, damit in sechs
  Monaten niemand eine Prüfung entfernt, deren Grund er nicht kennt.
- **Die Meta-Invariante ist nicht abschaltbar.** Der Runner protokolliert, was
  tatsächlich gelaufen ist, und schlägt Alarm, wenn eine Prüfung zweimal in
  Folge übersprungen wurde. Ein Selbsttest, der still nichts prüft, ist genau
  der Fehler, den wir in den Harness-Tests dreimal hatten.
- **Autofix in drei Klassen.** GRÜN läuft selbst und nur idempotent, GELB legt
  einen PR an und mergt ihn nicht, ROT meldet und rührt nichts an: Geld,
  externe Kommunikation, Löschungen, alles was eine fremde Partei betrifft.
- **Jeder GRÜN-Autofix trägt einen Deckel**, Vorgabe drei Läufe je 24 h. Eine
  Reparatur, die sich dreimal am Tag wiederholt, repariert nichts; beim
  Überschreiten setzt sie aus und meldet FAIL.

## Fremde APIs: der Endpunkt kommt aus der Doku (HART, ab 04.10.2026)

**Vor jedem Aufruf einer fremden API wird der Endpunkt gegen die Primärdoku
geprüft** — nicht aus dem Gedächtnis, nicht aus einem Blogpost, nicht aus einer
älteren Stelle im eigenen Code.

Dieselbe Regel wie „ein Feld, das existiert, ist kein Beleg" (website-deploy.md
§4.1a2), eine Schicht früher: **ein Endpunkt, der plausibel aussieht, ist keine
Doku.** Bei LinkedIn sind `POST /v2/ugcPosts` und `POST /rest/posts` beide echt,
einer ist Legacy, und welcher antwortet, entscheidet ein Version-Header — aus
der URL-Form nicht zu erraten.

- **Microsoft/LinkedIn/Azure:** MCP-Server `microsoft-learn`
  (`https://learn.microsoft.com/api/mcp`, keine Zugangsdaten, read-only).
  Werkzeuge `microsoft_docs_search` / `microsoft_code_sample_search` /
  `microsoft_docs_fetch`, Argument heißt **`query`**.
- **Als geprüft gilt nur:** Doku-URL mitsamt `?view=`-Parameter, die
  Pflicht-Header wörtlich zitiert, und bei zwei konkurrierenden APIs die
  Begründung, welche benutzt wird. Details und gemessene Beispiele:
  `docs/linkedin-api.md`.
- **„Hat funktioniert, als ich es probiert habe" ist kein Nachweis.** Ein
  Endpunkt, der heute auf einen Aufruf ohne Version antwortet, ist genau das,
  was an einer Monatsgrenze bricht, die niemand gewählt hat.

## Eine Verneinung ist keine Bejahung (HART, ab 05.10.2026)

**Jedes Zählmuster entfernt Verneinungen, bevor es sucht — und jeder Test dazu
enthält einen Negativfall.** Ein Muster, das „nein" als „ja" zählt, meldet eine
Zahl, die nicht falsch aussieht.

Am 05.10.2026 habe ich gezählt, wie viele der 919 x402-Dienste eine
Identitätsprüfung nennen, und **77** gemeldet. Die Zahl ist **24**. Ursache: das
Muster suchte unter anderem `api key`, und die Beschreibungen sagen „**no**
account and **no** API key" — in diesem Markt bewirbt jeder fünfte Dienst
ausdrücklich, dass er keinen Schlüssel verlangt. Sieben der ersten zwanzig
sahen dadurch aus, als verlangten sie einen Schlüssel und bewarben gleichzeitig,
keinen zu brauchen. Dass das ein Widerspruch war, ist mir aufgefallen; dass es
mein Muster war, erst beim Nachsehen.

- **Negationen zuerst heraus, dann suchen.** `no|without|never|zero` vor dem
  Begriff, mitsamt Plural und Artikel, und erst auf dem bereinigten Text den
  eigentlichen Test fahren.
- **Ein Zählmuster ohne Negativfall im Test ist unfertig.** Nicht „findet es
  den Treffer", sondern „lässt es den Gegenteil-Satz liegen". Beides gehört in
  denselben Test.
- **Ein Widerspruch im Ergebnis ist ein Befund über den Prüfer.** Wenn eine
  Zeile zwei Dinge sagt, die sich ausschließen, liegt der Fehler fast immer
  beim Zähler und nicht in der Welt. Nachsehen, nicht erklären.
- **Nachsehen heißt im bereinigten Text nachsehen.** Mein erster Kontrollblick
  suchte die Treffer im Rohtext und zeigte genau die Verneinungen, die ich
  gerade entfernt hatte — ein Kontrollblick, der die falsche Fassung liest,
  bestätigt den Fehler statt ihn zu finden.

Verwandt mit der Regel oben und mit „kein Fix ohne Wächter": derselbe Fehlertyp
hat am 03.10. drei Invarianten grün gemeldet, am 04.10. eine Blockademeldung
erzeugt und am 05.10. diese Zahl verdreifacht. **Text vergleichen ist nicht
Verhalten messen**, und eine Verneinung ist der billigste Weg, das zu beweisen.

## Kein BLOCKIERT ohne Blick in die eigene Konfiguration (HART, ab 04.10.2026)

**Bevor etwas als blockiert gemeldet wird, wird nachgesehen, ob es schon
läuft.** Eine Blockademeldung ist eine Aussage über den Ist-Zustand, und sie
unterliegt derselben Beweispflicht wie jede andere.

Am 04.10.2026 habe ich gemeldet: „BLOCKIERT: healthchecks.io-Konto kann ich
nicht anlegen, `HEALTHCHECK_URL` fehlt in den Secrets." Beides war falsch. Das
Konto bestand, der Check `moltstack-watchdog` lief mit Periode 1 h und Grace
30 min, und `HEALTHCHECK_URL` stand in `~/.moltrust_secrets` — der Watchdog
pingte stündlich, mit HTTP 200 um 10:00, 11:00, 12:00 und 13:00 desselben Tages.
Grundlage meiner Meldung war **ein** `grep` mit einem Zeilenanker, dessen
Negativergebnis ich als Befund genommen habe.

- **Ein negatives `grep` ist kein Befund.** Dasselbe Muster findet die Zeile
  heute. Warum der eine Aufruf nichts zurückgab, ist nicht rekonstruierbar —
  und genau das ist der Punkt: eine Messung, die sich nicht wiederholen lässt,
  trägt keine Aussage.
- **Vier Stellen hätten geantwortet**, und drei davon kosten nichts: die Zeile
  in den Secrets, die Variable in der Umgebung nach `set -a; . secrets`, der
  Aufruf im Code, und der Vollzug im Log. Wer eine davon prüft und über den
  Ist-Zustand berichtet, hat eine Stichprobe und nennt sie Befund.
- **Das Log ist die stärkste der vier.** `healthcheck ping: ok -> HTTP 200`
  belegt nicht nur die Konfiguration, sondern ihre Wirkung. Konfiguration kann
  da sein und nichts tun; ein Vollzug im Log kann nicht da sein und etwas tun.
- **Ein Alarm, der nie gefeuert hat, ist nicht als empfangen belegt.** Dass der
  Ping ankommt, heißt nicht, dass die Benachrichtigung einen Menschen erreicht.
  Das ist eine zweite Frage und wird getrennt beantwortet oder als offen
  ausgewiesen (`delivery_verified: false` im Erwartungsregister).

Gilt für jede Blockademeldung: fehlender Schlüssel, fehlendes Konto, fehlendes
Recht, fehlendes Werkzeug. Erst der Blick, dann die Meldung.

## Erst den Bestand fragen, dann bauen (HART, ab 28.09.2026)

**Bevor für eine fremde Plattform etwas gebaut wird, wird deren eigene Suche
befragt.** Ein Katalog weiss, was in ihm liegt; wir wissen es nicht.

Am 28.09.2026 ist der Skill `moltrust-identity` gebaut, gescannt und eingereicht
worden — und existierte bereits seit dem 20.09. unter demselben Handle, mit 108
Installationen. Ein `clawhub search moltrust-identity` hätte das in vier
Sekunden beantwortet. Die Arbeit war nicht ganz umsonst, weil die neue Fassung
fünf Dinge ergänzt, aber sie war als Neubau geplant und hätte eine Ergänzung
sein müssen.

- **Erst suchen, dann schreiben.** `clawhub search`, `npm view`, `pip index`,
  `gh search repos` — was die Plattform anbietet.
- **Auch unter unserem eigenen Namen suchen.** Der Treffer stand unter
  `@moltycel`. Zwei Konsolen arbeiten parallel und sehen einander nicht; das
  gilt für Veröffentlichungen genauso wie für PRs in fremden Repos.
- **Findet sich etwas, ist die Frage eine andere:** nicht „wie baue ich das",
  sondern „was fehlt dem, was schon da ist".

## Vorgaben mit Wirkung auf öffentliche Artefakte (HART, ab 27.09.2026)

**Eine Vorgabe von Lars, die ein öffentliches Artefakt betrifft, gilt ab dem
Augenblick ihrer Erteilung repo-weit — nicht nur in dem Auftrag, in dem sie
gefallen ist.** Wer das Artefakt schreibt, wird mitgeprüft: jeder Exporter,
jeder Cron, jede Seite, jede Abfrage.

Am 26.09.2026 um 19:14 lautete die Vorgabe: eine Partnerplattform restlos aus
dem Zählbereich, Partner nur noch als Aggregatzeile ohne DIDs. Sie wurde in dem
Pfad umgesetzt, in dem sie erteilt worden war. Ein zweiter Erzeugungspfad in
diesem Repo schrieb weiter zeilenweise, und am 27.09. lag
`registry-proof.json` mit **50 einzelnen Partner-DIDs** im Web-Root.

Was daraus folgt:

- **Vor der Umsetzung `grep` über das ganze Repo**, nicht über den
  Auftragsordner. Betroffen ist jede Datei, die das Artefakt erzeugt, ausliefert
  oder beschreibt — `.py`, `.sql`, `.sh`, `.html`, `.json`, `.md`.
- **Zwei Pfade zu einem öffentlichen Artefakt sind der Defekt**, nicht die
  Ursache eines Defekts. Wer einen zweiten findet, führt sie zusammen, statt
  beide zu pflegen.
- **Ein Ausschluss steht als Positivliste.** Wer zählt, nennt die Plattformen,
  die zählen. Ein `NOT IN` vergisst der Nächste, der einen Eimer hinzufügt; eine
  fehlende Zeile in einer Positivliste erzeugt gar keine Ausgabe.
- **Die Probe ist der ausgelieferte Stand, nicht der Branch.** `curl` gegen die
  Live-URL, `grep` gegen die Antwort. Deploy ist der wirksame Schritt.

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

## MolTrust-Gate: wo es läuft und was es misst (ab 21.09.2026)

Das Gate prüft **offline**. Ein Aufrufer bringt eine MolTrust-signierte
Attestation und eine Signatur mit dem eigenen Schlüssel; beides wird gegen ein
JWKS geprüft, das der Prozess schon hat. **Kein Netzaufruf im Request-Pfad** —
ein Ausfall bei uns darf beim Betreiber keine Latenz werden.

- **Rabatt, kein Riegel.** MoltGuard: 20 % ab Score 50, `allowWithheld=false`.
  Niemand wird abgewiesen, der Preis bewegt sich. Die erste Messung, ob
  Verifikation etwas wert ist, darf keinen Verkauf kosten.
- **Ein zurückgehaltener Score zahlt vollen Preis.** Kein niedriger Score, und
  auch kein Grund, weniger zu verlangen.
- **Drei Implementierungen, ein Satz Vektoren.** `@moltrust/x402`,
  `moltrust_enforce.gate` und die einvendorte Kopie in
  `moltguard/src/middleware/moltrust-gate.ts` spielen alle
  `packages/x402/test/parity-vectors.json` ab. Verhalten ändern heißt: Vektoren
  neu erzeugen, sonst fällt jede Kopie um. Die Vektoren erzeugt
  `node packages/x402/test/make-fixtures.js`.
- **Der JWKS-Pfad ist Konfiguration, kein Fetch.** `MOLTRUST_JWKS_PATH`, auf dem
  Server `/home/moltstack/moltguard/jwks.json`, per Cron 04:23 aufgefrischt —
  Neustart nur, wenn die Datei sich geändert hat. Ein rotierter Schlüssel
  meldet sich als `attestation_invalid` mit `no key for kid …`; das ist die eine
  Ablehnung, die *Datei auffrischen* heißt und nicht *der Aufrufer irrt*.
- **Gemessen an `GET /guard/moltrust/gate-stats`:** bepreiste Anfragen,
  rabattierte, der Anteil, und die Ablehnungen **nach Grund**. Die Mischung ist
  der zweite Befund — überwiegend `attestation_missing` heißt, die Agenten
  kennen das nicht; überwiegend `score_withheld` heißt, sie kennen es und sind
  zu neu.
- **Herkunft `gate`** in der Funnel-Taxonomie: wer über den Hinweis in der
  Ablehnung registriert, kommt mit `platform=gate` an.

## Moltbook: was gemeldet werden darf (ab 23.09.2026)

**Keine Moltbook-Posts-Zahl in Reports, bis die Differenz geklärt ist.**
`/agents/me/posts` liefert 517, das Profil nennt 668, ohne Lücke am alten Ende.
Vier Parameter (`include_deleted`, `deleted`, `status`, `include_removed`)
werden angenommen und ignoriert, die Differenz ist aus der API nicht sichtbar.
Bei `moltguard_v1` dasselbe: 370 gegen 426. Bis eine Antwort vorliegt, wäre
jede Post-Zahl von dort eine, hinter der wir nicht stehen können.

Gemeldet werden dürfen Kommentarzahlen und die Spam-Quote — beide gegen die
Profilzahl geprüft — sowie Registrierungen mit `platform=moltbook` aus unserer
eigenen Datenbank. `scripts/moltbook_stats.py` gibt genau das aus und bewusst
keine Post-Zahl.

**`agent=` und `author=` sind zwei verschiedene Parameter, und nur einer
filtert.** `GET /posts?agent=<name>` liefert den globalen Feed — 14 732 Posts
von 712 Autoren, drei davon unsere. Derselbe Fehlertyp wie `payTo` bei CDP.

`GET /posts?author=<name>` filtert dagegen **korrekt**, liefert aber unabhängig
von `limit` nur die **drei neuesten** eigenen Posts und meldet dazu
`has_more: false`. `agents/ambassador.py` nutzt diesen Weg und sieht deshalb nie
mehr als drei eigene Threads — für seinen Zweck genügt das.

**Für Zählungen über eigene Posts ist keiner der beiden Wege zulässig.** `agent=`
zählt die Plattform, `author=` zählt drei. Wer eine Gesamtzahl braucht, nimmt
`/agents/me/posts` und prüft das Ergebnis gegen `posts_count` im Profil — und
solange dort 517 gegen 668 steht, wird gar keine Post-Zahl gemeldet.

**Zwei Paginierungsformen in derselben API.** `/agents/me/comments` schickt
`has_more`, `/agents/me/posts` nur `next_cursor`. Wer auf `has_more` prüft,
holt 100 von 668 und hält das für alles.

**Ursache der Spam-Historie, korrigiert am 23.09.2026.** `agents/ambassador.py`
stufte Antworten nach einem **Zähler** hoch: erste Antwort sachlich, zweite ein
Anstupser, ab der dritten der volle Pitch — „register a DID at moltrust.ch",
„pip install moltrust-mcp-server", „free tier, no strings attached" —,
unabhängig davon, was die Person geschrieben hatte. Das erklärt, warum 91 von
100 Kommentaren markiert sind, während von 517 Posts **keiner** markiert ist:
die Posts tragen keinen Pitch.

Seit #463 entscheidet der Kommentar. Ohne Nachfrage bleibt es bei Stufe 1, und
`post_reply` prüft jeden Entwurf gegen die Inhaltsregel, bevor er das Netz
erreicht — der Text kommt von einem Modell, die Stufenanweisung ist Empfehlung,
die Regel ist die Regel.

**Wer schreiben darf, steht in `scripts/moltbook_writers.py`.** Das Skript liest
Repo, Crontab und systemd-Units, ermittelt jede Datei, die mit einem unserer
Moltbook-Schlüssel an Moltbook posten kann, und vergleicht die eingeplanten
gegen `DECLARED`. Ein eingeplanter Schreiber ohne Eintrag ist ein Alarm, kein
Zufallsfund. Beim ersten Lauf hat es `agents/auditor.py` gefunden — montags
10:00, eine Stunde nach dem Tagespost, montags also zwei Posts.

**Ein Antwortpfad, nicht zwei.** Auf Moltbook antwortet `agents/ambassador.py`
(Cron, alle 30 Minuten). Der Antwortpfad in `moltbook/heartbeat.py` bleibt über
`MOLTBOOK_REPLY_ONLY` abgeschaltet, solange das so ist. Am 23.09. war er elf
Minuten lang eingeschaltet, bevor auffiel, dass der Ambassador dieselben
Kommentare beantwortet; gesendet hat er in der Zeit nichts.

## Identity Kontext

**MoltyCel = Lars Kroehls GitHub-Identität** (lars@moltrust.ch, "Lars Kroehl"). Kein separater Bot, kein separater privater Account. Manuelle Posts via MoltyCel-Account sind normal. Autonom posten nur die in WORKFLOW.md §0.1 benannten Dauerpipelines (Stand 08.10.2026); alles andere geht über Lars, keine Sitzung postet selbst.

## Deploy läuft über GitHub Actions (HART, ab 02.10.2026)

**Ein Pfad, für Mac und Cloud derselbe.** `.github/workflows/deploy.yml` fährt
`/home/moltstack/bin/deploy.sh <repo> <sha>` über SSH. Ausgelöst wird er vom
Merge auf `main` oder von Hand per `workflow_dispatch` — letzteres geht auch vom
Handy, ohne Laptop.

- **Die Console deployt nicht mehr selbst.** Kein `scp`, kein `install`, kein
  `git pull` auf dem Server. Sie stößt den Workflow an:
  `gh workflow run deploy.yml -R MoltyCel/<repo> --ref main` und liest das
  Ergebnis mit `gh run watch`.
- **Ausnahme nur bei Workflow-Ausfall** (Actions-Störung, Runner-Stau). Dann
  der alte Weg, aber mit Telegram-Hinweis, was von Hand lief und warum.
- Der Deploy-Key auf dem Server trägt `command="…/deploy.sh"` und `restrict`.
  Er kann nichts anderes; ein beliebiger Befehl landet in der Usage-Meldung.
- `deploy.sh` nimmt nur Commits, die Vorfahr von `origin/main` sind, hält
  `flock`, probt danach die Gesundheit und rollt bei Fehlschlag auf den zuletzt
  erfolgreich deployten SHA zurück. Stand: `/home/moltstack/.deployed/<repo>`,
  Protokoll: `/home/moltstack/logs/deploy.log`.

## Ein Eigentümer für den Server-Checkout (HART, ab 03.10.2026)

**In `/home/moltstack/moltstack` schreibt genau eine Session.** Alle anderen
committen auf einen Branch und lassen `.github/workflows/deploy.yml` ausrollen.
Kein `git pull`, kein `scp`, kein `install`, kein Editieren im Checkout.

Eigentümer steht in `/home/moltstack/.checkout_owner` — derzeit die
Mac-Console. Wer wechselt, schreibt die Datei um; es gibt keinen zweiten Ort,
an dem das steht.

Am 03.10.2026 um 11:27 UTC hat ein Deploy abgelehnt, weil eine andere Session
31 nicht committete Zeilen in `app/registry_export.py` im Checkout liegen
hatte — live, unversioniert, in keinem PR. Das Tor hat richtig entschieden, und
niemand wusste neun Minuten lang, von wem die Änderung war. Zurückgesetzt
wurde sie nicht: ein laufender Fix am öffentlichen Registry-Artefakt still
rückgängig zu machen, ist der schlimmere Fehler.

Was die Regel trägt:

- **Der Deploy-Key trägt einen forced command.** Ein Workflow kann nur
  `deploy.sh` aufrufen, nichts anderes.
- **`deploy.sh` lehnt ab, wenn verfolgte Dateien im Checkout geändert sind** —
  und sagt jetzt auch, wer den Lock hält: `.deploy.lock.info` nennt PID,
  Startzeit, Repo und SHA, der Trap räumt sie auch im Fehlerfall weg, und die
  Notiz eines abgestürzten Laufs wird gemeldet statt überschrieben.
- **`agents/supervision.py` prüft stündlich**, dass der Checkout sauber ist und
  auf dem deployten SHA steht. Ein zweiter Schreiber ist damit innerhalb einer
  Stunde sichtbar und nicht erst beim nächsten Deploy.
- **Kein Shell-Login wird daran gehindert.** Wer den `moltstack`-Key hat, kann
  dort schreiben. Deshalb ist die Regel beobachtbar gemacht und nicht behauptet,
  und deshalb nennt die Datei einen Eigentümer.

**Nichts davon wird automatisch korrigiert.** Fremde, nicht committete Arbeit zu
committen oder zu verwerfen ist genau das Urteil, das ein Reparaturwerkzeug
nicht fällen darf — `host/checkout/*` steht auf keiner Positivliste.

## Job-Abstimmung zwischen Sessions (HART, ab 02.10.2026)

Zwei Konsolen arbeiten parallel und sehen einander nicht. Am 20.09.2026 sind so
zwei widersprüchliche PRs auf dieselbe Datei entstanden.

- **Vor Arbeitsbeginn** offene PRs und Branches prüfen, die dieselben Dateien
  berühren:
  `gh pr list -R MoltyCel/<repo> --state open --json number,headRefName,files`.
  Treffer → **nicht anfangen**, sondern melden, welcher PR die Datei hält.
- **Arbeit sofort als Draft-PR anlegen.** Der Draft ist die Belegung. Was keinen
  PR hat, ist für andere Sessions unsichtbar; ein Branch allein genügt nicht.
- **Nie Dateien direkt auf dem Server ändern.** Ein Hotfix im Web-Root oder im
  Checkout ist beim nächsten Deploy weg und erzeugt Reconcile-Arbeit.

## Zeitgarantien kauft man nicht bei einem Best-Effort-Zeitplan (HART, ab 04.10.2026)

**Gemessen am 04.10.2026: der stündliche GitHub-Actions-Zeitplan feuerte 2 von
13 fälligen Takten — 15 % — und die beiden, die kamen, starteten 46 und 22
Minuten zu spät.** Ein Zeitplan mit dieser Quote trägt keine Zeitgarantie.

Daraus folgt eine Zuordnung, nicht eine Reparatur:

- **Der Totmann braucht die Garantie, der Selbsttest nicht.** „Hat sich der
  Server in der letzten Stunde gemeldet" ist die eine Frage, bei der Pünktlichkeit
  die Antwort *ist*. Sie liegt deshalb bei **healthchecks.io**: der Watchdog
  pingt nach jedem *beendeten* Lauf, der Dienst alarmiert selbst, über seine
  eigene Telegram-Anbindung. Ein Server, der ausfällt, kann die Nachricht über
  seinen Ausfall nicht senden.
- **Der Selbsttest bleibt stündlich konfiguriert und gilt als Best-Effort.**
  Erwartung: 24 h ohne Lauf = Eintrag im Sammelbericht, **kein Alarm**. Eine
  Drei-Stunden-Erwartung gegen 15 % misst GitHubs Warteschlange, nicht unseren
  Zustand, und ein Wächter, der ständig rot ist, wird stummgeschaltet — danach
  meldet er auch den Fall nicht mehr, für den er gebaut wurde.
- **Nur ein beendeter Lauf pingt.** Ein Watchdog, der sich gesund meldet,
  während er scheitert, ist der Fehler, den wir am 03.10. zweimal hatten. Ein
  Absturz schickt `/fail` statt zu schweigen.
- **Die Ping-URL ist ein Geheimnis besonderer Art.** Sie gewährt keinen
  Zugriff, sie unterdrückt einen Alarm — wer sie hat, kann verhindern, dass der
  Ausfall gemeldet wird. Also wie jede Zugangsdaten behandeln: nicht in Logs,
  nicht in Reports, nicht in Job-Summaries. `ping_healthcheck` gibt Statuscodes
  und Ausnahmetypen zurück, nie die URL.
- **Die Quote läuft als Dauermessung mit** (`scripts/check_external_runs.py
  --ratio`, wöchentlich im Sammelbericht). **Über etwa 70 % kommt die
  Zeiterwartung zurück** — die Entscheidung hängt an der Zahl, nicht an der
  Stimmung.

## Stille braucht einen Grund (HART, ab 03.10.2026)

**Ein Lauf schreibt Ergebnis *und* Grund, nie nur eine Zahl.** Eine Pipeline,
die nichts produziert hat, meldet warum — mit einem Wort, das vorher in
`config/expectations.yaml` deklariert wurde. Stille ohne deklarierten Grund ist
ein Fehler und kein Ruhezustand.

Dreimal hat dasselbe Muster zugeschlagen: die Liste antwortete im September 24
Stunden mit HTTP 400, in derselben Woche waren die Credits leer, und am
02.10. hat der Breaker zwei von vier Radar-Läufen abgesagt. Jedes Mal meldete
der Lauf „0 Kandidaten" — eine Zahl, die nicht falsch sein kann, weil sie nichts
behauptet.

- **Das Register ist die Quelle**, nicht der Prüfer. Eine Pipeline aufnehmen
  heißt einen Block in `config/expectations.yaml` schreiben: Takt, Beleg
  (Heartbeat, Log oder Ledger), erwartete Ausgabe je Zeitfenster, die erlaubten
  Stille-Gründe mit dem String, den der eigene Log dafür druckt, Quellen je
  Zweig, Kostenband.
- **Heartbeat vor Log.** Wo ein Lauf `{timestamp, status, detail}` schreibt, ist
  das der Beleg — er trägt Ergebnis und Grund in einem. Ein Heartbeat, der nur
  zählt (`"0 drafts, 0/8 today"`), ist eine Lücke; bis sie geschlossen ist,
  liest `agents/supervision.py` den Grund aus dem Log nach.
- **Korrigiert wird nur, was wortgleich auf der Positivliste in
  `scripts/selfheal.py` steht.** Alles andere: Alarm mit Befund, keine
  Interpretation, keine Reparatur. Rot wird nie korrigiert — rot heißt
  unbekannt.
- **Dieselbe Korrektur dreimal in einer Woche ist ein Konstruktionsfehler** und
  wird als solcher gemeldet, nicht ein viertes Mal ausgeführt
  (`scripts/supervision_report.py`, sonntags 06:50 UTC).
- **Beide Seiten überwachen einander.** Der Workflow `supervise` läuft stündlich
  zur :17 auf GitHub und prüft den Server; `agents/watchdog.py` prüft, ob sich
  der Workflow gemeldet hat. Ein Wächter, der auf der überwachten Maschine
  läuft, kann seinen eigenen Ausfall nicht melden.

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

### Basiswert der Gate-Messung (festgeschrieben 21.09.2026, 20:55 UTC)

**Organisch aktiviert: 0.** Bei 242 Registrierungen insgesamt. Das ist der
Vergleichswert, gegen den jede spätere Messung läuft.

| Eimer | DIDs | Key gebunden | auth. Call | aktiviert |
|---|---:|---:|---:|---:|
| Bounty, Plattform `taskmarket` | 114 | 68 | 14 | 2 |
| Bounty, am Verhalten erkannt | 13 | 5 | 0 | 0 |
| Eigen (Test, System, eigene Agents, Harald) | 46 | 23 | 11 | 11 |
| Partner (Ownify, aeoess) | 51 | 43 | 10 | 10 |
| **Organisch** | **18** | 13 | **0** | **0** |

Die fünf Eimer summieren sich auf 242; das ist die Vollständigkeitsprobe.
Aufschlüsselung und Namen: `~/Downloads/registrierungen-242-aufschluesselung.md`.

**Die Kohorte ist 127, nicht 114.** Dreizehn Agents haben im Bounty-Fenster
registriert und danach genau die Aufgabenschritte auf ihre eigene DID gefahren,
aber einen anderen `platform`-Wert gesetzt — `cekuu35-taskmarket`,
`qwen-taskmarket-worker`, `taskmarket-94637`, `Mythos` und neun weitere unter
`a2a`, `base`, `moltbook`. Wer nur auf `platform='taskmarket'` filtert, bucht
dreizehn bezahlte Registrierungen als organisch und bläht damit genau die Zahl
auf, gegen die gemessen wird. `app/sql/taskmarket_cohort.sql` erkennt sie über
das Verhalten mit.

**„Öffentlicher Call" misst, wer nachgeschlagen wird, nicht wer handelt.**
Während der Aufgabe fiel beides zusammen, weil der Agent seine eigene DID
abrief. Danach nicht mehr: seit Task-Schluss am 21.09. um 14:45 UTC hat **keine**
der 127 einen authentifizierten Aufruf gemacht, und die drei DIDs, die im Log
auftauchen, wurden von Dritten aufgelöst. Für „nach Task-Schluss noch aktiv" ist
die Antwort null, nicht drei.

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

## Spec-Pfade: Merge nur nach Lars' Go (HART, ab 05.10.2026)

**Ein PR, der `docs/spec-fakten/` oder `docs/specs/` berührt, wird nicht autonom
gemergt.** Lars gibt frei, sichtbar als Label `lars-go`. Bis dahin steht der Check
`spec paths need lars-go` auf rot.

- Das gilt auch für Reverts und Ein-Zeilen-Korrekturen. Steht ein ausdrückliches Go
  im Auftrag, setzt die Console das Label selbst und nennt den Auftrag im PR.
- Das Ruleset „main protection" hat seit 05.10.2026 keine Bypass-Akteure mehr. Auch ein
  Admin-Merge (`--admin`, API) scheitert am roten Check; geprüft an einem Wegwerf-PR.
  Direkte Pushes auf `main` gehen damit ebenfalls nicht mehr, alles läuft über PRs.
- Arbeitsmaterial für eine künftige Revision eines Drafts gehört in kein öffentliches
  Repo. Ablage ist das private Repo `cryptokri/aae-internal`.

## Eine Wache, die einen bekannten Zustand wiederholt, meldet nicht — sie sammelt (HART, ab 07.10.2026)

Der stündliche Selftest schickte bei **jedem** Lauf eine Telegram-Nachricht.
Gemessen über 48 Stunden: **50 von 50 Läufen, 50 Nachrichten, alle nach
ALERTS** — weil `a-track-record-burst` in jedem einzelnen Lauf fehlschlug. Wer
fünfzig Mal dasselbe liest, liest beim einundfünfzigsten Mal nicht mehr, und
dann geht der eine neue Befund mit unter. Ein Selftest, dessen Meldungen
überlesen werden, ist kein Selftest.

**Die Läufe bleiben. Nur die Meldung wird gedrosselt.**

### Drei Begriffe

**bekannt** — der Befund steht in `~/selftest/bekannte-abweichungen.json`, mit
Grund und einem Datum, an dem er grün sein soll. Zählt in die Sammelmeldung,
löst nichts aus.

**neu** — steht nicht im Register. Geht sofort raus. Auch ein bekannter
Befund, der sich verschärft (WARN → FAIL), ist neu: das Register kennt den
anderen Zustand.

**verfallen** — das erwartete Grün-Datum ist verstrichen und der Befund steht
weiter. Gilt ab diesem Lauf als neu, geht sofort raus mit dem Satz
„erwartetes Grün-Datum verstrichen", und fällt aus dem Register.

### Was die Regel verlangt

- **Ein Eintrag ohne `gruen_erwartet` wird nicht angenommen.** Kein
  Standardwert, ein Fehler. Ein Eintrag, der nie abläuft, ist Dauerstumm — und
  ein Register, das Befunde verschwinden lässt, ist schlimmer als keines.
- **Sofort gehen nur drei Fälle raus:** ein Befund, der nicht im Register
  steht; ein Autofix, der rot zurückkommt; ein verstrichenes Grün-Datum. Nichts
  sonst. Insbesondere nicht der gewollte Zustand — ein 503 mit Grund auf einer
  abgeschalteten Route ist keine Abweichung.
- **Die Sammelmeldung geht immer**, 08:00 und 20:00 UTC, auch bei null neuen
  Befunden. Autofix GRÜN gehört in diese Zeile, nicht in eine eigene Meldung.
- **Eine Wache über der Wache.** healthchecks.io `selftest-digest`, Takt 12 h,
  Gnadenfrist 60 min, Ping erst nach dem Absenden. Bleibt die Zeile aus, ist
  das das Signal — nicht die Stille. Dasselbe Prinzip wie bei der
  Attestat-Wache: eine Wache, die nur bei Treffern spricht, ist im Schweigen
  nicht von einer zu unterscheiden, die nicht läuft.
- **Ein Eintrag, der zwei Wochen steht**, ist keine Erwartung mehr, sondern ein
  Zustand. Er bleibt gültig und wird in der Sammelmeldung als veraltet benannt.

### Was nicht gedrosselt wird

Alles außerhalb des Selftest-Pfades. Diese Regel gilt für wiederkehrende
Befunde einer Wache, nicht für Ereignisse. Ein Einlöseversuch, ein Aufruf gegen
eine abgeschaltete Route, eine gescheiterte Zahlung — das sind Ereignisse und
gehen weiter sofort raus.

## SPEC-FAKTEN-PIN (aae)

- **Zitier-Primärquelle** = die **publizierte** `draft-kroehl-agentic-trust-aae-02`, gepostet
  2026-09-06, expires 2027-03-10, sha256 `08e202ec…a473b4c`, **live verifizierbar** via Datatracker
  (`https://www.ietf.org/archive/id/draft-kroehl-agentic-trust-aae-02.txt`, 131860 bytes). Lokale
  Referenz `~/Downloads/aae-02.txt`, byte-identisch zur Datatracker-Fassung. Inhalt: 9-Step-
  Verifikation (§5), `delegator_aae_hash` §3, §2.2.2 Action Binding, §6 Verdicts and Ratification,
  §7.5 Delegation Revocation (Cascade), §7.6 Clock Skew. Citations IMMER gegen diesen Draft.
- **Achtung Abschnittsnummern:** -02 hat umnummeriert. Cascade Revocation und Clock Skew lagen in
  -00 unter §6.5/§6.6 und liegen in -02 unter §7.5/§7.6. Wer eine §-Angabe aus einem älteren
  Dokument übernimmt, prüft sie gegen -02, statt sie fortzuschreiben.
- **`2847f4da` = `-00`, superseded** (uploaded 2026-05-21, 48500 bytes). Bleibt als Historie
  stehen, ist **keine Zitierquelle** mehr. Die lokale Arbeitsrevision „-04" war inhaltsgleich zu
  -00 und ist damit ebenfalls überholt.
- **KB-Derivat** = `~/moltstack/docs/spec-fakten/aae.md` trägt als Inhalts-Pin weiterhin `2847f4da`
  (-00) und daneben -01 — steht also auf dem alten Stand und ist ohnehin **KEINE Zitierquelle**
  (Integritäts-Index). Nachzug auf -02 ist ein eigener Vorgang.
- **`b619d163` = veraltete lokale `.md`** (7-Step, **kein** `delegator_aae_hash`, kein §6.5/§6.6) —
  **NIE Quelle.** Falsch-Pin aus #185 entfernt (pinte auf b619d163 + erklärte `2847f4da` „entfernt").
  Fehlergrund: Suche **nur auf lokalen Hosts** ohne Live-Datatracker-Fetch — „nicht lokal gefunden"
  wurde fälschlich als „Artefakt existiert nicht" gelesen.
- **Strukturregel (verhindert Wiederholung):** Spec-Primärquelle IMMER per Live-Datatracker/Repo-Fetch
  verifizieren, nie nur gegen lokale Hosts. **„Nicht lokal gefunden" ≠ „existiert nicht".**


## Gebaut ist nicht ausgerollt (HART, ab 07.10.2026)

**„Ausgerollt" steht in einem Bericht erst, wenn die Merge-Gates grün sind, der
Deploy durch ist und der Bericht den Commit nennt, den das laufende System
ausführt.**

Drei Bedingungen, alle drei, und die dritte ist die, die niemand prüft: welcher
Stand läuft gerade. Ein Repo-Stand ist keine Aussage über den Server.

Am 07.10.2026 ging PR #642 als ausgerollt in einen Bericht, während zwei Dinge
dagegenstanden. Der Bandit-Gate war auf dem PR-Kopf `0a03976` rot — zwei
B310-Befunde aus dem Zweig selbst, die ein ruff-`noqa` trugen, aber keine
bandit-Markierung; der PR hätte so nie mergen können. Und der Deploy scheiterte
an einer nicht eingecheckten Datei im Checkout, sodass der Server den Stand
`7fb8b98` weiterfuhr, während `main` schon drei Commits weiter war.

Was ein Bericht dafür nennen muss:

- **Die Gates.** Nicht „CI grün", sondern welche. Ein Lauf, der auf einem
  anderen Ereignis als `pull_request` rot ist, gehört genannt und erklärt.
- **Den Deploy-Lauf.** Seine Nummer und seinen Ausgang, nicht die Vermutung,
  dass er gefeuert hat, weil der Merge durch ist.
- **Den laufenden Commit.** `git log --oneline -1` im Checkout, nicht der
  Merge-Commit auf `main`. Weichen sie ab, ist der Bericht „gemergt, nicht
  ausgerollt" und nennt den Grund.

Der billige Teil daran: alle drei Angaben kosten je einen Befehl. Der teure
Teil ist der Bericht, der ohne sie stimmt, bis jemand nachsieht.


## Ein Schreiber, und der ist der Deploy (HART, ab 07.10.2026)

**Keine Sitzung schreibt in `/home/moltstack/moltstack`. Jede Arbeit läuft im
eigenen Worktree. Der Checkout gehört dem Deploy.**

**Vor jeder Änderung an einer getrackten Datei ein Schloss. Ein fremdes
frisches Schloss heißt: nicht anfangen, melden.**

Das Schloss liegt in `~/moltstack/.locks/<pfad-slug>.lock` und nennt
Sitzungskennung, PID, Zeitstempel und den Auftrag in einer Zeile. Älter als
vier Stunden gilt als verwaist und darf übernommen werden — mit Vermerk im
neuen Schloss, damit die Übernahme sichtbar bleibt und nicht wie ein
Erstzugriff aussieht.

Zwei Fälle an einem Tag, beide am 07.10.2026:

- Zwei Sitzungen arbeiteten gleichzeitig an `scripts/selftest.py`. Die eine
  schrieb direkt in den Checkout, die andere mergte einen PR auf dieselbe
  Datei. `deploy.sh` lehnte ab — *tracked files are modified* — und der Server
  blieb drei Commits zurück, bis jemand die Lage auseinandersortiert hatte.
  Das Tor hat richtig entschieden; verloren war die Zeit davor.
- Am selben Tag überschrieb eine Sitzung `~/gate-proof-key.txt`. Die DID
  `did:moltrust:373c7752846d439c` hat seitdem keinen Schlüssel mehr, zu dem
  sie gehört — verwaist, nicht widerrufen, und von außen nicht von einer
  gültigen zu unterscheiden.

Was die Regel nicht kann: ein Shell-Login hindert sie an nichts. Wer den
`moltstack`-Schlüssel hat, schreibt dort weiter. Deshalb prüft
`agents/supervision.py` stündlich, ob der Checkout sauber ist und auf dem
deployten Stand steht, und deshalb nennt `~/.checkout_owner` einen
Eigentümer. Die Regel ist beobachtbar gemacht, nicht erzwungen.


## Dieselbe Nachricht zweimal ist eine Nachricht mit einem Zähler (HART, ab 08.10.2026)

**Die Drosselung sitzt an der Sendestelle, nicht in der Wache.**

Am 08.10.2026 schickte die R4-Wache fünfzehn gleichlautende Fehlalarme in vier
Stunden. Sie war von der Drosselung ausgenommen, weil sie als wichtig galt —
und kam deshalb fünfzehnmal durch. Wichtig und wiederholt sind zwei
verschiedene Eigenschaften, und eine Wache, die sich für zu wichtig zum
Drosseln hält, ist genau die, die den Kanal zuschüttet.

`app/notify.py` drosselt jede Nachricht, über jeden Kanal, ALERTS
eingeschlossen. **Eine Ausnahme gibt es nicht.** Wer eine braucht, hat in
Wahrheit ein Fingerabdruck-Problem: ein Alarm, der sich ändert — anderer
Exitcode, anderer Grund, andere Datei — trägt einen anderen Fingerabdruck und
kommt sofort.

- **Erstmals** → sofort.
- **Gleicher Fingerabdruck** → höchstens einmal je Stunde, und diese Nachricht
  trägt den Zähler: *„(14x seit 10:36Z, gleichlautend)"*. Gedrosselt heißt
  nicht verschwiegen.
- **24 Stunden nicht gesehen** → gilt wieder als erstmals.

Der Fingerabdruck ist die SHA-256 der Nachricht, nachdem das Flüchtige
herausgenommen ist: Zeitstempel, Datum, Uhrzeit, Dauer, Git-Hash, PID und der
eigene Zähler. Die Liste steht in `_VOLATIL` als benannte Einträge, nicht als
ein Sammelmuster — jeder Eintrag ist einzeln begründbar, und ohne sie wäre
jede Nachricht neu, weil sie einen Zeitstempel trägt.

**Jeder Versuch hinterlässt eine Zeile** in `~/selftest/telegram-sent.jsonl`:
Zeitstempel, Kanal, Erfolg, HTTP-Status, Fingerabdruck, die ersten 80 Zeichen,
und bei Unterdrückung der Grund. Auch der Fehlschlag, auch das vom Gate
Geblockte, auch das Gedrosselte. Vorher ließ sich *„ist das rausgegangen"* nur
ableiten: am 08.10. war der einzige Beleg für eine gesendete Vorwarnung, dass
ein Flag gesetzt war, das nur nach erfolgreichem Versand gesetzt wird. Das ist
eine Kette, keine Quelle.

## Eine Quelle je Endpunkt (hart, 09.10.2026)

Ein Endpunkt oder Zugang wird an einer Stelle festgelegt: in
`.moltrust_secrets`. Eine Crontab-Zeile, die eine dieser Variablen setzt,
ueberstimmt den Quelltext unsichtbar und gilt als Defekt.

Belegt am 09.10.2026: `POLL_RPC_URL=https://mainnet.base.org` stand auf der
Poller-Zeile der Crontab, waehrend `BASE_RPC` in den Secrets auf den eigenen
Anbieter zeigte. Der Poller lief sechzehn Stunden gegen den oeffentlichen
Knoten, 429 nach 429. In den Secrets war nichts falsch, im Quelltext war
nichts falsch — der Wert kam von einer dritten Stelle, die niemand liest, wenn
er einen Endpunkt sucht.

Dazu gehoert: eine Variable traegt den Namen dessen, was sie liest. Im Poller
hiess sie `BASE_RPC` und las `POLL_RPC_URL`. Deshalb fand die URL-Durchsicht
vom 07.10. diese Stelle nicht — sie suchte nach dem, was der Code liest, und
der Name log darueber.

Und: kein Rueckfallwert auf einen oeffentlichen Knoten. Fehlt die Variable,
bricht der Aufruf ab. Ein stiller Wechsel verdeckt, dass der eigene Knoten
nicht antwortet, und sieht in jedem anderen Signal gesund aus.

## Eine Sendestelle fuer Telegram (hart, 09.10.2026)

Telegram wird ueber `app/notify.py` gesendet, nicht daneben. Wer die Adresse
`api.telegram.org` selbst in eine Anfrage schreibt, umgeht drei Dinge auf
einmal: das Gate `MOLTRUST_NOTIFY`, die Drosselung und das Sendeprotokoll
`~/selftest/telegram-sent.jsonl`. Aus der Shell geht es ueber
`python -m app.notify --channel <kanal> --stdin`.

Belegt am 09.10.2026: `deploy.sh` baute die URL selbst und schickte sie per
curl. Am 07./08.10. waren das 35 Meldungen in 24 Stunden, 32 davon "ok" — und
keine einzige davon stand im Sendeprotokoll oder wurde als Wiederholung
gedrosselt. Die Wirkung war nicht, dass eine Meldung fehlte, sondern dass in
der Menge die eine, die zaehlte, nicht mehr zu finden war.

Ein Rueckfall daneben zaehlt als dieselbe Ausnahme: ein curl, der nur greift,
wenn notify schweigt, ist der alte Weg in seltener. Wenn notify nicht senden
kann, steht das im Konsolenprotokoll, und der Vorgang laeuft weiter — was
nicht passieren darf, ist dass ein Vorgang an seiner eigenen Meldung haengt.

Noch offen, als Sperrklinke festgehalten in
`tests/test_telegram_eine_sendestelle.py`: 27 Dateien senden weiter selbst.
Die Liste kann nur kuerzer werden — ein neuer Eintrag macht den Test rot, und
eine Datei, die den Weg verlaesst, muss daraus gestrichen werden.
