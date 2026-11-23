'use strict';
/**
 * Receiver-side gate for x402 payments.
 *
 * A resource server or an x402 facilitator registers one hook before verify
 * and settle. The hook aborts every payment that does not carry a MolTrust
 * verdict bound to this one payment. It makes no network call: the verdict is
 * recomputed locally from the mandate the agent sends and the transaction
 * derived from the payment itself.
 *
 *   const { requireMoltrustVerdict, trustMandates } = require('@moltrust/x402/x402-gate');
 *   const hook = requireMoltrustVerdict({ acceptMandate: trustMandates(MANDATE_DIGEST) });
 *   server.onBeforeVerify(hook);   // x402ResourceServer or x402Facilitator (@x402/core)
 *   server.onBeforeSettle(hook);
 *
 * This is the JavaScript form of `moltrust_enforce.x402_gate` (Python). Both
 * replay the same vectors and reach the same decision with a byte-identical
 * core_digest.
 *
 * What the agent sends
 * --------------------
 * The receipt travels inside the x402 payment payload, under
 * `extensions.moltrust`, because that is the part of a payment both hooks see:
 *
 *   {
 *     "version": "moltrust-x402/v1",
 *     "mandate": {...},                                        the mandate the verdict was made under
 *     "record":  {"core": {...}, "core_digest": "sha256:..."}, from /enforce/check
 *     "ancestors": [...]                                       optional, parent mandates, root first
 *   }
 *
 * The receipt carries no transaction. The transaction is always derived from
 * the payment (x402Transaction), so an agent cannot describe one payment and
 * pay another.
 *
 * Derivation rule
 * ---------------
 * Only x402 version 2, scheme `exact`, an `eip155:*` network and an EIP-3009
 * authorization are accepted. From `payload.authorization` (what the payer
 * signed and what settles) and `accepted` (the requirements the payer chose):
 *
 *   {
 *     "action": {"verb": "x402.pay", "scheme": accepted.scheme,
 *                "network": accepted.network, "asset": lower(accepted.asset)},
 *     "from":   lower(authorization.from),
 *     "to":     lower(authorization.to),
 *     "amount": int(authorization.value),
 *     "nonce":  lower(authorization.nonce)
 *   }
 *
 * Before deriving, `authorization.to` must equal `accepted.payTo` and
 * `authorization.value` must equal `accepted.amount`; when the hook context
 * carries the server's requirements, scheme, network, asset, amount and payTo
 * must equal those too.
 *
 * What passes
 * -----------
 * A payment passes only when all of these hold; every other path aborts,
 * including a receipt that cannot be parsed and any exception:
 *
 *   1. the receipt is present and well-formed;
 *   2. `acceptMandate` accepts the mandate (the receiver decides which
 *      mandates count; without that, an agent could bring a mandate that
 *      allows everything);
 *   3. `core_digest` is the digest of the `core` shipped with it;
 *   4. recomputing the verdict from the mandate and the derived transaction
 *      gives PERMIT and the same `core_digest`.
 */

const core = require('./enforce-core');

const EXTENSION_KEY = 'moltrust';
const RECEIPT_VERSION = 'moltrust-x402/v1';
const X402_VERB = 'x402.pay';
const PAYMENT_HEADER = 'PAYMENT-SIGNATURE';

const DECIMAL = /^(0|[1-9][0-9]{0,15})$/;
const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const NONCE = /^0x[0-9a-fA-F]{64}$/;
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const MAX_SAFE_INT = Number.MAX_SAFE_INTEGER;

/** The payment or its receipt cannot be checked; the message says why. */
class ReceiptError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ReceiptError';
  }
}

function isDict(x) {
  return x !== null && typeof x === 'object' && !Array.isArray(x);
}

function own(obj, key) {
  return isDict(obj) && Object.prototype.hasOwnProperty.call(obj, key) ? obj[key] : undefined;
}

/** A payload or requirements object as a plain wire object (copy). */
function asWire(obj, what) {
  if (isDict(obj)) return { ...obj };
  throw new ReceiptError(`${what} is not an object`);
}

function strField(obj, key, what) {
  const value = own(obj, key);
  if (typeof value !== 'string' || !value) throw new ReceiptError(`${what}.${key} missing or not a string`);
  return value;
}

