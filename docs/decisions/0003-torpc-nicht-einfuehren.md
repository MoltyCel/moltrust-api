# 0003 — TORPC nicht einführen

**Datum:** 2026-09-26
**Status:** Accepted
**Entscheider:** Lars

## Entscheidung

**TORPC wird nicht eingeführt: ein Parsing-Bruch für 10 % auf dem heissen Pfad ist der
Tausch nicht wert.**

## Grundlage

Gemessen am 26.09. gegen unseren eigenen Base-Endpunkt. Der Header ist dort heute schon
aktiv — `Accept-Token-Tier: 1|2` wird beantwortet, `Token-Tier` kommt zurück, T3 deckelt
auf 2, ohne Header bleibt alles wie bisher.

| Methode | roh | mit T2 | Δ |
|---|---:|---:|---:|
| `eth_getTransactionCount` — unser häufigster Aufruf | 40 | 39 | −2 % |
| `eth_getBalance`, `eth_call` | 50 / 102 | unverändert | 0 |
| `eth_getBlockByNumber` | 12 752 | 11 440 | −10 % |
| `eth_getTransactionReceipt`, 1 Log | 1 872 | 813 | −57 % |

Auf eine Kaltsuche gerechnet: rund 10 %, und die fast vollständig aus dem einen
Blockabruf. Die einundzwanzig Nonce-Aufrufe zusammen sparen 21 Byte.

Dagegen steht, dass T2 Felder umbenennt — `blockNumber` → `block`, `gasUsed` → `gas_used`,
`transactionHash` → `tx`, und `logsBloom`, `type`, `cumulativeGasUsed` entfallen. Der
Header lässt sich also nicht zentral setzen und der Rest unverändert lassen; jeder Leser
müsste wissen, welche Stufe er bekommen hat.

Dazu der Reifegrad: Status Draft, Konformitätssuite mit genau einem Golden Case, null
Stars, null Forks, null fremde Issues, ein Contributor. Die Spezifikation sagt selbst,
dass niemand sich heute konform nennen darf, Ankr eingeschlossen.

## Was stattdessen passiert ist

Derselbe Blockabruf, der die 10 % ausmacht, ist ersatzlos entfallen: Base hält zwei
Sekunden je Block, über 1,5 Millionen Blöcke mit null Sekunden Abweichung gemessen, also
wird der Zeitstempel gerechnet statt geholt. Das spart rund 93 % der Kaltsuch-Payload —
ohne Abhängigkeit von einer Entwurfs-Spezifikation und ohne ein einziges umbenanntes Feld.

## Wann das neu zu bewerten wäre

- Wenn `usdc.py` oder der Abgleich ohnehin umgeschrieben werden: dort liegen 51–57 % je
  Beleg, und die T2-Log-Dekodierung erspart das Zerlegen von Topics und Hex-Offsets.
- Wenn die Spezifikation den Entwurfsstatus verlässt und eine Konformitätssuite hat, die
  mehr als einen Vektor kennt.

Volle Erhebung: `~/Downloads/ankr-torpc-recon.md` (2026-09-26).
