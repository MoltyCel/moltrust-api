import { Request, Response, NextFunction } from 'express';

export interface Jwk {
  kty: 'OKP';
  crv: 'Ed25519';
  kid: string;
  x: string;
  use?: string;
  alg?: string;
}

export interface Jwks {
  keys: Jwk[];
}

export interface GateAttestation {
  did: string;
  publicKey: string;
  trustScore: number | null;
  withheld: boolean;
  credentialTypes: string[];
  computedAt: string;
  validUntil: string;
  policyVersion: string;
  version: number;
}

export type DenialReason =
  | 'attestation_missing'
  | 'proof_missing'
  | 'attestation_invalid'
  | 'proof_invalid'
  | 'proof_replayed'
  | 'score_withheld'
  | 'score_missing'
  | 'score_below_minimum'
  | 'credential_missing';

export interface Decision {
  allowed: boolean;
  reason: DenialReason | 'ok';
  detail: string;
  did?: string;
  trustScore?: number | null;
  credentialTypes?: string[];
}

export interface GateOptions {
  /** Lowest trust score that passes. Omit to leave the score unconsulted. */
  minScore?: number | null;
  /** A credential the DID must hold, by type. */
  credentialType?: string | null;
  /** Several required credentials, if one is not enough. */
  requiredCredentials?: string[];
  /** The registry key set: a parsed JWKS or a path to one. Never fetched. */
  jwks: Jwks | string;
  /** Freshness window for the caller's proof, in seconds. Default 300. */
  maxAgeSeconds?: number;
  /**
   * Let an agent through whose score we have never computed. Off by default:
   * a real choice for a discount tier, a bad one for a spend authorisation.
   * Has no effect on `minScore` — a withheld score is null, so any numeric
   * threshold still denies.
   */
  allowWithheld?: boolean;
  /** Replay store. Return false if this proof has been presented before. */
  seen?: (proof: string) => boolean;
  /** Called instead of the default 403 response. */
  onDeny?: (req: Request, res: Response, decision: Decision) => void;
}

declare global {
  namespace Express {
    interface Request {
      moltrust?: Decision;
    }
  }
}

export function requireMolTrust(options: GateOptions): (
  req: Request,
  res: Response,
  next: NextFunction
) => void;

export function gateFor(options: GateOptions): (
  method: string,
  path: string,
  headers: Record<string, string | string[] | undefined> | Headers,
  now?: number
) => Decision;

export function verifyAttestation(token: string, jwks: Jwks, now?: number): GateAttestation;

export function bindingString(
  method: string, path: string, did: string, timestamp: string | number
): Buffer;

export function loadJwks(pathOrObject: Jwks | string): Jwks;

export const BINDING_VERSION: string;
export const HEADER_ATTESTATION: string;
export const HEADER_TIMESTAMP: string;
export const HEADER_PROOF: string;