function amount(value, what) {
  if (typeof value !== 'string' || !DECIMAL.test(value)) {
    throw new ReceiptError(`${what} is not a decimal integer string`);
  }
  const n = Number(value);
  if (n > MAX_SAFE_INT) throw new ReceiptError(`${what} exceeds 2**53-1`);
  return n;
}

function address(value, what) {
  if (typeof value !== 'string' || !ADDRESS.test(value)) {
    throw new ReceiptError(`${what} is not a 20-byte hex address`);
  }
  return value.toLowerCase();
}

/**
 * Derive the transaction to recompute from an x402 payment payload.
 * Throws ReceiptError for anything outside the derivation rule. `requirements`
 * are the server's own requirements; when given, `accepted` must match them.
 */
function x402Transaction(paymentPayload, requirements) {
  const p = asWire(paymentPayload, 'payment payload');
  if (own(p, 'x402Version') !== 2) throw new ReceiptError('only x402 version 2 payloads are supported');
  const accepted = own(p, 'accepted');
  if (!isDict(accepted)) throw new ReceiptError('payment payload has no accepted requirements');
  const scheme = strField(accepted, 'scheme', 'accepted');
  const network = strField(accepted, 'network', 'accepted');
  let asset = strField(accepted, 'asset', 'accepted');
  const payTo = address(own(accepted, 'payTo'), 'accepted.payTo');
  const acceptedAmount = amount(own(accepted, 'amount'), 'accepted.amount');
  if (scheme !== 'exact') throw new ReceiptError(`scheme '${scheme}' is not supported, only 'exact'`);
  if (!network.startsWith('eip155:')) {
    throw new ReceiptError(`network '${network}' is not supported, only eip155:*`);
  }
  asset = address(asset, 'accepted.asset');

  const inner = own(p, 'payload');
  if (!isDict(inner)) throw new ReceiptError('payment payload has no scheme payload');
  const auth = own(inner, 'authorization');
  if (!isDict(auth)) throw new ReceiptError('payload has no EIP-3009 authorization');
  const payer = address(own(auth, 'from'), 'authorization.from');
  const to = address(own(auth, 'to'), 'authorization.to');
  const value = amount(own(auth, 'value'), 'authorization.value');
  const nonce = own(auth, 'nonce');
  if (typeof nonce !== 'string' || !NONCE.test(nonce)) {
    throw new ReceiptError('authorization.nonce is not a 32-byte hex value');
  }

  if (to !== payTo) throw new ReceiptError('authorization.to differs from accepted.payTo');
  if (value !== acceptedAmount) throw new ReceiptError('authorization.value differs from accepted.amount');

  if (requirements !== undefined && requirements !== null) {
    const r = asWire(requirements, 'requirements');
    let same;
    try {
      same = own(r, 'scheme') === scheme && own(r, 'network') === network
        && address(own(r, 'asset'), 'requirements.asset') === asset
        && amount(own(r, 'amount'), 'requirements.amount') === acceptedAmount
        && address(own(r, 'payTo'), 'requirements.payTo') === payTo;
    } catch (err) {
      if (!(err instanceof ReceiptError)) throw err;
      same = false;
    }
    if (!same) throw new ReceiptError("accepted requirements differ from the server's requirements");
  }

  return {
    action: {
      verb: X402_VERB, scheme, network, asset,
    },
    from: payer,
    to,
    amount: value,
    nonce: nonce.toLowerCase(),
  };
}

/** The digest a receiver pins to accept a mandate (same as `core.mandate_digest`). */
function mandateDigest(mandate) {
  return core.mandateDigest(mandate);
}

/** An `acceptMandate` function that accepts the given mandate digests and no others. */
function trustMandates(...digests) {
  const pinned = digests.flat().filter((d) => typeof d === 'string' && DIGEST.test(d));
  if (pinned.length === 0) throw new Error('trustMandates needs at least one sha256:<64 hex> digest');
  return function accept(mandate, digest /* , payer */) {
    return pinned.some((d) => core.ctEq(digest, d));
  };
}

function decision(allowed, reason, detail, extra) {
  return {
    allowed,
    reason,
    detail: detail || '',
    verdict: null,
    core_digest: null,
    mandate_digest: null,
    transaction: null,
    ...(extra || {}),
  };
}

function deny(reason, detail, extra) {
  return decision(false, reason, detail, extra);
}

