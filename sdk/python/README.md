# moltrust-enforce

Referenz-Client für den MolTrust-Laufzeit-Check `POST /enforce/check` (`constraint_mode = "enforce"`).

Dünn und ausdrücklich: kein Decorator, kein Framework-Hook, keine versteckte Middleware. Zwei Methoden, und der Betreiber sieht bei beiden, was passiert. Framework-agnostisch — wo der Aufruf im eigenen Code steht, entscheidet der Betreiber.

Das SDK **prüft** Mandate. Es stellt keine aus: Erzeugen und Signieren von Mandaten ist nicht Teil davon.

## Installation

```bash
pip install -e sdk/python              # nur nachrechnen und verifizieren
pip install -e "sdk/python[client]"    # dazu der HTTP-Client für POST /enforce/check
```

Die Basis trägt `jcs` und `cryptography` — genau das, was der Nachrechen-Pfad braucht. Der HTTP-Client steht ab 0.3.0 im Extra `client`; ein Dritter, der nur ein Urteil prüft, installiert damit 5 Pakete statt 12. `[verify]` ist ein leeres Extra und existiert, damit `pip install "moltrust-enforce[verify]"` läuft und die Antwort im Paket selbst steht: der Verifizierer braucht nichts über die Basis hinaus.

**Änderung gegenüber 0.2.0:** dort kam `httpx` unbedingt mit. Wer nach dem Upgrade `EnforceClient` ohne das Extra anfasst, bekommt keinen nackten `ModuleNotFoundError`, sondern:

```
EnforceClient needs the HTTP client, which is not installed. It moved into an extra
in 0.3.0: pip install 'moltrust-enforce[client]'. Recomputing and verifying work without it.
```

Auf PyPI liegen 0.1.0 und 0.2.0. Jede Veröffentlichung ist ein eigener, menschlich freigegebener Schritt; 0.3.0 ist nicht draußen.

## Muster 1 — dem Server glauben

Der einfache Weg. Ein Aufruf, ein Verdikt.

```python
from moltrust_enforce import EnforceClient

client = EnforceClient("https://api.moltrust.ch", api_key=API_KEY)

verdict = client.check(mandate, transaction)

if verdict.permitted:
    execute(transaction)
else:
    log.warning("blocked: %s (%s)", verdict.verdict, verdict.reason)
```

`verdict.permitted` ist nur bei `PERMIT` wahr. `PENDING` ist keine Erlaubnis, `DENY` erst recht nicht.

## Muster 2 — selbst nachrechnen

Der eigentliche Punkt. Das Verdikt hängt allein an Mandat und Transaktion — kein Serverzustand, keine Uhr, keine Datenbank. Wer beide Eingaben hat, rechnet es lokal nach und braucht dem Server nicht zu glauben.

```python
verdict = client.check(mandate, transaction)
result = client.verify(verdict, mandate, transaction)

if not result.ok:
    # Der Server hat etwas anderes gesagt als die Eingaben hergeben.
    alert("enforce server disagrees with local recompute", result.mismatches)
    return                       # nicht ausführen

if verdict.permitted:
    execute(transaction)
```

`verify()` prüft dreifach: ob die Antwort sich selbst trägt (`core_digest` passt zum mitgelieferten `core`), ob die lokale Auswertung denselben Digest ergibt, und ob der Server dasselbe Verdikt nennt wie die lokale Auswertung. Jede Abweichung landet in `result.mismatches`.

Ganz ohne Server geht es auch — der Kern ist öffentlich:

```python
from moltrust_enforce import enforce_check
local = enforce_check(mandate, transaction)
```

## Fail-closed

Ein PERMIT entsteht ausschließlich aus einer gelesenen 200-Antwort, die PERMIT sagt. Alles andere ist DENY:

| Lage | Ergebnis |
|---|---|
| Server nicht erreichbar, Timeout, DNS-Fehler | `DENY`, `from_server=False` |
| HTTP 4xx/5xx | `DENY`, `from_server=False` |
| Antwort ist kein JSON / hat die falsche Form | `DENY`, `from_server=False` |
| `verdict`-Wert unbekannt | `DENY`, `from_server=False` |
| Kein gültiges Mandat im Request | `DENY` (der Server antwortet 200 mit DENY-Record) |

Wer die Störung lieber als Ausnahme behandelt:

```python
client = EnforceClient(..., on_transport_error="raise")   # wirft EnforceTransportError
```

Beide Einstellungen sind fail-closed. Ein drittes, durchlassendes Verhalten gibt es nicht — kein Schalter, der eine unerreichbare Prüfung in eine Erlaubnis verwandelt.

## PENDING

`check()` gibt `PENDING` unverändert zurück. Das SDK löst es nicht auf, und `permitted` bleibt False — eine PENDING-Aktion kommt hier nie still durch.

Der optionale Haken meldet, er entscheidet nicht: sein Rückgabewert wird ignoriert, das Verdikt bleibt `PENDING`.

