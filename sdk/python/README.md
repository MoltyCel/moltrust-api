# moltrust-enforce

Reference client for the MolTrust runtime check `POST /enforce/check` (`constraint_mode = "enforce"`).

Thin and explicit: no decorator, no framework hook, no hidden middleware. Two methods, and the operator sees what happens in both. Framework-agnostic — where the call sits in your own code is the operator's decision.

The SDK **checks** mandates. It does not issue them: creating and signing mandates is not part of it.

> **0.5.0 breaks every digest — for the second time, for a different reason.** The digested core carries no free text any more: neither the verdict's `reason` nor the one on each predicate. Both stay in the **response** — the reason remains readable — only outside the value two implementations have to hit byte for byte (AAE -02 §2.5.2/§2.5.3). Affected are the `core_digest` of verdict, ratification and AER decision; `action_binding`, `mandate_digest` and `transaction_digest` stay. How to tell: `enforce_version`, `ratify_version` and `aer_version` now read `"3.0"`. A record from 0.4.0 or earlier can no longer be recomputed with this package. Details in the [CHANGELOG](CHANGELOG.md).

## Installation

```bash
pip install -e sdk/python              # recompute and verify only
pip install -e "sdk/python[client]"    # plus the HTTP client for POST /enforce/check
```

The base carries `jcs` and `cryptography` — exactly what the recompute path needs. The HTTP client sits in the extra `client` from 0.4.0 on; a third party who only checks a verdict installs 5 packages instead of 12. `[verify]` is an empty extra and exists so that `pip install "moltrust-enforce[verify]"` runs and the answer sits in the package itself: the verifier needs nothing beyond the base.

**Change against 0.3.0:** there `httpx` came along unconditionally. Anyone who touches `EnforceClient` after the upgrade without the extra gets no bare `ModuleNotFoundError`, but:

```
EnforceClient needs the HTTP client, which is not installed. It moved into an extra
in 0.4.0: pip install 'moltrust-enforce[client]'. Recomputing and verifying work without it.
```

Releases are published to PyPI; the latest version there is the current one. Each publish is its own human-approved step. The current release digests a core without free text — see the note above and the [CHANGELOG](CHANGELOG.md).

## Pattern 1 — believe the server

The simple way. One call, one verdict.

```python
from moltrust_enforce import EnforceClient

client = EnforceClient("https://api.moltrust.ch", api_key=API_KEY)

verdict = client.check(mandate, transaction)

if verdict.permitted:
    execute(transaction)
else:
    log.warning("blocked: %s (%s)", verdict.verdict, verdict.reason)
```

`verdict.permitted` is true for `PERMIT` only. `PENDING` is not permission, `DENY` even less so.

## Pattern 2 — recompute it yourself

The actual point. The verdict hangs on mandate and transaction alone — no server state, no clock, no database. Whoever holds both inputs recomputes it locally and does not have to believe the server.

```python
verdict = client.check(mandate, transaction)
result = client.verify(verdict, mandate, transaction)

if not result.ok:
    # The server said something other than the inputs give.
    alert("enforce server disagrees with local recompute", result.mismatches)
    return                       # do not execute

if verdict.permitted:
    execute(transaction)
```

`verify()` checks three things: whether the response carries itself (`core_digest` matches the `core` shipped with it), whether the local evaluation yields the same digest, and whether the server names the same verdict as the local evaluation. Every deviation lands in `result.mismatches`.

It also works without a server at all — the kernel is public:

```python
from moltrust_enforce import enforce_check
local = enforce_check(mandate, transaction)
```

## Fail-closed

A PERMIT arises exclusively from a 200 response that was read and says PERMIT. Everything else is DENY:

| Situation | Result |
|---|---|
| Server unreachable, timeout, DNS failure | `DENY`, `from_server=False` |
| HTTP 4xx/5xx | `DENY`, `from_server=False` |
| Response is not JSON / has the wrong shape | `DENY`, `from_server=False` |
| `verdict` value unknown | `DENY`, `from_server=False` |
| No valid mandate in the request | `DENY` (the server answers 200 with a DENY record) |

For anyone who prefers to handle the failure as an exception:

```python
client = EnforceClient(..., on_transport_error="raise")   # raises EnforceTransportError
```

Both settings are fail-closed. A third, permitting behaviour does not exist — no switch that turns an unreachable check into permission.

## PENDING

`check()` returns `PENDING` unchanged. The SDK does not resolve it, and `permitted` stays False — a PENDING action never passes through here quietly.

The optional hook reports, it does not decide: its return value is ignored, the verdict stays `PENDING`.

```python
def queue_for_approval(verdict):
    approvals.put(verdict.core_digest)          # report

client = EnforceClient(..., on_pending=queue_for_approval)

verdict = client.check(mandate, transaction)
if verdict.pending:
    return                                       # the operator has to act
```

Without the hook the same happens, only without the report: `PENDING` comes back, `permitted` is False, nothing is executed.

## Mandate and transaction

A mandate carries grants. A grant binds to an action (`action_binding`), declares its fields (`type_fields`), holds constraints and a `disposition`:

