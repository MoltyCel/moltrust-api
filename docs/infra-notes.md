# Infra notes (server-side, not repo-managed)

Server infrastructure (nginx / systemd / cron) is **not** managed in any repo.
This file records applied server changes so they are not silent
`live ≠ repo` drift. Each entry: what, why, where, when.

## 2026-09-23 — Plausible: vier Share-Ziele angelegt, zwei Testereignisse im Datenbestand

**Was.** In der Plausible-Instanz (`plausible-plausible_db-1`, Site 1 =
`moltrust.ch`) vier Goals eingetragen. Die `goals`-Tabelle war leer.

| id | event_name | display_name |
|---:|---|---|
| 1 | `share:x` | Share — X |
| 2 | `share:linkedin` | Share — LinkedIn |
| 3 | `share:bluesky` | Share — Bluesky |
| 4 | `share:copy` | Share — Copy link |

**Warum.** Plausible nimmt Custom-Events ohne Goal an und zeigt sie nicht. Die
Share-Leiste (`moltrust-web` #245) sendet genau diese vier Namen, jeweils mit dem
Post-Pfad als Property. Ohne die Zeilen wäre gemessen worden, aber nichts
sichtbar.

**Zwei Testereignisse bleiben im Datenbestand:** `share:copy` am 23.09.2026 um
**11:14:46** (curl gegen `/api/event`) und **11:17:46** (Klick in echtem Chrome),
beide auf `/blog/enforcement-you-can-recompute.html`. Sie entstanden beim
Nachweis, dass die Kette vom Knopf bis ClickHouse trägt.
`scripts/sm_kpis.py::share_events` schließt genau diese beiden Zeitstempel aus
(`TEST_EVENTS`). Gelöscht werden sie nicht — Analytics-Zeilen zu entfernen, damit
eine Zahl stimmt, ist die schlechtere Gewohnheit.

**Zwei Dinge, die dabei auffielen und kein Defekt sind.** Der Plausible-Client
verwirft Events bei gesetztem `navigator.webdriver`, sofern nicht
`window.__plausible` gesetzt ist. Und der Server verwirft danach still alles mit
`HeadlessChrome` in der User-Agent-Zeile — **mit HTTP 202**. Wer eine Messkette
headless prüft, bekommt also eine Erfolgsantwort und keine Zeile. Beides ist
Bot-Filterung und korrekt; es kostete zwei Fehlversuche, bis die Ursache klar war.

**Wo.** `api.moltrust.ch`, Docker-Stack `plausible`. Postgres:
`plausible_db.goals`. Kein Repo verwaltet das.

## 2026-09-23 — Sudoers: `assets/css` und `assets/js` für `moltstack`

**Was.** Zwei Zeilen ergänzt (von Lars eingetragen):

```
moltstack ALL=(root) NOPASSWD: /usr/bin/install -m 644 -o root -g root /home/moltstack/blog-deploy-stage/* /var/www/html/assets/css/*
moltstack ALL=(root) NOPASSWD: /usr/bin/install -m 644 -o root -g root /home/moltstack/blog-deploy-stage/* /var/www/html/assets/js/*
```

**Warum.** Angenommen wurde, `share.css` und `share.js` lägen außerhalb des
bisherigen Bereichs, weil `/var/www/html/*` kein `/` überquere. **Diese Annahme
war falsch** — siehe den Nachtrag unten. Die Zeilen schaden nicht und machen die
Absicht ausdrücklich, nötig waren sie nicht.

**Reichweite.** Die Flags müssen wortgleich mitgegeben werden — `-m 644 -o root
-g root` —, sonst greift NOPASSWD nicht. Round-Trip am 23.09. um 11:43 UTC gegen
beide Verzeichnisse geprüft.

### Nachtrag 24.09.2026: der Schreibbereich ist der ganze Webroot

Gemessen mit einer **nicht existierenden Quelldatei** — sudo entscheidet zuerst,
`install` scheitert danach an der fehlenden Datei, geschrieben wird nichts:

| Ziel | Ergebnis |
|---|---|
| `/var/www/html/x.html` | NOPASSWD erlaubt |
| `/var/www/html/blog/x.html` | NOPASSWD erlaubt |
| `/var/www/html/enterprise/index.html` | NOPASSWD erlaubt |
| `/var/www/html/a/b/deep.html` | NOPASSWD erlaubt |
| `/var/www/html/assets/css/probe.css` | NOPASSWD erlaubt |
| `/etc/nginx/x.conf` | verweigert, Passwort nötig |

Die Console kann also **jede ausgelieferte Datei in beliebiger Tiefe**
überschreiben; begrenzt wird sie nur durch den Webroot. Außerhalb von
`/var/www/html` ist nichts erreichbar.

**`sudo -l <befehl>` beantwortet das nicht.** Es prüft die Regel einschließlich
der passwortpflichtigen `(ALL:ALL) ALL`-Zeile und meldete deshalb auch
`/etc/nginx/x.conf` als erlaubt. Nur ein Lauf mit `-n` unterscheidet.

**Teuer gelernt.** Ein „geht dieser Pfad?"-Test mit einer **echten** Quelldatei
hat am 24.09. `/var/www/html/enterprise/index.html` mit der Root-`index.html`
überschrieben. Die Enterprise-Seite trug knapp zwei Minuten den falschen Titel,
bis sie aus dem Repo wiederhergestellt war. Eine Berechtigung wird nie durch
Schreiben geprüft.

**Wo.** `/etc/sudoers.d/moltstack-assets`.

## 2026-09-23 — 45 Streudateien unter `/var/www/html/blog/`, entstanden und entfernt

**Was.** Beim Deploy der Share-Leisten wurden statt 68 Post-Dateien 113
installiert. Ursache: der Deploy iterierte über `blog-deploy-stage/*.html` statt
über die Dateiliste des Commits. Der Stage-Ordner trug 248 Altbestände früherer
Deploys, darunter 45 Root-Seiten (`about.html`, `pricing.html`, `moltguard.html`,
…), die so unter `/blog/` landeten.

**Warum es zählte.** Die echten Root-Seiten blieben unberührt — aber
`generate_blog_index.py` scannt `/var/www/html/blog/*.html` und baut aus jeder
Datei eine Karte. Neun von zehn geprüften Streuern hätten als Karte im Blog-Index
gestanden, auf den Tag datiert, also vor jedem echten Post. Der Cron lief in
sieben Minuten.

**Wie aufgefangen.** Alle 45 mit einem 336-Byte-Redirect-Stub auf die jeweils
echte Seite überschrieben — über den erlaubten `install`-Pfad, also ohne
Wartezeit. Der Generator überspringt Dateien unter 500 Byte, die `http-equiv`
enthalten; danach sah er wieder genau 68 Posts. Der Index war zu keinem Zeitpunkt
falsch. Die Stubs hat Lars anschließend entfernt (`rm`, außerhalb von NOPASSWD).

**Regel daraus**, festgeschrieben in `moltrust-web/docs/website-deploy.md` §4.1:
**das Manifest ist die Dateiliste des Commits, nie der Inhalt des
Stage-Ordners.** Der Ordner ist ein Sammelbecken ohne Verfallsdatum.

**Wo.** `/var/www/html/blog/`. Keine Repo-Änderung nötig.


## 2026-09-23 — `Co-Authored-By: Claude` steht in 10 gemergten Commits auf `main`

**Was.** Die Regel lautet: keine Werkzeug-Signatur in öffentlichen Artefakten,
Autor ist `Lars Kroehl <lars@moltrust.ch>`. In den Commits von PR #445 stand
trotzdem ein `Co-Authored-By: Claude`-Trailer. Ab sofort nicht mehr.

**Warum das hier steht statt entfernt zu sein.** Der Trailer liegt nicht auf
dem Branch, sondern auf `main`. #445 wurde gesquasht; die Zeile steht in
`30ee5a1`, und sie steht dort nicht allein — neun ältere Commits tragen sie
auch, der erste ist `577fe12` vom 2026-08.

```
30ee5a1  Hand third-party replies to a human, and prove they were posted (#445)
054f7fc  test(gate): one set of vectors every implementation replays (#426)
fc415ae  sql: one canonical count for the taskmarket cohort (#419)
c0892a8  feat(gate): require_moltrust — an offline trust gate (#418)
97ed324  fix(notify): route the three senders the channel scan turned up (#403)
9b952fa  Telegram channels, organic milestones, Herald hook/reply (#401)
50f7921  fix(a2a): re-sign the public agent card (#397)
164509c  chore(panel): show what the internal bucket swallowed (#381)
38a22be  feat(funnel): our own registrations leave organic (#380)
577fe12  feat(agents): record where an agent comes from (#378)
```

Der Branch `feat/radar-intent-and-detect` existiert auf `origin` noch, aber ein
Rebase darauf ändert nichts an `main` — der gesquashte Commit ist eine eigene
Zeile in der Historie. Die Trailer aus `main` zu entfernen hieße, zehn bereits
veröffentlichte Commits neu zu schreiben und `main` force-zu-pushen. Das bricht
`post-sha == repo-sha` (WORKFLOW §11.1), jeden anderen Worktree und den
Deploy-Checkout auf dem Server, und zwar für eine Zeile in einer
Commit-Nachricht. Diese Abwägung gehört Lars, nicht der Console.

**Wenn es doch weg soll**, ist der Weg `git filter-repo --message-callback` über
die zehn Commits, danach ein koordinierter Force-Push mit Neu-Klon aller
Checkouts. Solange das nicht entschieden ist, ist die Historie so, wie sie ist,
und diese Notiz ist der Nachweis, dass es bemerkt und nicht übersehen wurde.

**Wo.** `MoltyCel/moltrust-api`, Branch `main`. Keine Server-Änderung.


## 2026-09-23 — Wochenbericht: `pool_spend` id 4 korrigiert, Moltbook-Spam-Quote als Kennzahl

**Why.** Zwei Vorgänge für den Wochenbericht, die sonst nur in einer DB-Zeile
und in einer Telegram-Nachricht stünden.

### `pool_spend` id 4, Begründung korrigiert

Die alte Begründung der Zeile behauptete, der Auditor habe nie zu TSK-9YFR1YF7
eingereicht. Das stimmt nicht. SUB-3YHAM31Q ist mit `0xf16F0882…4ECA`
gezeichnet und lag am 2026-09-20 um 14:12:49 UTC vor, acht Stunden vor der
Auszahlung um 22:39 UTC. Die On-Chain-Worker-Adresse `0x6668C4C7…7426` ist eine
zweite Wallet desselben Betreibers.

Derselbe Betreiber erhielt in Runde 1 zusätzlich 0,925 USDC für Rang 3 und
0,50 USDC Defekt-Bonus. Mit den 5,00 USDC aus id 4 sind das 6,425 von 11,50 USDC
leistungsbezogener Auszahlung, also 56 %.

**Keine Rückforderung** (Entscheidung Lars, 2026-09-23). Das Feld `purpose` der
Zeile trägt die Korrektur seit dem 23.09.

Für Runde 2: Audit-Auftrag und Bounty-Teilnahme trennen, oder die Doppelrolle
vor dem Start benennen und in den Ausschreibungstext schreiben.

### Moltbook-Spam-Quote, neue laufende Kennzahl

`scripts/sm_kpis.py` misst sonntags je Identität, welchen Anteil der eigenen
Kommentare Moltbook mit `is_spam` markiert. Schwelle: das Agent-zu-Agent-Angebot
an die 43 Dialogpartner geht raus, sobald der Wert unter 30 % liegt.

Stand 2026-09-23 über sieben Tage: `u/moltrust-agent` 90,6 % (328 von 362
Kommentaren), `u/moltguard_v1` 0 % (0 von 7, Stichprobe klein).

Die Zahl kommt aus `GET /api/v1/agents/me/comments`. `limit` ist bei 100
gedeckelt, ein höherer Wert liefert trotzdem 100 Zeilen ohne Fehler. Der
Parameter `cursor` blättert; `after`, `before`, `offset` und `page` werden
angenommen und ignoriert (beides am 23.09. geprüft). Der Leser blättert deshalb
bis ans Ende des Fensters. Schöpft er dabei die 20 Seiten aus, meldet der Report
eine Stichprobe mit ihrer Größe und ausdrücklich keinen Wochenwert.

Eine Quote über „die letzten 100 Kommentare" wäre für diesen Zweck unbrauchbar:
bei `u/moltrust-agent` decken 100 Kommentare knapp zwei Tage ab, und sobald der
Heartbeat nicht mehr kommentiert, decken sie Wochen ab und melden die alten
Werbekommentare weiter. Das Gate ginge nie auf. Bei `u/moltguard_v1` trennt es
2,6 % über die gesamte Historie von 0 % in der Woche.

## 2026-09-21 — `moltrust-vet` als System-Agent klassifiziert

**Why.** Der Trockenlauf von `revoke_inactive.py` listete
`did:moltrust:157224190be24072` (`moltrust-vet`, platform `clawhub`) als
Kandidaten. Der Datensatz trug `agent_type='external'`, obwohl es unser eigener
auf ClawHub veröffentlichter Skill ist; seine DID steht als `author` im
öffentlichen Manifest. Ab 2026-12-19 wäre er revoziert worden — eine
öffentlich referenzierte Identität, weil sie sich nie bei uns authentifiziert.

**What (applied).** Ein UPDATE auf eine Zeile:

```sql
UPDATE agents SET agent_type = 'system'
 WHERE did = 'did:moltrust:157224190be24072' AND agent_type = 'external';
-- UPDATE 1
```

Vorwert `external`, festgehalten für den Rückweg.

**Folge für die Zahlen.** `app/funnel.is_organic()` gibt für
`agent_type='system'` False zurück, gemessen mit der Repo-eigenen Funktion über
alle 242 Zeilen:

| | vorher | nachher |
|---|---:|---:|
| organisch | **57** | **56** |
| `agent_type='system'` ausgeschlossen | 5 | 6 |
| bezahlt / Partner / intern | 114 / 29 / 37 | unverändert |

Eine Registrierung weniger im 100-in-90-Tagen-Ziel. Sie war nie eine fremde,
also ist die kleinere Zahl die richtige.

**Verify.** `revoke_inactive.py --days 1` listet `moltrust-vet` nicht mehr.

## 2026-09-21 — Weekly Proof Post, Digest-Messung, Social-KPIs

**Why.** Der Digest misst sich bisher nicht selbst, und die Wochenzahlen
existierten nur als Ad-hoc-Abfrage. Impressionen sind als Einzelwert wertlos und
als Reihe brauchbar; eine Zahl, die in einer Telegram-Nachricht steht, ist eine
Woche später weg.

**What (applied).**

- **`agents/proof_post.py`**, Cron `0 8 * * 0`. Ein Post pro Woche mit
  2×2-Kachelkarte: neue Registrierungen, Credential-Anchors auf Base,
  x402-Receipts, ClawHub-Installs. Registrierungen schließen `ownify` und `test`
  aus — Ownifys eigene Agents sind per Vereinbarung dauerhaft frei, `test` sind
  unsere. Zahlen kommen bei jedem Lauf frisch aus DB und ClawHub-API, nie aus
  einem mitgeführten Zähler. Der Entwurf läuft durch beide Gates, und (g) prüft
  jede Zahl gegen genau die Messung, die sie erzeugt hat.
- **`scripts/digest_metrics.py`**, Cron `0 18 * * *`, sechs Stunden nach dem
  Digest. Liest die Tweet-ID aus `herald_state.json`, holt `public_metrics` und
  hängt eine Zeile an `data/digest_metrics.jsonl` (0640). Telegram bekommt die
  Zahlen, die Datei behält sie.
- **`scripts/sm_kpis.py`**, aufgerufen von `daily_stats.sh` sonntags vor 12 UTC.
  Fünf Werte aus vier Systemen: Follower und Reply-Zahlen über die X-API,
  Top-Post-Impressionen aus Timeline plus `digest_metrics.jsonl`,
  Social-Referrer aus dem selbst gehosteten Plausible, Registrierungen aus der
  DB. Eigene Telegram-Nachricht statt Einbau in `TG_MSG`: vier Quellen können
  einzeln ausfallen, und das darf die Tagesstatistik nicht kosten.
- **Kartenmodul.** `digest_card.py` bekommt `render_metrics()` für Kacheln. Die
  Kacheln tragen bewusst keine eigene Farbe — das sind Größen, keine Kategorien
  und keine Zustände. Statusfarbe bleibt den Risiko-Tiers des Digests
  vorbehalten. Kontrast geprüft: alle Textpaare ≥ 4,5:1.

**Ein Reply zählt nur, wenn er einem fremden Thread antwortet.** Eigene
Thread-Fortsetzungen antworten auf uns selbst; ohne diese Unterscheidung würde
jeder Syndication-Thread die Reply-Zahl um vier aufblähen.

**Verify (Dry-Run 21.09. 07:49 UTC).** Proof-Post: beide Gates PASS, Karte
53 208 Bytes, Tweet 195/280. `digest_metrics.py` gegen den letzten Herald-Post:
4 Impressionen nach 9,8 h. `sm_kpis.py`: 26 Follower, 36 Posts, 0 Replies
gesendet, Top-Post 67 Impressionen, 2 Social-Referrer von 99 Events,
109 Registrierungen aus 5 Plattformen.

**Offen.** Plausible liegt nur auf der Startseite (99 Events in 7 Tagen, alle
`moltrust.ch`). `/blog/`, `/integrity.html` und `/skills.html` tragen kein
Script — also genau die Seiten, auf denen Social-Traffic landet. Der
Social-Referrer-KPI misst deshalb vorerst fast nichts. Nachrüsten ist eine
moltrust-web-Änderung an ausgelieferten Seiten, eigener Vorgang.

## 2026-09-21 — Telegram-Token aus den Logs, Logrotate mit 0640

**Why.** `httpx` protokolliert jede Request-URL auf INFO. Agents, die
`logging.basicConfig(level=INFO)` setzen und den Telegram-Versand über `httpx`
fahren, schreiben damit `POST https://api.telegram.org/bot<id>:<secret>/sendMessage`
im Klartext in ihre Logdatei. Gefunden am 20.09. beim Digest-Deploy: **243 Zeilen**
— 220 in `logs/watchdog.log` (aktive Quelle, zuletzt 2026-09-15), 23 in
`logs/ambassador.log` (historisch, letzte vom 2026-02-25). Dateien lagen auf
`0664` in einem `0775`-Verzeichnis.

**What (applied).**

- `agents/watchdog.py` und `agents/ambassador.py` halten `httpx` jetzt auf
  `WARNING` (wie `herald_v3.py` und `syndicate.py` seit dem 20.09.).
  `ambassador.py` sendet heute selbst kein Telegram mehr — die Zeile steht dort
  als Vorsorge, weil das Skript `httpx` breit benutzt und ins selbe Logdir schreibt.
- **Logrotate unprivilegiert**, Config repo-verwaltet unter
  `config/logrotate.conf`, Cron als `moltstack`:
  `0 4 * * 0 /usr/sbin/logrotate -s ~/.logrotate.state ~/moltstack/config/logrotate.conf`.
  Kein `/etc/logrotate.d`-Eintrag, kein root. `weekly`, `rotate 8`, `compress`,
  `copytruncate`, `create 0640`. `copytruncate` ist nötig, weil die Agents ihr
  Log über die Cron-Umleitung einmal öffnen und dann anhängen.
- Bestandslogs über `scripts/scrub_telegram_token.sh --apply` bereinigt: Backup
  nach `~/log-scrub-backup/<stamp>` (0600, enthält den Token noch), dann `sed`
  in-place auf das Token-Muster, danach `chmod 640` auf jede Logdatei.
- Logdir bleibt vorerst `0775`. Die Dateirechte tragen den Schutz; eine Änderung
  am Verzeichnis wäre ein eigener Vorgang.
- **Zwei Logs bleiben außen vor.** `blog_index_selfheal.log` und
  `mp_consumer.log` gehören root-Cronjobs (`0644 root:root`). `copytruncate`
  braucht Schreibrecht auf der *Datei*, nicht auf dem Verzeichnis — der
  unprivilegierte Lauf wäre daran jede Woche gescheitert (live geprüft: `open(…,
  "a")` gibt `Permission denied`). Sie stehen deshalb in einem vorgezogenen
  Block mit `size 1000G`, den sie nie erreichen; logrotate nimmt den ersten
  passenden Block und rührt sie damit nicht an. Ihre Rotation gehört zu den
  root-Jobs, die sie schreiben. Beide tragen keinen Token.

**Verify.** Kein Treffer mehr auf das Token-Muster unter `logs/` (vorher 243),
**38 von 40** Logdateien auf `0640 moltstack:moltstack` — die zwei root-eigenen
bleiben `0644 root:root`, `crontab -l | grep -c logrotate` = 1.

**Offen (Lars).** Token-Rotation über BotFather und Eintrag des neuen Werts in
`~/.moltrust_secrets`. Danach das Backup löschen — solange es liegt, steht der
alte Token weiter auf der Platte.

## 2026-09-21 — X-Posting: Herald auf 1 Digest/Tag, Syndication-Job neu, drei Cron-Leichen entfernt

**Why.** Die Ist-Aufnahme vom 20.09. hat die Reichweite gemessen, nicht geschätzt:
@moltrust hatte 26 Follower bei 1275 Posts seit dem 17.02.2026. Die letzten 20 Posts
kamen zusammen auf 163 Impressionen (Schnitt 8,2, Median 4, max 67), bei null Likes,
null Retweets, null Quotes. Herald v3 lief viermal täglich und erzeugte dabei 4–6
Tweets, weil ein Lauf gelegentlich einen Zwei-Tweet-Thread baut. Die reinen
Link-Tweets („Check it: …") als zweiter Thread-Teil holten 0–15 Impressionen. Mehr
Frequenz auf dieser Basis bringt nichts; ein Post pro Tag mit Bild und drei Märkten
trägt mehr Information als vier Einzelposts.

**What (applied).**

- **Herald.** Cron `0 7,12,17,22 * * *` (`herald_v3.py`, vier Läufe) ersetzt durch
  `0 12 * * *` mit `herald_v3.py digest`. Der Digest nimmt die drei Märkte mit
  `riskTier: "high"` und dem höchsten `anomalyScore` (Gleichstand → höhere
  24h-Volumenänderung), rendert eine 1200×675-PNG-Karte und postet **einen** Tweet
  mit Bild. Kein Füller-Zweittweet mehr — der Link wandert am 21.09. mit #401
  in eine Reply, weil X die Reichweite eines Posts mit Auslink drosselt. `flag_records` werden weiter geschrieben, jetzt
  drei pro Tag statt einem pro Lauf, alle mit derselben `created_tweet_id`. Der alte
  Einzelpost-Pfad bleibt als `herald_v3.py` ohne Argument erhalten (manuell,
  `--dry-run`).
- **Syndication.** Neuer Job `agents/syndicate.py`, Cron `*/30 * * * *`. Pollt
  `moltrust.ch/blog/feed.xml`, baut aus einem neuen Eintrag einen Thread von 4–6
  Tweets (Hook ohne Link, Link im letzten Tweet), schickt einen LinkedIn-Entwurf
  per Telegram und spiegelt nach Bluesky. Der erste Lauf auf leerem State markiert
  alle 40 vorhandenen Feed-Einträge als gesehen und postet nichts.
- **Cron-Leichen entfernt.** Drei Einträge, die nichts mehr taten:
  `0 10 1 4 * … agents/x_wallet_binding.py` (feuerte jährlich am 1. April),
  `0 18 31 3 * … /tmp/tweet2.py` und `0 20 31 3 * … /tmp/tweet3.py` — die beiden
  `/tmp`-Skripte existieren seit einem Reboot nicht mehr, die Jobs liefen jährlich
  ins Leere. `agents/x_wallet_binding.py` und `agents/x_thread_followup.py` bleiben
  als manuell aufrufbare Skripte im Repo liegen.
- **venv.** `Pillow` neu installiert (Kartenrendering). Steht jetzt auch in
  `requirements.txt`. Schriften kommen aus dem System-DejaVu-Paket, keine weitere
  Abhängigkeit.

**Gate.** Beide Jobs laufen vor dem Senden durch `agents/voice_gate.py`, den
(a)–(f)-Scan gegen `anti-KI-Sprech.md` und `my-voice-en.md`. Ein blockierter Entwurf
geht per Telegram raus statt auf X; der Syndication-Job versucht denselben Eintrag
noch zweimal und lässt ihn dann liegen.

**Offen (nicht Teil dieser Änderung).** Der Bluesky-Mirror ist gebaut, aber ohne
Account: `moltrust.bsky.social`, `moltrust.ch` und `molttrust.bsky.social` lösen am
20.09. nicht auf. Ohne `BLUESKY_HANDLE` und `BLUESKY_APP_PASSWORD` in
`~/.moltrust_secrets` protokolliert der Job den Sprung und macht weiter.

**Verify.** `crontab -l | grep -c herald_v3` = 1, `grep -c syndicate` = 1,
`grep -cE "x_wallet_binding|tweet2.py|tweet3.py"` = 0. Backup der Vorfassung unter
`~/crontab.bak.<stamp>`.

## 2026-08-08 — status.moltrust.ch: `GH_PAT` erneuert, Auto-Update-Workflows deaktiviert

**Why.** Die Upptime-Instanz meldete ab 2026-08-07 11:09 durchgehend `Uptime CI`-
Fehlschläge, vier Annotations pro Lauf. Keine davon war ein Endpoint: dreimal
`fatal: could not read Username for 'https://github.com': terminal prompts disabled`
plus `The process '/usr/bin/git' failed with exit code 128`, alle aus dem Schritt
**Checkout**. Der Job starb zwei Schritte vor `Check endpoint status`, die drei
Retries erklären die 35–40 s Laufzeit. Alle sechs überwachten Endpoints waren zu
dem Zeitpunkt live 200; der zuletzt aufgezeichnete Stand lag zwischen 99,32 % und
99,89 % Uptime. Also **kein Ausfall, sondern ein blinder Melder**.

Ursache: `uptime.yml` reicht `${{ secrets.GH_PAT || github.token }}` an Checkout
*und* Monitor. Ist `GH_PAT` gesetzt, aber ungültig, greift der `||`-Fallback nicht —
ein nicht-leerer String gewinnt. Das Secret war zuletzt am 2026-05-09 gesetzt; plus
90 Tage ergibt den 2026-08-07, den Tag des Kipppunkts. Alle sechs Upptime-Workflows
im Repo teilen dieses eine Secret und fielen im selben Fenster aus.

**What (applied).**

- Neuer fine-grained PAT, nur auf `status.moltrust.ch`, Permissions **Contents:
  Read/write** und **Issues: Read/write**. Issues ist nicht optional — Upptimes
  Störungsmeldungen *sind* GitHub-Issues (40 im Repo, z. B. „🛑 Agent Score (Free)
  is down"). Mit Contents allein committet Upptime weiter Messwerte und meldet
  keine Ausfälle mehr.
- Kein Workflow-Schreibrecht vergeben. Folge: `update-template.yml` (täglich 00:00)
  und `updates.yml` (täglich 03:00) regenerieren den Repo-Inhalt inklusive
  `.github/workflows/*.yml` und liefen damit in ein 403. Beide daher über die
  Actions-API auf `disabled_manually` gesetzt (`gh workflow disable`) — Dateien
  bleiben liegen, `gh workflow enable` macht es rückgängig. Upptime steht damit
  fest auf **v1.43.13**.

**Verify.** Ein grüner Lauf allein beweist nichts: `Uptime CI` protokollierte
sechsmal `Skipping commit, status is up` — das `update`-Kommando schreibt nur bei
einem Statuswechsel. Der Schreibpfad wurde deshalb separat über `Response Time CI`
geprüft, das bei jedem Lauf committet: `master` wanderte `731f6ab7 → 18874c8d`,
sechs neue Commits in `history/`, Dateiinhalt auf `master` gegengelesen
(`lastUpdated: 2026-08-08T11:57:06.070Z`). Ungeprüft blieb das Issues-Recht — das
löst nur ein echter Ausfall aus.

## 2026-07-28 — nginx: Discovery-Aliases im `moltrust.ch`-Block

**Why.** Wiederkehrende 404 von Discovery-Crawlern (AgenstryBot, Terminus-
Observatory, GuzzleHttp, AgentRadar) auf Pfaden, deren Daten längst vorlagen.
`api.moltrust.ch` beantwortete vier der fünf bereits per App-Route (PR #207/#212),
der statische Web-Host `moltrust.ch` nicht — die nginx-Blöcke sind getrennt, unter
`moltrust.ch` greift nur `try_files` gegen `/var/www/html`. Volumen über ~14 Tage:
70 vergebliche Abrufe auf `agent.json`, 5 auf `x402`.

**What (applied).** Zwei `location`-Blöcke im `moltrust.ch`-Server-Block,
eingefügt nach dem bestehenden `a2a`-Redirect:

```nginx
location = /.well-known/agent.json {
    alias /var/www/html/.well-known/agent-card.json;
    default_type application/json;
    add_header Access-Control-Allow-Origin "*" always;
    add_header Cache-Control "public, max-age=3600";
}
location = /.well-known/x402 {
    return 301 /.well-known/x402.json;
}
```

`agent.json` ist der Vor-Rename-Name aus A2A und inhaltlich identisch zur
`agent-card.json` — per `alias` dieselbe Datei, kein zweites File, keine Drift.
Exakt-Match (`location =`) statt Prefix wie bei den Nachbarblöcken, damit nicht
versehentlich `agent.jsonX` mitgefangen wird.

**Verify.** `agent.json` → 200, Body per `cmp` byte-identisch zu `agent-card.json`
(md5 beidseitig `6f36c6b4…3bdfdc`); `x402` → 301 mit Ziel `/.well-known/x402.json`,
gefolgt → 200 `application/json`. api-Block unberührt, Nachbarpfade (`jwks.json`,
`a2a`, `did.json`, `llms.txt`, `sitemap.xml`, `blog/`) unverändert. Backup unter
`~/nginx-backups/default.bak-2026-07-28-0817`, Audit-Zeile in
`~/moltguard-infra-audit.log`.

**Offen.** `/.well-known/mcp` und `mcp.json` fehlen auf dem Web-Host weiterhin
(braucht die Datei, nicht nur eine nginx-Zeile). `mcp/server-card.json` und
`agent-directory.json` bleiben ohne belegten Nutzen — SEP-2127 ist Draft und nennt
einen anderen Pfad (`.well-known/ai-catalog.json`), und MolTrust ist bei Agenstry
bereits über `agent-card.json` gelistet. Nebenbefund: `x402.json` liegt im Web-Root,
ohne von einem Repo gedeckt zu sein.

## 2026-06-28 — trouvart DB-backup cron: daily → weekly (+ one-time prune)

**Why.** `~/trouvart/scripts/backup.sh` (cron `0 4 * * *`) copied the full ~2.7G
trouvart SQLite DB **daily**; with its own `-mtime +14` retention that plateaued at
~37G — ~60% of the 75G root disk (which had reached **86% used**). trouvart's live
data is only ~3.4G, so 14 daily 2.7G copies was disproportionate.

**What (applied).**

- **Cron** (user `moltstack` crontab): backup frequency `0 4 * * *` → `0 4 * * 0`
  (weekly, Sunday 04:00). The daily trouvart `run_scan.sh` (`0 2 * * *`) is unchanged.
- **One-time prune**: deleted `trouvart_*.db` backups older than 7 days (7 files,
  ~16 GiB); kept the 8 most recent + the live DB (`~/trouvart/data/trouvart.db`).
- Also cleared verified-stale items (May-12 pre-deploy snapshot, old KB raw exports
  `export-2026-06-01/-12.json`, April html backups in `~/backups/`). **Kept** the
  active `~/backups/moltstack_*.sql` daily DB dumps (separate `backup_db.sh`, which
  already self-prunes at `-mtime +7`).

**Where/when.** `crontab` (user `moltstack`), applied 2026-06-28; crontab backed up
to `~/crontab.bak-2026-06-28-*`. Disk **86% → 61%** (29G free).

**Note.** `backup.sh` itself unchanged — weekly run + `-mtime +14` retention now
yields ~2 retained restore points; bump retention if more weekly history is wanted.

## 2026-06-27 — nginx: mask `api_key=` in access-log query strings

**Why.** Default `combined` log_format logs the full request line incl. the query
string. External MCP-discovery crawlers (`agent-tools.cloud-crawler`, some
`python-httpx` clients) append `?api_key=…` to `POST /mcp`, landing those tokens in
plaintext in `/var/log/nginx/access.log*` (~14-day retention). Analysis: the leaked
values are **not** MolTrust keys (no `mt_` prefix, 0 matches in the `api_keys` table) —
they are the crawlers' own credentials — but logging third-party secrets is a
liability, and a real `mt_` key could land the same way. The API itself authenticates
only via the `X-API-Key` **header**, so a query-string `api_key` is functionally
ignored (the `200/202` status ≠ "key accepted" — verified against the DB, not assumed).

**What (applied rule).** In `http {}` (Logging Settings), mask only the `api_key`
value while preserving every other param, so `daily_stats.sh` `profile=` extraction
and awk field positions keep working:

```nginx
map $request $request_masked {
    "~^(?<pre>.*[?&]api_key=)[^& ]*(?<post>.*)$"  "${pre}REDACTED${post}";
    default                                        $request;
}
log_format masked '$remote_addr - $remote_user [$time_local] '
                  '"$request_masked" $status $body_bytes_sent '
                  '"$http_referer" "$http_user_agent"';
access_log /var/log/nginx/access.log masked;
```

Replaces the prior `access_log /var/log/nginx/access.log;` (implicit `combined`).
No per-server `access_log` override exists, so this covers all vhosts. Named captures
(`${pre}`/`${post}`) chosen over positional `${1}` for unambiguous runtime resolution.

**Where/when.** `/etc/nginx/nginx.conf`, applied 2026-06-27 via `systemctl reload
nginx` (graceful — master PID unchanged, workers reloaded; backup
`/etc/nginx/nginx.conf.bak-2026-06-27-*`).

**Verify (live, 2026-06-27 11:48 UTC).**
`curl -s "https://api.moltrust.ch/health?profile=keepme&api_key=MASKPROBE<epoch>"` →
log line shows `…?profile=keepme&api_key=REDACTED…`; the raw marker is absent from the
log (grep count 0). `profile=` preserved.

**Backfill.** Pre-existing plaintext entries age out via the 14-day logrotate; no
MolTrust-secret rotation needed (leaked values were external, per the analysis above).

## 2026-06-18 — nginx Cache-Control for the static web root (moltrust.ch)

**Why.** Served `*.html` had **no `Cache-Control` header**, so browsers applied
heuristic caching and returning visitors reused stale HTML (e.g. EU visitors
saw an old `/pricing.html` that pre-dated the EUR auto-detect, appearing as
"EU stuck on USD" even though the deployed JS was correct). Root cause was the
cache layer, not the page code (confirmed by headless render of a fresh
context: `de-DE → EUR`, `en-US → USD`).

**What (applied rule).** In the `server { listen 443 ssl; server_name
moltrust.ch; root /var/www/html; }` block:

- Server-level, alongside the existing security headers:
  ```nginx
  add_header Cache-Control "no-cache, must-revalidate" always;
  ```
  This is inherited by `location /` (no own `add_header`), so **all HTML and
  the homepage `/`** revalidate on every request (304 when unchanged) instead
  of being served stale from browser cache.

- Versioned static assets are exempted with a long-lived immutable policy via a
  regex location (which re-adds the security headers, since `add_header` in a
  location does not inherit server-level headers):
  ```nginx
  location ~* \.(?:js|mjs|css|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf|eot|map)$ {
      add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
      add_header X-Content-Type-Options "nosniff" always;
      add_header X-Frame-Options "DENY" always;
      add_header Cache-Control "public, max-age=31536000, immutable" always;
  }
  ```
  Assets are cache-busted by query string (`nav.js?v=5`, `pricing.js?v=1`, …);
  bump `?v=` when an asset changes.

  `txt` / `json` / `xml` (e.g. `llms.txt`, `sitemap.xml`, `x402.json`) are
  intentionally **not** in the immutable set — they revalidate like HTML
  (some already set their own `max-age=3600` in dedicated locations).

**Where.** `/etc/nginx/sites-enabled/default` — the moltrust.ch `:443`
server block. Note: `sites-enabled/default` is a **regular file** and **differs
from** `sites-available/default`; nginx loads the `sites-enabled` copy, so edit
that one. Backup taken at `/etc/nginx/default.cachefix-bak-20260618`.
Backups must **never** live under `sites-enabled/` (the `*` glob would load a
`.bak` as a second server config → duplicate-directive `nginx -t` failure).

**Verify.**
```
curl -sI https://moltrust.ch/pricing.html   | grep -i cache-control
#   cache-control: no-cache, must-revalidate
curl -sI https://moltrust.ch/assets/js/nav.js | grep -i cache-control
#   cache-control: public, max-age=31536000, immutable
```
Applied with `nginx -t` (pass) + `systemctl reload nginx`; security headers
(HSTS / nosniff / X-Frame-Options) confirmed still present on HTML responses.

---

## Cron: milestone trigger (2026-09-26)

Crontab is not repo-managed, so the entry is recorded here.

```
0 6 * * * set -a && source /home/moltstack/.moltrust_secrets && set +a \
  && cd /home/moltstack/moltstack \
  && /home/moltstack/moltstack/venv/bin/python scripts/milestone_trigger.py \
  >> logs/milestone_trigger.log 2>&1
```

06:00 UTC, in line with the rest of the crontab; the server runs `Etc/UTC`.
Installed by appending to `crontab -l`, 161 lines before and 162 after, and the
diff against the saved copy showed that one added line and nothing else.

First real run the same day: count 309 of 1,000, rate7 35.0/day, projection
19.7 days, no stage reached, so nothing was sent. State lives in
`~/.milestone_trigger.json` and holds the two-consecutive-days flag that T-3
needs.

The job sends messages through `app/notify` (STATS at T-1, ALERTS at T-2 and
T-3) and holds no posting credentials. Rationale and the release gate:
`docs/decisions/0004-zieldatum-1000.md`.

---

## Cron: registry-proof refresh (2026-09-27)

```
40 5 * * * /home/moltstack/moltstack/scripts/registry_proof_publish.sh \
  >> /home/moltstack/moltstack/logs/registry_proof.log 2>&1
```

05:40 UTC, twenty minutes before the milestone trigger, so the trigger's probe
of `registry-proof.html` sees the day's file. The script exports, checks what it
exported, and installs only a file that replays; a failed check leaves the web
root holding yesterday's numbers rather than wrong ones.

`registry-proof.py` is served as `application/octet-stream` because nginx has no
type for `.py`. The `curl -sO` on the page works; a browser click downloads the
file instead of showing it. Fixing that is an nginx edit and has not been made.

## 2026-10-03 — `~/bin/deploy.sh` accepts `diagnose`

Not repo-managed, so it is recorded here. `~/bin/deploy.sh` gained one branch
immediately after argument validation:

```bash
if [ "$SHA" = diagnose ]; then
  [ "$REPO" = moltrust-api ] || { echo "diagnose runs in moltrust-api only" >&2; exit 2; }
  exec /home/moltstack/moltstack/venv/bin/python \
       /home/moltstack/moltstack/ops/diagnose.py
fi
```

It returns before the lock, before any git command, and before anything is
installed or restarted. Backup of the previous file:
`~/bin/deploy.sh.bak-2026-10-03`.

**Why it has to live there.** The deploy key carries a forced command, so the
workflow cannot run an arbitrary command over SSH — which is the property worth
keeping. A read-only question therefore has to be a known argument to the one
script the key may run, and the branch is the narrowest shape that allows it:
one literal word, one script, no write path reachable from it.

The script itself is repo-managed (`ops/diagnose.py`) and deploys normally, so
what the branch *does* is under review even though the branch is not.

## 2026-10-03 — `~/bin/deploy.sh` accepts `supervise` and `superheal`

Second entry for the same non-repo file (see the `diagnose` note above). Two
more literal arguments, inserted directly after the `diagnose` branch and
before the sha validation, so both return before the lock, the fetch and the
install:

```bash
if [ "$SHA" = supervise ] || [ "$SHA" = superheal ]; then
  [ "$REPO" = moltrust-api ] || { echo "supervise runs in moltrust-api only" >&2; exit 2; }
  if [ "$SHA" = supervise ]; then
    exec /home/moltstack/moltstack/ops/supervise.sh check
  fi
  exec /home/moltstack/moltstack/ops/supervise.sh heal
fi
```

Backup: `~/bin/deploy.sh.bak-2026-10-03b`. `supervise` is read-only and prints
JSON; `superheal` may run the positive list in `scripts/selfheal.py` and
nothing else. Both scripts are repo-managed, so what the branch *does* is under
review even though the branch itself is not.

## 2026-10-03 — two cron entries for the supervision suite

```
0 5 * * 0   pytest tests/failure_paths.py   → logs/failure_paths.log
50 6 * * 0  scripts/supervision_report.py --send
```

Sunday 05:00 is before every other Sunday job, so a broken alarm path is known
before the reports that depend on it run. 06:50 sits ten minutes ahead of the
07:00 KPI report from `daily_stats.sh`, which is the report this one is
"in addition to".

`ops/crontab.txt` in this repo is a record from 2026-07-21 and has been stale
since; it is not the source of truth and was not edited. The live crontab is.

## 2026-10-03 — the deploy lock leaves a note, and the checkout has an owner

Two more changes to the non-repo `~/bin/deploy.sh` (backup:
`~/bin/deploy.sh.bak-2026-10-03c`):

- `flock -n` first, so a waiting run logs **who** it is waiting for before it
  blocks for up to thirty minutes.
- `$LOCK.info` beside the lock with `pid`, `started`, `repo`, `sha` and `owner`.
  Written after the lock is held, cleared by a trap on the way out including on
  failure, and a note left by a process that no longer exists is logged before
  being overwritten.

New server files, both outside the repo by nature:

- `/home/moltstack/.checkout_owner` — JSON naming the owning session, the rule,
  and honestly what does *not* enforce it (a shell login).
- `/home/moltstack/.checkout_owner.name` — one line, read by `deploy.sh` for the
  `owner=` field in the lock note.

The hourly half is repo-managed: `agents/supervision.py::check_checkout`.

## 2026-10-03 — two checks the diagnosis cannot make (known gap)

`ops/diagnose.py` and `agents/supervision.py` run with `MOLTYCEL_GH_TOKEN`, a
fine-grained PAT. Two reads it is not entitled to, both answering **403
`Resource not accessible by personal access token`**:

| endpoint | question it would answer | where to look instead |
|---|---|---|
| `GET /repos/:o/:r/actions/permissions` | is Actions restricted at repo level | Settings → Actions → General |
| `GET /notifications` | did GitHub mail about disabling scheduled workflows | Lars's mailbox |

**Deliberately not widened.** The token is cut narrow, both questions are
answerable through the interface in under a minute, and neither is asked often
enough to justify a broader credential sitting on the server for months. The
cost of the gap is that a workflow diagnosis cannot rule those two out on its
own — so it says so rather than reporting a clean sweep.

Established while diagnosing the missing `:17` schedule ticks on 03.10, where
the cause turned out to be neither: the schedule was syntactically correct, both
workflows `active`, the repo public and not archived, and one scheduled run had
fired at 16:17 UTC and failed at `Fail on red` by design.

## 2026-10-03 — `~/bin/deploy.sh` takes a third argument: the run's origin

Backup: `~/bin/deploy.sh.bak-2026-10-03d`. Two arguments for a deploy, three for
`supervise`/`superheal`, and the third is whitelisted rather than passed
through:

```bash
case "$ORIGIN" in
  workflow:[0-9]*|dispatch:[0-9]*|cron:[A-Za-z0-9._-]*) : ;;
  local:*@*) : ;;
  *) ORIGIN=unknown ;;
esac
```

An argument, not an environment variable, because the forced command lets no
environment across the SSH boundary — which is the property worth keeping. The
string reaches a script argument, so anything outside the four shapes becomes
`unknown`, and `unknown` is a fault state the collected report names.

## 2026-10-05 — `~/bin/deploy.sh` verifies the versioned paths after a deploy

Backup: `~/bin/deploy.sh.bak-20261005T104650Z`. A new `verify_versions()` runs
in the success branch, after `record "$SHA" ok`, for `moltrust-api` only:

```bash
out=$(cd "$API_DIR" && PYTHONPATH="$API_DIR" ./venv/bin/python \
      scripts/deploy_verify.py --alert --sha "$SHA" 2>&1)
```

Three properties, each for a reason:

- **Never fatal, never a rollback trigger.** It returns 0 whatever it finds. A
  mismatch means the commit is incomplete — on 05.10. a file the prompt needed
  was not on main — and rolling back to the previous commit does not fix that.
  Rolling back a deploy because the *check* was unhappy would also make the
  check the most dangerous thing in the path.
- **Guarded by `[ -f … ]`.** A commit from before this script existed deploys
  normally and logs `script not in this commit, skipped`, so the check cannot
  break a rollback onto an older SHA.
- **`--alert` sends its own single message.** Deliberately outside the
  twice-daily collected report and not a fourth entry in
  `supervision.IMMEDIATE`: this fires once per deploy and only when the deploy
  did not take. A silent fallback is indistinguishable from health in every
  other signal, and twelve hours of drafting under the wrong rule cannot be
  undone.

What it compares is the **effective artefact**, not the version string. On
2026-10-05 the radar logged `prompt v3-2026-10-05` and drafted under the old
rule: `scripts/citation_index.py` was not on main, `citation_block()` returned
`""` as designed, and `draft_reply` dropped the source rule — correctly, because
a source rule pointing at an empty index turns every candidate into a SKIP. The
version was right. The log line was right. Every other signal was green.

The four paths and their floors live in `config/versioned_paths.yaml`; each one
has a fallback, and each fallback can take over without a word.

## 2026-10-05 — `~/bin/deploy.sh` refuses a page with an internal pre-publish note

Backup: `~/bin/deploy.sh.bak-20261005T111739Z`. `deploy_web()` runs
`moltrust-web/scripts/predeploy_gate.py` after the checkout and **before any
install**, and returns 1 when it refuses, so nothing reaches the web root.
Guarded by `[ -f … ]`, so a commit from before the script existed deploys and
logs `script not in this commit, skipped`.

The index/feed coupling runs after the per-path probes, with `--live`, and is
**reported only**. The index is regenerated by a root cron every 15 minutes, so
immediately after an install it legitimately lags the new page; a fatal check
there would fail correct deploys for a quarter of an hour.

Why it exists: on 2026-10-05 three served blog pages carried a
`Verification status` block. `moltproof-announcement.html` carried two
`UNVERIFIED` entries whose own text said the claims "must be verified (or
softened) before this post is published", and the page had been live since
2026-07-09 from a commit titled *"moltproof: website preview (draft — do not
deploy until service live)"*. The note travelled with the page instead of
stopping it. Rule: `website-deploy.md` 4.1a3.

Proven before wiring: run against the unmodified `main` content the gate names
all three pages with line numbers and exits 1; the deploy of 2954160 logged
`predeploy gate: ok` and `gate: ok   index/feed coupling`.

Also on this day, the evergreen cron (`syndicate.py --evergreen`, Tue/Thu 09:00
UTC) was commented out with a dated reason while the pages were cleaned, and
restored afterwards. Backup of the crontab: `~/crontab.bak-20261005T*`.

## 2026-10-05 — GitHub token: `moltycel-console-2026-10`, one key under one name

| | |
|---|---|
| Name | `moltycel-console-2026-10` (fine-grained PAT) |
| Variable | `MOLTYCEL_GH_TOKEN` in `~/.moltrust_secrets`, nothing else |
| Issued | 2026-10-05 · **expires 2027-01-03** (90 days) |
| Resolved through | `app/gh.py` → `gh.token()`, read at call time |

**Deleted the same day, do not look for them:** `moltstack-console-2026-08` and
`moltycel-console-2026-09`. `GH_TOKEN` is gone from the secrets as a variable,
and no code reads a second name — `tests/failure_paths.py` asserts it.

The old `GH_TOKEN` was found in clear text at mode 664 inside the doc mirror's
`.git/config`, on a host with a second human account, still carrying push and
admin on both private repos. It was not put there by hand: `ensure_web_docs`
wrote the current token back into the remote URL on every refresh.

**Verified reach** (live, `GET /repos/MoltyCel/<name>`, all 200):

`moltrust-api` · `moltrust-web` *(the only private one)* · `moltguard` ·
`moltrust-mcp-server` · `moltrust-x402` · `moltrust-openclaw` ·
`moltrust-sdk` · `moltrust-protocol` · `openclaw-skills` ·
`status.moltrust.ch` · `moltrust-vet`

Ten of eleven are public, so the scope binds at exactly one place and that one
is covered. `moltrust-vet` was in the old scope and is not in the new one; it
needs none — it is public, and no path addresses it through a token at all
(`funnel_test.py` hands its URL to our own scan endpoint, `proof_post.py` reads
ClawHub).

Workflow dispatch answers **422** on a nonexistent ref, so `actions: write` is
present. `GET /repos/<r>/traffic/*` answers **403** and stays that way by
decision: traffic figures carry no decision, and a right that feeds one side
measurement does not belong in a token that may write six repos. The snapshot
records that as `*_14d_basis: "unreadable"` from now on.

Known gaps, deliberately not closed: `GET /notifications` (403) and the
Actions-permissions read. Both were named as gaps on 2026-10-04 and are still
gaps on purpose — the token is cut narrow.

## 2026-10-08 — syndicate armed in the crontab

Server-infra change, outside §11 (the live crontab is not repo-managed), recorded
here as the audit entry.

- **What changed:** two crontab lines, nothing else. `SYNDICATE_ARMED=1` now
  precedes the python call in the `*/30` new-post line and the `0 9 * * 2,4`
  evergreen line. Backup before the edit:
  `/home/moltstack/crontab.bak.20261008T041922Z`.
- **Why:** standing approval from Lars on 2026-10-08 for blog syndication (P2).
  The code side was live first: #650 (post only when armed, otherwise a draft to
  Telegram) and #652 (48 h freshness, 2 per run / 4 per UTC day, intact state,
  open X breaker).
