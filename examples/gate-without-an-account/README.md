# A discount for an agent that can prove itself — without an account

One page. No MolTrust account, no API key, and nothing of ours on your request
path.

## The line that sets the discount

```js
const price = (proved) => (proved ? FULL * (10000 - DISCOUNT_BPS) / 10000 : FULL);
```

That is the whole business decision. `DISCOUNT_BPS = 2000` is 20 %; set it to
whatever the proof is worth to you, and to `0` while you measure whether anyone
presents one.

## Two checks that answer different questions

| | question | needs |
|---|---|---|
| **offline gate** (`@moltrust/x402`) | is this attestation ours, and what does it say | our JWKS, fetched once, cached on disk |
| **`confirmAnchor`** (this folder) | is this credential in a Merkle root that was written to Base | a Base endpoint of your choosing |

The second needs **nothing of ours**. Use both if you want both answers; use
only the second if you would rather not depend on us at all.

## Install

```bash
npm install @moltrust/x402
curl -fsS https://api.moltrust.ch/.well-known/jwks.json > jwks.json
```

**Measured on 2026-10-05, empty folder to first decision: 1 second.** The
package has **zero dependencies** and `node_modules` comes to 40 KB; the JWKS
is 999 bytes with four keys. Nobody needs ten minutes for this — the install
was never the hard part.

```js
const { loadJwks, gateFor } = require('@moltrust/x402');
const jwks = loadJwks('./jwks.json');
const gate = gateFor({ minScore: 60, jwks });

const d = gate(req.method, req.url, req.headers);   // → { allowed, reason, … }
```

`gateFor` takes a method, a path and headers, so it fits Express, Fastify,
Hono, a Worker, a bare `http.createServer` — anything. `requireMolTrust` is the
Express wrapper if you want it. The Python package `moltrust-x402` exposes the
same three names and replays the same test vectors.

## The chain check, in full

`@moltrust/x402` stops short of confirming the anchor on purpose, and says so
in its own source: *"Confirming the anchor on chain is deliberately not done
here — that is a check reads `decision.trackRecord.anchor_tx` and verifies it
on its own."* So here it is, about forty lines, standard library plus one
`fetch`:

```js
const { confirmAnchor } = require('./confirm-anchor.js');

const r = await confirmAnchor(
  { merkle_proof: anchor.merkle_proof, anchor_tx: anchor.anchor_tx },
  { rpc: process.env.BASE_RPC }        // yours, not ours
);
// r.ok, r.leaf, r.replayedRoot, r.rootOnChain
```

**Verified on 2026-10-05 against five published anchors: 5 of 5 replay to the
root on chain.**

### Where the inputs come from, with no account

`https://moltrust.ch/registry-proof.json` is public and carries, for every
anchored credential, `merkle_proof: {leaf, path}`, `anchor_tx` and
`anchor_block`. It also documents its own recipe in its header:

```
leaf_preimage    sha256(credential_id|subject_did|credential_type|issued_at|proof_value)
calldata_prefix  MolTrust/VC/v1
chain            eip155:8453
```

For a request-path check you do not fetch that file — the **caller presents its
own credential**, and you confirm it. The file is how you check the recipe, and
how you test your implementation against known-good data before you trust it.

## What each check does not prove

**`confirmAnchor` does not prove the numbers are true.** It proves the
credential sits in a root that was on Base at a stated block, so the set cannot
have been extended afterwards. Whether the wallet really has the history the
credential claims is what our signature is for — that is the offline gate.

**The offline gate does not prove the credential was anchored.** It checks our
signature over the attestation. An attestation can be ours, current and
correct, and still describe a credential whose anchor you never looked at.

Neither is a substitute for the other, and a host that wants only one should
pick the one that matches what it is pricing.

## Failure is full price, never a discount

Every path that cannot complete the check charges full price and says why:

| answer | price |
|---|---|
| no credential presented | full, `no credential presented` |
| anchor could not be read | full, `anchor not confirmed: <reason>` |
| replays to a different root | full, plus both roots in the body |
| confirmed | discounted, with `anchor_tx` and the root |

**A proof we could not check is not a proof.** An endpoint that gives a
discount on an unreadable answer is giving a discount to anyone who can make
the check fail.

## Run it

```bash
node server.js
curl -i localhost:3000/price
curl -i -H "X-MolTrust-Credential: $(base64 -w0 < credential.json)" \
     localhost:3000/price
```

`server.js` is 70 lines and holds no state. `BASE_RPC` picks the endpoint; the
default is the public one, which promises nothing and is entitled to refuse at
any moment — point it at whatever you already pay for.
