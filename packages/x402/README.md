# @moltrust/x402

An offline trust gate for x402 endpoints. Charge less, or open a route at all,
for an agent that can prove who it is and what MolTrust says about it — without
your server calling ours on the request path.

```js
const express = require('express');
const { requireMolTrust, loadJwks } = require('@moltrust/x402');

const jwks = loadJwks('/etc/moltrust/jwks.json');

app.post('/audit',
  requireMolTrust({ minScore: 60, jwks }),
  (req, res) => res.json({ ok: true, agent: req.moltrust.did }));
```

## Why offline

Version 1 of this package asked `api.moltrust.ch` for a score on every request
and believed the answer. Two things were wrong with that.

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
and an expiry. The public key is what makes the offline check possible:
`did:moltrust:<hex>` is a random identifier, not a hash of a key, so without a
signed statement binding the two you could verify that a score exists for some
DID and never that the caller is that DID.

The proof binds one method, one path, one DID and one moment:

```
moltrust-gate/v1\nPOST\n/audit\ndid:moltrust:abc123\n1790003453
```

Signing a bare nonce would let anyone who saw the proof replay it against a
different route.

## Worked example: a discount for verified agents

Two prices on the same route. Verified agents pay 0.05 USDC, everyone else
0.20. Nothing is denied — the gate decides the price, not the access.

```js
const { gateFor, loadJwks } = require('@moltrust/x402');

const jwks = loadJwks('/etc/moltrust/jwks.json');
const verified = gateFor({ minScore: 60, jwks });

app.get('/guard/api/agent/score/:address', (req, res, next) => {
  const decision = verified(req.method, req.path, req.headers);
  req.price = decision.allowed ? '0.05' : '0.20';
  req.moltrust = decision;
  next();
}, x402Paywall(), handler);
```

And the other shape — a route that only verified agents may call at all, plus a
credential requirement:

```js
app.post('/guard/vc/skill/issue',
  requireMolTrust({
    minScore: 70,
    credentialType: 'SkillAuditCredential',
    jwks,
  }),
  handler);
```

## Deny by default

Everything that is not an explicit allow is a denial, including the cases that
look like infrastructure problems: a malformed header, an unknown key id, a key
set with nothing usable in it. Each denial names a reason, so a caller can fix
it without a support round-trip.

| `reason` | Meaning |
|---|---|
| `attestation_missing` | no `X-MolTrust-Attestation` |
| `proof_missing` | no proof or no timestamp |
| `attestation_invalid` | bad signature, unknown kid, expired, or a v1 payload |
| `proof_invalid` | wrong key, wrong route, wrong method, or stale |
| `proof_replayed` | seen before, when a replay store is configured |
| `score_withheld` | no score has been computed for this agent |
| `score_missing` | nothing to compare against `minScore` |
| `score_below_minimum` | the score is real and too low |
| `credential_missing` | the DID does not hold a required credential |

**A withheld score is a denial.** A score we have not computed is not a low
score, and it is not a pass. `allowWithheld: true` lets those agents through —
a reasonable choice for a discount tier, a bad one for a spend authorisation.
It does not bypass `minScore`: a withheld score is `null`, so a numeric
threshold still denies.

## Replay

The proof is fresh within `maxAgeSeconds` (default 300). Inside that window the
same proof can be presented more than once unless you pass `seen`, a function
that records a proof and returns `false` if it has seen it before:

```js
const used = new Map();
const seen = (proof) => {
  if (used.has(proof)) return false;
  used.set(proof, Date.now());
  return true;
};
requireMolTrust({ minScore: 60, jwks, seen });
```

Without it the gate is replay-resistant, not replay-proof. That is stated here
rather than left to be discovered.

## Keeping the key set current

```bash
curl -fsS https://api.moltrust.ch/.well-known/jwks.json > /etc/moltrust/jwks.json.new \
  && mv /etc/moltrust/jwks.json.new /etc/moltrust/jwks.json
```

Run it on whatever schedule suits you and reload the process. A rotated key
shows up as `attestation_invalid` with `no key for kid …` in the detail, which
is the one denial that means *refresh the file*, not *the caller did something
wrong*.

## Testing

```bash
node test/index.test.js
```

The suite builds its own registry key and its own agent key, so it runs with no
network at all. That is also the point: if any of this needed MolTrust to be
reachable, the tests could not run offline either.

## Upgrading from 1.x

`requireScore({ minScore })` is gone. It took a wallet address out of the
payment header and asked our API about it — no signature anywhere in the chain,
and no way for the caller to prove the wallet was theirs. There is no shim,
because a shim would have to either keep calling our API or silently start
denying every caller that has not been told about the new headers. Both are
worse than a version bump that says what changed.

MIT.
