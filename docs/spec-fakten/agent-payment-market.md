# Agentenzahlungen — Marktgröße, Stichtagsmessung

**Status:** ⏱ **STICHTAGSMESSUNG**, kein Dauerzustand · **Stichtag:** 2026-10-05
**Fenster:** 2026-09-05T14:48:51Z bis 2026-10-05T14:48:51Z (Base Block
50.915.192–52.211.192) · **Methode:** `docs/spec-fakten/`-Konvention (a)/(b)/(c)

> ## Bei Wiederverwendung neu messen
>
> Dieses Dokument ist eine Momentaufnahme und altert schnell. Jede Zahl darin
> gilt für dreißig Tage, die am 05.10.2026 endeten. Wer eine Zahl hier
> herausnimmt, nennt den Stichtag mit oder misst neu.
>
> **Was besonders schnell verfällt:**
>
> | Angabe | warum sie verfällt |
> |---|---|
> | Katalogstände und Aufrufzähler | der CDP-Katalog wuchs während der Messung um etwa neun Einträge je Minute |
> | `l30Days*`-Zähler | rollendes 30-Tage-Fenster, dessen Beginn CDP nicht benennt |
> | Settlement-Anteile je Facilitator | 32,2 % im 30-Tage-Fenster gegen 70,7 % im 24-Stunden-Fenster derselben Quelle |
> | Markt A | Visa berichtet am 27.10.2026, Mastercard am 29.10.2026 — beide könnten erstmals bezifferen |
> | Visa/Artemis-Bereinigung | Datenstand 21.04.2026, seither nicht aktualisiert |
>
> **Neu messen heißt:** den Katalog über die Offset-Paginierung des
> CDP-Facilitators neu ziehen, die `payTo`-Adressen und ihre Listenpreise
> daraus bilden, und die preisgleichen USDC-Zuflüsse über `eth_getLogs` auf
> einem über Blockzeitstempel verankerten 30-Tage-Fenster summieren. Filter:
> USDC auf Base, Preis ≤ 10 USDC, Betrag gleich einem Listenpreis dieser
> Adresse oder diesem minus 10/20/25/50 %. Die Dublettenregel ändert das
> Ergebnis um 0,1 %, der Asset-Filter um 6,4 %.
>
> **Lücke, offen:** die Läufe vom 05.10.2026 (`onchain_all.py`,
> `onchain_matched.py`, `crosscheck.py`, `profile_addr.py`) und die
> ausführliche Methodenbeschreibung liegen **nicht in diesem Repo**, sondern im
> Session-Scratchpad und auf dem Server unter `/tmp`, beides flüchtig. Wer
> diese Datei dauerhaft nachrechenbar haben will, überführt die Skripte nach
> `scripts/`. Bis dahin ist der Weg oben beschrieben, aber nicht ausführbar
> hinterlegt.
>
> **Was nicht verfällt** und der eigentliche Grund für diese Datei: die
> Feststellung, dass die Bazaar-Zähler vom Facilitator kommen und nicht vom
> Dienst (§1), und die sechs Zahlen, die eine Messung vortäuschen (§4). Beides
> sind Eigenschaften der Quellen, keine Momentwerte.

---

## 1 — Woher `l30DaysTotalCalls` und `l30DaysUniquePayers` kommen

Das war die Sperrfrage: wenn der Dienst diese Zahlen selbst meldet, ist die
ganze Bazaar-Auswertung hinfällig.

**Sie ist nicht hinfällig. Der Facilitator zählt, nicht der Dienst.** Drei
Belege, zwei davon aus dem eigenen System.

**Die Spezifikation kennt die Felder nicht.** Beide Fassungen von
`specs/extensions/bazaar.md` geholt und durchsucht — die der x402 Foundation
(25.617 Byte) und die von Coinbase (16.729 Byte). Treffer für `quality`,
`l30Days`, `uniquePayers`, `totalCalls`: **null, in beiden.** Ein Dienst hat
also keinen vorgesehenen Kanal, über den er so etwas erklären könnte. **(a)**

**Wir senden sie nirgends.** `moltguard` und `moltstack` vollständig
durchsucht, inklusive aller Skripte: kein Vorkommen von `l30Days`,
`uniquePayers` oder `lastCalledAt`. Unser Katalogeintrag trägt sie trotzdem,
mit Werten, und `lastCalledAt` ist auf die Sekunde unsere eigene Abrechnung
vom 23.09. Das kann nur von der anderen Seite kommen. **(a)**

