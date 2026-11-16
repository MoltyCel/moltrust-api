// failBehavior was published in 1.0.3 from a tree that never reached this
// repo; 1.0.4 restores it (ops NPM-HYG, 16.11.2026). A caller who set
// 'closed' must not get fail-open from a newer version.
const test = require('node:test');
const assert = require('node:assert');
const mpp = require('../index');

const WALLET = '0x742d35Cc6634C0532925a3b844Bc9e7595f8fE00';

function req() {
  const cred = { challenge: {}, payload: {}, source: '1:' + WALLET };
  return { headers: { authorization: 'Payment ' + Buffer.from(JSON.stringify(cred)).toString('base64') } };
}

function res() {
  const r = { code: null, body: null };
  r.status = (c) => { r.code = c; return r; };
  r.json = (b) => { r.body = b; return r; };
  return r;
}

async function run(options, score) {
  const original = mpp.getScore;
  mpp.getScore = async () => score;
  try {
    const r = res();
    let passed = false;
    const rq = req();
    await mpp.requireScore(options)(rq, r, () => { passed = true; });
    return { passed, r, rq };
  } finally {
    mpp.getScore = original;
  }
}

test('closed: unreachable API denies with 403', async () => {
  const { passed, r } = await run({ failBehavior: 'closed' }, null);
  assert.strictEqual(passed, false);
  assert.strictEqual(r.code, 403);
  assert.strictEqual(r.body.error, 'trust_api_unavailable');
});

test('default stays open: unreachable API passes and says so', async () => {
  const { passed, rq } = await run({}, null);
  assert.strictEqual(passed, true);
  assert.strictEqual(rq.moltrust.failOpen, true);
});

test('closed does not touch a reachable API: a good score passes', async () => {
  const { passed } = await run({ failBehavior: 'closed', minScore: 60 }, 80);
  assert.strictEqual(passed, true);
});

test('closed does not touch a reachable API: a low score is denied as before', async () => {
  const { passed, r } = await run({ failBehavior: 'closed', minScore: 60 }, 10);
  assert.strictEqual(passed, false);
  assert.strictEqual(r.body.error, 'trust_score_insufficient');
});
