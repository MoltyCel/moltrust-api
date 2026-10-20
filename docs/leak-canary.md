# Leak canary for delegating agents

An agent that delegates work hands credentials to other agents. When one of
those credentials turns up somewhere it was never meant to go, the delegating
agent wants two answers: that it happened, and through which counterparty.

A leak canary gives both. The delegating agent (the *issuer*) asks MolTrust for
one canary credential per counterparty. MolTrust records which counterparty
received which canary, and only the issuer can read that record. When the canary
is presented at the MolTrust verifier by a party that should not hold it,
MolTrust writes a signed leak event that names the counterparty, and, if the
issuer asked for it, puts that counterparty on hold in the issuer's enforce
mandates.

The same works for delegated secrets that are not credentials: a canary of kind
`token` is a delegation token, and its presentation point is the token endpoint
(see *Token canaries*).

Decision record: [`docs/decisions/ADR-LC1-leak-canary.md`](decisions/ADR-LC1-leak-canary.md).

## What a canary is

A canary is an ordinary MolTrust Verifiable Credential. It is issued by the same
code path as `POST /credentials/issue`, signed with the same keys, stored in the
same `credentials` table, published and anchored the same way. Nothing in the
credential says that it is a canary. The only place that knows is a server-side
table that maps the credential's proof to the issuer and the counterparty.

That property is what makes the canary useful: a party holding a leaked
credential cannot tell a canary from any other credential, so it has no reason
to avoid presenting it. A test in this repository holds the structural equality
of a canary and a regular credential from the same issuer
(`tests/test_leak_canary.py`).

## Prerequisite: the counterparty is registered

The counterparty must be a registered MolTrust agent, and not a revoked one.
The canary is issued to the counterparty's DID, and every credential's subject
is a registered agent (`credentials.subject_did` references `agents(did)`). A
revoked DID's key no longer authenticates a request, so a presentation could
never be attributed to it.

`POST /canary/issue` checks this before anything is issued, stored or
published, in both modes and for both kinds. An unregistered counterparty gets
`422` with `counterparty_did is not a registered agent. The counterparty
registers its DID first: POST /identity/register`; a revoked one gets `422`
with `counterparty_did is revoked`. In both cases no credential, no token and no
canary record is created.

## Modes

Two modes cover the two ways a delegating agent hands something out. In both,
the credential's subject is the counterparty's registered DID; the modes differ
only in who is expected to present it.

**`bound`** — the counterparty holds the credential as its own. Presentations
by the issuer or by the counterparty are expected and stay silent. A
presentation by anyone else is a leak.

**`decoy`** — the counterparty receives the credential as bait among real
material and has no reason to present it. Every presentation except the
issuer's own is a leak, the counterparty's included.

Attribution works the same in both modes: an event names the counterparty as
the origin only when the counterparty's own key signed the presentation (see
*What a presentation proves*).

For a token canary the same two modes apply to the token's audience, which is
the counterparty's DID in both modes. Two token canaries for the same
counterparty with the same fields in the same second are the same token; the
second request gets `409` and can be repeated a second later.

## Token canaries

Agents delegate more than credentials. A delegating agent also hands out
delegation tokens: UCAN 0.10.0 JWTs from `POST /delegation/create`, signed by
the MolTrust registry key, with the delegating agent in `fct.delegator` and the
receiving agent as `aud`. A token canary is one of those.

1. **Issue.** The issuer calls `POST /canary/issue` with `"kind": "token"`, the
   counterparty's DID, a mode, the reaction on leak, and the fields it would send
   to `POST /delegation/create` (`capabilities`, `ttl_seconds`, optionally
   `nonce` and `facts`; no `proofs`). The token is minted by the same function
   as every token from `POST /delegation/create`, with the issuer as delegator,
   and passes the same delegation policy (`agent_delegation_config`). The
   response carries `canary_id`, `canary_ref` and, under `delegation`, the answer
   `POST /delegation/create` gives for that token (`ucan`, `issuer`, `delegator`,
   `audience`, `ucv`). Issuing a token canary costs the same as issuing a token.
