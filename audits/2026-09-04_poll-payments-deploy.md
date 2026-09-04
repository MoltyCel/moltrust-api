# USDC-Poller: Deploy, Catch-up, Cron-Änderung

**Datum:** 2026-09-04 · **Typ:** Deploy + Server-Cron-Änderung (nicht repo-verwaltet) · **Scope:** `moltstack.service`, `monitor/.poll_state.json`, `crontab` von `moltstack`
**Repo-Stand:** `991cdf5` (PRs #331, #332, #333 gemergt)

## §1 Deploy

`git pull --ff-only origin main` auf `da18e46` → `991cdf5`, `systemctl restart moltstack.service`,
Unit `active`, `/health` 200.

## §2 Catch-up

Der Blockzeiger stand seit 2026-05-14 08:00:03 auf `45978127`. Aufgeholt in drei
manuellen Läufen mit temporär erhöhter Chunk-Größe:

```
POLL_RPC_URL=https://mainnet.base.org
POLL_CHUNK_BLOCKS=10000
POLL_MAX_CHUNKS_PER_RUN=600
```

| Lauf | Ergebnis | Zeiger danach |
|---|---|---|
| 1 | `Done. 0 new payment(s), 0.00 USDC total. Caught up to block 50858520.` | 50858520 |
| 2 | `Caught up to block 50858620.` | 50858620 |
| 3 | `Caught up to block 50858621.` | 50858621 |

Exit-Code jeweils 0. 4.872.056 Blöcke in rund zwei Sekunden Gesamtlaufzeit.

**Null Zahlungen gefunden — und das ist korrekt.** Ein read-only Vollscan derselben
Spanne am 2026-09-03 (488 Requests, 0 Fehler) hatte bereits null eingehende
USDC-Transfers ergeben, und die Deposit-Wallet hält `0.000000 USDC`. Die Erwartung
„der erste Lauf erkennt reale Zahlungen" lässt sich nicht erfüllen, weil es in der
Lücke keine gab. Das unterscheidende Signal ist jetzt die Logzeile: `Caught up to
block N` statt des früheren `Done. 0 new payment(s)`, das Ausfall und Erfolg
ununterscheidbar machte.

## §3 Alert-Pfad verifiziert

Kontrollierter Fehlversuch mit geworfenem `getLogs`:

```
Scanning blocks 50858622 to 50858671...
getLogs failed for blocks 50858622-50858671: SIMULIERTER Fehler
ABORTED after 0 chunk(s): blocks 50858622-50858671: SIMULIERTER Fehler
Done with errors. 0 new payment(s), 0.00 USDC total, 500 blocks behind.
Exit-Code: 1
Zeiger vorher/nachher: 50858621 / 50858621  -> UNVERAENDERT
```

`MOLTRUST_NOTIFY=on`, `telegram_allowed()` liefert `True`, der Alarm ist abgesetzt
worden. Die State-Datei ist byte-identisch mit dem Backup davor.

## §4 Cron-Änderung

`POLL_RPC_URL` fest auf `https://mainnet.base.org` gesetzt. Grund: `1rpc.io/base`
deckelt `eth_getLogs` bei 50 Blöcken und lieferte bei einem Direktaufruf vom Server
ein HTTP 403; `mainnet.base.org` bedient bis 10.000 und lief im Catch-up fehlerfrei.

Chunk-Größe bleibt beim Default **50** — die 10.000 waren ausdrücklich nur fürs
Aufholen, nicht für den Dauerbetrieb.

Vorher:
```
0 * * * * … && cd /home/moltstack/moltstack && /home/moltstack/moltstack/venv/bin/python monitor/poll_payments.py >> logs/poll_payments.log 2>&1
```

Nachher:
```
0 * * * * … && cd /home/moltstack/moltstack && POLL_RPC_URL=https://mainnet.base.org /home/moltstack/moltstack/venv/bin/python monitor/poll_payments.py >> logs/poll_payments.log 2>&1
```

Backup: `/tmp/crontab.backup-20260904-072509`.

**Fehler unterwegs, für die Nachwelt:** der erste `sed`-Versuch setzte die Variable
vor `cd` statt vor den Python-Aufruf — `POLL_RPC_URL=… cd …` gilt nur für `cd`, die
Variable hätte den Prozess nie erreicht. Aus dem Backup zurückgesetzt und korrigiert.
Danach im Prozess nachgewiesen: `BASE_RPC im Prozess: https://mainnet.base.org`.

Kapazität im Dauerbetrieb: 50 × 200 = 10.000 Blöcke pro Lauf gegen etwa 1.800, die
Base pro Stunde produziert.

## §5 Nicht angefasst

- `POLL_CHUNK_BLOCKS`, `POLL_MAX_CHUNKS_PER_RUN`, `POLL_LAG_ALERT_BLOCKS` — alle auf
  Default, nirgends gesetzt.
- `~/.moltrust_secrets` — unverändert; die RPC-URL ist Konfiguration, kein Secret,
  und steht deshalb in der Cron-Zeile.
- Kein weiterer Dienst neu gestartet.
