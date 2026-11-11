# POST /vc/aae/evaluate: where each input comes from

A verdict from `/vc/aae/evaluate` rests on inputs of two kinds. Some this service
determines or checks itself before a verdict uses them. The others are the caller's
statement and are used as given. Since ops V5 (2026-10-23) every response says which
is which, and every stored evaluation says it for the inputs it read.

## In the response

`inputs` maps every input of the request to `established` or `asserted`:

```json
"inputs": {
  "aae_ref": "established",
  "action_context.agent_did": "established",
  "action_context.vc_id": "established",
  "action_context.timestamp": "established",
  "action_context.nonce": "asserted",
  "action_context.domain": "asserted",
  "action_context.value": "asserted",
  "action_context.currency": "asserted",
  "action_context.value_source": "asserted"
}
```

Each entry of `evaluations` carries its own `inputs` with the names that verdict read,
for example `rate_limit` → `{"aae_ref": "established", "action_context.agent_did":
"established"}`. These sit inside `evaluations`, which the verdict signature covers
(`app/enforcement/verdict_sign.py`). A stored verdict therefore keeps its provenance,
and changing it breaks the signature. The top-level `inputs` is not signed. It is
computed from the same sets and can be recomputed from the stored `action_context`.

## The rule

| Input | Marked | Why |
|---|---|---|
| `aae_ref` (request body) | established, once the envelope is found | The envelope row is looked up by it; its signature was checked when it was stored. An `aae_ref` with no stored envelope stays `asserted` and the verdict is DENY `envelope_not_found`. |
| `action_context.agent_did` | established | The endpoint refuses (403) an `agent_did` that is not the authenticated principal, from the API key's owner or the RFC 9421 signature. |
| `action_context.vc_id` | established | It has to equal the stored envelope's `aae_id`, or the request is refused (422). |
| `action_context.timestamp` | established | The server overwrites it with its own clock before any check. |
| `action_context.nonce` | asserted | The caller's replay token. A second use of the same `(agent_did, nonce)` is refused. That binds the value to one use; it does not make it the server's. |
| `action_context.value`, `.currency` | asserted | Checked for form (integer minor units, bounded) and compared with the envelope, never against a payment rail. |
| `action_context.value_source` | asserted | The caller writes it. A `required` `max_transaction_value` needs `rail_verified`, and nothing in this service establishes that value. |
| `action_context.domain` | asserted | Compared with the envelope's allowlist. Nothing checks that the action goes to that domain. |
| `action_context.aae_ref` and any other field | asserted | Not read by any verdict (see below). |

A field this service has never heard of is `asserted`. The default is the caller's word,
and a name becomes `established` only where the code above checks it
(`app/main.py`, `aae_evaluate`; `app/enforcement/evaluator.py`, `evaluate_envelope`).

## single_use and rate_limit count over the stored envelope

Both counters count rows in `aae_evaluations` for one envelope. Until V5 they took the
envelope from `action_context.aae_ref`, which the caller sets:

- `single_use` counted `WHERE aae_ref = NULL` when the field was left out, found nothing,
  and answered `ALLOW "first use"` on every call. A foreign value had the same effect.
- `rate_limit` failed closed when the field was missing, but a different value on each
  call opened a fresh window each time.

Live on 2026-10-23 with our own test DID and two envelopes of our own: a `single_use`
envelope gave three ALLOWs, and a `rate_limit` of one per hour gave two more ALLOWs
after it was used up.

Both now count over the `aae_ref` of the stored envelope row. Without that reference
they return DENY with code `mandate_ref_missing`, also for `required: false`. The
reason contains "fail-closed", so the penalty class is O and no agent is penalized for
it. `action_context.aae_ref` is ignored by both and reported as `asserted`.

Tests: `tests/test_evaluator_mandate_ref.py` (nine cases, all red against the code
before V5), `tests/test_aae_evaluate_endpoint.py::test_response_marks_inputs_established_or_asserted`.

## Against the specification texts

Compared with the AAE draft as published (rev 02, §2.4 VALIDITY) and the internal
design texts under `docs/specs/`. None of those files is changed here.

- **Draft §2.4:** `single_use` is OPTIONAL, Boolean, default false. The evaluator reads
  it from the envelope's `validity` and checks it only when true. Consistent.
- **`docs/specs/aae-constraint-taxonomy.md`, rate_limit:** "RP MUSS akzeptierte Aktionen
  im Fenster zählen". The evaluator counts ALLOW verdicts per agent and envelope inside
  the window. Consistent; until V5 the envelope came from the caller.
- **`docs/specs/aae-constraint-taxonomy.md`, single_use:** "Replay per VC-id ablehnen".
  The evaluator counts per `aae_ref`, the hash of the stored envelope. These differ:
  `aae_envelopes` is unique on `(aae_id, digest(scope_canonical))`, not on `aae_id`, so
  the same VC id stored with a different scope is a second envelope with its own count.
  Counting per `aae_id` alone has its own problem: `aae_id` is chosen by the issuer, and
  another issuer could store the same id and use it up. Left open. The decision belongs
  with the specification texts, not with this change.
- **`docs/specs/aae-evaluator-design-v4.md`, C2 value authenticity:** `rail_verified`
  against `self_asserted`. The caller still sets `value_source`; the verdict now reports
  it as `asserted`. Refusing a client-set `rail_verified` is a separate change
  (moltrust-api#609).
