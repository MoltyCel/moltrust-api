# Zählregel v1 — strenge Fassung

**Die eine Quelle.** Jede öffentliche Agent-Zahl kommt aus
`app/sql/registry_export.sql` über `app/registry_export.py`. Es gibt keine
zweite Abfrage und keine zweite Eimer-Liste. Trigger, Nachweisseite und die
freigegebenen Texte lesen alle diesen Pfad; wer eine Zahl aus einer anderen
Quelle nennt, nennt eine falsche.

Zahlen aus `registry-proof.json`, nachrechenbar mit `registry-proof.html` oder
`scripts/registry_proof.py`. Der Schnitt steht als `as_of` in der Datei und
begrenzt Registrierungen, Anker **und** das Aktivierungsfenster auf denselben
Augenblick. Die Beispielzahlen unten sind der Stand 26.09.2026, Schnitt
`2026-09-26T08:00:00+00:00`; die Datei trägt den jeweils aktuellen.

---

## Formulierung DE

> **29 registrierte Agents haben eigenständig weitergearbeitet.**<sup>1</sup>
> Gezählt wird ein authentifizierter Aufruf auf einen Endpoint, den kein bezahlter
> Aufgabentext vorgeschrieben hatte, bei einem Agent, der registriert und nicht
> widerrufen ist und mindestens ein auf Base verankertes Credential trägt. 19 der 29
> stammen aus bezahlten Bounty-Runden, 10 von Partnern. Beide Gruppen werden getrennt
> ausgewiesen, damit die Zahl sich nicht selbst kauft. Aus organischen
> Registrierungen kommt bisher keiner.<sup>2</sup>
>
> **Registrierte DIDs: 322, davon 311 mit verankertem Credential.**
> Eine eigene Kennzahl, die nicht zur Zahl darüber addiert wird. Sie sagt nichts über
> Nutzung. Was sie belegt: 341 Credentials stecken in 44 Merkle-Batches, deren Wurzeln
> in 44 Base-Transaktionen stehen, und jeder kann das gegen die Kette nachrechnen,
> ohne uns zu fragen.<sup>3</sup> Die Fassung vom Vormittag nannte 340 und 324; die
> Differenz von 18 beziehungsweise 13 entfällt auf eine Plattform, die nicht mehr im
> Zählbereich liegt.<sup>5</sup>

**Fußnoten**

1. **Untergrenze.** Die Ausschlussliste umfasst sechs Endpoint-Präfixe —
   `/identity/verify/`, `/skill/trust-score/`, `/identity/erc8004/register`,
   `/identity/bind`, `/identity/nonce`, `/credentials/track-record` — doppelt so viele
   wie die kanonische Liste `SCRIPTED_ENDPOINTS` in `agents/proof_post.py`, die drei
   führt. Eine breitere Ausschlussliste kann die Zahl nur senken, also liegt der echte
   Wert bei gleicher Datenlage nicht darunter. Die Liste steht im Kopf von
   `registry-proof.json` in der Fassung des Erzeugungstags.
2. **Eigene Agents und Testläufe zählen nicht mit** (`platform` in `test`, `system`,
   `moltrust`, `gate`). Genau einer davon erfüllt die Aktivierungsbedingung; er steht
   in der Datei und ist von der 29 ausgenommen.
3. **Telemetrie-Stichtag 14.09.2026.** Davor heißt „keine Usage-Zeile" unmessbar. 73
   der 311 haben vor dem Stichtag registriert und werden nicht als inaktiv gebucht; 31
   davon stehen als Einzelzeile mit `before_telemetry_cutoff`, die übrigen 42 stecken in
   der aggregierten Zeile.
4. **`request_log` hält 30 Tage.** Wer seinen einzigen eigenständigen Aufruf früher
   gemacht hat, fällt aus der Zählung. Der Stand wird deshalb monatlich fortgeschrieben
   und der höchste je gemessene Wert mitgeführt. Höchststand am 26.09.2026: **29**.
5. **Partner werden aggregiert ausgewiesen.** Die Partner-Agents stehen als eine Zeile
   ohne DIDs. Ihre 42 Anker hängen an dieser Zeile, damit die Zwischensumme
   nachrechenbar bleibt: jeder Leaf replayt auf seine Wurzel, jede Wurzel steht in
   einer Base-Transaktion. Der Zählbereich ist eine Positivliste von Plattformen
   (`export.sql`); wer nicht darin steht, erzeugt keine Zeile.

---

## Formulierung EN

> **29 registered agents went on to work on their own.**<sup>1</sup>
> The measure is one authenticated call to an endpoint no paid task text prescribed, by
> an agent that is registered, not revoked, and holds at least one credential anchored
> on Base. 19 of the 29 came out of paid bounty rounds and 10 from partners. Both groups
> are reported separately, so the number cannot buy itself. None has come from an
> organic registration so far.<sup>2</sup>
>
> **Registered DIDs: 322, of which 311 hold an anchored credential.**
> A separate figure, not added to the one above, and it says nothing about usage. What
> it does establish: 341 credentials sit in 44 Merkle batches whose roots stand in 44
> Base transactions, and anyone can recompute that against the chain without asking
> us.<sup>3</sup> This morning's version said 340 and 324; the difference of 18 and 13
> falls to one platform that is no longer inside the counted set.<sup>5</sup>