**Auf der Kette steht es 1:1.** Der größte Eintrag des Katalogs, AX1 Console,
behauptet 354.713 Aufrufe und damit 7.094,26 USDC. Gemessen an seiner
`payTo`-Adresse über dieselben 30 Tage: **354.819 eingehende USDC-Transfers,
7.096,38 USDC.** Abweichung 0,03 % in Betrag und Anzahl. Ein Zähler, der
Verifizierungen statt Zahlungen zählte, könnte das nicht treffen. **(a)**

### Was CDP selbst sagt, wörtlich

> „Each result's `quality` field reports its call count and unique payer count
> over the last 30 days, plus when it was last called."

> „Search returns at most 20 resources, ranked by a blend of query relevance
> and quality. Quality considers recent call volume and unique payers alongside
> the completeness of the description, output schema, and service metadata."

> „The x402 Bazaar is a catalog of payment-gated services discovered by the CDP
> Facilitator."

**CDP definiert nirgends, was als „call" gilt**, und sagt nirgends, wer die
Zahl erzeugt. Beide Fragen bleiben in der Dokumentation offen. **(a)**

### Die Lücke, die es trotzdem gibt

Die Spezifikation lässt das Zählen an der Verifizierung zu. Der Abschnitt
*Facilitator Behavior* lautet vollständig: *„When a facilitator receives a
`PaymentPayload` containing the `bazaar` extension, it should: 1. Validate the
`info` field against the provided `schema` 2. Extract the discovery
information"* — **Settlement kommt dort nicht vor.** Und weiter unten: *„After
processing a `PaymentPayload`, a facilitator **MAY** append extension outcomes
to the sidechannel on verify or settlement responses."* Beides selbst im
Dokument nachgelesen. **(a)**

Ein Dritter hat diesen Pfad gemessen und am 21.08.2026 als Issue #3226 im
Spec-Repo dokumentiert: eine Nur-Verifizierung ohne Abrechnung bekam
`{"bazaar":{"status":"processing"}}` zurück, ohne dass Token bewegt wurden.
Seine Folgerung: ein Eintrag samt Zählern kostet eine HTTP-Anfrage und
verlangt kein Guthaben. **(b)** — ich habe seine Messung nicht wiederholt.

Die beiden Belege, die dieses Issue für sich anführt, tragen das allerdings
nicht: #3045 und #2112 handeln beide vom **Gegenteil**, nämlich von Diensten,
die trotz echter, on-chain bestätigter Abrechnungen nicht in den Katalog
kamen. Die Zuschreibung „census … found catalogued rows whose payee addresses
show no inbound transfers" steht im Text von #3045 nicht. Sie mag in einem der
180 Kommentare stehen; als Beleg verwende ich sie nicht.

**Selbst gemessen, wie groß der Effekt ist:** von 1.394 Empfängeradressen des
Katalogs haben **47 keinen einzigen preisgleichen Zufluss**, obwohl ihnen
Aufrufe zugeschrieben sind — zusammen 1.744 Aufrufe und 20,18 USDC. Das sind
3,4 % der Adressen und 0,12 % des behaupteten Umsatzes. Der Pfad existiert und
ist für die Gesamtzahl unerheblich. **(a)**

### Und die Gegenrichtung, am eigenen Fall

Unser Eintrag meldet 3 Aufrufe. An unsere `payTo` gingen im selben Fenster
**7** Zuflüsse von je 0,05 USDC, der letzte 0,04. Katalogisiert wird nur, was
die `bazaar`-Erweiterung mitschickt: *„If the extension is omitted, discovery
cataloging will not occur."* Der Zähler ist damit eher eine **Untergrenze** der
Abrechnungen als eine Obergrenze. **(a)**

---

## 2 — Unabhängige Gegenprobe auf der Kette

Fenster Block 50.915.192–52.211.192, über Blockzeitstempel verankert,
2026-09-05T14:48:51Z bis 2026-10-05T14:48:51Z. Alle 1.394 `payTo`-Adressen des
Katalogs. **(a)**

