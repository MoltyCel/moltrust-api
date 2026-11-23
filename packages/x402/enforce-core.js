'use strict';
/**
 * Enforce-mode verdict engine, JavaScript port of `moltrust_enforce._core`
 * (Python SDK, itself a copy of the server's app/enforcement/enforce_check.py).
 *
 * Pure and deterministic: no network, no clock, no process state.
 * `enforceCheck(mandate, transaction)` returns a verdict and a record whose
 * `core_digest` anyone holding the same mandate and transaction recomputes
 * byte for byte, in Python or here.
 *
 * Decision rules (same as the Python implementation):
 * - deny by default: PERMIT only when a grant binds the action digest, all its
 *   constraints hold and its disposition is `allow`;
 * - type form before binding: the action is an object carrying exactly the
 *   grant's `type_fields`;
 * - PENDING only for an explicit `disposition: "hold"`;
 * - `forbid` outranks every allowing grant;
 * - fail closed on any malformed input.
 *
 * Parity is tested against the AAE conformance vectors and against vectors
 * generated from the Python implementation (test/enforce-core-vectors.json).
 *
 * One limit of the language: JavaScript has a single number type, so `5` and
 * `5.0` are the same value here while Python keeps them apart. Every integer
 * this package derives from an x402 payment is parsed from a decimal string,
 * so the payment path never meets the difference; a mandate that writes a
 * range bound as `5.0` is read here as the integer 5.
 */

const crypto = require('crypto');

const PERMIT = 'PERMIT';
const DENY = 'DENY';
const PENDING = 'PENDING';

const ENFORCE_VERSION = '3.0';

// Domain separation per digest role; each tag ends in one NUL byte.
const TAG_ACTION = Buffer.from('aae:enforce-action:v1\x00', 'utf8');
const TAG_MANDATE = Buffer.from('aae:enforce-mandate:v1\x00', 'utf8');
const TAG_TRANSACTION = Buffer.from('aae:enforce-transaction:v1\x00', 'utf8');
const TAG_CORE = Buffer.from('aae:enforce-core:v1\x00', 'utf8');

const DISPOSITIONS = ['allow', 'hold', 'forbid'];
const CONSTRAINT_TYPES = ['exact', 'enum', 'range'];
const TYPE_FIELD_VERB = 'verb';
const DIGEST_RE = /^sha256:[a-f0-9]{64}$/;

const MAX_GRANTS = 256;
const MAX_CONSTRAINTS_PER_GRANT = 64;
const MAX_ENUM_MEMBERS = 512;
const MAX_TYPE_FIELDS = 32;
const MAX_FIELD_DEPTH = 8;
const MAX_ABS_INT = 1e15;
const MAX_ANCESTORS = 8;
// RFC 7493 section 2.2 (I-JSON): beyond this an integer has no exact double.
const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;

const PASS = 'PASS';
const FAIL = 'FAIL';

// --------------------------------------------------------------------------
// JSON shape helpers. A "dict" is a plain JSON object, never an array or null.
// --------------------------------------------------------------------------

function isDict(x) {
  return x !== null && typeof x === 'object' && !Array.isArray(x);
}

function has(obj, key) {
  return Object.prototype.hasOwnProperty.call(obj, key);
}

/** `obj.get(key)` in Python terms: the value, or null when absent. */
function get(obj, key) {
  return isDict(obj) && has(obj, key) ? obj[key] : null;
}

function isStr(x) {
  return typeof x === 'string';
}

function isInt(x) {
  return typeof x === 'number' && Number.isInteger(x) && Math.abs(x) <= MAX_ABS_INT;
}

/** Python's `sorted()` on strings: by code point, not by UTF-16 unit. */
function cmpCodePoint(a, b) {
  const A = Array.from(a);
  const B = Array.from(b);
  const n = Math.min(A.length, B.length);
  for (let i = 0; i < n; i += 1) {
    const d = A[i].codePointAt(0) - B[i].codePointAt(0);
    if (d !== 0) return d;
  }
  return A.length - B.length;
}

function sortedStrings(list) {
  return list.slice().sort(cmpCodePoint);
}

/**
 * Python's `repr()` for the values the engine prints. Only free text uses it,
 * except one place: an unknown constraint type goes into the core as
 * `str(type)`, which pyStr() reproduces for JSON values.
 */