- **Not in `~/.moltrust_secrets`:** checked, 0 occurrences. `armed()` treats the
  variable as unset if it ever appears there.
- **`ops/crontab.txt` not updated in this change:** the file is held by the open
  PR #351. The snapshot lags the live crontab on these two lines until that PR
  is merged or the snapshot is refreshed separately.


## 2026-10-08 — TrouvArt scan and backup switched off in the crontab

- **What changed:** two crontab lines commented out, not deleted, each with a
  dated reason line above it: the daily scan at 02:00
  (`trouvart/run_scan.sh`) and the Sunday backup at 04:00
  (`trouvart/scripts/backup.sh`). Backup before the edit:
  `/home/moltstack/crontab.bak.20261008T210134Z`. `ops/crontab.txt` equals the
  live crontab again in the same change.
- **Why:** decision by Lars (AS1). Since 2026-05-03 the OOM killer ended the
  feed step `gen_feed.py` 159 times, the feeds in the web root have not been
  renewed since 2026-03-21, and they had no external request in 14 days of
  logs. On alternate nights the same 02:00 memory peak took
  `moltrust-mcp-http` down instead.
- **Kept:** `~/trouvart`, its SQLite file, the three feeds in the web root and
  `trouvart/index.html`. The decision about removing them for good is due on
  2026-11-07 (moltrust-web `docs/cleanup-list.md`).
- **Check:** the night of 2026-10-09 is the counter-test — whether the OOM
  killer ran, and how large `moltrust-mcp-http` was at 02:00.