function check(paymentPayload, requirements, acceptMandate) {
  const p = asWire(paymentPayload, 'payment payload');
  const tx = x402Transaction(p, requirements);

  const ext = own(p, 'extensions');
  const receipt = isDict(ext) ? own(ext, EXTENSION_KEY) : undefined;
  if (receipt === undefined || receipt === null) {
    return deny('receipt_missing', `no extensions.${EXTENSION_KEY} in the payment payload`, { transaction: tx });
  }
  if (!isDict(receipt)) return deny('receipt_invalid', 'receipt is not an object', { transaction: tx });
  if (own(receipt, 'version') !== RECEIPT_VERSION) {
    return deny('receipt_invalid', `receipt version is not ${RECEIPT_VERSION}`, { transaction: tx });
  }
  const mandate = own(receipt, 'mandate');
  const record = own(receipt, 'record');
  const ancestors = own(receipt, 'ancestors');
  if (!isDict(mandate)) return deny('receipt_invalid', 'receipt has no mandate', { transaction: tx });
  if (!isDict(record)) return deny('receipt_invalid', 'receipt has no record', { transaction: tx });
  if (ancestors !== undefined && ancestors !== null && !Array.isArray(ancestors)) {
    return deny('receipt_invalid', 'receipt.ancestors is not a list', { transaction: tx });
  }
  const recCore = own(record, 'core');
  const claimed = own(record, 'core_digest');
  if (!isDict(recCore) || typeof claimed !== 'string' || !DIGEST.test(claimed)) {
    return deny('receipt_invalid', 'record needs core and a sha256 core_digest', { transaction: tx });
  }

  const mDigest = mandateDigest(mandate);
  if (mDigest === null) return deny('receipt_invalid', 'mandate not canonicalizable', { transaction: tx });
  if (acceptMandate(mandate, mDigest, tx.from) !== true) {
    return deny('mandate_not_accepted', 'this receiver does not accept the mandate for this payer',
      { mandate_digest: mDigest, transaction: tx });
  }

  const shipped = core.coreDigest(recCore);
  if (shipped === null || !core.ctEq(shipped, claimed)) {
    return deny('receipt_tampered', 'core_digest is not the digest of core',
      { mandate_digest: mDigest, transaction: tx });
  }

  const prev = own(recCore, 'prev_core_digest');
  const fresh = core.enforceCheck(mandate, tx, prev === undefined ? null : prev,
    ancestors === undefined ? null : ancestors);
  if (fresh.core_digest === null || !core.ctEq(fresh.core_digest, claimed)) {
    return deny('receipt_not_bound', 'the receipt was made for a different payment or mandate',
      { verdict: fresh.verdict, mandate_digest: mDigest, transaction: tx });
  }
  if (fresh.verdict !== core.PERMIT) {
    return deny('verdict_not_permit', `verdict is ${fresh.verdict}: ${fresh.reason}`, {
      verdict: fresh.verdict, core_digest: claimed, mandate_digest: mDigest, transaction: tx,
    });
  }
  return decision(true, 'ok', '', {
    verdict: core.PERMIT, core_digest: claimed, mandate_digest: mDigest, transaction: tx,
  });
}

/**
 * Check one x402 payment against the MolTrust receipt it carries. Offline and
 * synchronous. `acceptMandate(mandate, mandateDigest, payer)` returns true for
 * a mandate the receiver accepts for this payer. Any exception is an abort.
 */
function checkPayment(paymentPayload, requirements, { acceptMandate } = {}) {
  if (typeof acceptMandate !== 'function') {
    return deny('check_failed', 'acceptMandate is required');
  }
  try {
    return check(paymentPayload, requirements, acceptMandate);
  } catch (err) {
    if (err instanceof ReceiptError) return deny('payment_unreadable', err.message);
    return deny('check_failed', (err && err.name) || 'Error');
  }
}

function abortReason(d) {
  return `moltrust:${d.reason}: ${d.detail}`.replace(/[: ]+$/, '');
}

/**
 * A before-verify / before-settle hook for @x402/core: register it with
 * `onBeforeVerify` and `onBeforeSettle` on an x402ResourceServer or an
 * x402Facilitator. It resolves to undefined to let the payment continue and to
 * `{abort: true, reason, message}` otherwise.
 *
 * `onDecision` is called with every decision for logging; its return value and
 * its exceptions are ignored.
 */