function pyRepr(v) {
  if (v === null || v === undefined) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  if (typeof v === 'number') return String(v);
  if (typeof v === 'string') {
    const q = v.includes("'") && !v.includes('"') ? '"' : "'";
    let out = '';
    for (const ch of v) {
      const c = ch.codePointAt(0);
      if (ch === '\\') out += '\\\\';
      else if (ch === q) out += `\\${q}`;
      else if (ch === '\n') out += '\\n';
      else if (ch === '\r') out += '\\r';
      else if (ch === '\t') out += '\\t';
      else if (c < 0x20 || c === 0x7f) out += `\\x${c.toString(16).padStart(2, '0')}`;
      else out += ch;
    }
    return q + out + q;
  }
  if (Array.isArray(v)) return `[${v.map(pyRepr).join(', ')}]`;
  if (isDict(v)) {
    return `{${Object.keys(v).map((k) => `${pyRepr(k)}: ${pyRepr(v[k])}`).join(', ')}}`;
  }
  return String(v);
}

function pyStr(v) {
  return typeof v === 'string' ? v : pyRepr(v);
}

// --------------------------------------------------------------------------
// RFC 8785 (JCS), refusing integers outside +-(2**53-1) and lone surrogates,
// as the Python implementation does. Throws on anything that is not a JSON value.
// --------------------------------------------------------------------------

function jcsString(s) {
  for (let i = 0; i < s.length; i += 1) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const d = s.charCodeAt(i + 1);
      if (!(d >= 0xdc00 && d <= 0xdfff)) throw new TypeError('lone surrogate');
      i += 1;
    } else if (c >= 0xdc00 && c <= 0xdfff) {
      throw new TypeError('lone surrogate');
    }
  }
  return JSON.stringify(s);
}

function jcsValue(v) {
  if (v === null) return 'null';
  if (v === true) return 'true';
  if (v === false) return 'false';
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) throw new TypeError('non-finite number');
    if (Number.isInteger(v) && Math.abs(v) > MAX_SAFE_INTEGER) {
      throw new RangeError('integer outside the I-JSON range');
    }
    // ECMAScript Number-to-String is the serialization RFC 8785 prescribes.
    return Object.is(v, -0) ? '0' : String(v);
  }
  if (typeof v === 'string') return jcsString(v);
  if (Array.isArray(v)) return `[${v.map(jcsValue).join(',')}]`;
  if (isDict(v)) {
    // RFC 8785 3.2.3: keys sorted by UTF-16 code units, which is Array#sort's default.
    const keys = Object.keys(v).sort();
    return `{${keys.map((k) => {
      if (v[k] === undefined) throw new TypeError('undefined value');
      return `${jcsString(k)}:${jcsValue(v[k])}`;
    }).join(',')}}`;
  }
  throw new TypeError(`not a JSON value: ${typeof v}`);
}

function canonicalize(obj) {
  return Buffer.from(jcsValue(obj), 'utf8');
}

/** `sha256:<hex>` over tag || JCS(obj), or null when obj is not canonicalizable. */
function digest(tag, obj) {
  let payload;
  try {
    payload = canonicalize(obj);
  } catch (_) {
    return null;
  }
  return `sha256:${crypto.createHash('sha256').update(tag).update(payload).digest('hex')}`;
}

/** Constant-time comparison of two strings; anything else is false. */
function ctEq(a, b) {
  if (!isStr(a) || !isStr(b)) return false;
  const A = Buffer.from(a, 'utf8');
  const B = Buffer.from(b, 'utf8');
  if (A.length !== B.length) {
    crypto.timingSafeEqual(A, A);
    return false;
  }
  return crypto.timingSafeEqual(A, B);
}

// --------------------------------------------------------------------------
// Trace
// --------------------------------------------------------------------------

function n(v) {
  return v === undefined ? null : v;
}

function pred(predicate, field, result, reason, value, bound) {
  return {
    predicate, field: n(field), value: n(value), bound: n(bound), result, reason,
  };
}

/** The trace as it goes into the core: no free-text `reason`. */
function traceForCore(trace) {
  return trace.map((e) => ({
    predicate: e.predicate, field: e.field, value: e.value, bound: e.bound, result: e.result,
  }));
}

