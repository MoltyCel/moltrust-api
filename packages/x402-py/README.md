# moltrust-x402

An offline trust gate for x402 endpoints. Charge less, or open a route at all,
for an agent that can prove who it is and what MolTrust says about it — without
your server calling ours on the request path.

```bash
pip install moltrust-x402
curl -fsS https://api.moltrust.ch/.well-known/jwks.json > /etc/moltrust/jwks.json
```

Five lines put it in front of a route:

```python
from moltrust_x402 import require_moltrust, load_jwks

jwks = load_jwks("/etc/moltrust/jwks.json")
gate = require_moltrust(min_score=60, jwks=jwks)

decision = gate(request.method, request.path, request.headers)
```

`decision.allowed` is the answer, `decision.did` and `decision.trust_score`
describe the caller, and `decision.reason` names the denial with
`decision.detail` saying what to change. The TypeScript package
`@moltrust/x402` exposes the same three names and replays the same test
vectors, so a mixed stack answers a caller the same way in both languages.

## Where the code lives

The verification itself is `moltrust_enforce.gate`, and it stays there. This
distribution re-exports it so that somebody searching PyPI for "x402" finds it.
A second copy of the logic would be a fourth implementation to keep in step
with the parity vectors, and duplicated security code drifts without anybody
noticing, because each copy passes its own tests.

Every name you import here is the same object as in `moltrust_enforce`, which
a test asserts by identity rather than by behaviour.

## Why offline

Version 1 of the TypeScript package asked `api.moltrust.ch` for a score on
every request and believed the answer. Two things were wrong with that.

It put a network call from your server into your request path, so an outage on
our side became latency on yours, and a timeout became a decision nobody
designed. And it verified nothing — the response was plain JSON over TLS, so
anyone able to answer that request could set any score they liked.

This version calls nobody. The caller brings a MolTrust-signed attestation and
a signature made with its own key. Both are checked against a key set you
already hold. The only thing that ever has to reach us is a periodic refresh of
that key set, on your schedule, outside the request path.

## What the caller sends

| Header | What it is |
|---|---|
| `X-MolTrust-Attestation` | the `gate_attestation` field from `GET https://api.moltrust.ch/skill/trust-score/<did>` |
| `X-MolTrust-Timestamp` | unix seconds, the moment the proof was made |
| `X-MolTrust-Proof` | base64url Ed25519 signature over the binding string, made with the DID's own key |

The attestation is a compact JWS carrying the DID, its **public key**, the
trust score, whether the score is withheld, the credential types the DID holds,
and an expiry. The offline check depends on that public key: `did:moltrust:<hex>`
is a random identifier rather than a hash of a key, so without a signed
statement binding the two you could verify that a score exists for some DID and
never that the caller is that DID.

The proof binds one method, one path, one DID and one moment:

```
moltrust-gate/v1\nPOST\n/audit\ndid:moltrust:abc123\n1790003453
```

Signing a bare nonce would let anyone who saw the proof replay it against a
different route.

## A discount rather than a door

Two prices on the same route. Verified agents pay 0.05 USDC, everyone else
0.20, and nobody is turned away:

```python
decision = gate(request.method, request.path, request.headers)
price = "0.05" if decision.allowed else "0.20"
```

The other shape is a route only verified agents may call, with a credential
requirement on top:

```python
issue_gate = require_moltrust(
    min_score=70,
    credential_type="SkillAuditCredential",
    jwks=jwks,
)
```

## Deny by default

Everything that is not an explicit allow is a denial, including the cases that
look like infrastructure problems: a malformed header, an unknown key id, a key
set with nothing usable in it. Each denial names a reason, so a caller can fix
it without asking you what happened.

| `reason` | Meaning |
|---|---|
| `attestation_missing` | no `X-MolTrust-Attestation` |
| `proof_missing` | no proof or no timestamp |
| `attestation_invalid` | bad signature, unknown kid, expired, or a v1 payload |
| `proof_invalid` | wrong key, wrong route, wrong method, or stale |
| `proof_replayed` | seen before, when a replay store is configured |
| `score_withheld` | no score has been computed for this agent |
| `score_missing` | nothing to compare against `min_score` |
| `score_below_minimum` | the score is real and too low |
| `track_record_invalid` | the `track_record` is malformed or has no usable anchor |
| `credential_missing` | the DID does not hold a required credential |

