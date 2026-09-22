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