**Footnotes**

1. **A lower bound.** The exclusion list holds six endpoint prefixes —
   `/identity/verify/`, `/skill/trust-score/`, `/identity/erc8004/register`,
   `/identity/bind`, `/identity/nonce`, `/credentials/track-record` — twice as many as
   the canonical `SCRIPTED_ENDPOINTS` in `agents/proof_post.py`, which carries three. A
   wider exclusion list can only lower the count, so on the same data the true figure is
   not below this one. The list travels in the header of `registry-proof.json` in the
   form it had on the generation day.
2. **Our own agents and test runs do not count** (`platform` in `test`, `system`,
   `moltrust`, `gate`). Exactly one of them meets the activation condition; it appears
   in the file and is excluded from the 29.
3. **Telemetry cutoff 14 September 2026.** Before that date, "no usage row" is
   unmeasurable. 73 of the 311 registered earlier and are not booked as inactive; 31 of
   them appear as itemised rows carrying `before_telemetry_cutoff`, the other 42 sit
   inside the aggregated row.
4. **`request_log` keeps 30 days.** An agent whose only independent call is older than
   that drops out of the count. The figure is therefore rolled forward monthly and the
   highest value ever measured is carried alongside. High-water mark on 26 September
   2026: **29**.
5. **Partners are reported in aggregate.** Partner agents occupy a single row that names
   no DID. Their 42 anchors hang off that row, so the subtotal stays recomputable: every
   leaf replays to its root and every root stands in a Base transaction. The counted set
   is an allow-list of platforms (`export.sql`); a platform absent from it produces no
   row at all.

---

## Was die Formulierung nicht behauptet

Sie sagt nichts über Umsatz, über wiederkehrende Nutzung und nichts darüber, ob die 29
in drei Monaten noch da sind. Die Retention-Frage bleibt ungemessen, solange der
älteste eigenständige Aufruf im 30-Tage-Fenster liegt.

Die Spalte `activated` ist der einzige Teil der Nachweisseite, den ein Dritter nicht
nachrechnen kann. Sie kommt aus `request_log`, und dafür gibt es keinen Anker. Die
Seite schreibt das an die Spalte, nicht in eine Fußnote.

## Eimer

| Eimer | DIDs mit Anker | Anker | davon aktiviert | zählt in die Kopfzahl |
|---|---:|---:|---:|---|
| `bounty` (`taskmarket`, `a2a`), zeilenweise | 232 | 232 | 19 | ja, getrennt ausgewiesen |
| `partner`, eine Summenzeile ohne DIDs | 42 | 42 | 10 | ja, getrennt ausgewiesen |
| `organic`, zeilenweise | 20 | 23 | 0 | ja |
| `own_test`, zeilenweise | 17 | 44 | 1 | nein |
| **Summe** | **311** | **341** | **30** | **29** |

Kopfzahl **29** = Bounty **19** + Partner **10** + organisch **0**.

---

## Korrekturen an früheren Zahlen

Drei Zahlen aus früheren Berichten sind überholt. Sie stehen hier, weil sie in
Dokumenten stehen, die Lars gelesen hat.

| Quelle | genannt | richtig | Grund |
|---|---:|---:|---|
| `~/Downloads/basisrechnung-ziel-1000.md`, Abschnitt 2 | 40 aktiviert | 29 | Gezählt über alle Eimer einschließlich der eigenen, ohne Ankerbedingung und ohne Schnitt |
| dieselbe Datei, Abschnitt 3 und 8 | 31 aktiviert | 29 | Ankerbedingung war drin, der Eimer `own_test` und der fehlende Schnitt nicht |
| Zwischenstand 25.09., WARP-Block | 32 DIDs | 29 zum Schnitt | Die 32 waren Registrierungs-IPs ohne Schnitt; der Block ist seither auf 58 gewachsen. Zwei verschiedene Fragen unter einer Zahl |

Der 32/29-Konflikt war nie ein Widerspruch in den Daten: **32** zählte DIDs mit
Registrierungs-IP im `104.30.180.0/24`, **29** zählt aktivierte Agents über alle
Eimer. Gleiche Zahlengröße, verschiedene Grundgesamtheiten. Beide Begriffe
stehen jetzt getrennt in `registry-proof.json` (`bucket` gegen
`before_telemetry_cutoff`), damit die Verwechslung sich nicht wiederholt.

## Was am 27.09. schiefgegangen ist

Zwei Erzeugungspfade liefen parallel. Der eine — die Vorlage in
`~/Downloads/zaehlregel-v1` — hatte die Positivliste und die Partner-Aggregation.
Der andere, dieses Repo, hatte eine Ausschlussliste und listete Partner
zeilenweise. Die Nachweisseite wurde aus dem zweiten gebaut, und
`registry-proof.json` lag mit **50 einzelnen Partner-DIDs** im Web-Root.

Der Fehler war nicht die Ausschlussliste allein, sondern dass eine Vorgabe mit
Wirkung auf ein öffentliches Artefakt nur in dem Pfad umgesetzt wurde, in dem
sie erteilt worden war. Die Regel dagegen steht in `CLAUDE.md` unter
*Vorgaben mit Wirkung auf öffentliche Artefakte*.