```python
def queue_for_approval(verdict):
    approvals.put(verdict.core_digest)          # melden

client = EnforceClient(..., on_pending=queue_for_approval)

verdict = client.check(mandate, transaction)
if verdict.pending:
    return                                       # der Betreiber muss handeln
```

Ohne Haken passiert dasselbe, nur ohne Meldung: `PENDING` kommt zurück, `permitted` ist False, ausgeführt wird nichts.

## Mandat und Transaktion

Ein Mandat trägt Grants. Ein Grant bindet an eine Aktion (`action_binding`), deklariert deren Felder (`type_fields`), führt Constraints und eine `disposition`:

```python
from moltrust_enforce import action_digest

action = {"verb": "transfer", "asset": "USDC", "chain": "base"}

mandate = {
    "grants": [{
        "action_binding": action_digest(action),
        "type_fields": ["verb", "asset", "chain"],    # woraus die Aktion besteht
        "disposition": "allow",                       # allow | hold | forbid
        "constraints": [
            {"type": "exact", "field": "to", "value": "0xABC…"},
            {"type": "enum",  "field": "region", "values": ["CH", "DE"]},
            {"type": "range", "field": "amount", "lo": 0, "hi": 1000},
        ],
    }],
}

transaction = {"action": action, "to": "0xABC…", "region": "CH", "amount": 500}
```

`type_fields` trennt die Aktion von ihren Argumenten. Die Aktion muss ein Objekt sein und genau diese Schlüssel tragen — kein fehlender, kein zusätzlicher. `verb` ist Pflicht. Empfänger und Betrag bleiben Geschwister der Aktion und werden über Constraints geprüft; wandert der Betrag in die Aktion, ist er Teil des Digests und jede Zahlung wäre eine andere Aktion. Ohne `type_fields` ist der Grant ungültig, und ein Mandat, das nur aus ihm besteht, trägt nichts.

`exact` vergleicht exakt — kein Präfix, kein Case-Folding, keine Normalisierung; eine Vanity-Adresse mit gleichem Anfang fällt durch. `enum` vergleicht jedes Element exakt. `range` ist ein geschlossenes Ganzzahl-Intervall `lo ≤ arg ≤ hi`; Fließkommazahlen werden abgewiesen, weil sie die Nachrechenbarkeit brechen.

PERMIT gibt es nur, wenn ein Grant per `action_binding` trifft, alle seine Constraints halten und die `disposition` `allow` ist. Eine Aktion, die kein Grant adressiert, ist DENY und nie PENDING. `forbid` hat Vorrang vor einem erlaubenden Grant.

## AER — Urteile über lebende Vorbedingungen

Ab 0.3.0 wertet das SDK auch Constraints aus, deren Antwort nicht in der Transaktion steht: ob eine Berechtigung widerrufen wurde, ob ein Empfänger auf einer Sanktionsliste steht, welcher Umrechnungskurs für ein Fiat-Limit galt. Solche Fakten leben außerhalb der Entscheidung und ändern sich; damit ein Dritter das Urteil trotzdem nachrechnen kann, wird jeder Faktenwert als signierte Aussage mit Gültigkeitsfenster mitgeführt — ein Evidenz-Item, verpackt als DSSE-Envelope (Dead Simple Signing Envelope, das Signatur-Format aus der Supply-Chain-Welt). Alle Items einer Entscheidung stehen in einem Bündel, das Bündel hat einen Hash `bundle_commit`, und der steht im Verdikt-Record.

```python
from moltrust_enforce import build_bundle, f_ext, verify_record

# Vier Constraint-Typen zeigen auf das Bündel statt auf die Transaktion.
mandate = {"grants": [{
    "action_binding": action_digest(action),
    "type_fields": ["verb", "asset", "chain"],
    "disposition": "allow",
    "constraints": [
        {"type": "exact", "field": "to", "value": "0xABC…"},
        {"type": "evidence_bool", "query": {"kind": "revocation", "subject": "aae:0f3a"},
         "expect": False},
        {"type": "evidence_enum", "query": {"kind": "jurisdiction", "subject": "0xABC…"},
         "values": ["CH", "DE"]},
        {"type": "evidence_scaled_range", "field": "amount", "rate_scale": 6,
         "query": {"kind": "fx", "pair": "USDC/EUR"}, "lo": 0, "hi": 500},
    ],
}]}

bundle = build_bundle(items, mandate, transaction, "2026-08-31T12:00:00Z")
record = f_ext(mandate, transaction, bundle)     # rein, ohne Netz und ohne Uhr
```

`evidence_bool`, `evidence_enum` und `evidence_range` vergleichen den Wert aus dem Bündel. `evidence_scaled_range` rechnet einen Betrag aus der Transaktion mit einem Kurs aus dem Bündel um und prüft ihn gegen eine Grenze: der Kurs steht als Ganzzahl in Einheiten von `10**rate_scale`, verglichen wird `betrag * kurs` gegen `grenze * 10**rate_scale`. 500 USDC-Minor-Units zum Kurs 0,92 ergeben 460 EUR-Minor-Units und halten unter einem Limit von 500. Gerechnet wird ohne Division und ohne Rundung, weil ein Fließkomma-Zwischenschritt je nach Plattform ein anderes Urteil ergeben kann.