| | Transfers | USDC |
|---|---:|---:|
| Katalog, Zähler × Listenpreis | 777.274 | 16.760,42 |
| Kette, **alle** USDC-Zuflüsse | 1.006.064 | 895.808,14 |
| Kette, **preisgleiche** Zuflüsse | 862.252 | **18.097,82** |

Die mittlere Zeile ist als Zahl unbrauchbar: eine `payTo` ist eine gewöhnliche
Wallet und empfängt auch alles andere. 53-fach über dem Katalogwert ist sie
eine Obergrenze, die nichts aussagt. Die untere Zeile zählt nur Zuflüsse, deren
Betrag genau einem Preis entspricht, den diese Adresse selbst ausschreibt, oder
diesem Preis minus 10, 20, 25 oder 50 % — 17.198 zulässige Beträge.

**Gegen die 17.901 USDC gehalten:** jene Zahl lief ohne Asset-Filter über alle
Netze. Auf USDC/Base eingeschränkt, also auf das kettenprüfbare Teil, sind es
16.760,42. Die Kette liegt mit 18.097,82 um **8,0 % darüber** — richtig so,
weil der Zähler eine Untergrenze ist und der Betragsfilter auch fremde Zahlungen
gleicher Höhe einsammelt.

**Zwei Quellen, wie verlangt.** Ankr erlaubt 50.000 Blöcke je `eth_getLogs`,
`mainnet.base.org` nur 500. Die Übereinstimmung wurde auf dem dichtesten
Tagesfenster hergestellt, Block 51.519.992–51.539.991: beide **246,140000 USDC
in 12.307 Transfers**, identisch in Betrag und Anzahl. Basescan fällt als
dritte Quelle aus — Etherscan V2 antwortet *„Free API access is not supported
for this chain"*, `api.basescan.org` steht hinter Cloudflare. **(a)**

**Facilitator-Adressen gibt es nicht zu messen.** Im `exact`-Schema geht USDC
direkt vom Zahler an `payTo` über `transferWithAuthorization` (EIP-3009,
Selektor `0xe3ee160e`, an einem echten Aufruf nachgesehen). Der Einreicher
zahlt nur Gas; in 60 untersuchten Transaktionen traten mindestens acht
verschiedene Einreicher auf. Eine USDC-haltende Facilitator-Adresse existiert
bauartbedingt nicht. **(a)**

Methode, Filter und Fehlerquellen vollständig in
`~/Downloads/bazaar-methode.md`.

---

## 3 — Abdeckung: was der Bazaar überhaupt sehen kann

Die x402 Foundation führt **15 produktive Facilitatoren** und schreibt dazu
selbst: *„Anyone can run a facilitator … The table below lists selected
production options; it is not an exhaustive catalog."* **(a)**

**Binance B402** fehlt in dieser Liste und in beiden öffentlichen Trackern.
Betreiber ist Binance selbst, Endpunkte `POST {BASE_URL}/papi/v2/b402/…`, nur
BNB Smart Chain, Assets U/USD1/USDC/USDT. Die `BASE_URL` ist nicht
veröffentlicht; die Doku schreibt *„Please contact us for access."* Eigenes
Bazaar mit **991 Ressourcen**, davon 941 auf einer einzigen
`payTo`-Adresse. Eine Spiegelung aus dem CDP-Katalog steht in der
Binance-Dokumentation nirgends. Aggregatzahlen zu B402: **nicht gefunden.**
**(a/b)**

Acht öffentlich lesbare Kataloge, vollständig gecrawlt, Ressourcen auf
`(Host, Pfad)` normalisiert. Union **54.201 Ressourcen auf 3.260 Hosts**:

| Katalog | Ressourcen | Anteil an der Union | nur dort |
|---|---:|---:|---:|
| thirdweb | 43.688 | 80,4 % | 1.648 |
| **CDP (Coinbase)** | **34.575** | **63,7 %** | **263** |
| PayAI | 14.451 | 26,4 % | 6.265 |
| Dexter | 3.234 | 6,0 % | 29 |
| Circle | 2.817 | 5,2 % | 2.599 |
| Ultravioleta DAO | 1.676 | 3,1 % | 522 |
| B402 (Binance) | 991 | 1,8 % | 845 |
| Solvador | 5 | 0,0 % | 0 |

CDP ∩ B402 beträgt 47 Ressourcen. Die Kataloge sind weitgehend getrennt;
thirdweb ist die Ausnahme und aggregiert fremde Kataloge (99,1 % der
CDP-Einträge liegen auch dort). **(a)**

