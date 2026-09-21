'use strict';
/**
 * Offline trust gate for x402 endpoints.
 *
 * Version 1 of this package asked api.moltrust.ch for a score on every
 * request and believed the answer. Two things were wrong with that. It put a
 * network call from someone else's server into their request path, so an
 * outage on our side became latency on theirs. And it verified nothing: the
 * response was plain JSON over TLS, so anyone able to answer that request
 * could set any score they liked.
 *
 * This version calls nobody. The caller brings a MolTrust-signed attestation
 * and a signature made with its own key; both are checked against a JWKS the
 * host already holds.
 *
 *   const { requireMolTrust, loadJwks } = require('@moltrust/x402');
 *   const jwks = loadJwks('/etc/moltrust/jwks.json');
 *   app.post('/paid', requireMolTrust({ minScore: 60, jwks }), handler);
 *
 * Headers the caller sends:
 *   X-MolTrust-Attestation  the `gate_attestation` from
 *                           GET /skill/trust-score/<did>
 *   X-MolTrust-Timestamp    unix seconds
 *   X-MolTrust-Proof        base64url Ed25519 signature over the binding
 *
 * Everything that is not an explicit allow is a denial, including a malformed
 * header and an unknown key id. A withheld score is a denial: a score we have
 * not computed is not a low score, and a gate that reads "unknown" as "fine"
 * is the failure this module exists to prevent.
 */

const crypto = require('crypto');
const fs = require('fs');

const BINDING_VERSION = 'moltrust-gate/v1';
const DEFAULT_MAX_AGE_SECONDS = 300;
const HEADER_ATTESTATION = 'x-moltrust-attestation';
const HEADER_TIMESTAMP = 'x-moltrust-timestamp';
const HEADER_PROOF = 'x-moltrust-proof';

// --------------------------------------------------------------------------
// base64url (RFC 7515 §2, unpadded)
// --------------------------------------------------------------------------

function b64urlDecode(value) {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error('empty base64url value');
  }
  return Buffer.from(value, 'base64url');
}

function b64urlEncode(buf) {
  return Buffer.from(buf).toString('base64url');
}

// --------------------------------------------------------------------------
// JWKS
// --------------------------------------------------------------------------

/**
 * Read a JWKS from a path, or accept one already in memory.
 *
 * Deliberately not a fetch. The gate is offline by design, so refreshing the
 * key set is an operational step with its own schedule. An HTTP call here
 * would quietly undo the property the module is for.
 *
 * The registry key set is published at
 * https://api.moltrust.ch/.well-known/jwks.json
 */
function loadJwks(pathOrObject) {
  const jwks = typeof pathOrObject === 'string'
    ? JSON.parse(fs.readFileSync(pathOrObject, 'utf8'))
    : pathOrObject;
  if (!jwks || !Array.isArray(jwks.keys) || jwks.keys.length === 0) {
    throw new Error('JWKS has no keys[]');
  }
  return jwks;
}

function keyForKid(jwks, kid) {
  const jwk = jwks.keys.find((k) => k.kid === kid);
  if (!jwk) {
    throw new Error(
      `no key for kid ${JSON.stringify(kid)} in the JWKS — refresh it, or the `
      + 'token was not issued by this registry',
    );
  }
  if (jwk.kty !== 'OKP' || jwk.crv !== 'Ed25519') {
    throw new Error(`unsupported key: kty=${jwk.kty} crv=${jwk.crv}`);
  }
  return crypto.createPublicKey({
    key: { kty: 'OKP', crv: 'Ed25519', x: jwk.x },
    format: 'jwk',
  });
}

function keyFromHex(hex) {
  const raw = Buffer.from(hex, 'hex');
  if (raw.length !== 32) throw new Error(`public_key must be 32 bytes, got ${raw.length}`);
  return crypto.createPublicKey({
    key: { kty: 'OKP', crv: 'Ed25519', x: b64urlEncode(raw) },
    format: 'jwk',
  });
}

// --------------------------------------------------------------------------
// Attestation
// --------------------------------------------------------------------------