2. **Hand over.** The issuer passes `delegation.ucan` to the counterparty.
3. **Presentation.** Someone checks the token at the token endpoint,
   `POST /delegation/verify`. The endpoint answers exactly as it would for any
   token; like the verifier, every answer now carries the `presentation` block
   (see *What a presentation proves*), with `subject_match` comparing the
   holder's DID with the token's `aud`. After the answer is sent, MolTrust looks
   the token's SHA-256 up in the canary table. The lookup runs for every
   presented token.
4. **Leak event.** If the token belongs to a canary and the caller is not an
   expected holder (see *Modes*), MolTrust writes the leak event described
   below, with `trigger` set to `token_presentation`. Expected holders, the
   dedupe window, holds and the enforce hook work as for a credential canary.

MolTrust stores no token canary in readable form: `leak_canaries` holds the hex
SHA-256 of the token (`token_hash`) and nothing from which the token could be
rebuilt. The token exists only in the issue response, as a token from
`POST /delegation/create` exists only in that response.

A relying party that accepts delegated tokens checks them with the SDK:

```python
from moltrust_enforce import canary

result = canary.check(token, base_url="https://api.moltrust.ch", api_key=KEY)
if not result.valid:
    ...
```

`canary.check(token, ...)` presents the token at `POST /delegation/verify` and
returns the endpoint's answer, fail-closed: a transport failure, an error status
or an unreadable answer yields `valid=False` with `from_server=False`. To carry
the holder's attribution along, the relying party forwards the request the
holder signed: `signed_request=(body_bytes, headers)`, where the body carries
the token and is sent unchanged.

## Flow

1. **Issue.** The issuer calls `POST /canary/issue` with the counterparty's DID
   (a registered agent, see *Prerequisite*), a mode and the reaction on leak
   (`hold` or `none`). The response carries the
   credential and a `canary_id`. Issuing a canary costs the same as issuing a
   credential.
2. **Hand over.** The issuer passes the credential to the counterparty through
   its usual channel. MolTrust is not involved in that step.
3. **Presentation.** Someone presents the credential at
   `POST /credentials/verify`. The verifier answers exactly as it would for any
   credential; the answer does not depend on whether the credential is a canary.
   Every answer carries a `presentation` block (see below) that says what the
   request was authenticated with. After the answer is sent, MolTrust looks the
   proof up in the canary table.
4. **Leak event.** If the proof belongs to a canary and the caller is not an
   expected holder (see Modes), MolTrust writes a leak event. The caller is
   identified only by the DID behind the API key it sent, if it sent one; the
   counterparty checks its own canary with its own API key. The event records
   the attribution class (see *What a presentation proves*). Repeated
   presentations by the same caller with the same attribution within ten
   minutes produce one event.
5. **Hold.** If the canary was issued with `on_leak: hold`, the counterparty's
   DID goes on the issuer's hold list.
6. **Enforce hook.** Before an enforce check, the issuer passes its mandate
   through `POST /canary/enforce-hook` and names the transaction field that
   carries the counterparty. The hook places, in front of every `allow` grant,
   a copy with disposition `hold` that applies only to held counterparties. The
   unchanged enforce kernel then returns `PENDING` for those counterparties,
   which the issuer can ratify or let stand. The patched mandate is an ordinary
   mandate; anyone can recompute the verdict from it.
7. **Review.** The issuer reads its canaries with `GET /canary/list`, its events
   with `GET /canary/events`, its holds with `GET /canary/holds`, and lifts a
   hold with `POST /canary/holds/release`.

The MCP tools `canary_issue`, `canary_list` and `canary_events` on
`api.moltrust.ch/mcp` call the same endpoints with the API key of the MCP
connection.

## What a presentation proves

Every credential MolTrust issues, a canary like any other, is published to IPFS
and can be fetched from the gateway by anyone who has its CID. Whoever has the
CID can present the credential. A presentation therefore proves that the
credential has left the counterparty's hands. On its own it does not prove that
the counterparty passed it on.

