"""Score aar_reader against vectors-agent-audit-record (release 0.28.0, 141 members).

usage: venv/bin/python api/tests/conformance/score_aar.py <corpus-dir> <out.tsv>

The reader under test is tests/conformance/aar_reader.py, written from the
text of draft-gilda-audit-agent-audit-record-03, Sections 4 and 5, and from no
other implementation of the format. It imports nothing from app.*.

What this runner does
---------------------
It reads MANIFEST.json twice, for two different purposes:

  1. verifier configuration, from top-level members only: keys.observer
     (the envelope signing key), payloadType, predicateType, skewTolerance
     (capped at 300, -03 L285-290), algorithm (ed25519 / sha256) and
     anchorTrust (treeHeads and blockHeaders). These are what a verifier
     holds before it sees a record (-03 L851-857, L859-863).
  2. scoring, after the reader has judged a member from that member's bytes
     alone: expected.verdict, readings (N2), expected.dating (R1-R16) and
     expected.set (CS7, CS8, CS17, SQ4, SQ5). No member entry is passed to
     the reader.

A member conforms when the reader's verdict equals expected.verdict, or for
the indeterminate member when it is one of its readings. The dating result of
a retained member and the set result of a set member are compared as well and
printed, but they do not enter the conforming count: the draft reports them
beside the verdict and never folds them into it (-03 L597-606, L773).

What the reader checks (each with its -03 line in the reader's comments)
------------------------------------------------------------------------
  * JSON profile while parsing: duplicate member names, nesting deeper than
    128, integers at or above 2^53 (4.1, 4.6)
  * DSSE: Ed25519 over PAE of the RFC 8785 bytes it derives itself, never the
    carried bytes (4.1.1, 4.4, 4.6)
  * Statement type, predicate type v1 (and v2 of Section 8), the seventeen
    required members, every closed vocabulary of Section 5, RFC 3339 times,
    lowercase hex digests under the one hashAlgorithm
  * subject convention, request digest, write chain replay, empty-tree
    constant, pathScope globs and derived inScope, effect.observed, agreement
    (Table 2) and posture.agreement, issuedAt at or after sealedAt
  * resource binding / argumentsDigest, subjectKind none, oversight, sequence,
    correlation.timeBasis / externalAnchor, remediation ordering
  * prior commitment: order before openedAt, digest recompute, keyid not in
    agent.signers
  * evaluation: members, entries, freshness at decision.reportedAt, the
    300-second bound on lastResult, the derived unavailable set against
    status and unavailableInput
  * consumption: members, non-negative integers, held sum and distinct keys,
    operationKey, retry identity, derived withinLimit
  * coverage gap inside pathScope, and the tier recompute (no downgrade)
  * retained records: envelopeDigest, anchors (kind, origin), validationData,
    received / receivedAt, the R4/R3/R6/R7 bracket with the 7200-second
    opentimestamps allowance, earliest validating anchor (block height between
    two opentimestamps anchors), receipt as asserted-consistent, the
    delegation root (established / not-established / mismatch)
  * anchor validation: transparency-log as a hash commitment (RFC 9162
    inclusion_proof_v2 recomputed to a held tree head) and opentimestamps (a
    DetachedTimestampFile recomputed to the Merkle root of a held block
    header; pending attestations never validate)
  * sets: consumption across records (5.9: stale revision omitting a held
    permit; release from held with no settling record) and sequence (5.10:
    missing numbers below the highest held; one seq with two identities)

What the reader does NOT check
------------------------------
  * rfc3161 anchors: no time-stamping authority trust anchor is held, so such
    an anchor never validates here. No member of release 0.28.0 carries one.
  * transparency-log anchors as signed tokens (tree head signature under a
    log key from validationData): no log key is held; only the hash
    commitment basis is implemented. Every anchor in the corpus is a hash
    commitment.
  * the envelope signature from validationData: the observer key comes from
    configuration; the jwk-set in validationData is not read for it.
  * priorCommitment.sig: the draft defines no preimage for it.
  * the post-quantum classification of anchors (4.7.2, SHOULD only).
  * the outage rule of 6.9 (R17, not shipped) and the producer-side
    aggregation of several values of one input (FR14's manifest
    `aggregation`), which a verifier holding the record cannot see.
  * hashAlgorithm other than sha256: the record is reported as indeterminate
    (no verdict reached, -03 L1078-1081). No member carries another value.
  * the "SHOULD" rules (doesNotAssert content, cross-party scope, entropy of
    bearer secrets) and the rule that authentication none is not used for a
    request that presented a credential, which no function of the bytes tests.

A negative control flips one payload byte of the first accept member; it must
not come back valid.
"""

from __future__ import annotations

import base64
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aar_reader as R  # noqa: E402


def conforming_set(entry: dict) -> list:
    exp = entry["expected"]
    if "verdict" in exp:
        return [exp["verdict"]]
    return [r["verdict"] for r in entry.get("readings", [])]