Jeder Evidenz-Constraint prüft zusätzlich das Fenster seines Items gegen den `decision_timestamp` des Bündels. Fehlt ein Item zu einer Frage, ist der Wert vom falschen Typ oder liegt der Zeitpunkt außerhalb des Fensters, ist das Ergebnis DENY — dieselbe fail-closed-Regel wie im statischen Fall. Ein Mandat ohne Evidenz-Constraints bekommt von `f_ext` dasselbe Verdikt wie von `enforce_check`; `tests/test_aer_ext_core.py` hält das über einen Fallkorpus nach.

### Nachrechnen ohne Server: `moltrust-verify`

Der Verifizierer bekommt Record, Bündel, Mandat, Transaktion und eine Trust-List — eine Datei, die sagt, welchen Quellen der Prüfende glaubt. Er öffnet keine Verbindung und liest keine Uhr:

```bash
moltrust-verify --input decision.json --trust-list sources.json
```

```
V1 PASS bundle commit and input binding hold
V2 PASS every item carries a signature from a trusted source
V3 PASS every item window covers the decision timestamp
V4 PASS recomputed PERMIT from the same inputs

PASS — recomputed verdict PERMIT
```

Eine fertige Entscheidung zum Ausprobieren liegt in [`examples/aer/`](examples/aer/) — `decision.json`, `trust.json` und das Skript, das beide erzeugt.

V1 prüft, dass der Commit zum Bündelinhalt passt und dass Bündel und Record dasselbe Mandat und dieselbe Transaktion meinen. V2 prüft je Item eine Ed25519-Signatur über die DSSE-PAE gegen einen Schlüssel aus der Trust-List. V3 prüft je Item das Gültigkeitsfenster gegen den Entscheidungszeitpunkt. V4 rechnet `f_ext` neu und vergleicht `core_digest` und Verdikt. Exit-Code 0 heißt, alle vier halten; 1 heißt, mindestens eine fällt; 2 heißt, die Eingabe war schon nicht lesbar. Dieselben Prüfungen als Bibliothek: `verify_record(record, bundle, mandate, transaction, trust_list)`.

Was damit belegt ist: der Betreiber hat genau diese Evidenz benutzt, sie war zum Entscheidungszeitpunkt gültig, und aus ihr folgt genau dieses Verdikt. Was offen bleibt: ob eine benannte Quelle die Wahrheit gesagt hat. Wer den Schlüssel einer gelisteten Quelle besitzt, kann im Fenster einen falschen Wert signieren, und ein Fakt kann sich innerhalb eines gültigen Fensters ändern — dagegen hilft ein kurzes Fenster oder ein erneuter Abruf unmittelbar vor der Ausführung. Das Vertrauen ist damit auf benannte, auditierbare Quellen verschoben und nicht beseitigt.

Das SDK prüft Evidenz und stellt keine aus. Signierende Quell-Adapter gehören nicht ins Paket; die Trust-List bringt der Prüfende mit, weil ein Verifizierer, der Schlüssel erst online auflösen müsste, kein Offline-Verifizierer wäre.

Der Prüfpfad lädt auch keinen HTTP-Stack: `EnforceClient` kommt erst beim Zugriff (PEP 562), `import moltrust_enforce.cli` zieht damit weder `httpx` noch `socket` oder `ssl` in den Prozess. Geladen sind `jcs` und `cryptography`. `tests/test_aer_verify.py` misst das am Prozess und nicht am Quelltext. Ohne das Extra `client` ist httpx gar nicht erst installiert.

## Kopplung an die Server-Signatur

Das SDK ist an die Signatur aus [PR #306](https://github.com/MoltyCel/moltrust-api/pull/306) gekoppelt:

- Request `{"mandate": …, "transaction": …, "prev_core_digest": "sha256:<64 hex>"|null}`
- Antwort `{"verdict", "reason", "grant_index", "trace", "record": {"core", "core_digest"}}`

`src/moltrust_enforce/_core.py` ist eine unveränderte Kopie von `app/enforcement/enforce_check.py`. Genau eine Zeile weicht ab: der Import der JCS-Kanonisierung zeigt hier direkt auf `jcs` statt auf `app.signature`, weil das SDK ohne das Server-Paket auskommen muss. `tests/test_core_parity.py` prüft beides — dass keine zweite Zeile abweicht, und dass beide Fassungen über einen Fallkorpus dieselben Digests liefern.

Ändert sich die Signatur, muss das SDK nachziehen.

## Tests

```bash
cd sdk/python && pip install -e ".[test]" && pytest
```

## Lizenz

MIT