```python
from moltrust_enforce import action_digest

action = {"verb": "transfer", "asset": "USDC", "chain": "base"}

mandate = {
    "grants": [{
        "action_binding": action_digest(action),
        "type_fields": ["verb", "asset", "chain"],    # what the action consists of
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

`type_fields` separates the action from its arguments. The action must be an object and carry exactly these keys — none missing, none extra. `verb` is mandatory. Recipient and amount stay siblings of the action and are checked through constraints; if the amount moves into the action, it becomes part of the digest and every payment would be a different action. Without `type_fields` the grant is invalid, and a mandate consisting of it alone carries nothing.

`exact` compares exactly — no prefix, no case folding, no normalization; a vanity address with the same beginning fails. `enum` compares every element exactly. `range` is a closed integer interval `lo ≤ arg ≤ hi`; floating-point numbers are rejected because they break recomputability.

PERMIT exists only when a grant matches via `action_binding`, all of its constraints hold and the `disposition` is `allow`. An action that no grant addresses is DENY and never PENDING. `forbid` takes precedence over a permitting grant.

## AER — verdicts over live preconditions

From 0.4.0 on the SDK also evaluates constraints whose answer does not sit in the transaction: whether an authorization has been revoked, whether a recipient is on a sanctions list, which exchange rate applied to a fiat limit. Such facts live outside the decision and change; so that a third party can still recompute the verdict, every fact value is carried along as a signed statement with a validity window — an evidence item, packaged as a DSSE envelope (Dead Simple Signing Envelope, the signature format from the supply-chain world). All items of a decision sit in a bundle, the bundle has a hash `bundle_commit`, and that sits in the verdict record.

```python
from moltrust_enforce import build_bundle, f_ext, verify_record

# Four constraint types point at the bundle instead of at the transaction.
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
record = f_ext(mandate, transaction, bundle)     # pure, without network and without clock
```

`evidence_bool`, `evidence_enum` and `evidence_range` compare the value from the bundle. `evidence_scaled_range` converts an amount from the transaction with a rate from the bundle and checks it against a limit: the rate sits as an integer in units of `10**rate_scale`, and `amount * rate` is compared against `limit * 10**rate_scale`. 500 USDC minor units at a rate of 0.92 give 460 EUR minor units and hold under a limit of 500. The arithmetic runs without division and without rounding, because a floating-point intermediate step can give a different verdict depending on the platform.

Every evidence constraint additionally checks the window of its item against the `decision_timestamp` of the bundle. If an item for a question is missing, if the value has the wrong type, or if the timestamp lies outside the window, the result is DENY — the same fail-closed rule as in the static case. A mandate without evidence constraints gets the same verdict from `f_ext` as from `enforce_check`; `tests/test_aer_ext_core.py` holds that over a case corpus.

### Recomputing without a server: `moltrust-verify`

The verifier receives record, bundle, mandate, transaction and a trust list — a file that says which sources the checking party believes. It opens no connection and reads no clock:

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

A finished decision to try out sits in [`examples/aer/`](examples/aer/) — `decision.json`, `trust.json` and the script that produces both.

V1 checks that the commit matches the bundle content and that bundle and record mean the same mandate and the same transaction. V2 checks per item an Ed25519 signature over the DSSE PAE against a key from the trust list. V3 checks per item the validity window against the decision timestamp. V4 recomputes `f_ext` and compares `core_digest` and verdict. Exit code 0 means all four hold; 1 means at least one fails; 2 means the input was not readable in the first place. The same checks as a library: `verify_record(record, bundle, mandate, transaction, trust_list)`.

What this establishes: the operator used exactly this evidence, it was valid at the decision timestamp, and exactly this verdict follows from it. What stays open: whether a named source told the truth. Whoever holds the key of a listed source can sign a wrong value inside the window, and a fact can change within a valid window — against that a short window helps, or a fresh fetch immediately before execution. Trust is thereby moved onto named, auditable sources and not removed.

The SDK checks evidence and does not issue any. Signing source adapters do not belong in the package; the trust list is brought by the checking party, because a verifier that first had to resolve keys online would be no offline verifier.

The check path also loads no HTTP stack: `EnforceClient` arrives on access (PEP 562), so `import moltrust_enforce.cli` pulls neither `httpx` nor `socket` or `ssl` into the process. Loaded are `jcs` and `cryptography`. `tests/test_aer_verify.py` measures that on the process and not on the source text. Without the extra `client`, httpx is not installed in the first place.

## Coupling to the server signature

The SDK is coupled to the signature from [PR #306](https://github.com/MoltyCel/moltrust-api/pull/306):

- Request `{"mandate": …, "transaction": …, "prev_core_digest": "sha256:<64 hex>"|null}`
- Response `{"verdict", "reason", "grant_index", "trace", "record": {"core", "core_digest"}}`

`src/moltrust_enforce/_core.py` is an unchanged copy of `app/enforcement/enforce_check.py`. Exactly one line differs: the import of the JCS canonicalization points directly at `jcs` here instead of at `app.signature`, because the SDK has to work without the server package. `tests/test_core_parity.py` checks both — that no second line differs, and that both versions deliver the same digests over a case corpus.

If the signature changes, the SDK has to follow.

## Tests

```bash
cd sdk/python && pip install -e ".[test]" && pytest
```

## License

MIT
