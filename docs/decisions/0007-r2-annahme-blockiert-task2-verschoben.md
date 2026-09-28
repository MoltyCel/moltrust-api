# 0007 — Runde-2-Annahme blockiert; Task 2 verschoben

**Datum:** 2026-09-28
**Status:** Accepted
**Entscheider:** Lars

## Entscheidung

**Task 2 wird nicht angelegt, solange die Fehlerursache ungeklärt ist oder der
Support nicht geantwortet hat.** Keine Direktzahlung vor Ablauf der Task am
**30.09.2026, 15:14 UTC**. Ein zweiter Annahmeversuch ist eine eigene Freigabe,
einmalig, mit neuem `idempotencyKey`.

## Was blockiert

`accept-submissions` für TSK-J3R0MDGA scheitert mit einem Custom-Error, den
keine öffentliche ABI kennt:

```
relay() reverted: 0xc6671ec1
idempotencyKey 576a991d-40a4-4709-877d-0333cd871d40, reason unclassified
```

Nichts ist on-chain passiert: Task weiter `open`, `awardCount 0`, die nonce der
Requester-Wallet steht auf 0. Die 0,001 USDC x402-Gebühr wurde zunächst
belastet und ist zurückgeflossen (5,638 → 5,637 → 5,638).

## Warum wir es nicht selbst reparieren können

Gemessen, nicht vermutet. `acceptSubmissions(bytes32,address[],uint16[],bytes32[],uint256)`
liegt in der Facette `0xe1cbf3e8…dddb` eines Diamond-Proxy `0xddc6cc3e…33f7`
hinter einem Forwarder `0x8884f95b…7144d`:

| Aufrufer | `eth_call` |
|---|---|
| Requester `0xa175` | `NotTrustedForwarder()` |
| Relayer-EOA `0x3c0820…` | `NotTrustedForwarder()` |
| Forwarder + ERC-2771-Anhang | `NoActiveForwardedCall()` |

Die Annahme ist **nur** über ihren Relay erreichbar. Eine Bisektion über 3, 10,
11, 20, 50 und 95 Empfänger liefert an jeder Größe denselben Fehler, scheitert
also vor jeder Empfänger-Logik — eine Empfängergrenze ist damit **weder belegt
noch widerlegt**, und niemand sollte das Gegenteil behaupten.

`0xc6671ec1` steht in keinem der geprüften Verträge: Forwarder (10 Errors),
Diamond (6), alle neun Facetten, Hook `0x8e28bb2c…f0ca`. Alle auf BaseScan
verifiziert.

**Teilannahmen scheiden aus.** Die Facette führt `SharesMustSumTo10000()`; ein
Aufruf mit Teilsummen revertet. Eine Aufteilung in zehn Aufrufe zu je zehn
Slots ist nicht möglich.

## Was läuft

- **Support:** `daydreamsai/skills-market#68`. Eine Mailadresse ist nirgends
  dokumentiert — die Wege sind GitHub, Discord und X —, und dort läuft
  erkennbar ihr Support. `info@moltrust.ch` steht als Rückkanal im Text.
- **Beobachtung:** `scripts/r2_settlement_watch.py`, stündlich. Meldet eine
  Antwort auf das Issue, einen Phasenwechsel der Task, neue `pendingActions`
  und jede USDC-Bewegung auf der Requester-Wallet. Führt nichts aus.

## Nach Ablauf

Laut `skills-market#61` geht eine abgelaufene Task ohne Award in
`awaiting_settlement` und **nur der Requester** kann sie bewegen. Es gibt keinen
Timer und keinen automatischen Refund; vierzig fremde Tasks hängen dort mit
370,7 USDC. Wer auf einen Rückfluss wartet, wartet vergeblich — der Schritt ist
unserer.

Die Fallback-Rechnung steht: 95 Direktzahlungen aus `0xa175`, 0,050043 USDC je
Slot (Doppel-Slots addiert), 5 225 000 Gas, **0,0000314 ETH** bei 0,006 gwei,
62-fach gedeckt. Sie wird erst nach Ablauf und nur mit eigener Freigabe
vorgelegt.

Was sie nicht leistet: die Worker bekommen keinen On-Chain-Award und damit
keine taskmarket-Reputation, und der Escrow bliebe gebunden — eine
Doppelzahlung, falls er später doch ausschüttet.

## Folgen für den Zeitplan

Task 2 war für den 30.09. vorgesehen, Runde 3 für den 07.10. Beide verschieben
sich um die Dauer der Klärung. Die Projektion des Meilenstein-Triggers rechnet
mit einer Rate aus den letzten sieben Tagen und fällt dadurch von selbst — sie
braucht keinen Eingriff, aber der Grund steht hier, damit niemand den Abfall
für nachlassendes Interesse hält.

Volle Diagnose: der Verlauf vom 28.09. und `~/r2-winners-20260928.json`.