function resolveField(transaction, path) {
  if (!isStr(path) || !path) return [false, null];
  const segments = path.split('.');
  if (segments.length > MAX_FIELD_DEPTH || segments.some((s) => s === '')) return [false, null];
  let cur = transaction;
  for (const seg of segments) {
    if (!isDict(cur) || !has(cur, seg)) return [false, null];
    cur = cur[seg];
  }
  return [true, cur];
}

// --------------------------------------------------------------------------
// Constraint predicates
// --------------------------------------------------------------------------

function evalExact(c, transaction) {
  const field = get(c, 'field');
  const expected = get(c, 'value');
  const [found, actual] = resolveField(transaction, field);
  if (!isStr(expected)) return pred('exact', field, FAIL, 'constraint value is not a string', null, expected);
  if (!found) return pred('exact', field, FAIL, 'field not present in transaction', null, expected);
  if (!isStr(actual)) return pred('exact', field, FAIL, 'transaction value is not a string', actual, expected);
  if (ctEq(actual, expected)) return pred('exact', field, PASS, 'exact match', actual, expected);
  return pred('exact', field, FAIL, 'value does not match exactly', actual, expected);
}

function evalEnum(c, transaction) {
  const field = get(c, 'field');
  const members = get(c, 'values');
  const [found, actual] = resolveField(transaction, field);
  if (!Array.isArray(members) || members.length === 0) {
    return pred('enum', field, FAIL, 'constraint values is not a non-empty array', null, members);
  }
  if (members.length > MAX_ENUM_MEMBERS) {
    return pred('enum', field, FAIL, 'constraint values exceeds member cap', null, members.length);
  }
  if (!found) return pred('enum', field, FAIL, 'field not present in transaction', null, members);
  if (!isStr(actual)) return pred('enum', field, FAIL, 'transaction value is not a string', actual, members);
  let hits = 0;
  for (const m of members) if (ctEq(actual, m)) hits += 1;
  if (hits) return pred('enum', field, PASS, 'value in enumeration', actual, members);
  return pred('enum', field, FAIL, 'value not in enumeration', actual, members);
}

function evalRange(c, transaction) {
  const field = get(c, 'field');
  const lo = get(c, 'lo');
  const hi = get(c, 'hi');
  const bound = { lo, hi };
  const [found, actual] = resolveField(transaction, field);
  if (!isInt(lo) || !isInt(hi)) {
    return pred('range', field, FAIL, 'constraint bounds are not bounded integers', null, bound);
  }
  if (lo > hi) return pred('range', field, FAIL, 'constraint bounds inverted (lo > hi)', null, bound);
  if (!found) return pred('range', field, FAIL, 'field not present in transaction', null, bound);
  if (!isInt(actual)) {
    return pred('range', field, FAIL, 'transaction value is not a bounded integer', actual, bound);
  }
  if (lo <= actual && actual <= hi) return pred('range', field, PASS, 'within range', actual, bound);
  return pred('range', field, FAIL, 'outside range', actual, bound);
}

function evalConstraint(c, transaction) {
  if (!isDict(c)) return pred('unknown', null, FAIL, 'constraint is not an object');
  const ctype = get(c, 'type');
  if (!CONSTRAINT_TYPES.includes(ctype)) {
    return pred(pyStr(ctype), get(c, 'field'), FAIL, 'unknown constraint type -> deny by default');
  }
  return { exact: evalExact, enum: evalEnum, range: evalRange }[ctype](c, transaction);
}

// --------------------------------------------------------------------------
// Structure
// --------------------------------------------------------------------------

function typeFieldsOk(tf) {
  if (!Array.isArray(tf) || tf.length === 0 || tf.length > MAX_TYPE_FIELDS) return false;
  for (const name of tf) if (!isStr(name) || !name) return false;
  if (new Set(tf).size !== tf.length) return false;
  return tf.includes(TYPE_FIELD_VERB);
}

function grantShapeOk(g) {
  if (!isDict(g)) return false;
  const ab = get(g, 'action_binding');
  if (!isStr(ab) || !DIGEST_RE.test(ab)) return false;
  if (!DISPOSITIONS.includes(get(g, 'disposition'))) return false;
  if (!typeFieldsOk(get(g, 'type_fields'))) return false;
  const cs = get(g, 'constraints');
  if (!Array.isArray(cs) || cs.length > MAX_CONSTRAINTS_PER_GRANT) return false;
  return true;
}

