'use strict';
/**
 * ES256 vectors for the gate (KEY-AGILITY, 20.11.2026).
 *
 * Kept apart from parity-vectors.json on purpose: that file pins the Ed25519
 * behaviour and must stay byte-identical. This one adds a P-256 registry key
 * next to the Ed25519 one and checks that each kid only verifies with its own
 * algorithm. Same rules as make-fixtures.js: constant seeds, pinned clock,
 * byte-identical output.
 *
 *   node test/make-es256-fixtures.js > test/es256-vectors.json
 */
process.env.MOLTRUST_NO_AUTO_REGISTER = '1';

const crypto = require('crypto');
const { gateFor, bindingString } = require('../index');

const b64 = (b) => Buffer.from(b).toString('base64url');

function edFromSeed(byte) {
  const der = Buffer.concat([
    Buffer.from('302e020100300506032b657004220420', 'hex'), Buffer.alloc(32, byte),
  ]);
  const priv = crypto.createPrivateKey({ key: der, format: 'der', type: 'pkcs8' });
  return { priv, pub: crypto.createPublicKey(priv) };
}

// P-256 private key from a constant scalar. ECDH derives the public point.
function ecFromSeed(byte, curve = 'prime256v1', crv = 'P-256', size = 32) {
  const d = Buffer.alloc(size, byte);
  const ecdh = crypto.createECDH(curve);
  ecdh.setPrivateKey(d);
  const pt = ecdh.getPublicKey();
  const x = pt.subarray(1, 1 + size);
  const y = pt.subarray(1 + size);
  const jwk = { kty: 'EC', crv, x: b64(x), y: b64(y), d: b64(d) };
  const priv = crypto.createPrivateKey({ key: jwk, format: 'jwk' });
  return { priv, pub: crypto.createPublicKey(priv), x: b64(x), y: b64(y) };
}

const edRegistry = edFromSeed(0x11);
const ecRegistry = ecFromSeed(0x44);
const ecImpostor = ecFromSeed(0x55);
const p384 = ecFromSeed(0x66, 'secp384r1', 'P-384', 48);
const agent = edFromSeed(0x22);

const ED_KID = 'parity-registry';
const EC_KID = 'parity-registry-es256';
const P384_KID = 'parity-registry-p384';
const DID = 'did:moltrust:0000000000000001';
const agentHex = Buffer.from(agent.pub.export({ format: 'jwk' }).x, 'base64url').toString('hex');

const jwks = {
  keys: [
    { kty: 'OKP', crv: 'Ed25519', kid: ED_KID,
      x: edRegistry.pub.export({ format: 'jwk' }).x, use: 'sig', alg: 'EdDSA' },
    { kty: 'EC', crv: 'P-256', kid: EC_KID, x: ecRegistry.x, y: ecRegistry.y,
      use: 'sig', alg: 'ES256' },
    { kty: 'EC', crv: 'P-384', kid: P384_KID, x: p384.x, y: p384.y,
      use: 'sig', alg: 'ES384' },
  ],
};

const NOW_MS = Date.UTC(2026, 9, 1, 12, 0, 0);
const NOW_S = Math.floor(NOW_MS / 1000);

// ECDSA signatures are randomized and Node has no RFC 6979 switch, so this
// file is not byte-identical across runs. The decisions are: each case states
// its expected outcome by hand, and generation aborts if the gate disagrees.
// Expectations are never read back from the implementation under test.
function sign(alg, priv, input, der) {
  if (alg === 'EdDSA') return crypto.sign(null, input, priv);
  const hash = alg === 'ES384' ? 'sha384' : 'sha256';
  return crypto.sign(hash, input, { key: priv, dsaEncoding: der ? 'der' : 'ieee-p1363' });
}

function attestation(o) {
  const opts = Object.assign({
    alg: 'ES256', signer: ecRegistry.priv, kid: EC_KID, trustScore: 75, der: false,
  }, o || {});
  const payload = {
    v: 2, did: DID, public_key: agentHex, trust_score: opts.trustScore, withheld: false,
    credential_types: ['AgentTrustCredential'],
    computed_at: new Date(NOW_MS).toISOString(),
    valid_until: new Date(NOW_MS + 3600 * 1000).toISOString(),
    policy_version: 'phase2',
  };
  const h = b64(JSON.stringify({ alg: opts.headerAlg || opts.alg, typ: 'JWT', kid: opts.kid }));
  const p = b64(JSON.stringify(payload));
  const sig = sign(opts.alg, opts.signer, Buffer.from(`${h}.${p}`, 'ascii'), opts.der);
  return `${h}.${p}.${b64(sig)}`;
}

function headers(token) {
  const ts = String(NOW_S);
  return {
    'x-moltrust-attestation': token,
    'x-moltrust-timestamp': ts,
    'x-moltrust-proof': b64(crypto.sign(
      null, bindingString('GET', '/api/agent/score', DID, ts), agent.priv)),
  };
}

const OPTS = { minScore: 50, allowWithheld: false, allowTrackRecord: true };

const OK = { allowed: true, reason: 'ok' };
const INVALID = { allowed: false, reason: 'attestation_invalid' };

const CASES = [
  ['ES256 attestation from the P-256 registry key', attestation(), OK],
  ['EdDSA attestation still verifies next to a P-256 key',
    attestation({ alg: 'EdDSA', signer: edRegistry.priv, kid: ED_KID }), OK],
  ['ES256 below the threshold', attestation({ trustScore: 49 }),
    { allowed: false, reason: 'score_below_minimum' }],
  ['ES256 signed by a foreign P-256 key', attestation({ signer: ecImpostor.priv }), INVALID],
  ['ES256 signature in DER instead of r||s', attestation({ der: true }), INVALID],
  ['header says ES256, kid is the Ed25519 key',
    attestation({ alg: 'EdDSA', headerAlg: 'ES256', signer: edRegistry.priv, kid: ED_KID }), INVALID],
  ['header says EdDSA, kid is the P-256 key', attestation({ headerAlg: 'EdDSA' }), INVALID],
  ['P-384 key is not accepted',
    attestation({ alg: 'ES384', signer: p384.priv, kid: P384_KID }), INVALID],
];

const vectors = CASES.map(([name, token, expected]) => {
  const v = { name, method: 'GET', path: '/api/agent/score', options: OPTS, headers: headers(token), expected };
  const got = gateFor(Object.assign({ jwks }, OPTS))(v.method, v.path, v.headers, NOW_MS);
  if (got.allowed !== expected.allowed || got.reason !== expected.reason) {
    throw new Error(`${name}: gate says ${got.reason} (${got.detail}), expected ${expected.reason}`);
  }
  return v;
});

process.stdout.write(JSON.stringify({
  generated_by: 'packages/x402/test/make-es256-fixtures.js',
  note: 'ES256 next to Ed25519. ECDSA is randomized, so signatures differ per run; decisions do not.',
  now_ms: NOW_MS,
  jwks,
  vectors,
}, null, 2) + '\n');