**A withheld score is a denial.** A score we have not computed is neither a low
score nor a pass. `allow_withheld=True` lets those agents through, which is a
reasonable choice for a discount tier and a bad one for a spend authorisation.
It does not bypass `min_score`: a withheld score is `None`, so a numeric
threshold still denies.


## See it before you build it

`scripts/gate_probe.py` in the [moltrust-api
repository](https://github.com/MoltyCel/moltrust-api/blob/main/scripts/gate_probe.py)
asks one endpoint for its price twice — once plain, once presenting your
attestation — and prints both. It pays nothing and it signs with your key, not
ours; there is no wallet key in it and no address hardcoded.

```bash
pip install pynacl

# Read it first. --register generates a private key, and a script that
# generates a key is not one to run unread. It is 232 lines.
less scripts/gate_probe.py

# No DID yet? This mints one and prints its key. Two calls, no account.
python3 scripts/gate_probe.py --register

python3 scripts/gate_probe.py --did did:moltrust:... --key <64-hex-chars>
```

```
trust_score     None  (withheld: True)
track_record    anchored 0xf207a648cc41973000ec2594d8821623e04a813311d4577a869b3bc9a84a5adc

  without proof 50000   (0.050000 USDC)
  with proof    40000   (0.040000 USDC)

The proof is worth 20 % here. Nothing has been paid.
```

A DID minted a minute ago prints the same number twice and says why. Point
`--api` and `--guard` at your own host to probe a gate you run yourself.

## How an agent qualifies

A score is withheld until three endorsers exist, and an agent that registered
this morning has none. Left there, the gate denies every newcomer for a reason
none of them can act on. `allow_track_record=True` is the way out.

An agent reaches it in three steps:

1. **Register.** `GET /identity/register-challenge`, solve the proof of work,
   `POST /identity/register-pop`.
2. **Bind a wallet.** `GET /identity/nonce`, then `POST /identity/bind` with the
   DID, the address, `wallet_chain: "base"` and a signature over the nonce.
3. **Issue a track record.** `POST /credentials/track-record`. MolTrust reads
   what that wallet has done on Base and issues a `TrackRecordCredential`
   carrying the numbers and the thresholds they were judged against.

The wallet clears two published thresholds: **at least one transaction it sent
itself**, and **at least seven days of age**. Both are a cost: a wallet with its
own history cannot be produced at the moment someone wants a discount. The first
one bites hardest: most agent marketplaces relay gaslessly, so a worker wallet
can be busy for weeks and still sit at nonce 0.

```python
gate = require_moltrust(
    min_score=50,
    allow_track_record=True,   # off by default
    jwks=JWKS,
)
```

With it on, an attestation carrying a well-formed `track_record` passes where a
withheld score alone would not, and `min_score` is not consulted for that caller
— there is no score to compare. `decision.via` says which requirement carried
it, `"score"` or `"track_record"`.

The substitute covers a score **nobody computed**, never one that was computed
and came out low. `track_record` reaches the attestation only once the
credential is anchored, which runs in a batch every two hours; `anchor_tx` is
the field a relying party checks, and it cannot be filled in before the
transaction is real. The anchor itself is not verified here — this module makes
no network calls. Read `decision.track_record["anchor_tx"]` and check it on your
own schedule if you want that too.
## Replay

The proof is fresh within `max_age_seconds` (default 300). Inside that window
the same proof can be presented more than once unless you pass `seen`, a
callable that records a proof and returns `False` if it has seen it before:

```python
used: dict[str, float] = {}

def seen(proof: str) -> bool:
    if proof in used:
        return False
    used[proof] = time.time()
    return True

gate = require_moltrust(min_score=60, jwks=jwks, seen=seen)
```

Without it the gate is replay-resistant rather than replay-proof. That is
stated here instead of left to be discovered.

## Keeping the key set current

```bash
curl -fsS https://api.moltrust.ch/.well-known/jwks.json > /etc/moltrust/jwks.json.new \
  && mv /etc/moltrust/jwks.json.new /etc/moltrust/jwks.json
```

Run it on whatever schedule suits you and reload the process. A rotated key
shows up as `attestation_invalid` with `no key for kid …` in the detail, which
is the one denial that means *refresh the file* and not *the caller did
something wrong*.

MIT.
