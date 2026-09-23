'use strict';
/**
 * Generate the parity vectors the vendored ports are checked against.
 *
 * The gate exists twice more than it should: here, in moltrust-enforce, and
 * vendored into moltguard, which is a separate repository and cannot import
 * this package until it is on npm. Three implementations of a security check
 * drift, and the drift is invisible — each one passes its own tests.
 *
 * So the reference emits fixed vectors and every port replays them. Keys come
 * from constant seeds, timestamps are pinned, and `now` is passed in, so the
 * file is byte-identical on every run and a diff means a behaviour change.
 *
 *   node test/make-fixtures.js > test/parity-vectors.json
 */

const crypto = require('crypto');
const { gateFor, bindingString } = require('../index');

const b64 = (b) => Buffer.from(b).toString('base64url');

// Ed25519 private keys from constant seeds, via the PKCS#8 wrapper Node wants.
function keyFromSeed(byte) {
  const seed = Buffer.alloc(32, byte);
  const der = Buffer.concat([
    Buffer.from('302e020100300506032b657004220420', 'hex'),
    seed,
  ]);
  const priv = crypto.createPrivateKey({ key: der, format: 'der', type: 'pkcs8' });
  return { priv, pub: crypto.createPublicKey(priv) };
}

const registry = keyFromSeed(0x11);
const agent = keyFromSeed(0x22);
const impostor = keyFromSeed(0x33);

const KID = 'parity-registry';
const DID = 'did:moltrust:0000000000000001';
const agentHex = Buffer.from(agent.pub.export({ format: 'jwk' }).x, 'base64url').toString('hex');

const jwks = {
  keys: [{
    kty: 'OKP', crv: 'Ed25519', kid: KID,
    x: registry.pub.export({ format: 'jwk' }).x, use: 'sig', alg: 'EdDSA',
  }],
};

// Pinned clock. Everything is expressed relative to it, so the vectors do not
// expire and a port cannot pass by being run at a lucky moment.
const NOW_MS = Date.UTC(2026, 9, 1, 12, 0, 0);
const NOW_S = Math.floor(NOW_MS / 1000);

// A well-formed track record, as the registry emits it for a DID with a bound
// Base wallet whose history clears the threshold.
const TRACK_RECORD = {
  issued_at: new Date(NOW_MS - 86400 * 1000).toISOString(),
  anchor_tx: '0x' + 'ab'.repeat(32),
};

function attestation(o) {
  const opts = Object.assign({
    signer: registry.priv, kid: KID, v: 2, did: DID, publicKey: agentHex,
    trustScore: 75, withheld: false, credentialTypes: ['AgentTrustCredential'],
    validForSeconds: 3600, trackRecord: undefined,
  }, o || {});
  const payload = {
    v: opts.v,
    did: opts.did,
    public_key: opts.publicKey,
    trust_score: opts.trustScore,
    withheld: opts.withheld,
    credential_types: opts.credentialTypes.slice().sort(),
    computed_at: new Date(NOW_MS).toISOString(),
    valid_until: new Date(NOW_MS + opts.validForSeconds * 1000).toISOString(),
    policy_version: 'phase2',
  };
  // Omitted rather than null when absent: a gate that reads a null here and
  // carries on is the failure the whole field exists to prevent.
  if (opts.trackRecord !== undefined) payload.track_record = opts.trackRecord;
  const h = b64(JSON.stringify({ alg: 'EdDSA', typ: 'JWT', kid: opts.kid }));
  const p = b64(JSON.stringify(payload));
  return `${h}.${p}.${b64(crypto.sign(null, Buffer.from(`${h}.${p}`, 'ascii'), opts.signer))}`;
}

function headers(token, o) {
  const opts = Object.assign({
    signer: agent.priv, method: 'GET', path: '/api/agent/score', did: DID,
    timestamp: NOW_S,
  }, o || {});
  const ts = String(opts.timestamp);
  return {
    'x-moltrust-attestation': token,
    'x-moltrust-timestamp': ts,
    'x-moltrust-proof': b64(crypto.sign(
      null, bindingString(opts.method, opts.path, opts.did, ts), opts.signer)),
  };
}

const METHOD = 'GET';
const PATH = '/api/agent/score';

// The moltguard configuration, so the vectors test the gate as it is deployed:
// 20 % off at score >= 50, withheld denied, a track record accepted in its
// place.
const DISCOUNT_OPTS = { minScore: 50, allowWithheld: false, allowTrackRecord: true };

