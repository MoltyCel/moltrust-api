'use strict';

/**
 * Register a MolTrust DID for the operator of a gate that carries none.
 *
 * Once per installation and API URL, on the first decision of a gate built
 * without `operatorDid`:
 *
 *   1. an Ed25519 key pair is generated on this machine,
 *   2. GET /identity/register-challenge fetches a challenge and a small
 *      proof-of-work seed,
 *   3. POST /identity/register-pop sends the public key, a signature over the
 *      challenge, the proof-of-work nonce, a display name derived from the
 *      public key, the platform `usage-x402` and the source `x402-package`,
 *   4. the DID, the public key and the private key are written to
 *      ~/.moltrust/x402-identity.json with file mode 0600.
 *
 * It runs in the background, after the decision that triggered it has been
 * returned, so no request waits for it and none can fail because of it. The
 * request path stays offline. A failure is one line through console.warn.
 *
 * Opt out with `autoRegister: false` or MOLTRUST_NO_AUTO_REGISTER=1. Nothing is
 * registered when `operatorDid` is set or when the CI environment variable is.
 */

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

const API_URL = 'https://api.moltrust.ch';
const ENV_OPT_OUT = 'MOLTRUST_NO_AUTO_REGISTER';
const ENV_IDENTITY_FILE = 'MOLTRUST_IDENTITY_FILE';
const ENV_PLATFORM = 'MOLTRUST_REGISTER_PLATFORM';
const DEFAULT_PLATFORM = 'usage-x402';
const SOURCE = 'x402-package';
const TIMEOUT_MS = 10000;
// A lock younger than this belongs to a process that is still registering.
const LOCK_STALE_MS = 120000;
const FILE_FORMAT = 1;

function disabledReason(options) {
  const o = options || {};
  if (o.autoRegister === false) return 'autoRegister=false';
  const env = String(process.env[ENV_OPT_OUT] || '').trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(env)) return ENV_OPT_OUT;
  if (typeof o.operatorDid === 'string' && o.operatorDid.trim()) return 'operatorDid';
  if (String(process.env.CI || '').trim()) return 'CI';
  return null;
}

function identityFile() {
  const raw = String(process.env[ENV_IDENTITY_FILE] || '').trim();
  return raw || path.join(os.homedir(), '.moltrust', 'x402-identity.json');
}

function readFile(file) {
  try {
    const data = JSON.parse(fs.readFileSync(file, 'utf8'));
    return data && typeof data === 'object' ? data : {};
  } catch (_) {
    return {};
  }
}

/** The identity stored for `apiUrl`, or null. */
function load(apiUrl, file) {
  const ids = readFile(file || identityFile()).identities;
  const entry = ids && typeof ids === 'object' ? ids[apiUrl] : null;
  if (!entry || typeof entry.did !== 'string') return null;
  return { did: entry.did, publicKey: entry.public_key || '', apiUrl };
}

function write(file, apiUrl, entry) {
  const data = readFile(file);
  const ids = data.identities && typeof data.identities === 'object' ? data.identities : {};
  ids[apiUrl] = entry;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify({ format: FILE_FORMAT, identities: ids }, null, 2),
    { mode: 0o600 });
  fs.renameSync(tmp, file);
  fs.chmodSync(file, 0o600);
}

function acquireLock(file) {
  const lock = `${file}.lock`;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  try {
    if (Date.now() - fs.statSync(lock).mtimeMs > LOCK_STALE_MS) fs.unlinkSync(lock);
  } catch (_) { /* no lock */ }
  try {
    fs.closeSync(fs.openSync(lock, 'wx', 0o600));
    return lock;
  } catch (_) {
    return null;
  }
}

function leadingZeroBits(buf) {
  let n = 0;
  for (const byte of buf) {
    if (byte === 0) { n += 8; continue; }
    return n + Math.clz32(byte) - 24;
  }
  return n;
}

/** Solve the proof-of-work in slices, so the event loop keeps serving requests. */
function solvePow(seed, bits) {
  return new Promise((resolve) => {
    let i = 0;
    const slice = () => {
      for (let end = i + 4096; i < end; i += 1) {
        const nonce = i.toString(16);
        const digest = crypto.createHash('sha256').update(seed + nonce, 'ascii').digest();
        if (leadingZeroBits(digest) >= bits) return resolve(nonce);
      }
      return setImmediate(slice);
    };
    slice();
  });
}

async function fetchJson(fetchImpl, url, init) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetchImpl(url, Object.assign({ signal: ctrl.signal }, init || {}));
    if (res.status >= 400) throw new Error(`${new URL(url).pathname} answered HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

function b64url(buf) {
  return Buffer.from(buf).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function register(apiUrl, version, fetchImpl) {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
  const pub = Buffer.from(publicKey.export({ format: 'jwk' }).x, 'base64url').toString('hex');
  const priv = Buffer.from(privateKey.export({ format: 'jwk' }).d, 'base64url').toString('hex');

  const doc = await fetchJson(fetchImpl, `${apiUrl}/identity/register-challenge`);
  const challenge = doc.challenge;
  const pow = doc.pow || {};
  const nonce = await solvePow(pow.seed || challenge.split('.')[0], Number(pow.difficulty_bits || 0));
  const body = {
    public_key: pub,
    challenge,
    signature: b64url(crypto.sign(null, Buffer.from(challenge, 'ascii'), privateKey)),
    pow_nonce: nonce,
    // The registry refuses a second agent with the same name and platform
    // within 24 hours, so the name carries part of the key.
    display_name: `x402-gate-${pub.slice(0, 12)}`,
    platform: String(process.env[ENV_PLATFORM] || '').trim() || DEFAULT_PLATFORM,
    source: SOURCE,
  };
  const out = await fetchJson(fetchImpl, `${apiUrl}/identity/register-pop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'User-Agent': `moltrust-x402/${version}` },
    body: JSON.stringify(body),
  });
  if (!out || typeof out.did !== 'string') throw new Error('register-pop answered without a DID');
  return {
    did: out.did,
    public_key: pub,
    private_key: priv,
    registered_at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
    package: `@moltrust/x402/${version}`,
    platform: body.platform,
  };
}

/**
 * This installation's operator identity, registered if there is none yet.
 * Never rejects; resolves to null when disabled, already running elsewhere,
 * or failed.
 */
async function ensure(options) {
  const o = options || {};
  try {
    if (disabledReason(o)) return null;
    const apiUrl = o.apiUrl || API_URL;
    const file = identityFile();
    const found = load(apiUrl, file);
    if (found) return found;
    const lock = acquireLock(file);
    if (!lock) return null;
    let entry;
    try {
      const again = load(apiUrl, file);
      if (again) return again;
      const fetchImpl = o.fetch || globalThis.fetch;
      if (typeof fetchImpl !== 'function') throw new Error('no fetch available');
      entry = await register(apiUrl, o.version || '0', fetchImpl);
      write(file, apiUrl, entry);
    } finally {
      try { fs.unlinkSync(lock); } catch (_) { /* gone */ }
    }
    console.warn(`MolTrust: registered ${entry.did} for this gate operator (stored in ${file}). `
      + `Opt out: ${ENV_OPT_OUT}=1`);
    return { did: entry.did, publicKey: entry.public_key, apiUrl };
  } catch (err) {
    console.warn(`MolTrust: automatic DID registration skipped (${err && err.message ? err.message : 'error'})`);
    return null;
  }
}

module.exports = { ensure, load, disabledReason, identityFile, solvePow, DEFAULT_PLATFORM, SOURCE };
