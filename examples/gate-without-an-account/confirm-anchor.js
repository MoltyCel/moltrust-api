// Confirm a MolTrust track record against Base, with no MolTrust account and
// no call to api.moltrust.ch on the request path.
//
// @moltrust/x402 deliberately stops short of this. Its own words:
//
//   "Confirming the anchor on chain is deliberately not done here — that is a
//    check reads `decision.trackRecord.anchor_tx` and verifies it on its own"
//
// So every host that wants the stronger check has to write this, and nobody
// ships it. Here it is: forty lines, node's standard library plus one fetch to
// a public Base endpoint of your choosing. We are not in the path.
//
// What it proves: the credential the caller presented is one of the leaves of
// a Merkle root that was written to Base in the transaction the credential
// names. It cannot have been added to that root afterwards.
//
// What it does not prove: that the numbers in the credential are true. That is
// what our signature is for, and the offline gate checks it. Use both.

const crypto = require('node:crypto');

const CALLDATA_PREFIX = 'MolTrust/VC/v1/';

const sha256 = (b) => crypto.createHash('sha256').update(b).digest();

/** The five pipe-joined fields the leaf is taken over. */
function leafPreimage(doc) {
  const ev = (doc.evidence || [])[0] || {};
  if (!ev.credentialId) throw new Error('evidence[0].credentialId missing');
  const types = (doc.type || []).filter((t) => t !== 'VerifiableCredential');
  if (types.length !== 1) throw new Error(`cannot pick a type from ${doc.type}`);

  // proof.created, not validFrom: the two W3C data model versions name the
  // top-level field differently and this one is in both.
  const created = doc.proof.created;
  let issuedAt;
  if (created.endsWith('Z')) issuedAt = created.slice(0, -1);
  else if (created.endsWith('+00:00')) issuedAt = created.slice(0, -6);
  else throw new Error(`proof.created is not UTC: ${created}`);

  return [ev.credentialId, doc.credentialSubject.id, types[0], issuedAt,
           doc.proof.proofValue].join('|');
}

/** Walk the sibling path from the leaf to the root. */
function replay(leaf, path) {
  let node = leaf;
  for (const step of path) {
    const sib = Buffer.from(step.hash, 'hex');
    if (step.position === 'left') node = sha256(Buffer.concat([sib, node]));
    else if (step.position === 'right') node = sha256(Buffer.concat([node, sib]));
    else throw new Error(`unknown position ${step.position}`);
  }
  return node;
}

/** The root written in that transaction's calldata, read straight from Base. */
async function rootOnChain(txHash, rpc) {
  const r = await fetch(rpc, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1,
                           method: 'eth_getTransactionByHash', params: [txHash] }),
  });
  const { result, error } = await r.json();
  if (error) throw new Error(`rpc: ${error.message}`);
  if (!result) throw new Error(`transaction ${txHash} not found on chain`);
  const text = Buffer.from(result.input.slice(2), 'hex')
    .toString('utf8').replace(/\0+$/, '');
  if (!text.startsWith(CALLDATA_PREFIX)) {
    throw new Error(`calldata is not a MolTrust anchor: ${text.slice(0, 40)}`);
  }
  return text.slice(CALLDATA_PREFIX.length);
}

/**
 * True when a track record replays to the root written on Base.
 *
 * Takes the shape that actually exists in public: `merkle_proof` as
 * moltrust.ch/registry-proof.json publishes it, plus the anchor transaction.
 *
 *   confirmAnchor({ merkle_proof: {leaf, path}, anchor_tx }, { rpc })
 *
 * The file documents its own recipe — `leaf_preimage` and `calldata_prefix`
 * are fields in it — so a host can check this without asking us anything and
 * without holding an account. `rpc` is yours; the default is the public Base
 * endpoint, which promises nothing and is entitled to refuse.
 *
 * Pass `credential` instead of `merkle_proof.leaf` to compute the leaf from a
 * credential document you hold, which is what an agent presenting its own
 * credential would do.
 */
async function confirmAnchor(input, { rpc = 'https://mainnet.base.org' } = {}) {
  const mp = input.merkle_proof || input.merkleProof || {};
  const txHash = input.anchor_tx || input.anchorTx;
  if (!txHash) throw new Error('anchor_tx missing');
  if (!Array.isArray(mp.path)) throw new Error('merkle_proof.path missing');

  const leaf = input.credential
    ? sha256(Buffer.from(leafPreimage(input.credential), 'utf8'))
    : Buffer.from(mp.leaf, 'hex');
  if (input.credential && mp.leaf && leaf.toString('hex') !== mp.leaf) {
    // The document and the published leaf disagree. Not a pass either way.
    throw new Error(`leaf from the document is ${leaf.toString('hex')}, `
                    + `the proof says ${mp.leaf}`);
  }

  const replayed = replay(leaf, mp.path).toString('hex');
  const onChain = await rootOnChain(txHash, rpc);
  return {
    ok: replayed === onChain,
    leaf: leaf.toString('hex'),
    replayedRoot: replayed,
    rootOnChain: onChain,
    anchorTx: txHash,
  };
}

module.exports = { confirmAnchor, leafPreimage, replay, rootOnChain };
