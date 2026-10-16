'use strict';
/**
 * The invitation in the 403, against the vectors the Python package also runs.
 *
 *   node test/invite.test.js
 *
 * Two kinds of probe throughout: one that the invite appears where it should,
 * and one that it stays away everywhere else. The second kind is the point.
 * A gate that attaches a referrer to the wrong denial credits registrations
 * to whoever happens to run that gate, and nobody downstream can tell.
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const {
  gateBlock, inviteFor, parseOperatorDid, requireMolTrust,
  REGISTER_URL, CHALLENGE_URL, INVITE_SOURCE, INVITE_TEXT,
} = require('../index');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`  ok  ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`FAIL  ${name}\n      ${err.message}`);
  }
}

const OPERATOR = 'did:moltrust:341e9f0e5a03b7df';

// -- the shared vectors ------------------------------------------------------

test('the package reproduces the shared invite vectors', () => {
  const file = path.join(__dirname, 'invite-vectors.json');
  const fixture = JSON.parse(fs.readFileSync(file, 'utf8'));
  assert.ok(fixture.vectors.length >= 15, 'too few vectors to be worth much');

  for (const v of fixture.vectors) {
    const got = gateBlock(v.decision, parseOperatorDid(v.operator_did));
    assert.deepStrictEqual(got, v.expected, v.name);
  }
});

test('every vector file in the repo is the same file', () => {
  const here = fs.readFileSync(path.join(__dirname, 'invite-vectors.json'));
  const there = path.join(
    __dirname, '..', '..', 'x402-py', 'tests', 'invite-vectors.json',
  );
  // If the two drift, "both packages pass their vectors" stops meaning they
  // agree with each other.
  assert.ok(here.equals(fs.readFileSync(there)), 'the two copies differ');
});

// -- parseOperatorDid --------------------------------------------------------

test('parseOperatorDid takes a well-formed DID and trims it', () => {
  assert.strictEqual(parseOperatorDid(OPERATOR), OPERATOR);
  assert.strictEqual(parseOperatorDid(`  ${OPERATOR}\n`), OPERATOR);
});

test('parseOperatorDid treats anything else as absent', () => {
  for (const bad of [
    undefined, null, '', '   ', 'did:moltrust:ZZZZ', 'did:web:moltrust.ch',
    'did:moltrust:341E9F0E5A03B7DF', 'did:moltrust:341e9f0e5a03b7d',
    'did:moltrust:341e9f0e5a03b7dff', OPERATOR + ' and more', 42, {},
  ]) {
    assert.strictEqual(parseOperatorDid(bad), null, String(bad));
  }
});

// -- the two rules -----------------------------------------------------------

test('attestation_missing with an operator DID gets the invite', () => {
  const invite = inviteFor({ reason: 'attestation_missing' }, OPERATOR);
  assert.deepStrictEqual(invite, {
    register_url: REGISTER_URL,
    challenge_url: CHALLENGE_URL,
    referrer_did: OPERATOR,
    source: INVITE_SOURCE,
    text: INVITE_TEXT,
  });
});

test('no operator DID means no invite and no fallback referrer', () => {
  assert.strictEqual(inviteFor({ reason: 'attestation_missing' }, null), null);
  const block = gateBlock({ reason: 'attestation_missing', detail: 'd' }, null);
  assert.deepStrictEqual(block, { reason: 'attestation_missing', detail: 'd' });
  assert.ok(!('invite' in block), 'no invite key at all, not an empty one');
  assert.ok(!JSON.stringify(block).includes('referrer_did'));
});

test('no other denial carries an invite', () => {
  for (const reason of [
    'proof_missing', 'attestation_invalid', 'proof_invalid', 'proof_replayed',
    'score_withheld', 'track_record_invalid', 'score_missing',
    'score_below_minimum', 'credential_missing', 'ok',
  ]) {
    assert.strictEqual(inviteFor({ reason }, OPERATOR), null, reason);
  }
});

test('a denial that names the caller carries no invite', () => {
  const decision = {
    reason: 'attestation_missing', detail: '', did: 'did:moltrust:0000000000000001',
  };
  assert.strictEqual(inviteFor(decision, OPERATOR), null);
});

test('the source is the package, not the moltguard service', () => {
  // The two have to be countable apart, or the cohort figures cannot say
  // whether third-party gates bring anyone in.
  assert.strictEqual(INVITE_SOURCE, 'x402-package-403');
  assert.notStrictEqual(INVITE_SOURCE, 'moltguard-402');
});

// -- the middleware ----------------------------------------------------------

function fakeExchange(options, headers) {
  const captured = {};
  const req = { method: 'POST', path: '/paid', headers: headers || {} };
  const res = {
    status(code) { captured.code = code; return this; },
    json(body) { captured.body = body; return this; },
  };
  let nexted = false;
  requireMolTrust(options)(req, res, () => { nexted = true; });
  captured.nexted = nexted;
  return captured;
}

// A real key, because loadJwks refuses an empty set. No request below ever
// presents an attestation, so the key is never used to verify anything -- it
// is here so the gate can be built at all.
const crypto = require('crypto');
const { publicKey } = crypto.generateKeyPairSync('ed25519');
const JWKS = {
  keys: [{
    kty: 'OKP',
    crv: 'Ed25519',
    kid: 'invite-test-1',
    x: publicKey.export({ type: 'spki', format: 'der' })
      .subarray(-32).toString('base64url'),
  }],
};

test('the 403 carries the moltrustGate block with the invite', () => {
  const out = fakeExchange({ jwks: JWKS, operatorDid: OPERATOR });
  assert.strictEqual(out.code, 403);
  assert.strictEqual(out.body.moltrustGate.reason, 'attestation_missing');
  assert.strictEqual(out.body.moltrustGate.invite.referrer_did, OPERATOR);
  assert.strictEqual(out.body.moltrustGate.invite.source, INVITE_SOURCE);
});

test('an unconfigured gate answers as it did before', () => {
  const out = fakeExchange({ jwks: JWKS });
  assert.strictEqual(out.code, 403);
  assert.ok(!('invite' in out.body.moltrustGate), 'no invite without an operator');
  // The fields integrators already read stay where they were.
  assert.strictEqual(out.body.error, 'attestation_missing');
  assert.ok(typeof out.body.detail === 'string' && out.body.detail.length > 0);
  assert.strictEqual(out.body.did, null);
  assert.ok(out.body.docs);
});

test('a malformed operatorDid gets no invite and no error', () => {
  const out = fakeExchange({ jwks: JWKS, operatorDid: 'did:moltrust:oops' });
  assert.strictEqual(out.code, 403);
  assert.ok(!('invite' in out.body.moltrustGate));
});

test('onDeny receives the block as its fourth argument', () => {
  let seen = null;
  const req = { method: 'POST', path: '/paid', headers: {} };
  requireMolTrust({
    jwks: JWKS,
    operatorDid: OPERATOR,
    onDeny: (_req, _res, _decision, gate) => { seen = gate; },
  })(req, {}, () => {});
  assert.ok(seen, 'onDeny was called');
  assert.strictEqual(seen.invite.referrer_did, OPERATOR);
});

test('an allowed request is not touched by any of this', () => {
  // Nothing here can produce an allow without a signed attestation, which is
  // the point; the probe is that a denial is the only path that adds a block.
  const out = fakeExchange({ jwks: JWKS, operatorDid: OPERATOR });
  assert.strictEqual(out.nexted, false);
});

console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed === 0 ? 0 : 1);
