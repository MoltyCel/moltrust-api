'use strict';
/**
 * Receiver-side x402 gate: same decisions as the Python gate, no network.
 *
 *   node --test test/x402-gate.test.js
 *
 * Three sources of truth, none written by this package:
 * - sdk/python/tests/x402-vectors.json, the vectors of moltrust_enforce.x402_gate;
 * - test/x402-python-recompute.json, the core_digest Python recomputes for each
 *   of those vectors that carries a receipt;
 * - test/enforce-core-vectors.json, enforce_check results of the Python
 *   implementation over the AAE conformance vectors and further cases.
 * The last two are written by test/make_core_vectors.py.
 */

process.env.MOLTRUST_NO_AUTO_REGISTER = '1';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const VECTORS = path.join(__dirname, '..', '..', '..', 'sdk', 'python', 'tests', 'x402-vectors.json');

// ---------------------------------------------------------------------------
// Network off for the whole file. Installed before the modules under test are
// loaded, so a connection attempt at load time is caught as well.
// ---------------------------------------------------------------------------

const attempts = [];
function refuse(what) {
  return function blocked() {
    attempts.push(what);
    throw new Error(`network disabled in this test: ${what}`);
  };
}
const net = require('node:net');
const tls = require('node:tls');
const http = require('node:http');
const https = require('node:https');
const dns = require('node:dns');
net.Socket.prototype.connect = refuse('net.Socket.connect');
net.connect = refuse('net.connect');
net.createConnection = refuse('net.createConnection');
tls.connect = refuse('tls.connect');
http.request = refuse('http.request');
http.get = refuse('http.get');
https.request = refuse('https.request');
https.get = refuse('https.get');
dns.lookup = refuse('dns.lookup');
dns.resolve = refuse('dns.resolve');
globalThis.fetch = refuse('fetch');

const gate = require('../x402-gate');
const core = require('../enforce-core');

const x402 = JSON.parse(fs.readFileSync(VECTORS, 'utf8'));
const recompute = require('./x402-python-recompute.json');
const coreVectors = require('./enforce-core-vectors.json');

const pyDigest = new Map(recompute.vectors.map((r) => [r.id, r]));

// ---------------------------------------------------------------------------
// Criterion 2: every vector of the Python gate, same decision, same reason,
// byte-identical core_digest.
// ---------------------------------------------------------------------------

test('x402 vectors: same decision and reason as the Python gate', async (t) => {
  assert.ok(x402.vectors.length >= 20, `only ${x402.vectors.length} vectors`);
  for (const v of x402.vectors) {
    await t.test(`${v.id}: ${v.expect} ${v.reason}`, () => {
      const accept = gate.trustMandates(v.accepted_mandate_digests);
      const d = gate.checkPayment(v.payment_payload, v.requirements, { acceptMandate: accept });
      assert.strictEqual(d.allowed ? 'pass' : 'abort', v.expect);
      assert.strictEqual(d.reason, v.reason);
      assert.strictEqual(d.allowed, v.expect === 'pass');
    });
  }
});

test('x402 vectors: recomputed core_digest is byte-identical to Python', async (t) => {
  assert.strictEqual(pyDigest.size, 12);
  for (const v of x402.vectors) {
    const py = pyDigest.get(v.id);
    if (!py) continue;
    await t.test(`${v.id}: ${py.verdict} ${py.core_digest}`, () => {
      const tx = gate.x402Transaction(v.payment_payload, v.requirements);
      assert.deepStrictEqual(tx, py.transaction);
      const receipt = v.payment_payload.extensions.moltrust;
      const prev = (receipt.record && receipt.record.core && receipt.record.core.prev_core_digest) || null;
      const fresh = core.enforceCheck(receipt.mandate, tx, prev, receipt.ancestors || null);
      assert.strictEqual(fresh.verdict, py.verdict);
      assert.strictEqual(fresh.core_digest, py.core_digest);
    });
  }
});

