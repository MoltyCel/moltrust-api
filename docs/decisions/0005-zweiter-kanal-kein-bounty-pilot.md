# 0005 — Kein zweiter Bounty-Kanal; 10 USDC zurück in die Rundenkasse

**Datum:** 2026-09-27
**Status:** Accepted
**Entscheider:** Lars

## Entscheidung

**Der Pilot auf einem zweiten Bounty-Marktplatz entfällt.** Die dafür
vorgesehenen 10 USDC gehen zurück in die Rundenkasse. Worauf sie verwendet
werden — Runden 5 und 6 oder ein Referral-Mechanismus — wird nach der
Task-2-Messung entschieden.

Eine Defekt-Task auf Claw Earn bleibt als Option im Defekt-Budget bestehen.
Heute passiert dazu nichts.

## Grundlage

Geprüft am 26.09. gegen ein Kriterium, das alles andere schlägt: eine Task,
viele Gewinner. Task 1 zahlt aus einem Escrow an 100 Adressen über Anteile in
Basispunkten; ohne das kostet dieselbe Reichweite hundert einzelne Tasks.

| Marktplatz | Format | Gebühr | Worker-Einsatz | Mindestprämie | Reife |
|---|---|---|---|---|---|
| taskmarket (Ist) | viele Gewinner je Task | 750 bps | keiner | — | in Betrieb |
| Claw Earn | „Single-start by default" | 10 % | 30 % bei der ersten Task | 9 USDC, im A2A-Weg 3 | nur Web-UI |
| ClawTasks | ein Worker je Task | 5 % | 10 % | nicht genannt | Beta, derzeit nur kostenlose Tasks |
| 0xWork | nicht angegeben | 5 %, 2 % für Token-Halter | 10 % in `$AXOBOTL` | nicht genannt | Zähler leer: „—AGENTS" |

Virtuals ACP kennt keinen offenen Bounty — `create-job` und `create-custom-job`
verlangen beide `--provider 0xSellerAddress`, im Changelog kommt „bounty" nicht
vor. Fetch.ai Agentverse vergibt keine bezahlten Aufträge. BasedAgents hat die
richtige Bauform, vergibt aber je Task an einen Claimant und steht bei 0 Stars.

Zwei Gründe tragen die Entscheidung. Bei Claw Earn im A2A-Weg wären 100 Köpfe
hundert Tasks à mindestens 3 USDC, also 300 statt 5,41. Und alle drei verlangen
ein Pfand vom Worker; ein Agent, der uns nicht kennt, hinterlegt nichts und
kauft erst recht keinen Token, um 0,05 USDC zu verdienen. Der Trichter lebt
davon, dass ein Versuch nichts kostet.

## Kassenstand

| Posten | USDC |
|---|---:|
| Rundenbudget gesamt | 50,00 |
| bereits als Escrow gebunden (TSK-J3R0MDGA) | −5,41 |
| Pilot zweiter Kanal, gestrichen | +10,00 zurück |
| **frei, Verwendung offen bis Task-2-Messung** | **44,59** |

Die 750-bps-Gebühr steckt in den 5,41 und kommt nicht obendrauf: 100 × 0,05
netto ergibt 5,00, geteilt durch 0,925 sind 5,405.

## Wann das neu zu bewerten wäre

Wenn ein Marktplatz Anteile je Einreichung unterstützt, also mehrere Gewinner
aus einem Escrow, und ohne Worker-Pfand auskommt. Dann ist die Rechnung eine
andere und der Vergleich neu zu führen.

Volle Erhebung: `~/Downloads/basisrechnung-ziel-1000.md`, Abschnitt 7
(2026-09-26, Quellen live geprüft).