function typeShapeProblem(action, typeFields) {
  if (!isDict(action)) return 'action is not an object';
  const have = new Set(Object.keys(action));
  const want = new Set(typeFields);
  const missing = sortedStrings([...want].filter((k) => !have.has(k)));
  const extra = sortedStrings([...have].filter((k) => !want.has(k)));
  if (missing.length && extra.length) {
    return `action fields do not match type_fields (missing ${pyRepr(missing)}, outside the type ${pyRepr(extra)})`;
  }
  if (missing.length) return `action is missing type_fields ${pyRepr(missing)}`;
  if (extra.length) return `action carries fields outside type_fields ${pyRepr(extra)}`;
  return null;
}

function mandateProblem(mandate) {
  if (!isDict(mandate)) return 'mandate missing or not an object';
  const grants = get(mandate, 'grants');
  if (!Array.isArray(grants) || grants.length === 0) return 'mandate.grants missing or empty';
  if (grants.length > MAX_GRANTS) return 'mandate.grants exceeds cap';
  for (let i = 0; i < grants.length; i += 1) {
    if (!grantShapeOk(grants[i])) {
      return `mandate.grants[${i}] malformed (action_binding/disposition/type_fields/constraints)`;
    }
  }
  return null;
}

// --------------------------------------------------------------------------
// Grant attenuation (AAE §5 step 9), same rules as the Python implementation
// --------------------------------------------------------------------------

const DISPOSITION_RANK = { allow: 0, hold: 1, forbid: 2 };

function pathOk(path) {
  if (!isStr(path) || !path) return false;
  const segments = path.split('.');
  return segments.length <= MAX_FIELD_DEPTH && segments.every((s) => s !== '');
}

/** What one constraint admits: {kind:'str', set} or {kind:'int', lo, hi}; null if never. */
function admitted(c) {
  if (!isDict(c) || !pathOk(get(c, 'field'))) return null;
  const ctype = get(c, 'type');
  if (ctype === 'exact') {
    const v = get(c, 'value');
    return isStr(v) ? { kind: 'str', set: new Set([v]) } : null;
  }
  if (ctype === 'enum') {
    const vs = get(c, 'values');
    if (!Array.isArray(vs) || vs.length === 0 || vs.length > MAX_ENUM_MEMBERS) return null;
    const members = new Set(vs.filter(isStr));
    return members.size ? { kind: 'str', set: members } : null;
  }
  if (ctype === 'range') {
    const lo = get(c, 'lo');
    const hi = get(c, 'hi');
    if (!isInt(lo) || !isInt(hi) || lo > hi) return null;
    return { kind: 'int', lo, hi };
  }
  return null;
}

function admitsSubset(a, b) {
  if (a === null) return true;
  if (b === null || a.kind !== b.kind) return false;
  if (a.kind === 'str') return [...a.set].every((x) => b.set.has(x));
  return b.lo <= a.lo && a.hi <= b.hi;
}

function admitsDisjoint(a, b) {
  if (a === null || b === null || a.kind !== b.kind) return true;
  if (a.kind === 'str') return ![...a.set].some((x) => b.set.has(x));
  return a.hi < b.lo || b.hi < a.lo;
}

function grantDead(g) {
  return g.constraints.some((c) => admitted(c) === null);
}

function unimplied(child, parent) {
  for (const c of parent.constraints) {
    const want = admitted(c);
    const field = isDict(c) ? get(c, 'field') : null;
    const ok = child.constraints.some((cc) => isDict(cc) && get(cc, 'field') === field
      && admitsSubset(admitted(cc), want));
    if (!ok) {
      const ctype = isDict(c) ? get(c, 'type') : null;
      return `parent constraint ${pyStr(ctype)} on ${pyRepr(field)} is absent or not narrowed`;
    }
  }
  return null;
}

function grantsDisjoint(a, b) {
  if (grantDead(a) || grantDead(b)) return true;
  return a.constraints.some((ca) => b.constraints.some((cb) => isDict(ca) && isDict(cb)
    && get(ca, 'field') === get(cb, 'field')
    && admitsDisjoint(admitted(ca), admitted(cb))));
}

