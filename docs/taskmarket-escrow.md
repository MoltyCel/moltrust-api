# TaskMarket: Escrow-Beträge klein halten

Stand 2026-09-21, aus dem ersten Bounty-Durchlauf.

## Die Beobachtung

Eine Einzahlung landet nicht direkt in einem Vertrag. Der Weg ist:

```
0xa175 (unsere CLI-Wallet)
   ->  0x3c0820e2dabd5feae1fd03b78079dee15c7f83d8   EOA, kein Contract
   ->  0xddc6cc3e4d11c1f3527b867c7dad4ed9869c33f7   Contract
```

`0x3c08` ist eine **externally owned account** und hielt am 21.09. rund
233 USDC. Unser Geld lag also zwischenzeitlich auf einer Adresse, die von einem
privaten Schlüssel kontrolliert wird, nicht von Vertragslogik.

Das ist keine Anschuldigung. Eine Relayer-EOA, die Einzahlungen bündelt und
weiterleitet, ist eine übliche Bauweise, und die Auszahlungen liefen korrekt
und vollständig. Aber der Unterschied zwischen „im Vertrag hinterlegt" und
„bei einem Betreiber zwischengeparkt" ist einer, den man kennen sollte, bevor
man die Summe erhöht.

## Die Regel

**Höchstens 10 USDC gleichzeitig im Escrow.** Steigen die Beträge, wird der
Verlust bei einem Betreiberausfall größer als der Nutzen einer größeren Bounty.

Zwei Tasks à 5 USDC ist die Obergrenze, die sich bewährt hat: sie zog
202 Einreichungen an. Mehr Geld hätte daran wenig geändert — was die Beteiligung
trug, war die Aufgabe, nicht der Preis.

## Rückholbarkeit, falls doch nötig

Vor einer Einzahlung wissen, wie man wieder herauskommt:

| Weg | Bedingung |
|---|---|
| `refund-expired` | nur bei **null** Einreichungen. Bei einer beliebten Bounty nie erfüllt. |
| `cancel` | erst nachdem alle Einreichungen abgelehnt sind |
| `reject-all-submissions` | lehnt jede ab, dann `cancel`. 0,001 USDC je eindeutigem Worker. |

Bei 164 Workern kostet eine Rückholung rund 0,17 USDC — machbar, aber die
Wallet muss das Guthaben dafür haben. Am 21.09. hatte `0xa175` null USDC und
null ETH und konnte ihre eigene Rückholung nicht bezahlen.

**Also: die CLI-Wallet nie auf null laufen lassen.** Ein kleiner Rest deckt die
Gebühren, die jede Korrektur kostet.

## Gebühren, gemessen

- **Plattformgebühr 750 Basispunkte = 7,5 %**, vom Preisgeld abgezogen, nicht
  obendrauf. Aus 5,00 werden 4,625 für die Gewinner und 0,375 für die Plattform.
- **`accept-submissions` kostet 0,001 USDC je Aufruf, nicht je Empfänger.** Im
  CLI-Quelltext nachgelesen: ein `x402Post` mit dem gesamten Gewinner-Array. Bei
  5 USDC Preisgeld sind das 0,02 %, unabhängig von der Gewinnerzahl.
- Anteile sind ganze Basispunkte und müssen auf 10 000 summieren.