def compare_dating(res: R.DatingResult, exp: dict) -> list:
    diffs = []
    if res.entry != exp.get("entry"):
        diffs.append(f"entry {res.entry} vs {exp.get('entry')}")
    got_at = R.fmt_time(res.dated_at)
    if got_at != exp.get("datedAt"):
        diffs.append(f"datedAt {got_at} vs {exp.get('datedAt')}")
    if res.receipt != exp.get("receipt"):
        diffs.append(f"receipt {res.receipt} vs {exp.get('receipt')}")
    dr = exp.get("delegationRoot", {})
    if res.delegation_root != dr.get("result"):
        diffs.append(f"delegationRoot {res.delegation_root} vs {dr.get('result')}")
    if "algorithm" in dr and res.delegation_root_algorithm != dr["algorithm"]:
        diffs.append(f"delegationRoot.algorithm {res.delegation_root_algorithm} vs {dr['algorithm']}")
    return diffs


def compare_set(res: R.SetResult, exp: dict) -> list:
    diffs = []
    if res.verdict != exp.get("verdict"):
        diffs.append(f"set {res.verdict} vs {exp.get('verdict')}")
    if "missing" in exp and res.missing != exp["missing"]:
        diffs.append(f"missing {res.missing} vs {exp['missing']}")
    if "withinLimit" in exp and res.within_limit != exp["withinLimit"]:
        diffs.append(f"withinLimit {res.within_limit} vs {exp['withinLimit']}")
    return diffs


def main(corpus: str, out: str) -> int:
    manifest = json.load(open(os.path.join(corpus, "MANIFEST.json")))
    cfg = R.VerifierConfig.from_manifest_trust(manifest)
    vectors = manifest["vectors"]

    rows = ["draftId\tid\tfile\tkind\texpected\treader\tconforms\treason_stage\tside_result"]
    per_class = collections.OrderedDict((k, [0, 0]) for k in ("accept", "reject", "indeterminate"))
    nonconforming = []
    side_diffs = []
    side_checked = 0
    for v in vectors:
        raw = open(os.path.join(corpus, v["file"]), "rb").read()
        res = R.judge_member(raw, cfg)          # bytes and configuration only
        want = conforming_set(v)                # scoring only, after the judgement
        ok = res.verdict in want
        per_class[v["kind"]][0] += 1
        per_class[v["kind"]][1] += ok
        side = ""
        if res.dating is not None and "dating" in v["expected"]:
            side_checked += 1
            d = compare_dating(res.dating, v["expected"]["dating"])
            side = ("dating ok" if not d else "dating DIFF: " + "; ".join(d)) + \
                   f" [entry={res.dating.entry}{(': ' + res.dating.reason) if res.dating.reason else ''}; " \
                   f"datedAt={R.fmt_time(res.dating.dated_at)}; root={res.dating.delegation_root}]"
            if d:
                side_diffs.append((v["draftId"], v["file"], d))
        if res.set is not None and "set" in v["expected"]:
            side_checked += 1
            d = compare_set(res.set, v["expected"]["set"])
            side = ("set ok" if not d else "set DIFF: " + "; ".join(d)) + \
                   f" [set={res.set.verdict}{(': ' + res.set.reason) if res.set.reason else ''}; " \
                   f"missing={res.set.missing}; withinLimit={res.set.within_limit}]"
            if d:
                side_diffs.append((v["draftId"], v["file"], d))
        exp_s = v["expected"]["verdict"] if "verdict" in v["expected"] else "{" + ",".join(want) + "}"
        rows.append("\t".join([v["draftId"], v["id"], v["file"], v["kind"], exp_s, res.verdict,
                               "yes" if ok else "no", res.reason.replace("\t", " "), side]))
        if not ok:
            nonconforming.append((v["draftId"], v["file"], exp_s, res.verdict, res.reason))

    # Negative control: one flipped payload byte in the first accept envelope.
    first = next(v for v in vectors if v["kind"] == "accept" and v["file"].startswith("statements/"))
    env = json.loads(open(os.path.join(corpus, first["file"]), "rb").read())
    p = bytearray(base64.b64decode(env["payload"]))
    i = p.index(b'"permit"') + 2
    p[i] = ord("q")
    env["payload"] = base64.b64encode(bytes(p)).decode()
    control = R.judge_member(json.dumps(env).encode(), cfg)
    assert control.verdict != R.VALID, control

    with open(out, "w", newline="\n") as f:
        f.write("\n".join(rows) + "\n")

    n = len(vectors)
    total = sum(c[1] for c in per_class.values())
    print("reader: tests/conformance/aar_reader.py (draft-gilda-audit-agent-audit-record-03, Sections 4 and 5)")
    print(f"corpusDigest {manifest['corpusDigest']} (as the manifest declares it)")
    print(f"manifest counts {json.dumps(manifest.get('counts'), sort_keys=True)}")
    for k, (exp_n, conf_n) in per_class.items():
        print(f"  {k:<13} expected {exp_n:>3}  conforming {conf_n:>3}")
    print(f"total: {total} of {n} conform")
    if nonconforming:
        print("non-conforming members:")
        for d, f, e, got, why in nonconforming:
            print(f"  {d}\t{f}\texpected {e}\treader {got}\t{why}")
    else:
        print("non-conforming members: none")
    print(f"side results (dating of retained members, set checks): {side_checked - len(side_diffs)} of {side_checked} agree with the manifest")
    for d, f, diffs in side_diffs:
        print(f"  {d}\t{f}\t{'; '.join(diffs)}")
    print(f"control ({first['draftId']} payload, one byte flipped): {control.verdict}, {control.reason}")
    return 0 if total == n and not side_diffs else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__.split("\n\n")[1])
    sys.exit(main(sys.argv[1], sys.argv[2]))
