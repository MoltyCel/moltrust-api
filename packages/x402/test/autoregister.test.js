'use strict';
/**
 * The operator DID a gate registers for itself on its first decision.
 * A fake registry checks the signature and the proof-of-work like the real
 * route; nothing here reaches the network.
 *
 *   node test/autoregister.test.js
 */

const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

const ar = require('../autoregister');

const BITS = 6;
const API = 'https://api.example.test';

function zeroBits(buf) {
  let n = 0;
  for (const b of buf) { if (b === 0) { n += 8; continue; } return n + Math.clz32(b) - 24; }
  return n;
}

function fakeRegistry(failStatus) {
  const state = { gets: 0, posts: [] };
  state.fetch = async (url, init) => {
    const p = new URL(url).pathname;
    const reply = (status, body) => ({ status, json: async () => body });
    if (p === '/identity/register-challenge') {
      state.gets += 1;
      return reply(200, { challenge: 'seedx.9999999999.tag', pow: { difficulty_bits: BITS, seed: 'seedx' } });
    }
    if (p === '/identity/register-pop') {
      const body = JSON.parse(init.body);
      state.posts.push(body);
      if (failStatus) return reply(failStatus, { detail: 'no' });
      const key = crypto.createPublicKey({ key: { kty: 'OKP', crv: 'Ed25519',
        x: Buffer.from(body.public_key, 'hex').toString('base64url') }, format: 'jwk' });
      assert.ok(crypto.verify(null, Buffer.from(body.challenge), key,
        Buffer.from(body.signature, 'base64url')), 'signature verifies');
      assert.ok(zeroBits(crypto.createHash('sha256').update(`seedx${body.pow_nonce}`).digest()) >= BITS);
      return reply(200, { did: `did:moltrust:${body.public_key.slice(0, 16)}` });
    }
    return reply(404, {});
  };
  return state;
}

function freshEnv() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'x402-ar-'));
  const file = path.join(dir, 'identity.json');
  for (const k of ['MOLTRUST_NO_AUTO_REGISTER', 'CI', 'MOLTRUST_REGISTER_PLATFORM']) delete process.env[k];
  process.env.MOLTRUST_IDENTITY_FILE = file;
  return file;
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const quiet = async (fn) => {
  const warn = console.warn; console.warn = () => {};
  try { return await fn(); } finally { console.warn = warn; }
};

test('first call registers once, second call reuses the file', async () => {
  const file = freshEnv();
  const reg = fakeRegistry();
  const a = await quiet(() => ar.ensure({ apiUrl: API, fetch: reg.fetch, version: '9' }));
  const b = await quiet(() => ar.ensure({ apiUrl: API, fetch: reg.fetch, version: '9' }));
  assert.ok(a && b && a.did === b.did);
  assert.strictEqual(reg.posts.length, 1);
  assert.strictEqual(reg.gets, 1);
  const body = reg.posts[0];
  assert.strictEqual(body.platform, 'usage-x402');
  assert.strictEqual(body.source, 'x402-package');
  assert.strictEqual(body.display_name, `x402-gate-${body.public_key.slice(0, 12)}`);
  assert.ok(!JSON.stringify(body).includes('private'));
  assert.strictEqual(fs.statSync(file).mode & 0o777, 0o600);
  assert.strictEqual(JSON.parse(fs.readFileSync(file, 'utf8')).identities[API].private_key.length, 64);
});

for (const [name, setup, opts] of [
  ['MOLTRUST_NO_AUTO_REGISTER=1', () => { process.env.MOLTRUST_NO_AUTO_REGISTER = '1'; }, {}],
  ['CI set', () => { process.env.CI = 'true'; }, {}],
  ['autoRegister: false', () => {}, { autoRegister: false }],
  ['operatorDid set', () => {}, { operatorDid: 'did:moltrust:0123456789abcdef' }],
]) {
  test(`nothing is sent when ${name}`, async () => {
    const file = freshEnv();
    setup();
    const reg = fakeRegistry();
    assert.strictEqual(await ar.ensure(Object.assign({ apiUrl: API, fetch: reg.fetch }, opts)), null);
    assert.strictEqual(reg.gets + reg.posts.length, 0);
    assert.ok(!fs.existsSync(file));
  });
}

test('a failed registration resolves to null and leaves no file or lock', async () => {
  const file = freshEnv();
  const reg = fakeRegistry(500);
  assert.strictEqual(await quiet(() => ar.ensure({ apiUrl: API, fetch: reg.fetch })), null);
  assert.ok(!fs.existsSync(file) && !fs.existsSync(`${file}.lock`));
});

test('an unreachable API resolves to null', async () => {
  freshEnv();
  const boom = async () => { throw new Error('fetch failed'); };
  assert.strictEqual(await quiet(() => ar.ensure({ apiUrl: API, fetch: boom })), null);
});

test('a lock held by another process means no second registration', async () => {
  const file = freshEnv();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(`${file}.lock`, '');
  const reg = fakeRegistry();
  assert.strictEqual(await ar.ensure({ apiUrl: API, fetch: reg.fetch }), null);
  assert.strictEqual(reg.posts.length, 0);
});

test('the gate decision does not wait for registration and stays offline', async () => {
  freshEnv();
  let called = 0;
  const orig = globalThis.fetch;
  globalThis.fetch = async () => { called += 1; throw new Error('fetch failed'); };
  try {
    const { gateFor } = require('../index');
    const decide = gateFor({ jwks: { keys: [{ kty: 'OKP', crv: 'Ed25519', kid: 'k',
      x: Buffer.alloc(32).toString('base64url') }] } });
    const d = decide('GET', '/x', {});
    assert.strictEqual(called, 0, 'no network call on the request path');
    assert.strictEqual(d.reason, 'attestation_missing');
    await quiet(() => new Promise((r) => setTimeout(r, 50)));
    assert.ok(called >= 1, 'registration started in the background');
  } finally {
    globalThis.fetch = orig;
  }
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log(`ok   ${name}`); } catch (e) { failed += 1; console.log(`FAIL ${name}\n     ${e.stack}`); }
  }
  console.log(`${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