/** Verify a compact JWS gate attestation. Throws with the reason. */
function verifyAttestation(token, jwks, now) {
  if (typeof token !== 'string') throw new Error('attestation is not a string');
  const parts = token.split('.');
  if (parts.length !== 3) {
    throw new Error(`not a compact JWS: ${parts.length} parts, expected 3`);
  }
  const [headerB64, payloadB64, sigB64] = parts;

  const header = JSON.parse(b64urlDecode(headerB64).toString('utf8'));
  if (header.alg !== 'EdDSA') throw new Error(`alg=${header.alg}, expected EdDSA`);
  if (!header.kid) throw new Error('header carries no kid');

  const key = keyForKid(jwks, header.kid);
  const ok = crypto.verify(
    null, Buffer.from(`${headerB64}.${payloadB64}`, 'ascii'), key, b64urlDecode(sigB64),
  );
  if (!ok) throw new Error('signature does not cover this payload');

  const payload = JSON.parse(b64urlDecode(payloadB64).toString('utf8'));
  if (payload === null || typeof payload !== 'object') {
    throw new Error('payload is not an object');
  }
  if (payload.v !== 2) {
    throw new Error(
      `payload version ${JSON.stringify(payload.v)} is not a gate attestation — `
      + 'the v1 trust-score payload carries no public key and cannot gate anything',
    );
  }
  for (const required of ['did', 'public_key', 'valid_until']) {
    if (!payload[required]) throw new Error(`payload has no ${required}`);
  }

  const validUntil = Date.parse(payload.valid_until);
  if (Number.isNaN(validUntil)) {
    throw new Error(`not an RFC 3339 timestamp: ${payload.valid_until}`);
  }
  const current = now === undefined ? Date.now() : now;
  if (current > validUntil) {
    throw new Error(`expired at ${payload.valid_until}; ask the agent for a fresh one`);
  }

  return {
    did: payload.did,
    publicKey: payload.public_key,
    trustScore: payload.trust_score === undefined ? null : payload.trust_score,
    withheld: Boolean(payload.withheld),
    credentialTypes: payload.credential_types || [],
    computedAt: payload.computed_at || '',
    validUntil: payload.valid_until,
    policyVersion: payload.policy_version || '',
    version: payload.v,
  };
}

// --------------------------------------------------------------------------
// Proof of control
// --------------------------------------------------------------------------

/**
 * What the calling agent signs. Method, path, DID and moment, newline
 * separated. Each element stops a specific reuse: a proof made for a free
 * route replayed against a paid one, a proof lifted from one agent and
 * presented by another, a proof kept and used tomorrow.
 */
function bindingString(method, path, did, timestamp) {
  return Buffer.from(
    [BINDING_VERSION, String(method).toUpperCase(), path, did, String(timestamp)].join('\n'),
    'utf8',
  );
}

function verifyProof(att, method, path, timestamp, proofB64, maxAgeSeconds, now) {
  const ts = Number(String(timestamp).trim());
  if (!Number.isFinite(ts)) return `timestamp ${JSON.stringify(timestamp)} is not a number`;
  const current = (now === undefined ? Date.now() : now) / 1000;
  const age = current - ts;
  if (age > maxAgeSeconds) return `proof is ${Math.round(age)}s old, limit ${maxAgeSeconds}s`;
  if (age < -maxAgeSeconds) {
    return `proof is ${Math.round(-age)}s in the future, limit ${maxAgeSeconds}s`;
  }

  let key;
  try {
    key = keyFromHex(att.publicKey);
  } catch (err) {
    return `public_key in the attestation is unusable: ${err.message}`;
  }
  let signature;
  try {
    signature = b64urlDecode(proofB64);
  } catch (err) {
    return `proof is not base64url: ${err.message}`;
  }
  const ok = crypto.verify(null, bindingString(method, path, att.did, timestamp), key, signature);
  return ok ? null : 'proof does not verify under the attested public key';
}

// --------------------------------------------------------------------------
// The gate
// --------------------------------------------------------------------------