test('x402 vectors: the digest over the shipped core matches the claim unless tampered', () => {
  for (const v of x402.vectors) {
    const r = v.payment_payload.extensions && v.payment_payload.extensions.moltrust;
    if (!r || typeof r !== 'object' || !r.record || !r.record.core) continue;
    const same = core.coreDigest(r.record.core) === r.record.core_digest;
    assert.strictEqual(same, v.reason !== 'receipt_tampered', v.id);
  }
});

test('the bound allow vector passes with the digest the receipt claims', () => {
  const v = x402.vectors.find((x) => x.id === 'e-bound-allow');
  const d = gate.checkPayment(v.payment_payload, v.requirements,
    { acceptMandate: gate.trustMandates(v.accepted_mandate_digests) });
  assert.strictEqual(d.allowed, true);
  assert.strictEqual(d.verdict, 'PERMIT');
  assert.strictEqual(d.core_digest, v.payment_payload.extensions.moltrust.record.core_digest);
});

// ---------------------------------------------------------------------------
// The verdict engine itself, against the Python implementation.
// ---------------------------------------------------------------------------

test('enforce_check: verdict, reason, grant_index and core_digest equal Python', async (t) => {
  const conformance = coreVectors.vectors.filter((v) => v.source.startsWith('aae-conformance-vectors@'));
  assert.strictEqual(conformance.length, 21);
  for (const v of coreVectors.vectors) {
    await t.test(v.id, () => {
      const i = v.input;
      const r = core.enforceCheck(i.mandate, i.transaction,
        i.prev_core_digest === undefined ? null : i.prev_core_digest,
        i.ancestors === undefined ? null : i.ancestors);
      assert.strictEqual(r.verdict, v.expected.verdict);
      assert.strictEqual(r.reason, v.expected.reason);
      assert.strictEqual(r.grant_index, v.expected.grant_index);
      assert.strictEqual(r.core_digest, v.expected.core_digest);
    });
  }
});

test('canonicalization refuses what Python refuses', () => {
  assert.strictEqual(core.digest(core.TAG_CORE, { a: 2 ** 53 }), null);
  assert.strictEqual(core.digest(core.TAG_CORE, { a: '\ud800' }), null);
  assert.notStrictEqual(core.digest(core.TAG_CORE, { a: 2 ** 53 - 1 }), null);
  assert.strictEqual(core.canonicalize({ b: [1, 'x'], a: null }).toString(), '{"a":null,"b":[1,"x"]}');
});

// ---------------------------------------------------------------------------
// Fail closed
// ---------------------------------------------------------------------------

const bound = x402.vectors.find((x) => x.id === 'e-bound-allow');
const pinned = gate.trustMandates(bound.accepted_mandate_digests);

test('anything that is not a payment aborts', () => {
  for (const bad of [null, undefined, 'x', 7, [], {}, { x402Version: 2 }]) {
    const d = gate.checkPayment(bad, bound.requirements, { acceptMandate: pinned });
    assert.strictEqual(d.allowed, false);
    assert.strictEqual(d.reason, 'payment_unreadable');
  }
});

test('an acceptMandate that throws or answers truthy-but-not-true aborts', () => {
  const throws = () => { throw new Error('boom'); };
  assert.strictEqual(gate.checkPayment(bound.payment_payload, bound.requirements,
    { acceptMandate: throws }).reason, 'check_failed');
  assert.strictEqual(gate.checkPayment(bound.payment_payload, bound.requirements,
    { acceptMandate: () => 1 }).reason, 'mandate_not_accepted');
  assert.strictEqual(gate.checkPayment(bound.payment_payload, bound.requirements, {}).allowed, false);
});

test('trustMandates refuses an empty pin list', () => {
  assert.throws(() => gate.trustMandates(), /at least one/);
  assert.throws(() => gate.trustMandates('sha256:short'), /at least one/);
});

// ---------------------------------------------------------------------------
// The @x402/core hook contract: resolve undefined to continue, {abort, reason}
// to stop.
// ---------------------------------------------------------------------------

