// A paid endpoint that gives a discount to an agent that can prove itself,
// with no MolTrust account and no call to us on the request path.
//
//   node server.js        then: curl -i localhost:3000/price
//
// Two checks, and they answer different questions.
//
//   the offline gate   is the attestation ours, and what does it say
//   confirmAnchor      is the credential in a root that was written to Base
//
// The first needs our JWKS, fetched once and cached on disk. The second needs
// nothing of ours at all — it reads Base through whatever endpoint you give it.
// A host that only wants the second can skip the JWKS entirely and still
// charge less for a caller whose track record is on chain.

const http = require('node:http');
const { confirmAnchor } = require('./confirm-anchor.js');

const FULL = 50000;         // 0.050000 USDC, in the smallest unit
const DISCOUNT_BPS = 2000;  // 20 %

// ── the one line that sets the discount ──────────────────────────────────────
const price = (proved) => (proved ? FULL * (10000 - DISCOUNT_BPS) / 10000 : FULL);
// ─────────────────────────────────────────────────────────────────────────────

const RPC = process.env.BASE_RPC || 'https://mainnet.base.org';

http.createServer(async (req, res) => {
  const send = (code, body) => {
    res.writeHead(code, { 'content-type': 'application/json' });
    res.end(JSON.stringify(body, null, 1));
  };

  // The caller presents the credential document it already holds. No lookup,
  // no account, no key: the document carries its own anchor and path.
  let credential = null;
  const raw = req.headers['x-moltrust-credential'];
  if (raw) {
    try {
      credential = JSON.parse(Buffer.from(raw, 'base64').toString('utf8'));
    } catch {
      return send(400, { error: 'X-MolTrust-Credential is not base64 JSON' });
    }
  }

  if (!credential) {
    // No proof offered is not an error. It is full price.
    return send(402, { price: price(false), reason: 'no credential presented',
                       hint: 'present X-MolTrust-Credential for a discount' });
  }

  let anchor;
  try {
    anchor = await confirmAnchor(credential, { rpc: RPC });
  } catch (e) {
    // A proof we could not check is not a proof. Full price, and say why —
    // never a discount on an unreadable answer.
    return send(402, { price: price(false), reason: `anchor not confirmed: ${e.message}` });
  }

  if (!anchor.ok) {
    return send(402, { price: price(false), reason: 'credential does not replay to the root on chain',
                       replayedRoot: anchor.replayedRoot, rootOnChain: anchor.rootOnChain });
  }

  send(402, {
    price: price(true),
    discount_bps: DISCOUNT_BPS,
    reason: 'anchor confirmed on Base',
    anchor_tx: anchor.anchorTx,
    root: anchor.rootOnChain,
    note: 'checked against Base directly. api.moltrust.ch was not called.',
  });
}).listen(3000, () => console.log('listening on :3000 · RPC ' + RPC));