function requireMoltrustVerdict({ acceptMandate, onDecision } = {}) {
  if (typeof acceptMandate !== 'function') {
    throw new TypeError('acceptMandate is required: the receiver decides which mandates count');
  }
  return async function moltrustX402Hook(context) {
    let d;
    try {
      const payload = own(context, 'paymentPayload');
      const reqs = own(context, 'requirements');
      d = checkPayment(payload, reqs, { acceptMandate });
    } catch (err) {
      d = deny('check_failed', (err && err.name) || 'Error');
    }
    if (typeof onDecision === 'function') {
      try { onDecision(d); } catch (_) { /* logging never decides */ }
    }
    if (d.allowed) return undefined;
    const reason = abortReason(d);
    return { abort: true, reason, message: reason };
  };
}

// --------------------------------------------------------------------------
// Transport helpers, for receivers that do not run @x402/core
// --------------------------------------------------------------------------

/** Decode a PAYMENT-SIGNATURE header value (base64 JSON). Throws ReceiptError. */
function decodePaymentHeader(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9+/]*={0,2}$/.test(value) || value.length % 4 !== 0) {
    throw new ReceiptError('payment header is not base64 JSON: not base64');
  }
  let data;
  try {
    data = JSON.parse(Buffer.from(value, 'base64').toString('utf8'));
  } catch (err) {
    throw new ReceiptError(`payment header is not base64 JSON: ${err.name}`);
  }
  if (!isDict(data)) throw new ReceiptError('payment header does not hold an object');
  return data;
}

function encodePaymentHeader(payload) {
  return Buffer.from(JSON.stringify(payload), 'utf8').toString('base64');
}

// --------------------------------------------------------------------------
// Agent side
// --------------------------------------------------------------------------

function recordFromResponse(checkResponse) {
  let c;
  let dg;
  if (isDict(checkResponse) && isDict(own(checkResponse, 'record'))) {
    c = checkResponse.record.core;
    dg = checkResponse.record.core_digest;
  } else if (isDict(checkResponse)) {
    c = checkResponse.core;
    dg = checkResponse.core_digest;
  }
  if (!isDict(c) || typeof dg !== 'string') {
    throw new ReceiptError('check response carries no record (core, core_digest)');
  }
  return { core: { ...c }, core_digest: dg };
}

/**
 * The `extensions.moltrust` object for one payment. Throws ReceiptError
 * unless the check response is a PERMIT bound to this one payment,
 * recomputed locally: a receipt the receiver will reject is a wasted attempt.
 */
function buildReceipt(mandate, paymentPayload, checkResponse, { ancestors } = {}) {
  const record = recordFromResponse(checkResponse);
  const receipt = { version: RECEIPT_VERSION, mandate: { ...mandate }, record };
  if (Array.isArray(ancestors) && ancestors.length) receipt.ancestors = ancestors.map((a) => ({ ...a }));
  const p = asWire(paymentPayload, 'payment payload');
  const trial = { ...p, extensions: { ...(own(p, 'extensions') || {}), [EXTENSION_KEY]: receipt } };
  const d = checkPayment(trial, undefined, { acceptMandate: () => true });
  if (!d.allowed) throw new ReceiptError(`receipt would be rejected: ${d.reason}: ${d.detail}`);
  return receipt;
}

/**
 * Headers for the x402 request: `{ 'PAYMENT-SIGNATURE': base64(JSON payload) }`
 * with `extensions.moltrust` added to the signed x402 V2 payload.
 */
function buildX402Headers(mandate, paymentPayload, checkResponse, { ancestors } = {}) {
  const receipt = buildReceipt(mandate, paymentPayload, checkResponse, { ancestors });
  const p = asWire(paymentPayload, 'payment payload');
  p.extensions = { ...(own(p, 'extensions') || {}), [EXTENSION_KEY]: receipt };
  return { [PAYMENT_HEADER]: encodePaymentHeader(p) };
}

module.exports = {
  EXTENSION_KEY,
  RECEIPT_VERSION,
  X402_VERB,
  PAYMENT_HEADER,
  ReceiptError,
  x402Transaction,
  mandateDigest,
  trustMandates,
  checkPayment,
  requireMoltrustVerdict,
  decodePaymentHeader,
  encodePaymentHeader,
  buildReceipt,
  buildX402Headers,
};