What ties a presentation to the counterparty is the counterparty's key. A
presentation may carry an RFC 9421 HTTP Message Signature by the holder's DID
over the request itself, in the profile MolTrust already uses for proof of
possession (`X-MolTrust-DID`, `Content-Digest`, `Signature-Input`,
`Signature`; covered components `@method`, `@path`, `x-moltrust-did`,
`content-digest`; key in force from the registry; nonce once within 300
seconds). The signature covers the body, and the body contains the credential.
A copy taken from IPFS carries no such signature. A relying party that received
a signed presentation from the holder can forward exactly that request.

The verifier checks the signature for every presentation that carries one and
reports the result in every answer:

```json
"presentation": {
  "api_key_did": "did:moltrust:…",
  "holder_proof": {"scheme": "rfc9421", "did": "did:moltrust:…", "valid": true,
                   "reason": null, "subject_match": true}
}
```

`holder_proof` is `null` when the request carries no signature. A signature that
does not verify does not change the credential's `valid`; it shows up as
`"valid": false` with the reason.

A leak event falls into one of two classes, recorded as `attribution`:

- **`counterparty`** — the presentation carries a valid holder proof by the
  counterparty's DID. In `bound` mode that DID is also the credential's subject.
  The counterparty's key signed this presentation of this credential.
- **`open`** — no holder proof, a proof that does not verify, or a proof by any
  other DID. The leak is proven; the way it took is not.

## Leak event

```json
{
  "event_version": 1,
  "event_id": "…",
  "canary_id": "…",
  "issuer_did": "did:moltrust:…",
  "counterparty_did": "did:moltrust:…",
  "mode": "bound",
  "trigger": "verifier_presentation",
  "observed_at": "2026-10-20T12:00:00Z",
  "presenter_did": null,
  "credential_valid": true,
  "canary_ref": "sha256:…",
  "presentation": {"api_key_did": null, "holder_proof": {…, "record": {…}}},
  "attribution": "counterparty",
  "aer": {"mandate": {…}, "transaction": {…}, "bundle": {…}, "record": {…}},
  "signature": {"alg": "Ed25519", "kid": "moltrust-registry-2026-v1", "sig": "…"}
}
```

`trigger` names where the presentation came in: `verifier_presentation` for
`POST /credentials/verify`, `token_presentation` for `POST /delegation/verify`.
An event from the token endpoint has exactly the members above, the same
signature and the same AER material; for a token, `credential_valid` says
whether the token verified. A test holds this equality
(`tests/test_leak_canary_token.py`).

`canary_ref` is `sha256:` plus the hex SHA-256 of the RFC 8785 (JCS) form of the
credential the issuer received, so the issuer can match an event to a credential
without asking MolTrust. For a token canary it is `sha256:` plus the hex SHA-256
of the token string as issued.

`presentation` is the block from the verifier's answer. For a valid holder proof
it adds `record`: `public_key_hex`, `signature_hex`, `signature_base_hex` and
`request_body_hex`. With them a third party checks the holder's signature over
the signature base, and checks that the base names the DID, the path and the
SHA-256 of the carried request body, without contacting MolTrust
(`app.leak_canary.check_holder_proof`). The path is the one the `trigger` names.

`signature.sig` is a hex-encoded Ed25519 signature by the registry key over the
bytes `moltrust:leak-canary-event:v1` `0x00` followed by JCS of the event without
the `signature` member. The public key is the registry JWK published in the
issuer DID document; `GET /canary/trust-list` carries the same key in base64.

`aer` carries the event as Attested-Evidence Replay material
([ADR-AER](decisions/ADR-AER-attested-evidence-replay.md)). MolTrust signs one
evidence item (source `did:web:api.moltrust.ch#leak-canary`, query
`{"kind": "leak_canary_presented", "canary_ref", "counterparty_did", "attribution"}`, value
`true`, valid for 30 days from `observed_at`) and evaluates a one-grant mandate
that allows the action `{"verb": "leak_canary_hold"}` only when that evidence is
true. The resulting record is `PERMIT`. A third party with the trust list from
`GET /canary/trust-list` recomputes it offline:

