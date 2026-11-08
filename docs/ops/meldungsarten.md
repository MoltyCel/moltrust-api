# Meldungsarten: HANDELN oder ABLAGE

Generated from `app/meldungsarten.py` (ops NOTIFY-4 criterion 2, MELD-1
criterion 2). Do not edit by hand: change the table in the module and run
`python -m app.meldungsarten > docs/ops/meldungsarten.md`.

HANDELN reaches Lars on Telegram, at most four lines; `buendel` goes into
the one decision message of the day (08:00 UTC). ABLAGE is not sent and is
written to `~/reports/<date>.md`. A first line `Lars muss: nichts` makes
any message ABLAGE.

| Art | Klasse | Zustellung | Grund |
|---|---|---|---|
| `alte-wallet-eingang` | HANDELN | sofort, Ausnahme sicherheit | Auf der abgeloesten, offengelegten Wallet ist etwas eingegangen. |
| `ambassador-ablesung` | HANDELN | buendel | Die Ablesung ist die Grundlage fuer Lars' Entscheidung ueber den Botschafter. |
| `auszahlung-stoerung` | HANDELN | sofort, Ausnahme geld | Eine Auszahlung ist begonnen und nicht abgeschlossen oder der Bestand reicht nicht; nachladen oder pruefen muss Lars. |
| `auszahlung-vorschlag` | HANDELN | sofort, Ausnahme geld | Eine Auszahlung wartet auf Lars' Freigabe; die Antwort laeuft ueber die Nachricht. |
| `befehl-antwort` | HANDELN | sofort | Antwort auf einen Befehl oder eine Antwort, die Lars eben geschickt hat; ohne sie weiss er nicht, ob sie gewirkt hat. |
| `content-scout-lead` | HANDELN | buendel | Ein Anknuepfungspunkt in einem fremden Thread; Lars entscheidet, ob wir antworten. |
| `discovery-thread` | HANDELN | buendel | Ein fremder Thread mit Empfehlung; Lars entscheidet, ob wir antworten. |
| `endpunkt-ausfall` | HANDELN | sofort, Ausnahme ausfall | Ein oeffentlicher Endpunkt antwortet nicht. |
| `entscheidungs-buendel` | HANDELN | sofort | Die eine Entscheidungsnachricht des Tages; je Punkt eine Zeile (app/entscheidungen.py). |
| `fehlerpfad-wache` | HANDELN | sofort, Ausnahme ausfall | Ein deklarierter Alarm feuert nicht mehr; ohne ihn bleibt ein Ausfall stumm. |
| `finanz-ausloeser` | HANDELN | sofort, Ausnahme geld | Ein Finanz-Ausloeser ist erreicht; der Schritt gehoert Lars. |
| `frist-vorwarnung` | HANDELN | sofort | Eine Frist laeuft binnen 24 Stunden ab und verlangt eine Entscheidung von Lars. |
| `geld-bewegung` | HANDELN | sofort, Ausnahme geld | Geld ist auf einer verwalteten Wallet eingetroffen und verlangt einen naechsten Schritt. |
| `guthaben-niedrig` | HANDELN | sofort, Ausnahme geld | Ein bezahlter Dienst laeuft leer oder antwortet nicht mehr; aufladen kann nur Lars. |
| `handlauf` | HANDELN | sofort | Eine Console meldet aus einem Handlauf einen Schritt fuer Lars. |
| `inhalt-entscheidung` | HANDELN | buendel | Ein Beitrag haengt fest (zurueckgestellt, ohne Aufhaenger, nicht im Feed); Lars entscheidet, ob er bleibt. |
| `integritaet-abweichung` | HANDELN | sofort, Ausnahme sicherheit | Ein verankerter Datensatz weicht von der Kette ab; das kann ein Eingriff sein. |
| `journal-erinnerung` | HANDELN | sofort | Das Wochenjournal fasst Lars' eigene Chats zusammen; das kann nur er. |
| `linkedin-entwurf` | HANDELN | sofort | Lars postet den Beitrag auf LinkedIn selbst, die API darf es nicht. |
| `linkedin-zahlen` | HANDELN | sofort | Die Zahlen stehen nur in Lars' LinkedIn-Analyse; er traegt sie nach. |
| `listwatch-treffer` | HANDELN | sofort | Ein Listenbeitrag antwortet Lars oder nennt MolTrust, AAE oder den Draft; Lars entscheidet, ob er antwortet, solange der Faden laeuft (MELD-LIST). |
| `llm-gateway-ausfall` | HANDELN | sofort, Ausnahme ausfall | Claude antwortet nicht; Status oder Zahlungsstand pruefen kann nur Lars. |
| `milestone-entwurf` | HANDELN | sofort | Ein Meilenstein-Post geht nicht mehr autonom raus; Lars postet ihn von Hand. |
| `moltbook-entwurf` | HANDELN | sofort | Ein Moltbook-Antwortentwurf mit Senden und Verwerfen; Lars entscheidet in der Nachricht. |
| `rechtsbuendel` | HANDELN | sofort, Ausnahme geld | Vor der naechsten Einzahlung muss Lars entscheiden, ob die Bedingungen angenommen werden. |
| `reply-entwurf` | HANDELN | sofort | Lars postet die Antwort auf X selbst, der Entwurf verfaellt nach Stunden. |
| `reply-leseliste` | HANDELN | sofort | Die Leseliste, aus der Lars von Hand antwortet. |
| `reply-rueckfrage` | HANDELN | sofort | Der Radar fragt Lars nach dem Grund einer Ablehnung; die Antwort laeuft ueber die Nachricht. |
| `reseller-antrag` | HANDELN | buendel | Ein Reseller-Antrag wartet auf Annahme oder Ablehnung. |
| `root-einzeiler` | HANDELN | sofort, Ausnahme root | Ein Befehl braucht sudo oder root; ausfuehren kann nur Lars. |
| `selftest-ausnahme` | HANDELN | sofort | Ein Befund unter Geld, Sicherheitsvorfall oder Ausfall (agents/supervision.py). |
| `selftest-sammelmeldung` | HANDELN | sofort | Nur wenn ihre erste Zeile einen Handgriff nennt; sonst ist sie ABLAGE. |
| `sicherheit-handgriff` | HANDELN | sofort, Ausnahme sicherheit | Der Sicherheitsbericht nennt einen Punkt, den nur Lars entscheiden oder tun kann (SEC-1101). |
| `social-entwurf` | HANDELN | sofort | Ein Beitrag ist nicht automatisch rausgegangen und liegt bei Lars zum Posten. |
| `tagesbericht` | HANDELN | sofort | Nur wenn seine erste Zeile einen Handgriff nennt; sonst ist er ABLAGE. |
| `testwallet-tx` | HANDELN | sofort, Ausnahme geld | Pflicht aus CLAUDE.md: jede Transaktion der Testwallet mit Hash, Betrag und Zweck. |
| `threadwatch-stillstand` | HANDELN | sofort | Der Moltbook-Botschafter steht; Lars bittet einen Console-Tab, ihn anzusehen. |
| `usdc-poller-ausfall` | HANDELN | sofort, Ausnahme ausfall | Zahlungseingaenge werden nicht gutgeschrieben, solange der Poller steht. |
| `zugang-abgelaufen` | HANDELN | sofort | Ein Zugang laeuft ab oder ist abgelaufen; erneuern kann nur Lars im Browser. |
| `zustellprobe` | HANDELN | sofort | Die Wache c-notify-kann-melden geht den Sendeweg bis vor den Transport; gesendet wird nichts. |
| `auto-repair` | ABLAGE |  | Kandidaten fuer Weiterleitungen; die Reparatur ist Console-Arbeit. |
| `beobachtung` | ABLAGE |  | Eine Wache hat etwas gesehen, das niemand ausloesen muss (Web-Root-Schreibvorgang, abgewiesene Anfragen, Escrow-Lage). |
| `budget-meldung` | ABLAGE |  | Ein Agent eines Betreibers naehert sich seinem Ausgabendeckel; den Rest regelt der Deckel. |
| `deploy` | ABLAGE |  | Ausgang eines Deploys; ein gescheiterter rollt selbst zurueck und gehoert der Console. |
| `dsgvo-retention` | ABLAGE |  | Retention-Laeufe stehen im Tagesbericht. |
| `entwurf-blockiert` | ABLAGE |  | Das Voice-Gate hat einen Entwurf gesperrt; gepostet wird nichts. |
| `messbericht` | ABLAGE |  | Zahlen, Kennzahlen, Wochenberichte. |
| `moltbook-wachhund` | ABLAGE |  | Stehender Befund, als Gate-Punkt in Issue #1 gemeldet. |
| `pipeline-bericht` | ABLAGE |  | Ein Lauf berichtet, was er getan hat. |
| `pipeline-fehler` | ABLAGE |  | Ein Lauf ist gescheitert, angehalten oder blockiert; das arbeitet die Console ab. |
| `scout-bericht` | ABLAGE |  | Fundstellen und Zusammenfassungen von Scouts, Listenwachen und Review-Laeufen. |
| `selftest-befund` | ABLAGE |  | Befunde des Selbsttests arbeiten die Consoles ab. |
| `usdc-eingang` | ABLAGE |  | Ein Zahlungseingang ist gutgeschrieben; zu tun ist nichts. |
| `watchdog-befund` | ABLAGE |  | Stille einer Pipeline oder Rueckkehr in Ordnung; das arbeiten die Consoles ab. Ausfaelle oeffentlicher Endpunkte meldet endpoint_probe. |

Messages without an art (senders outside the repository) are matched
on their first line:

- `^Claude ist gerade nicht erreichbar` -> `llm-gateway-ausfall`
- `^x402-Test\b|Testwallet` -> `testwallet-tx`
- `^MolTrust deploy\b` -> `deploy`
- `^TEST\b` -> `handlauf`
- `^Lars muss: (?!nichts)` -> `handlauf`