test('hook: undefined for a bound PERMIT, abort with reason otherwise', async () => {
  const seen = [];
  const hook = gate.requireMoltrustVerdict({ acceptMandate: pinned, onDecision: (d) => seen.push(d.reason) });
  assert.strictEqual(await hook({ paymentPayload: bound.payment_payload, requirements: bound.requirements }), undefined);
  const missing = x402.vectors.find((x) => x.id === 'a-no-receipt');
  const r = await hook({ paymentPayload: missing.payment_payload, requirements: missing.requirements });
  assert.strictEqual(r.abort, true);
  assert.match(r.reason, /^moltrust:receipt_missing: /);
  assert.strictEqual(r.message, r.reason);
  assert.deepStrictEqual(seen, ['ok', 'receipt_missing']);
});

test('hook: a broken context or a throwing logger still decides closed', async () => {
  const hook = gate.requireMoltrustVerdict({ acceptMandate: pinned, onDecision: () => { throw new Error('x'); } });
  for (const ctx of [undefined, null, 'x', {}, { paymentPayload: 'x' }]) {
    const r = await hook(ctx);
    assert.strictEqual(r.abort, true);
  }
  assert.strictEqual(await hook({ paymentPayload: bound.payment_payload, requirements: bound.requirements }), undefined);
  assert.throws(() => gate.requireMoltrustVerdict({}), /acceptMandate is required/);
});

// ---------------------------------------------------------------------------
// Agent side: headers built from the check response pass the receiver's hook.
// ---------------------------------------------------------------------------

function withoutReceipt(payload) {
  const p = JSON.parse(JSON.stringify(payload));
  delete p.extensions;
  return p;
}

test('agent to receiver: buildX402Headers, decode, hook passes', async () => {
  const receipt = bound.payment_payload.extensions.moltrust;
  const headers = gate.buildX402Headers(receipt.mandate, withoutReceipt(bound.payment_payload),
    { record: receipt.record });
  const decoded = gate.decodePaymentHeader(headers[gate.PAYMENT_HEADER]);
  const hook = gate.requireMoltrustVerdict({ acceptMandate: pinned });
  assert.strictEqual(await hook({ paymentPayload: decoded, requirements: bound.requirements }), undefined);
});

test('agent side refuses a receipt the receiver would reject', () => {
  const hold = x402.vectors.find((x) => x.id === 'c-hold');
  const r = hold.payment_payload.extensions.moltrust;
  assert.throws(() => gate.buildReceipt(r.mandate, withoutReceipt(hold.payment_payload), r.record),
    /verdict_not_permit/);
  const other = x402.vectors.find((x) => x.id === 'b-other-amount');
  const ro = other.payment_payload.extensions.moltrust;
  assert.throws(() => gate.buildReceipt(ro.mandate, withoutReceipt(other.payment_payload), ro.record),
    /receipt_not_bound/);
});

test('decodePaymentHeader refuses what is not base64 JSON of an object', () => {
  for (const bad of ['%%%', 'abc', Buffer.from('[1]').toString('base64'), Buffer.from('nope').toString('base64')]) {
    assert.throws(() => gate.decodePaymentHeader(bad), gate.ReceiptError);
  }
});

// ---------------------------------------------------------------------------
// Criterion 3: no network call in the check path.
// ---------------------------------------------------------------------------

test('no network: no connection was attempted by any test above', () => {
  assert.deepStrictEqual(attempts, []);
});

test('no network: the guard is live (a fetch here is refused and recorded)', () => {
  const before = attempts.length;
  assert.throws(() => globalThis.fetch('https://api.moltrust.ch/health'), /network disabled/);
  assert.strictEqual(attempts.length, before + 1);
  attempts.length = before;
});

test('no network: the gate modules load nothing but node:crypto and each other', () => {
  for (const file of ['x402-gate.js', 'enforce-core.js']) {
    // Code only: the module docs show a require() of the package itself.
    const src = fs.readFileSync(path.join(__dirname, '..', file), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    const reqs = [...src.matchAll(/require\(\s*['"]([^'"]+)['"]\s*\)/g)].map((m) => m[1]);
    for (const r of reqs) assert.ok(['crypto', './enforce-core'].includes(r), `${file} requires ${r}`);
    assert.doesNotMatch(src, /\bfetch\s*\(|https?:\/\/api\.|XMLHttpRequest|import\s*\(/, file);
  }
});
