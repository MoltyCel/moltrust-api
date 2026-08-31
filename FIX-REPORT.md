# FIX-REPORT — moltrust-api, Security-Review 2026-08-31

Branch `security/hardening-2026-08`, ab `origin/main` = `7e6d2bd`.
Quelle: Haralds Review vom 31.08. (Shallow-Clone `7e6d2bd`, 20.08.).
Phase 1 des Console-Auftrags: Money-Pfade bauen, moltguard-Kern nur als Diff-Plan.

Kein Merge, kein Deploy. Der Branch ist PR-ready.

## Vorbedingung: der geprüfte Stand ist der laufende Stand

`origin/main` steht seit dem 20.08. unverändert auf `7e6d2bd` — dem Commit, den
Harald geprüft hat. Die 11 Tage zwischen Review und Abarbeitung haben nichts
verschoben. Der Server-Checkout `/home/moltstack/moltstack` steht auf demselben
Commit.

## Erledigt

| FIX | Fund | Commit | Test |
|---|---|---|---|
| FIX 2 | K1 — `/credits/deposit` ohne Absender-Bindung | `f0d2bf4` | `tests/test_deposit_sender_binding.py`, 5 Fälle |
| FIX 7b | H6 — Sports-Commits ohne Ownership-Check | `9f68171` | `tests/test_sports_commit_ownership.py`, 4 Fälle |

Testlauf gegen `moltstack_sandbox` (localhost:5432, Konvention aus
`tests/conftest.py` mit hartem Guard gegen die Live-DB `moltstack`):

```
tests/test_deposit_sender_binding.py + tests/test_sports_commit_ownership.py
  9 passed in 3.58s
tests/ (volle Suite)
  537 passed, 0 failed in 39.28s
```

Keine Regression. Der On-Chain-Leg ist in beiden Testdateien gestubbt — für
FIX 2, weil sich keine echte Base-Transaktion fabrizieren lässt; für FIX 7b,
weil `anchor_to_base` sonst echtes Gas aus `BASE_WALLET_KEY` verbrennen würde.
Alles unterhalb des Stubs — Bindungs-Lookup, 403/200-Entscheidung, Deposit-Zeile,
Credit-Grant — läuft gegen die echte Datenbank.

### FIX 2 — Korrektur am Anker

Das Playbook nennt `wallet_links` als Bindungstabelle und schreibt dazu „existiert
bereits (poll_payments nutzt sie)". Die Tabelle existiert weder in `moltstack` noch
in `moltstack_sandbox`. Die Angabe ist aus `monitor/poll_payments.py:112`
abgeleitet, nicht an der Datenbank geprüft.

Verwendet wird stattdessen `agents.wallet_address`. Diese Spalte wird von
`POST /identity/bind` (`main.py:2911`) geschrieben, nachdem eine ECDSA-Signatur
über eine servergenerierte Nonce geprüft und ausgeschlossen wurde, dass die
Wallet bereits an ein anderes DID gebunden ist. Dieselbe Spalte liest der
Basescan-Webhook (`main.py:3155`). Vergleich case-insensitiv, weil web3
Checksum-Adressen zurückgibt und der Binding-Endpoint exakt speichert.

### FIX 7b — zwei Abweichungen vom Review

**7a (Rate-Limits) war bereits da.** Der Review notiert „kein `@limiter.limit`
auf allen drei Sports-Commit-Endpoints". Gegen HEAD stimmt das nicht:
`/sports/predictions/commit` 30/min, `/sports/signals/register` 10/min,
`/sports/fantasy/lineups/commit` 30/min. Nichts hinzugefügt.

**7c (Anchor-Budget) ist nicht gebaut.** Es braucht eine neue Tabelle
`anchor_budget` und eine Tagesobergrenze — das ist Entscheidung E3 und gehört
in Phase 3. Der Ownership-Check nimmt den Fremd-DID-Gasvektor bereits weg; was
bleibt, ist ein Agent, der unter seinem eigenen DID im Rahmen seines
Rate-Limits Gas verbrennt.

## SKIPPED

### FIX 8 / H7 — `monitor/poll_payments.py`

Übersprungen nach §4.6 (Suchanker weg), mit zwei unabhängigen Gründen.

**Der Anker existiert nicht.** Der Fix soll gegen `usdc_deposits` deduplizieren
und die `wallet_links`-Auflösung reparieren. `wallet_links` gibt es in keiner
der beiden Datenbanken. Zeile 112 des Pollers würde bei jedem Aufruf werfen.

**Der Pfad ist seit dem 14.05. tot.** Der stündliche Cron läuft
(`0 * * * * … monitor/poll_payments.py`), erreicht aber die DB-Funktion nie:

```
2026-08-31 17:00:03  Scanning blocks 45978128 to 45980127...
2026-08-31 17:00:03  getLogs failed: {'code': -32602,
                     'message': 'eth_getLogs is limited to 0 - 50 blocks range'}
2026-08-31 17:00:03  Done. 0 new payment(s), 0.00 USDC total.
```