Settlement-Anteil über 30 Tage, zwei unabhängige Tracker mit Attribution über
bekannte Settler-Adressen: **CDP 32,2 % der Transaktionen und 56,2 % des
USDC-Werts** von 5.960.880 Transaktionen / 945.225 USD über 16 Facilitatoren.
Die beiden Tracker stimmen bei CDP auf 34 USD überein (531.220 gegen 531.254).
Das Fenster verschiebt das Bild erheblich: im 24-Stunden-Fenster sind es 70,7 %
der Transaktionen und 86,9 % des Werts. **(a)**

**Der Gesamtanteil ist nicht beantwortbar**, und das ist eine Eigenschaft der
Sache, keine Recherchelücke. Vier Lücken stapeln sich:

1. Abwicklung ohne Facilitator ist vorgesehen — *„an optional but recommended
   service"* — und on-chain nicht erkennbar. Das offene Foundation-Issue #2335
   formuliert es so: *„third-party observers cannot reliably determine which
   facilitator handled a settlement from chain data alone."*
2. Facilitatoren außerhalb der Tracker-Whitelists fehlen vollständig, darunter
   Binance und Circle.
3. Die Tracker messen USDC auf wenigen Ketten. x402-list nennt die eigene Zahl
   *„a measured floor, not an ecosystem total."*
4. Sichtbarkeit im Bazaar verlangt zusätzlich das Opt-in des Verkäufers: *„If
   the extension is omitted, discovery cataloging will not occur."*

Zu Punkt 4 eine Größenordnung: CDPs eigene Zähler summieren über 30 Tage auf
841.795 Aufrufe, während die Tracker für denselben Facilitator rund 1,92
Millionen Settlements messen. Rund 44 % der CDP-Abrechnungen landen also auf
katalogisierten Ressourcen. Die Zählbasen sind nicht identisch, deshalb trägt
die Zahl die Richtung und nicht die Stelle. **(c)**

**Cloudflare ist kein Facilitator.** Die eigene Doku: *„The gateway settles the
payment through the Coinbase x402 Facilitator."* Dieser Verkehr liegt im
CDP-Topf. **(b)**

---

## 4 — Die zwei Märkte

### Markt A — Kartenschienen, menschlich mandatiert

| Anbieter | gemessenes Ist | Wortlaut | Stichtag | Kl. |
|---|---|---|---|---|
| **Alipay AI Pay** | **> 120 Mio. Transaktionen in einer Woche** | „its AI Pay … exceeded 120 million transactions in the past week" | 2026-02-13 | (b) |
| Visa | **„hundreds"** | „hundreds of secure, agent-initiated transactions have now been successfully completed" | 2025-12-18 | (b) |
| Mastercard | **keine** | auf dem Q1-Call: „In terms of where volumes are, we're still at early stage." | 2026-04-30 | (b) |
| Stripe ACP | **keine** | weder Instant-Checkout- noch Suite-Mitteilung nennt Transaktionen, Orders oder GMV | 2025-12-11 | (a) |
| Google AP2 | **keine, nie** | Launch-Blog, Protokollseite, GitHub-Releases: keine Transaktions- oder Händlerzahl | 2025-09-16 ff. | (a) |

Zu Alipay fehlt jeder Betrag, es gibt nur Stückzahlen, und der Zuschnitt ist
breiter als Chatbot-Checkout: Sprachbefehle auf Datenbrillen zählen mit.

Die Händlerseite ist deutlicher als die Anbieterseite. Etsy auf dem
Q2-Call: *„traffic from agentic experiences is still less than 1% of our
overall traffic."* Walmart zu Instant Checkout: *„conversion rates three times
lower."* OpenAI hat Instant Checkout am 24.03.2026 eingestellt, sechs Monate
nach Start. Und der größte Betreiber dämpft selbst — Ant-CEO Han Xinyi am
21.09.2026: *„the actual rollout has been notably slower than anticipated."*
**(b)**

Keiner der vier westlichen Anbieter hat General Availability erklärt. Die
Visa-Produktseite trägt bis heute *„Product is currently in the process of
deployment."*

### Markt B — Maschine zu Maschine, ohne Mensch je Transaktion