function grantAttenuationProblem(child, parent) {
  for (const [who, m] of [['child', child], ['parent', parent]]) {
    const problem = mandateProblem(m);
    if (problem !== null) return `${who} ${problem}`;
  }
  const pgrants = parent.grants;
  const cgrants = child.grants;
  const forbidden = pgrants.filter((q) => q.disposition === 'forbid').map((q) => q.action_binding);

  for (let i = 0; i < cgrants.length; i += 1) {
    const g = cgrants[i];
    const same = [];
    pgrants.forEach((q, j) => { if (ctEq(q.action_binding, g.action_binding)) same.push(j); });
    if (same.length === 0) return `grant[${i}] binds an action no parent grant binds`;
    if (g.disposition === 'forbid' || grantDead(g)) continue;
    if (forbidden.some((f) => ctEq(f, g.action_binding))) {
      return `grant[${i}] disposition=${g.disposition} for an action the parent forbids`;
    }
    let onDisposition = null;
    let onConstraints = null;
    let covered = false;
    for (const j of same) {
      const q = pgrants[j];
      if (DISPOSITION_RANK[g.disposition] < DISPOSITION_RANK[q.disposition]) {
        onDisposition = onDisposition
          || `grant[${i}] disposition ${g.disposition} is broader than parent grant[${j}] ${q.disposition}`;
        continue;
      }
      let why = unimplied(g, q);
      if (why === null && g.disposition === 'allow') {
        const blocker = same.find((k) => k < j && pgrants[k].disposition === 'hold'
          && !grantsDisjoint(g, pgrants[k]));
        if (blocker !== undefined) why = `parent grant[${blocker}] holds first on the same transactions`;
      }
      if (why === null) {
        covered = true;
        break;
      }
      onConstraints = onConstraints || `grant[${i}] vs parent grant[${j}]: ${why}`;
    }
    if (!covered) return onConstraints || onDisposition;
  }
  return null;
}

function attenuationTrace(mandate, ancestors, trace) {
  if (!Array.isArray(ancestors) || ancestors.length > MAX_ANCESTORS) {
    const reason = `ancestors must be an array of at most ${MAX_ANCESTORS} mandates`;
    trace.push(pred('grant_attenuation', null, FAIL, reason));
    return reason;
  }
  const chain = ancestors.concat([mandate]);
  for (let k = 0; k < ancestors.length; k += 1) {
    const parent = chain[k];
    const child = chain[k + 1];
    let problem = grantAttenuationProblem(child, parent);
    const field = `ancestors[${k}]`;
    const value = digest(TAG_MANDATE, child);
    const bound = digest(TAG_MANDATE, parent);
    if (problem === null && (value === null || bound === null)) problem = 'mandate not canonicalizable';
    if (problem !== null) {
      const reason = `grant attenuation, hop ${k}: ${problem}`;
      trace.push(pred('grant_attenuation', field, FAIL, reason, value, bound));
      return reason;
    }
    trace.push(pred('grant_attenuation', field, PASS,
      `hop ${k}: every child grant stays inside the parent`, value, bound));
  }
  return null;
}

// --------------------------------------------------------------------------
// Public
// --------------------------------------------------------------------------

function actionDigest(action) {
  return digest(TAG_ACTION, action);
}

function coreDigest(core) {
  return digest(TAG_CORE, core);
}

function mandateDigest(mandate) {
  return digest(TAG_MANDATE, mandate);
}