// The same gate before the track record existed. Kept as its own option set so
// the vectors show what the switch does rather than only what it allows.
const SCORE_ONLY_OPTS = { minScore: 50, allowWithheld: false };

const CASES = [
  ['good request, score above the threshold', attestation(), headers(attestation()), DISCOUNT_OPTS],
  ['score exactly at the threshold', attestation({ trustScore: 50 }), null, DISCOUNT_OPTS],
  ['score just below the threshold', attestation({ trustScore: 49.9 }), null, DISCOUNT_OPTS],
  ['withheld score', attestation({ trustScore: null, withheld: true }), null, DISCOUNT_OPTS],
  ['withheld allowed deliberately', attestation({ trustScore: null, withheld: true }), null,
    { allowWithheld: true }],
  ['v1 payload', attestation({ v: 1 }), null, DISCOUNT_OPTS],
  ['expired', attestation({ validForSeconds: -1 }), null, DISCOUNT_OPTS],
  ['unknown kid', attestation({ kid: 'rotated-away' }), null, DISCOUNT_OPTS],
  ['foreign registry signature', attestation({ signer: impostor.priv }), null, DISCOUNT_OPTS],
  ['credential required and held', attestation({ credentialTypes: ['AgentTrustCredential', 'SkillAuditCredential'] }),
    null, { minScore: 50, credentialType: 'SkillAuditCredential' }],
  ['credential required and missing', attestation(), null,
    { minScore: 50, credentialType: 'SkillAuditCredential' }],

  // The track record, and the four ways it does not work.
  ['withheld score carried by a track record',
    attestation({ trustScore: null, withheld: true, trackRecord: TRACK_RECORD }),
    null, DISCOUNT_OPTS],
  ['track record present, host has not enabled it',
    attestation({ trustScore: null, withheld: true, trackRecord: TRACK_RECORD }),
    null, SCORE_ONLY_OPTS],
  // The negative case that matters: an issuer only puts anchor_tx in when the
  // credential is anchored, and it only issues at all for a bound wallet. A
  // track record without that anchor is not a track record, and buys nothing.
  ['track record without an anchor',
    attestation({ trustScore: null, withheld: true,
      trackRecord: { issued_at: TRACK_RECORD.issued_at } }),
    null, DISCOUNT_OPTS],
  ['track record with a malformed anchor',
    attestation({ trustScore: null, withheld: true,
      trackRecord: { issued_at: TRACK_RECORD.issued_at, anchor_tx: '0xdeadbeef' } }),
    null, DISCOUNT_OPTS],
  // A track record stands in for a score nobody has computed, never for one
  // that was computed and came out low.
  ['track record does not rescue a low score',
    attestation({ trustScore: 12, withheld: false, trackRecord: TRACK_RECORD }),
    null, DISCOUNT_OPTS],
];

const vectors = [];
for (const [name, token, fixedHeaders, opts] of CASES) {
  const h = fixedHeaders || headers(token);
  const decision = gateFor(Object.assign({ jwks }, opts))(METHOD, PATH, h, NOW_MS);
  vectors.push({ name, method: METHOD, path: PATH, options: opts, headers: h,
                 expected: { allowed: decision.allowed, reason: decision.reason } });
}

// Cases where the proof, not the attestation, is what fails.
const good = attestation();
const PROOF_CASES = [
  ['proof signed by another key', headers(good, { signer: impostor.priv })],
  ['proof made for another path', headers(good, { path: '/api/sybil/scan' })],
  ['proof made for another method', headers(good, { method: 'POST' })],
  ['proof an hour old', headers(good, { timestamp: NOW_S - 3600 })],
  ['proof an hour in the future', headers(good, { timestamp: NOW_S + 3600 })],
  ['no headers at all', {}],
];
for (const [name, h] of PROOF_CASES) {
  const decision = gateFor(Object.assign({ jwks }, DISCOUNT_OPTS))(METHOD, PATH, h, NOW_MS);
  vectors.push({ name, method: METHOD, path: PATH, options: DISCOUNT_OPTS, headers: h,
                 expected: { allowed: decision.allowed, reason: decision.reason } });
}

process.stdout.write(JSON.stringify({
  generated_by: '@moltrust/x402 test/make-fixtures.js',
  note: 'Pinned clock and constant key seeds. Regenerate only when behaviour '
      + 'is meant to change, and expect every port to fail until it is updated.',
  now_ms: NOW_MS,
  jwks,
  vectors,
}, null, 1) + '\n');
