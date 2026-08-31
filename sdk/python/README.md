# moltrust-enforce

Referenz-Client für den MolTrust-Laufzeit-Check `POST /enforce/check` (`constraint_mode = "enforce"`).

Dünn und ausdrücklich: kein Decorator, kein Framework-Hook, keine versteckte Middleware. Zwei Methoden, und der Betreiber sieht bei beiden, was passiert. Framework-agnostisch — wo der Aufruf im eigenen Code steht, entscheidet der Betreiber.

Das SDK **prüft** Mandate. Es stellt keine aus: Erzeugen und Signieren von Mandaten ist nicht Teil davon.

## Installation

```bash
pip install -e sdk/python          # aus dem Repo
```

Nicht auf PyPI. Die Veröffentlichung ist bewusst ein eigener, menschlich freigegebener Schritt.

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