function evaluateGrants(mandate, transaction, actDigest, trace) {
  let grantIndex = null;
  const grants = mandate.grants;
  const action = get(transaction, 'action');
  const typed = [];
  let firstProblem = null;
  grants.forEach((g, i) => {
    const problem = typeShapeProblem(action, g.type_fields);
    if (problem === null) typed.push(i);
    else if (firstProblem === null) firstProblem = [i, problem];
  });

  let verdict;
  let reason;
  if (typed.length === 0) {
    const [i, problem] = firstProblem;
    verdict = DENY;
    reason = `grant[${i}]: ${problem}`;
    trace.push(pred('type_fields', 'action', FAIL, reason,
      isDict(action) ? sortedStrings(Object.keys(action)) : null,
      grants[i].type_fields.slice()));
  } else {
    trace.push(pred('type_fields', 'action', PASS,
      `action carries exactly the type_fields of grant(s) ${pyRepr(typed)}`,
      sortedStrings(Object.keys(action)), grants[typed[0]].type_fields.slice()));
    const matched = typed.filter((i) => ctEq(grants[i].action_binding, actDigest));
    if (matched.length === 0) {
      verdict = DENY;
      reason = 'unaddressed action: no grant binds this action digest';
      trace.push(pred('action_binding', 'action', FAIL, reason, actDigest, null));
    } else {
      trace.push(pred('action_binding', 'action', PASS, `bound by grant(s) ${pyRepr(matched)}`,
        actDigest, actDigest));
      const forbidden = matched.filter((i) => grants[i].disposition === 'forbid');
      if (forbidden.length) {
        grantIndex = forbidden[0];
        verdict = DENY;
        reason = `grant[${grantIndex}] disposition=forbid`;
        trace.push(pred('disposition', null, FAIL, reason, 'forbid', null));
      } else {
        verdict = DENY;
        reason = 'no matching grant satisfied its constraints';
        for (const i of matched) {
          const g = grants[i];
          const preds = g.constraints.map((c) => evalConstraint(c, transaction));
          trace.push(...preds);
          if (preds.every((p) => p.result === PASS)) {
            grantIndex = i;
            const disp = g.disposition;
            verdict = disp === 'allow' ? PERMIT : PENDING;
            reason = `grant[${i}] matched, all constraints hold, disposition=${disp}`;
            trace.push(pred('disposition', null, PASS, reason, disp, null));
            break;
          }
        }
      }
    }
  }
  return [verdict, reason, grantIndex];
}

/**
 * Evaluate `transaction` against `mandate`. Returns
 * `{verdict, reason, grant_index, trace, core, core_digest}`.
 */
function enforceCheck(mandate, transaction, prevCoreDigest = null, ancestors = null) {
  const trace = [];
  let grantIndex = null;
  let verdict;
  let reason;

  const problem = mandateProblem(mandate);
  const txOk = isDict(transaction);
  const actDigest = txOk ? actionDigest(get(transaction, 'action')) : null;
  const mDigest = digest(TAG_MANDATE, mandate);
  const txDigest = digest(TAG_TRANSACTION, transaction);

  if (problem !== null) {
    verdict = DENY; reason = problem;
    trace.push(pred('mandate_present', null, FAIL, problem));
  } else if (!txOk) {
    verdict = DENY; reason = 'transaction missing or not an object';
    trace.push(pred('transaction_present', null, FAIL, reason));
  } else if (actDigest === null) {
    verdict = DENY; reason = 'transaction.action missing or not canonicalizable';
    trace.push(pred('action_binding', 'action', FAIL, reason));
  } else if (mDigest === null) {
    verdict = DENY; reason = 'mandate not canonicalizable';
    trace.push(pred('mandate_digest', null, FAIL, reason));
  } else if (txDigest === null) {
    verdict = DENY; reason = 'transaction not canonicalizable';
    trace.push(pred('transaction_digest', null, FAIL, reason));
  } else {
    trace.push(pred('mandate_present', null, PASS, 'mandate structurally valid'));
    const noChain = ancestors === null || ancestors === undefined
      || (Array.isArray(ancestors) && ancestors.length === 0);
    const chainProblem = noChain ? null : attenuationTrace(mandate, ancestors, trace);
    if (chainProblem !== null) {
      verdict = DENY; reason = chainProblem;
    } else {
      [verdict, reason, grantIndex] = evaluateGrants(mandate, transaction, actDigest, trace);
    }
  }

  const core = {
    enforce_version: ENFORCE_VERSION,
    mandate_digest: mDigest,
    transaction_digest: txDigest,
    action_digest: actDigest,
    verdict,
    grant_index: grantIndex,
    trace: traceForCore(trace),
    prev_core_digest: isStr(prevCoreDigest) ? prevCoreDigest : null,
  };
  const cDigest = coreDigest(core);
  if (cDigest === null && verdict !== DENY) {
    verdict = DENY; reason = 'verdict core not canonicalizable'; grantIndex = null;
  }
  return {
    verdict, reason, grant_index: grantIndex, trace, core, core_digest: cDigest,
  };
}

module.exports = {
  PERMIT,
  DENY,
  PENDING,
  ENFORCE_VERSION,
  TAG_ACTION,
  TAG_MANDATE,
  TAG_TRANSACTION,
  TAG_CORE,
  canonicalize,
  digest,
  ctEq,
  actionDigest,
  coreDigest,
  mandateDigest,
  enforceCheck,
  grantAttenuationProblem,
};