```bash
pip install moltrust-enforce
moltrust-verify --input event-aer.json --trust-list trust-list.json
```

where `event-aer.json` is the `aer` member of the event. The verifier checks the
bundle commitment, the evidence signature, the evidence window and the verdict,
and needs no connection to MolTrust.

The whole event is checked offline with the same package:

```python
from moltrust_enforce import canary

report = canary.check(event, trust_list=trust_list)   # GET /canary/trust-list
assert report.ok, report.failures
```

It verifies the event signature against the key in the trust list, recomputes
the AER decision (`PERMIT`), checks that the signed evidence names the event's
`canary_ref`, `counterparty_did` and `attribution` and that the AER transaction
carries its `event_id`, and, for attribution `counterparty`, checks the holder
proof against the path of the event's `trigger`. Events from both triggers pass
the same check.

## Data model

Three tables, all owned by the application role
(`migrations/2026-10-20_leak_canary.sql`, token kind added by
`migrations/2026-10-20_leak_canary_token.sql`).

| Table | Holds |
|---|---|
| `leak_canaries` | `id`, `issuer_did`, `counterparty_did`, `mode`, `on_leak`, `kind` (`credential` or `token`), `proof_value` (the lookup key of a credential, the same value as in `credentials`), `token_hash` (the lookup key of a token: its hex SHA-256), `canary_ref`, `credential_type` (credentials only), `created_at` |
| `leak_canary_events` | `id`, `canary_id`, `issuer_did`, `counterparty_did`, `presenter_did`, `observed_at`, `event` (the signed event as JSONB) |
| `leak_canary_holds` | `issuer_did`, `counterparty_did`, `disposition`, `event_id`, `created_at`, `released_at`; one row per issuer and counterparty |

The credential itself lives only in `credentials`, as every credential does. A
token canary is not stored at all, only its SHA-256.

## Privacy

- **No personal data.** MolTrust stores DIDs, digests and timestamps. It stores
  no IP address, no user agent, no name, no e-mail address and no free text for
  canaries or events. The presenter appears only as the DID behind its API key,
  or as `null`.
- **Attribution only for the issuer.** Every canary endpoint resolves the issuer
  from the API key and returns only that issuer's rows. A request for another
  issuer's canary or event gets the same `404` as a request for one that does
  not exist.
- **Nothing for the presenter.** The verifier's answer to the presenter is the
  same for canaries and other credentials. The lookup happens after the answer
  is sent.
- **Retention.** This version has no automatic expiry for canaries, events or
  holds. A hold ends when the issuer releases it.

## Limits

A presentation proves that a canary is out, and names the counterparty only with
a holder proof by the counterparty's key (see *What a presentation proves*).
Because every issued credential can be fetched from IPFS by anyone with its CID,
an event of class `open` may stem from such a copy and says nothing about the
counterparty.

A canary detects presentations at the MolTrust verifier and, for a token
canary, at the token endpoint. A party that checks a leaked credential's or
token's signature on its own, without calling MolTrust, leaves no trace; so does
a party that only stores it.

The token endpoint requires an API key, so every token presentation has a
caller; its DID is the `presenter_did` of the event, or `null` when the key is
bound to no DID.

The hook can hold, but it cannot forbid a single counterparty: the kernel applies
`forbid` to the action binding before any constraint is read, so a per-counterparty
forbid would forbid the action for every counterparty. An issuer who wants that
removes the counterparty from its `allow` grants instead.

A presenter who sends no API key is unknown, and in `bound` mode an unknown
presenter counts as a leak. A counterparty that has its own relying parties
verify the credential will therefore trigger events; the issuer should hand out
`bound` canaries only where the counterparty is expected to present to the
issuer alone.