| Angabe | Wert | Methode / Wortlaut | Stichtag | Kl. |
|---|---|---|---|---|
| x402 kumuliert, roh | 190.551.441 Tx · 41.876.015 USD | „built from public on-chain settlement activity: transfers initiated by known facilitator addresses" | 2026-10-05 | **(a)** |
| x402 kumuliert, bereinigt | **15,0 Mio. USD · 109,6 Mio. Tx** | „Adjusted figures … exclude wash and test transactions" (Visa × Artemis) | Daten 2026-04-21 | (b) |
| Bereinigungsquote | **88,9 % der Dollar, 38,5 % der Tx entfernt** | aus dem Zahlenpaar desselben Reports | 2026-04-21 | (c) |
| x402 September 2026 | 11.859.643 Tx · **263.863 USD** · Ø $0,022 | Monatsreihe derselben Quelle | 2026-09 | (a) |
| **echt agentischer Anteil** | **5.000–11.000 USD je Monat** | „the two tests put the share that appears to be agentic at between 0.6% and 7.5%" (TRM Labs) | 2026-09-09 | (b) |
| Stripe/Tempo MPP | ~25.000 USD · ~115.000 Tx | „approximately $25,000 in adjusted volume" | Daten 2026-04-21 | (b) |
| Verkäufer x402, bereinigt | ~5.300 | „~5,300 adjusted sellers (i.e., merchants) to date" | 2026-04-21 | (b) |

Oktober bis Dezember 2025 tragen 81,6 % des gesamten x402-Volumens.
Chainalysis ordnet diesen Gipfel dem PING-Memecoin-Farming zu: *„Much of the
growth was driven by meme coin farming activity."* **(b)** Die Tagesreihe vom
26.09. bis 04.10.2026 liegt in einem Band von ±2 % — getakteter Verkehr, keine
wachsende Nachfrage. **(a)**

