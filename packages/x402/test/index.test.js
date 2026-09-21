'use strict';
/**
 * The gate denies unless everything checks out, and it never calls home.
 *
 * The test builds its own registry key and its own agent key, so the whole
 * flow runs without a server. That is also the property under test: if any of
 * this needed MolTrust to be reachable, this file could not run offline.
 *
 *   node test/index.test.js
 */

const assert = require('assert');
const crypto = require('crypto');

const {
  gateFor, verifyAttestation, bindingString, loadJwks,
} = require('../index');

const KID = 'test-registry-1';
const METHOD = 'POST';
const PATH = '/guard/vc/skill/issue';
const DID = 'did:moltrust:abc123';

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`  ok   ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`  FAIL ${name}\n       ${err.message}`);
  }
}

// --- key material ---------------------------------------------------------

function newKeypair() {
  return crypto.generateKeyPairSync('ed25519');
}

function rawPublic(publicKey) {
  const jwk = publicKey.export({ format: 'jwk' });
  return Buffer.from(jwk.x, 'base64url');
}

const registry = newKeypair();
const agent = newKeypair();
const agentHex = rawPublic(agent.publicKey).toString('hex');

const jwks = {
  keys: [{
    kty: 'OKP',
    crv: 'Ed25519',
    kid: KID,
    x: registry.publicKey.export({ format: 'jwk' }).x,
    use: 'sig',
    alg: 'EdDSA',
  }],
};

// --- builders -------------------------------------------------------------

function b64(buf) { return Buffer.from(buf).toString('base64url'); }

function makeAttestation(opts) {
  const o = Object.assign({
    signer: registry.privateKey,
    did: DID,
    publicKey: agentHex,
    trustScore: 75,
    withheld: false,
    credentialTypes: ['AgentTrustCredential'],
    validForSeconds: 3600,
    kid: KID,
    version: 2,
  }, opts || {});

  const now = new Date();
  const payload = {
    v: o.version,
    did: o.did,
    public_key: o.publicKey,
    trust_score: o.trustScore,
    withheld: o.withheld,
    credential_types: o.credentialTypes.slice().sort(),
    computed_at: now.toISOString(),
    valid_until: new Date(now.getTime() + o.validForSeconds * 1000).toISOString(),
    policy_version: 'phase2',
  };
  const h = b64(JSON.stringify({ alg: 'EdDSA', typ: 'JWT', kid: o.kid }));
  const p = b64(JSON.stringify(payload));
  const sig = crypto.sign(null, Buffer.from(`${h}.${p}`, 'ascii'), o.signer);
  return `${h}.${p}.${b64(sig)}`;
}

function makeHeaders(token, opts) {
  const o = Object.assign({
    signer: agent.privateKey, method: METHOD, path: PATH, did: DID,
    timestamp: Math.floor(Date.now() / 1000),
  }, opts || {});
  const ts = String(o.timestamp);
  const proof = crypto.sign(null, bindingString(o.method, o.path, o.did, ts), o.signer);
  return {
    'x-moltrust-attestation': token,
    'x-moltrust-timestamp': ts,
    'x-moltrust-proof': b64(proof),
  };
}

// --- the happy path -------------------------------------------------------

console.log('\n@moltrust/x402 — offline gate\n');

test('a good request passes', () => {
  const decide = gateFor({ minScore: 60, jwks });
  const d = decide(METHOD, PATH, makeHeaders(makeAttestation()));
  assert.strictEqual(d.allowed, true, d.detail);
  assert.strictEqual(d.did, DID);
  assert.strictEqual(d.trustScore, 75);
});

test('the gate opens no socket', () => {
  const net = require('net');
  const original = net.Socket.prototype.connect;
  net.Socket.prototype.connect = () => { throw new Error('the gate opened a socket'); };
  try {
    const decide = gateFor({ minScore: 60, jwks });
    assert.strictEqual(decide(METHOD, PATH, makeHeaders(makeAttestation())).allowed, true);
  } finally {
    net.Socket.prototype.connect = original;
  }
});

// --- deny by default ------------------------------------------------------

for (const drop of ['x-moltrust-attestation', 'x-moltrust-proof', 'x-moltrust-timestamp']) {
  test(`a missing ${drop} denies`, () => {
    const headers = makeHeaders(makeAttestation());
    delete headers[drop];
    const d = gateFor({ minScore: 60, jwks })(METHOD, PATH, headers);
    assert.strictEqual(d.allowed, false);
    assert.ok(['attestation_missing', 'proof_missing'].includes(d.reason), d.reason);
  });
}

test('no headers at all denies', () => {
  assert.strictEqual(gateFor({ minScore: 0, jwks })(METHOD, PATH, {}).allowed, false);
});

test('a foreign registry key denies', () => {
  const other = newKeypair();
  const token = makeAttestation({ signer: other.privateKey });
  const d = gateFor({ minScore: 60, jwks })(METHOD, PATH, makeHeaders(token));
  assert.strictEqual(d.allowed, false);
  assert.strictEqual(d.reason, 'attestation_invalid');
});

test('an unknown kid denies', () => {
  const token = makeAttestation({ kid: 'rotated-away' });
  const d = gateFor({ minScore: 60, jwks })(METHOD, PATH, makeHeaders(token));
  assert.strictEqual(d.allowed, false);
  assert.ok(d.detail.includes('no key for kid'), d.detail);
});

test('a tampered payload denies', () => {
  const token = makeAttestation({ trustScore: 10 });
  const [h, p, s] = token.split('.');
  const payload = JSON.parse(Buffer.from(p, 'base64url').toString('utf8'));
  payload.trust_score = 99;
  const forged = b64(JSON.stringify(payload));
  const d = gateFor({ minScore: 60, jwks })(METHOD, PATH, makeHeaders(`${h}.${forged}.${s}`));
  assert.strictEqual(d.allowed, false);
  assert.strictEqual(d.reason, 'attestation_invalid');
});

test('an expired attestation denies', () => {
  const token = makeAttestation({ validForSeconds: -1 });
  const d = gateFor({ minScore: 60, jwks })(METHOD, PATH, makeHeaders(token));
  assert.strictEqual(d.allowed, false);
  assert.ok(d.detail.includes('expired'), d.detail);
});

test('a v1 payload is not a gate attestation', () => {
  const token = makeAttestation({ version: 1 });
  const d = gateFor({ minScore: 60, jwks })(METHOD, PATH, makeHeaders(token));
  assert.strictEqual(d.allowed, false);
  assert.ok(d.detail.includes('not a gate attestation'), d.detail);
});

// --- proof of control -----------------------------------------------------

test('a proof from another key denies', () => {
  const impostor = newKeypair();
  const headers = makeHeaders(makeAttestation(), { signer: impostor.privateKey });
  const d = gateFor({ minScore: 60, jwks })(METHOD, PATH, headers);
  assert.strictEqual(d.allowed, false);
  assert.strictEqual(d.reason, 'proof_invalid');
});

test('a proof for another route denies', () => {
  const headers = makeHeaders(makeAttestation(), { path: '/guard/api/market/feed' });
  const d = gateFor({ minScore: 60, jwks })(METHOD, PATH, headers);
  assert.strictEqual(d.allowed, false);
  assert.strictEqual(d.reason, 'proof_invalid');
});

test('a proof for another method denies', () => {
  const headers = makeHeaders(makeAttestation(), { method: 'GET' });
  const d = gateFor({ minScore: 60, jwks })('POST', PATH, headers);
  assert.strictEqual(d.allowed, false);
});

test('a stale proof denies', () => {
  const headers = makeHeaders(makeAttestation(),
    { timestamp: Math.floor(Date.now() / 1000) - 3600 });
  const d = gateFor({ minScore: 60, jwks, maxAgeSeconds: 300 })(METHOD, PATH, headers);
  assert.strictEqual(d.allowed, false);
  assert.ok(d.detail.includes('old'), d.detail);
});

test('a proof from the future denies', () => {
  const headers = makeHeaders(makeAttestation(),
    { timestamp: Math.floor(Date.now() / 1000) + 4000 });
  const d = gateFor({ minScore: 60, jwks, maxAgeSeconds: 300 })(METHOD, PATH, headers);
  assert.strictEqual(d.allowed, false);
  assert.ok(d.detail.includes('future'), d.detail);
});

test('replay is caught when a store is supplied', () => {
  const used = new Set();
  const seen = (proof) => {
    if (used.has(proof)) return false;
    used.add(proof);
    return true;
  };
  const decide = gateFor({ minScore: 60, jwks, seen });
  const headers = makeHeaders(makeAttestation());
  assert.strictEqual(decide(METHOD, PATH, headers).allowed, true);
  const second = decide(METHOD, PATH, headers);
  assert.strictEqual(second.allowed, false);
  assert.strictEqual(second.reason, 'proof_replayed');
});

// --- score and credentials ------------------------------------------------

test('a score below the minimum denies', () => {
  const d = gateFor({ minScore: 60, jwks })(
    METHOD, PATH, makeHeaders(makeAttestation({ trustScore: 40 })));
  assert.strictEqual(d.allowed, false);
  assert.strictEqual(d.reason, 'score_below_minimum');
});

test('a withheld score denies', () => {
  const token = makeAttestation({ trustScore: null, withheld: true });
  const d = gateFor({ minScore: 60, jwks })(METHOD, PATH, makeHeaders(token));
  assert.strictEqual(d.allowed, false);
  assert.strictEqual(d.reason, 'score_withheld');
  assert.ok(d.detail.includes('not a low score'), d.detail);
});

test('withheld can be allowed deliberately', () => {
  const token = makeAttestation({ trustScore: null, withheld: true });
  const d = gateFor({ jwks, allowWithheld: true })(METHOD, PATH, makeHeaders(token));
  assert.strictEqual(d.allowed, true, d.detail);
});

test('allowWithheld does not bypass a score threshold', () => {
  const token = makeAttestation({ trustScore: null, withheld: true });
  const d = gateFor({ minScore: 60, jwks, allowWithheld: true })(
    METHOD, PATH, makeHeaders(token));
  assert.strictEqual(d.allowed, false);
  assert.strictEqual(d.reason, 'score_missing');
});

test('a required credential must be held', () => {
  const d = gateFor({ credentialType: 'SkillAuditCredential', jwks })(
    METHOD, PATH, makeHeaders(makeAttestation()));
  assert.strictEqual(d.allowed, false);
  assert.strictEqual(d.reason, 'credential_missing');
});

test('a held credential passes', () => {
  const token = makeAttestation({
    credentialTypes: ['AgentTrustCredential', 'SkillAuditCredential'],
  });
  const d = gateFor({ credentialType: 'SkillAuditCredential', jwks })(
    METHOD, PATH, makeHeaders(token));
  assert.strictEqual(d.allowed, true, d.detail);
});

// --- shape ----------------------------------------------------------------

test('a JWKS without keys is refused at build time', () => {
  assert.throws(() => gateFor({ minScore: 60, jwks: { keys: [] } }), /no keys/);
});

test('verifyAttestation is usable on its own', () => {
  const att = verifyAttestation(makeAttestation(), jwks);
  assert.strictEqual(att.did, DID);
  assert.strictEqual(att.publicKey, agentHex);
  assert.strictEqual(att.version, 2);
  assert.throws(() => verifyAttestation('not.a.jws', jwks));
});

test('loadJwks accepts an object unchanged', () => {
  assert.strictEqual(loadJwks(jwks).keys.length, 1);
});


// --- parity vectors -------------------------------------------------------
//
// The same file every port replays. Checked here too, so regenerating it is a
// deliberate act: if the reference stops reproducing its own vectors, the
// behaviour changed and every vendored copy is now wrong.

test('the reference reproduces its own parity vectors', () => {
  const fs = require('fs');
  const path = require('path');
  const file = path.join(__dirname, 'parity-vectors.json');
  const fixture = JSON.parse(fs.readFileSync(file, 'utf8'));
  assert.ok(fixture.vectors.length >= 15, 'too few vectors to be worth much');

  for (const v of fixture.vectors) {
    const decide = gateFor(Object.assign({ jwks: fixture.jwks }, v.options));
    const got = decide(v.method, v.path, v.headers, fixture.now_ms);
    assert.strictEqual(got.allowed, v.expected.allowed, `${v.name}: allowed`);
    assert.strictEqual(got.reason, v.expected.reason, `${v.name}: ${got.detail}`);
  }
});

console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed === 0 ? 0 : 1);
