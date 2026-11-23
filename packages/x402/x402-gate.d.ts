/** Receiver-side gate for x402 payments. See x402-gate.js and the README. */

export const EXTENSION_KEY: 'moltrust';
export const RECEIPT_VERSION: 'moltrust-x402/v1';
export const X402_VERB: 'x402.pay';
export const PAYMENT_HEADER: 'PAYMENT-SIGNATURE';

export class ReceiptError extends Error {}

export type Verdict = 'PERMIT' | 'DENY' | 'PENDING';

export type DecisionReason =
  | 'ok'
  | 'receipt_missing'
  | 'receipt_invalid'
  | 'mandate_not_accepted'
  | 'receipt_tampered'
  | 'receipt_not_bound'
  | 'verdict_not_permit'
  | 'payment_unreadable'
  | 'check_failed';

/** The transaction recomputed from an x402 payment (derivation rule in the README). */
export interface X402Transaction {
  action: { verb: 'x402.pay'; scheme: string; network: string; asset: string };
  from: string;
  to: string;
  amount: number;
  nonce: string;
}

export interface PaymentDecision {
  /** Never true by omission. */
  allowed: boolean;
  reason: DecisionReason;
  detail: string;
  verdict: Verdict | null;
  core_digest: string | null;
  mandate_digest: string | null;
  transaction: X402Transaction | null;
}

/** Return true for a mandate this receiver accepts for this payer. Anything else aborts. */
export type AcceptMandate = (
  mandate: Record<string, unknown>,
  mandateDigest: string,
  payer: string,
) => boolean;

export interface MoltrustReceipt {
  version: 'moltrust-x402/v1';
  mandate: Record<string, unknown>;
  record: { core: Record<string, unknown>; core_digest: string };
  ancestors?: Record<string, unknown>[];
}

/** Hook context as passed by x402ResourceServer and x402Facilitator (@x402/core). */
export interface HookContext {
  paymentPayload: unknown;
  requirements?: unknown;
  [key: string]: unknown;
}

export type HookResult = void | { abort: true; reason: string; message?: string };

export function x402Transaction(paymentPayload: unknown, requirements?: unknown): X402Transaction;
export function mandateDigest(mandate: unknown): string | null;
export function trustMandates(...digests: Array<string | string[]>): AcceptMandate;
export function checkPayment(
  paymentPayload: unknown,
  requirements: unknown,
  options: { acceptMandate: AcceptMandate },
): PaymentDecision;
export function requireMoltrustVerdict(options: {
  acceptMandate: AcceptMandate;
  onDecision?: (decision: PaymentDecision) => void;
}): (context: HookContext) => Promise<HookResult>;
export function decodePaymentHeader(value: string): Record<string, unknown>;
export function encodePaymentHeader(payload: unknown): string;
export function buildReceipt(
  mandate: Record<string, unknown>,
  paymentPayload: unknown,
  checkResponse: unknown,
  options?: { ancestors?: Record<string, unknown>[] },
): MoltrustReceipt;
export function buildX402Headers(
  mandate: Record<string, unknown>,
  paymentPayload: unknown,
  checkResponse: unknown,
  options?: { ancestors?: Record<string, unknown>[] },
): { 'PAYMENT-SIGNATURE': string };