Erster Fehlschlag dieser Art am **2026-05-14 09:00**, seither 2603 Vorkommen —
jeder Lauf seit dreieinhalb Monaten. Der Poller fragt eine Spanne von 2000
Blöcken ab, der RPC-Endpoint erlaubt 50. Der Blockzeiger steht unverändert auf
45978128. Live-Stand: `usdc_deposits` 0 Zeilen, `payment_events` 1 Zeile.

Beide vom Review beschriebenen Defekte können damit nicht eintreten. Die
„Credit-Locking"-Variante scheitert zusätzlich an einem zweiten Punkt:
`usdc_deposits.to_did` hat einen Fremdschlüssel auf `agents(did)`, und eine
`agents`-Zeile mit `did='unknown'` existiert nicht — der Insert aus Zeile 131
würde am FK scheitern, in Zeile 133 abgefangen und als „skipped" geloggt. Die
Zeile landet nie, kann also auch keinen späteren Claim blockieren.

Was hier tatsächlich offen ist, ist kein Sicherheitsfund, sondern ein
Verfügbarkeitsdefekt: seit dem 14.05. werden eingehende USDC-Zahlungen nicht
mehr erkannt. Das gehört als eigener Vorgang aufgenommen, nicht in diesen
Branch. Sobald der Poller wieder Zahlungen sieht, werden Haralds beide
Dedupe-Defekte real — der Fix ist dann fällig, aber gegen den korrigierten
Anker `agents.wallet_address`.

## Offene Entscheidung für Lars

`/credits/deposit` weist ungebundene Absender-Wallets jetzt mit 403 ab
(E2-Default, fail-closed). Live sind **3 von 98** Agents wallet-gebunden.
Für die übrigen 95 heißt das: erst `POST /identity/bind`, dann einzahlen.

Das ist kein Nebeneffekt, sondern die einzige Variante, die K1 tatsächlich
schließt. Ließe man ungebundene Absender zu, bliebe der Front-Running-Angriff
für genau diese 95 Agents offen — der Angreifer nimmt einen fremden TX-Hash,
dessen Absender-Wallet ungebunden ist, und claimt ihn.

Die Alternative aus dem Playbook (Signatur über den Claim-Body statt
DB-Lookup) ist nicht gebaut; sie wäre ein neuer Flow, kein Bugfix.

---

# Phase 2 — Fail-Open-Startup-Guards

Branch `security/hardening-2026-08-phase2`, ab `origin/main` = `a533390`.

| FIX | Fund | Commit |
|---|---|---|
| FIX 4 | 🟠 H2 `STRIPE_WEBHOOK_SECRET` Fail-Open | `9748e1c` |
| FIX 10 | 🟠 H8 `/webhooks/payment` Fail-Open + kein Rate-Limit | `9748e1c` |
| FIX 3 | 🟠 H1 Test-Harness in der Produktiv-App | `9748e1c` |

```
tests/test_startup_guards.py   8 passed
tests/ (volle Suite)         545 passed, 0 failed
```

Die Guard-Tests importieren die Module in einem Subprozess mit absichtlich
kaputter Umgebung, damit keine dieser Umgebungen in die übrige Suite
durchschlägt.

## Muster

Alle drei folgen demselben Schema wie `NONCE_SECRET` (`main.py:917`): ein
leeres Secret hält den Prozess an, statt die Prüfung zu überspringen. Beide
Secrets sind auf dem Server gesetzt (44 bzw. 64 Zeichen), der Start bleibt also
unverändert.

Der Punkt bei beiden Webhooks ist, dass ein leeres Secret die Signaturprüfung
nicht abschaltet, sondern die Signatur berechenbar macht — der Code ist
öffentlich.

## FIX 3 — was verschwindet und was bleibt

Nicht mehr gemountet bei `MOLTRUST_ENV=production`: `/test-harness/invoke` und
`/test-harness/info` aus `app/test_harness/routes.py`.

Weiterhin gemountet: `/test-harness/endorse`. Der Endpoint ist direkt auf `app`
deklariert (`main.py:9237`), verlangt einen Partner-Key und trägt 60/min — der
Review führt ihn ausdrücklich als das korrekt gebaute Gegenstück.

**Blast-Radius:** `/test-harness/*` hat in 30 Tagen 1174 Requests, darunter
Path-Traversal-Sonden (`..\..\..\etc/passwd`). Auf `invoke` und `endorse` steht
kein einziger 2xx — nur 422, 400, 401 und 429. Es gibt keine funktionierende
Partner-Integration, die hier abreißt.

## FIX 10 — Blast-Radius

`/webhooks/payment` hat 201 × 401 und 2 × 405, keinen einzigen 200. Die
HMAC-Prüfung lief also bereits (das Secret ist gesetzt) und schlug bei jedem
Aufruf fehl. Die Pflichtprüfung ändert am beobachteten Verkehr nichts.

## Backlog

- **G-2:** alle 7 Python-Repos ohne Lockfiles (Supply-Chain-Drift). Nicht Teil
  dieser Phase.
- **MCP-Query-Param-Key:** `moltrust-mcp-server/src/moltrust_mcp_server/server.py:75`
  liest den API-Key weiterhin aus `?api_key=` → landet in Proxy-Logs. War als
  erledigt geführt, ist es nicht. 🔵, eigener Vorgang.
- **poll_payments RPC-Range:** siehe SKIPPED oben.
