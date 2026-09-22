# Interne Defekte — gefunden von uns, ohne Bounty

Was hier steht, hat die Console oder Lars selbst gefunden. **Kein Anspruch auf
das Defekt-Bounty** — der Topf ist für Meldungen von außen da, und eine eigene
Zeile daraus zu bezahlen wäre eine Umbuchung, keine Prämie.

Der Zweck ist ein anderer: ein Fehler, der einmal durchgerutscht ist, rutscht
in derselben Form gern ein zweites Mal durch. Jeder Eintrag nennt deshalb nicht
nur den Defekt, sondern auch, was ihn künftig rot werden lässt.

Regeln für einen Eintrag: Datum des Fundes, was falsch war, wie es bemerkt
wurde, wo es behoben ist, und die Prüfung, die es beim nächsten Mal abfängt.
Ein Eintrag ohne diese letzte Zeile ist ein offener Posten.

---

## 2026-09-22 — Ein Rate-Limit der Base-RPC wurde als „Agent existiert nicht" gemeldet

**Was falsch war.** `/resolve/erc8004/{id}` fing in `resolve_onchain_agent`
jede Ausnahme aus `ownerOf(...)` ab und machte daraus
`Agent ID N not found on Base IdentityRegistry` — 404. `BASE_RPC` ist der
öffentliche Endpunkt `mainnet.base.org` und drosselt. Sechs Aufrufe
hintereinander auf Agent 21351, seit der Registrierung on-chain und mit Owner
`0x3802…`, ergaben `200 404 404 200 404 404`. Die 404er waren
`429 Too Many Requests`, verkleidet als Befund über den Agenten.

**Warum das dieselbe Familie ist.** Am selben Tag ging es um MCP-Tools, die
`withheld` als „not found" ausgaben. Hier liegt es eine Schicht tiefer und ist
dieselbe Verwechslung: fehlende Auskunft als geprüftes Negativ. Wer die Antwort
liest, hat keinen Anhaltspunkt, dass niemand nachgesehen hat.

**Wie es auffiel.** Durch die neue Montagsprobe, in ihrer ersten Minute. Sie
meldete `moltrust_erc8004` als Renderfehler, obwohl die API zehn Sekunden zuvor
für dieselbe ID `withheld` geliefert hatte. Der Widerspruch war der Hinweis.

**Behoben.** `_is_transport_error()` trennt eine Antwort der Kette (Revert,
nicht existierender Token → 404, `absent`) von einem nicht erreichten Knoten
(429, Timeout, 5xx → **503** mit `Retry-After`, `unavailable`). Zwei Versuche
mit kurzer Pause fangen die Spitze ab; ein dritter wäre Drängeln an einem
Endpunkt, der schon abgelehnt hat. Ein Revert wird nicht wiederholt.

**Was es künftig abfängt.** `tests/test_erc8004_rpc_absence.py` — 13 Tests,
darunter die sechs Transport-Formen, drei Vertragsantworten, und der Nachweis,
dass ein Revert genau einen Aufruf kostet. Im CI-Job `unit-tests`, also
ausgeführt und nicht nur importiert.

**Offen für Lars:** das ist ein Pflaster auf einem öffentlichen RPC ohne
Schlüssel. Zwei Versuche verschieben die Schwelle, sie beseitigen sie nicht.
Ein eigener Base-RPC-Endpunkt (Alchemy, QuickNode oder CDP) wäre die
Abstellung — Secret, also deine Entscheidung.

**Kein Bounty.** Eigener Fund, eigener Fix.

---

## 2026-09-22 — `mt_get_trust_score` nannte bei `withheld` einen erfundenen Grund

**Was falsch war.** Das Tool rannte bei jedem zurückgehaltenen Score dieselbe
Begründung aus: *„Score: WITHHELD (fewer than 3 independent endorsers)"*. Bei
einer fremden DID-Methode stimmt das nicht — die API schickt
`withheld_reason: did_method_not_issued_here` und dazu eine Notiz, die
ausdrücklich sagt, dass ein zurückgehaltener Score kein niedriger Score ist.
Das Tool las beide Felder nicht und setzte an ihre Stelle eine Behauptung über
die Zahl der Endorser, die niemand geprüft hatte.

**Der unangenehme Teil.** Die drei Nachbartools (`moltrust_verify`,
`mt_get_badge`, `moltrust_erc8004`) warfen `withheld` schlicht weg — ein
sichtbarer Verlust. Dieses hier sagte `withheld` und klang deshalb richtig.
Ein Modell, das die Antwort liest, hat keinen Anlass, an der Begründung zu
zweifeln.

**Wie es auffiel.** Beim Nachprüfen des 1.2.3-Deploys am gehosteten Endpunkt,
nicht durch einen Test. Gesucht war etwas anderes: ob die DID-Formprüfung
greift.

**Behoben in 1.2.4** (`629af13`, veröffentlicht 22.09.). Ein Renderer für alle
vier Tools; der Grund der API gewinnt, der Fallback des Aufrufers füllt nur ein
nacktes Flag, und die Notiz wird wörtlich übernommen.

**Was es künftig abfängt.**
`moltrust-mcp-server/tests/test_withheld_states.py` — 20 Tests, darunter ein
Vektortest über Antworten, die von der Live-API aufgenommen sind, und
ausdrücklich der Fall *sagt withheld, nennt aber den falschen Grund*. Dazu die
Montagsprobe `check_withheld_rendering()` im Watchdog, die dieselbe Frage am
laufenden Origin stellt — weil ein veraltetes venv am Endpunkt keinen einzigen
Test rot macht.

**Kein Bounty.** Eigener Fund, eigener Fix.