Skyfire, Nevermined, Payman, L402: **keine Volumenzahl, von keinem, jemals.**
Payman gehört ohnehin nicht in diesen Markt — dort gibt ein Mensch eine Policy
vor („Agent sends $150 → Needs approval").

Mastercard AP4M (Agent Pay for Machines, Start 10.06.2026) gehört in Markt B,
nicht A. Volumen: nicht offengelegt.

### Zahlen, die eine der beiden Definitionen vortäuschen

Diese sechs sind beim Weiterverwenden die gefährlichsten:

1. **Der Zähler auf `x402.org`** zeigt „Last 30 Days · 75,41M Transactions ·
   $24,24M Volume". Dieselben vier Werte stehen in Wayback-Snapshots vom
   01.08., 01.09. und 01.10.2026 und live heute. Ein 30-Tage-Zähler, der zehn
   Monate stillsteht, ist keine Messung; er liegt beim Volumen um Faktor 26 zu
   hoch. **Nicht zitieren.** **(a)**
2. **„MPP verarbeitete 14 Mio. USD in 60 Tagen"** verweist auf eine
   Stripe-Seite, die keine einzige Dollarzahl enthält. Belegbar sind 25.000
   USD. Faktor 560. **(a)**
3. **„Nearly all Mastercards … enabled"** beschreibt Credential-Bereitstellung,
   nicht Nutzung.
4. **Nevermineds Startseiten-Dashboard** („3.412 paid calls today") ist im
   Roh-HTML hartcodiert. **(a)**
5. **Virtuals aGDP 481,8 Mio. USD** ist Durchlaufnotional — die eigene
   Definition rechnet bei einem Trade über 5.000 USD mit 10 USD Gebühr einen
   aGDP von 5.010 USD.
6. **Verkäuferzahlen für x402 im gleichen Quartal:** 100.000+ (Coinbase) ·
   22.000 (x402.org, eingefroren) · ~5.300 bereinigt · 3.892 mit
   nachgewiesener Abrechnung · 1.387 Domains im Discovery-Snapshot. Spanne
   Faktor 26 bis 72.

Dazu zwei Coinbase-Angaben ohne Datum, die sich widersprechen: *„more than 100
million transactions and $28 million in payment volume"* gegen *„230M+
transactions and $54M+ in volume"*.

### Prognosen, ausdrücklich keine Ist-Zahlen

Juniper nennt 1,5 Billionen USD für 2030 und schreibt im selben Absatz *„growing
from only pilot deployments in 2025 and 2026"*. Mastercard Australien nennt
A$670 Milliarden mit dem Verb *„influence"* — beeinflusste Ausgaben, nicht
abgewickelte. Die vielzitierte Juniper-Zahl „8 Mrd. USD in 2026" steht in der
Pressemitteilung nicht; beide Fassungen geprüft. **(a)**

---

## 5 — Was ist gemessen, was nicht

| Gegenstand | Status | Zahl mit Stichtag | Kl. |
|---|---|---|---|
| Herkunft der Bazaar-Zähler | **gemessen** | Facilitator zählt; 0,03 % Abweichung zur Kette beim größten Eintrag | (a) |
| Umsatz der Bazaar-Endpunkte, 30 Tage | **gemessen, zwei Quellen** | 18.097,82 USDC preisgleich · 16.760,42 aus den Zählern | (a) |
| Instrument gegengeprüft | **ja** | zwei RPC-Betreiber, identisch im dichten Fenster | (a) |
| Zählt der Katalog Verifizierungen mit? | **Pfad existiert, Effekt gemessen** | 47 von 1.394 Adressen ohne Zufluss = 0,12 % des Umsatzes | (a) |
| Untererfassung des Zählers | **gemessen, n=1** | eigener Eintrag 3 Aufrufe gegen 7 Abrechnungen | (a) |
| Katalogabdeckung CDP | **gemessen** | 63,7 % von 54.201 Ressourcen in acht Katalogen | (a) |
| Settlement-Anteil CDP, 30 Tage | **gemessen** | 32,2 % der Tx · 56,2 % des USDC-Werts | (a) |
| CDP-Settlement gesamt, 30 Tage | **gemessen, zwei Tracker** | ~531.000 USD · ~1,92 Mio. Tx | (a) |
| **Anteil am x402-Gesamtverkehr** | **nicht beantwortbar** | kein Nenner existiert | — |
| x402-Volumen ohne Facilitator | **nicht messbar** | on-chain nicht unterscheidbar (Issue #2335) | — |
| Binance B402 Volumen | **nicht gefunden** | nur 991 Katalogeinträge | (a) |
| Circle x402 Volumen | **nicht gefunden** | 2.857 Katalogeinträge | (a) |
| Wash- und Testanteil in meinem Fenster | **nicht gemessen** | Vergleichswert: 88,9 % der Dollar (Artemis, 21.04.) | (b) |
| Markt A, westliche Kartenschienen | **praktisch unbeziffert** | Visa „hundreds" · Mastercard keine · Stripe keine · AP2 keine | (b) |
| Markt A, China | **Stückzahl, kein Betrag** | > 120 Mio. Tx in einer Woche, 13.02.2026 | (b) |
| Markt B, x402 bereinigt | **beziffert, Stichtag alt** | 15,0 Mio. USD kumuliert, Daten 21.04.2026 | (b) |
| Markt B, echt agentisch | **beziffert, gefiltert** | 5.000–11.000 USD je Monat, 09.09.2026 | (b) |
| Händleranteil agentischer Verkehr | **beziffert** | Etsy < 1 %, 06.08.2026 | (b) |

---

## Was daraus für einen Text folgt

Die Bazaar-Auswertung steht, und sie steht jetzt auf zwei unabhängigen
Messungen statt auf einem Katalogfeld. Zitierbar ist: **„Die im CDP x402
Bazaar gelisteten Endpunkte setzen in dreißig Tagen rund 18.000 USDC um,
gemessen als preisgleiche USDC-Zuflüsse auf Base, Stand 05.10.2026."** Mit
Nenner, mit Kette, mit Stichtag.

Nicht zitierbar ist jede Aussage über „den x402-Markt" oder „den
Agentenzahlungsmarkt". Für die erste fehlt der Nenner, für die zweite gibt es
zwei Märkte mit völlig verschiedener Evidenzlage: Markt A hat Partnerlisten und
eine chinesische Stückzahl, Markt B hat Kettendaten und einen
Bereinigungsabzug von 89 %.

Die beiden Earnings-Calls, die Markt A erstmals beziffern könnten, liegen in
den nächsten vier Wochen: Visa am 27.10., Mastercard am 29.10.2026. Der
Artemis-Datensatz ist seit dem 21.04.2026 nicht aktualisiert.
