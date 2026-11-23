// Shared parts of the Express and Hono examples. Everything runs in one
// process without a network: the facilitator is in-process, and its payment
// scheme is a stand-in that accepts any signature, so the example needs no
// chain, no wallet and no funds. The MolTrust hook is the real one.
//
// What is a stand-in here, and what replaces it in production:
//   localFacilitator()   ->  HTTPFacilitatorClient({ url }) of your facilitator
//   moltrustCheck()      ->  POST https://api.moltrust.ch/enforce/check
//   signPayment()        ->  the x402 client of the paying agent (@x402/fetch, @x402/axios)

import { createRequire } from 'node:module';
import { x402Facilitator } from '@x402/core/facilitator';
import { x402ResourceServer } from '@x402/core/server';
import { ExactEvmScheme } from '@x402/evm/exact/server';

const require = createRequire(import.meta.url);
const gate = require('@moltrust/x402/x402-gate');
const { enforceCheck, actionDigest } = require('@moltrust/x402/enforce-core');

export { gate };

export const NETWORK = 'eip155:8453';                                   // Base
export const USDC = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';       // USDC on Base
export const PAY_TO = '0x1111111111111111111111111111111111111111';     // the merchant
export const AGENT = '0x2222222222222222222222222222222222222222';      // the paying agent

// The route: 0.01 USDC (10000 in six-decimal units) for GET /report.
export const ROUTES = {
  'GET /report': {
    accepts: {
      scheme: 'exact',
      network: NETWORK,
      payTo: PAY_TO,
      price: { amount: '10000', asset: USDC, extra: { name: 'USD Coin', version: '2' } },
    },
    description: 'report',
    mimeType: 'application/json',
  },
};

// The mandate the agent works under: pay this merchant in USDC on Base, at most
// 0.05 USDC per payment. The receiver pins its digest; nothing else counts.
const PAY_ACTION = { verb: 'x402.pay', scheme: 'exact', network: NETWORK, asset: USDC.toLowerCase() };
export const MANDATE = {
  mandate_version: '1.0',
  grants: [{
    action_binding: actionDigest(PAY_ACTION),
    type_fields: ['asset', 'network', 'scheme', 'verb'],
    disposition: 'allow',
    constraints: [
      { type: 'exact', field: 'to', value: PAY_TO },
      { type: 'range', field: 'amount', lo: 1, hi: 50000 },
    ],
  }],
};
export const MANDATE_DIGEST = gate.mandateDigest(MANDATE);

/** A FacilitatorClient for x402ResourceServer, backed by an in-process x402Facilitator. */
function localFacilitator() {
  const stubScheme = {
    scheme: 'exact',
    caipFamily: 'eip155:*',
    getExtra: () => undefined,
    getSigners: () => [],
    // Stand-in: a real facilitator checks the EIP-3009 signature and settles on chain.
    verify: async (payload) => ({ isValid: true, payer: payload.payload.authorization.from }),
    settle: async (payload, requirements) => ({
      success: true, transaction: `0x${'00'.repeat(32)}`, network: requirements.network,
      payer: payload.payload.authorization.from,
    }),
  };
  const facilitator = new x402Facilitator().register(NETWORK, stubScheme);
  return {
    verify: (p, r) => facilitator.verify(p, r),
    settle: (p, r) => facilitator.settle(p, r),
    getSupported: async () => facilitator.getSupported(),
  };
}

/** The resource server with the MolTrust hook before verify and before settle. */
export function resourceServer(log) {
  const hook = gate.requireMoltrustVerdict({
    acceptMandate: gate.trustMandates(MANDATE_DIGEST),
    onDecision: (d) => log(`  moltrust: ${d.allowed ? 'pass' : 'abort'} (${d.reason})`),
  });
  return new x402ResourceServer(localFacilitator())
    .register(NETWORK, new ExactEvmScheme())
    .onBeforeVerify(hook)
    .onBeforeSettle(hook);
}

/** Stand-in for the paying agent's x402 client: an EIP-3009 payload for `accepted`. */
export function signPayment(accepted, { value = accepted.amount, nonceByte = 'ab' } = {}) {
  return {
    x402Version: 2,
    accepted,
    payload: {
      authorization: {
        from: AGENT, to: accepted.payTo, value, validAfter: '0', validBefore: '4102444800',
        nonce: `0x${nonceByte.repeat(32)}`,
      },
      signature: `0x${'00'.repeat(65)}`,
    },
  };
}

/**
 * Stand-in for POST /enforce/check: the server computes exactly this record
 * from the same mandate and transaction, which is why the receiver can
 * recompute it offline.
 */
export function moltrustCheck(mandate, payload) {
  const r = enforceCheck(mandate, gate.x402Transaction(payload));
  return { verdict: r.verdict, record: { core: r.core, core_digest: r.core_digest } };
}

/** The three requests each example sends, and what each must answer. */
export async function runScenario(baseUrl, log) {
  const results = [];
  log('1. no payment');
  const unpaid = await fetch(`${baseUrl}/report`);
  log(`   -> HTTP ${unpaid.status}`);
  results.push(unpaid.status === 402);
  const required = JSON.parse(Buffer.from(unpaid.headers.get('payment-required'), 'base64').toString());
  const accepted = required.accepts[0];

  // 2. Paid, but without a MolTrust receipt: the hook aborts verify.
  log('2. payment, no receipt');
  const bare = signPayment(accepted);
  const r2 = await fetch(`${baseUrl}/report`, { headers: { 'PAYMENT-SIGNATURE': gate.encodePaymentHeader(bare) } });
  log(`   -> HTTP ${r2.status}`);
  results.push(r2.status === 402);

  // 3. Paid with a receipt bound to this payment: passes, the handler runs.
  log('3. payment with a receipt bound to it');
  const payload = signPayment(accepted);
  const check = moltrustCheck(MANDATE, payload);
  const headers = gate.buildX402Headers(MANDATE, payload, check);
  const r3 = await fetch(`${baseUrl}/report`, { headers });
  log(`   -> HTTP ${r3.status} ${await r3.text()}`);
  results.push(r3.status === 200);

  // 4. The same receipt on a different payment (another nonce): aborts.
  log('4. the same receipt on another payment');
  const other = signPayment(accepted, { nonceByte: 'cd' });
  const replay = { ...other, extensions: JSON.parse(Buffer.from(headers['PAYMENT-SIGNATURE'], 'base64').toString()).extensions };
  const r4 = await fetch(`${baseUrl}/report`, { headers: { 'PAYMENT-SIGNATURE': gate.encodePaymentHeader(replay) } });
  log(`   -> HTTP ${r4.status}`);
  results.push(r4.status === 402);

  return results.every(Boolean);
}