function header(headers, name) {
  if (!headers) return '';
  if (typeof headers.get === 'function') return headers.get(name) || '';
  const direct = headers[name];
  if (direct) return String(direct);
  for (const [k, v] of Object.entries(headers)) {
    if (k.toLowerCase() === name) return String(v);
  }
  return '';
}

function deny(reason, detail, extra) {
  return Object.assign({ allowed: false, reason, detail: detail || '' }, extra || {});
}

/**
 * Build a gate. Returns a decision function; `requireMolTrust` below wraps it
 * as Express middleware.
 */
function gateFor(options) {
  const {
    minScore = null,
    credentialType = null,
    requiredCredentials = [],
    jwks: rawJwks,
    maxAgeSeconds = DEFAULT_MAX_AGE_SECONDS,
    allowWithheld = false,
    seen = null,
  } = options || {};

  const jwks = loadJwks(rawJwks);
  const wanted = Array.from(new Set(
    requiredCredentials.concat(credentialType ? [credentialType] : []),
  )).sort();

  return function decide(method, path, headers, now) {
    const token = header(headers, HEADER_ATTESTATION);
    const proof = header(headers, HEADER_PROOF);
    const timestamp = header(headers, HEADER_TIMESTAMP);

    if (!token) {
      return deny('attestation_missing',
        `send the gate_attestation from GET /skill/trust-score/<did> in ${HEADER_ATTESTATION}`);
    }
    if (!proof || !timestamp) {
      return deny('proof_missing', `${HEADER_PROOF} and ${HEADER_TIMESTAMP} are both required`);
    }

    let att;
    try {
      att = verifyAttestation(token, jwks, now);
    } catch (err) {
      return deny('attestation_invalid', err.message);
    }

    const problem = verifyProof(att, method, path, timestamp, proof, maxAgeSeconds, now);
    if (problem) return deny('proof_invalid', problem, { did: att.did });

    if (seen && !seen(proof)) {
      return deny('proof_replayed', 'this proof has been presented before', { did: att.did });
    }

    if (att.withheld && !allowWithheld) {
      return deny('score_withheld',
        'no score has been computed for this agent; that is not a low score, '
        + 'and this gate does not read it as one',
        { did: att.did, credentialTypes: att.credentialTypes });
    }

    if (minScore !== null && minScore !== undefined) {
      if (att.trustScore === null) {
        return deny('score_missing', 'the attestation carries no score to compare',
          { did: att.did, credentialTypes: att.credentialTypes });
      }
      if (att.trustScore < minScore) {
        return deny('score_below_minimum', `score ${att.trustScore} is below ${minScore}`,
          { did: att.did, trustScore: att.trustScore, credentialTypes: att.credentialTypes });
      }
    }

    const missing = wanted.filter((c) => !att.credentialTypes.includes(c));
    if (missing.length) {
      return deny('credential_missing',
        `holds ${JSON.stringify(att.credentialTypes)}, needs ${JSON.stringify(missing)}`,
        { did: att.did, trustScore: att.trustScore, credentialTypes: att.credentialTypes });
    }

    return {
      allowed: true,
      reason: 'ok',
      detail: '',
      did: att.did,
      trustScore: att.trustScore,
      credentialTypes: att.credentialTypes,
    };
  };
}

/** Express middleware. On a denial it answers 403 with the reason. */
function requireMolTrust(options) {
  const decide = gateFor(options);
  const onDeny = (options || {}).onDeny || null;

  return function moltrustGate(req, res, next) {
    const decision = decide(req.method, req.path || req.url, req.headers);
    if (decision.allowed) {
      req.moltrust = decision;
      return next();
    }
    if (onDeny) return onDeny(req, res, decision);
    return res.status(403).json({
      error: decision.reason,
      detail: decision.detail,
      did: decision.did || null,
      docs: 'https://moltrust.ch/developers.html',
    });
  };
}

module.exports = {
  requireMolTrust,
  gateFor,
  verifyAttestation,
  bindingString,
  loadJwks,
  BINDING_VERSION,
  HEADER_ATTESTATION,
  HEADER_TIMESTAMP,
  HEADER_PROOF,
};
