"""Replay vectors-agent-audit-record (agent-evidence-vectors v0.18.1, 68 members)
against the checks moltrust-api already applies to its own decision records.

usage: venv/bin/python tests/conformance/score_aar.py <api-checkout> <corpus-dir> <out.tsv>

The corpus is vectors-agent-audit-record at probityai/agent-evidence-vectors
33d545104ff2f8c124cff465c96c013eeb809156 (v0.18.1), corpusDigest
811c1affc0b1669529c572efa7cf1869d09312df22ffafdc490844d4c43a0ba1. Its members
are draft-gilda-wimse-agent-audit-record-02 records.

No rule of the audit-record format is implemented here. The verifier is built
from what the decision-record path of /vc/aae/evaluate does today, in its order:

  1. intake    app/enforcement/json_intake.parse where the checkout has it
               (since #881), else json.loads as Starlette's request.json()
               (last occurrence wins, no depth bound)
  2. integers  app.safe_integers.require_safe_integers (RFC 7493 bound)
  3. signature app.signature.canonicalize (RFC 8785) over the parsed
               Statement, DSSE PAE around it, Ed25519 verify under the corpus
               observer key; the same shape as
               app.enforcement.verdict_sign.verify_verdict, which rebuilds the
               canonical bytes from the record and never trusts carried bytes
  4. members   none: our code has no check for any predicate member

Variant `dupguard` swaps step 1 for the duplicate-refusing object_pairs_hook
of app/enforcement/jws_common.py, our other JSON intake.

A negative control flips one payload byte in A1; it must not come back valid.

Every member that does not conform carries a cause class:
  O  our decision record has no counterpart to the member, so no check exists
  R  our record holds a counterpart, but no verifier recomputes or checks it
  J  our JSON intake lacks the rule
"""

import base64
import json
import os
import sys

CAUSE = {
    "F1": ("O", "no member list for this format; our counterpart to agent is request_proof (did, keyid)"),
    "F2": ("O", "no delegation.subject or subjectKind in our record"),
    "F3": ("O", "no resource.binding or argumentsDigest in our record"),
    "F3b": ("O", "as F3"),
    "F3c": ("O", "as F3"),
    "F4": ("R", "counterpart is content_digest over the body (request_proof); checked at intake only, "
                "no verifier recomputes it from the stored record"),
    "F5": ("O", "no correlation.timeBasis or externalAnchor in our record"),
    "F6": ("O", "no posture in our record"),
    "F7": ("O", "no remediation events in our record"),
    "T1": ("R", "re-signed with the real key; the verdict follows from evaluations (any DENY gives DENY), "
                "verify_verdict does not recompute it"),
    "T4": ("J", "no depth bound at intake (before #881)"),
    "D1": ("R", "no agreement member; derived values are not recomputed (as T1)"),
    "D2": ("O", "no agreement vocabulary in our record"),
    "D3": ("R", "as D1"),
    "S1": ("O", "no subject entries or interval in our record"),
    "S2": ("O", "as S1"),
    "I1r": ("O", "as S1"),
    "TI1": ("O", "no tier or observation in our record"),
    "TI2": ("O", "as TI1"),
    "TI3": ("O", "as TI1"),
    "TI4": ("O", "as TI1"),
    "TI5": ("O", "as TI1"),
    "C1": ("O", "no priorCommitment in our record"),
    "C2": ("O", "as C1"),
    "C3": ("O", "as C1"),
    "E1": ("O", "no effect or write set in our record"),
    "E2": ("O", "as E1"),
    "E3": ("O", "as E1"),
    "V1": ("R", "closed sets are enforced by the database on write (CHECK on verdict, value_source), "
                "no verifier checks them on read"),
    "V2": ("O", "no pathScope in our record"),
    "V3": ("O", "as V2"),
    "FE1": ("O", "no fieldEvidence in our record"),
    "FE2": ("O", "as FE1"),
    "EV1": ("O", "no evaluation.status; an evaluation error is stored as DENY with a reason text"),
    "EV2": ("O", "as EV1"),
    "EV3": ("O", "as EV1"),
    "EV4": ("O", "as EV1"),
    "EV5": ("O", "as EV1"),
    "EV6": ("O", "as EV1"),
    "EV7": ("O", "as EV1"),
    "EV8": ("O", "as EV1"),
    "EV9": ("O", "as EV1"),
    "EV10": ("O", "as EV1"),
    "OV1": ("O", "no oversight in our record"),
    "OV2": ("O", "as OV1"),
    "OV3": ("O", "as OV1"),
}


def main(repo, corpus, out):
    sys.path.insert(0, repo)
    from app.safe_integers import require_safe_integers
    from app.signature import canonicalize
    from app.enforcement.jws_common import JwsGuardError, reject_duplicate_keys
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    # The file decides, not the import: a failing import must stop the run
    # rather than fall back to the old intake.
    if os.path.exists(os.path.join(repo, "app/enforcement/json_intake.py")):
        from app.enforcement.json_intake import parse as strict_parse
    else:
        strict_parse = None
    print("intake:", "json_intake.parse" if strict_parse else "json.loads (request.json)")

    manifest = json.load(open(f"{corpus}/MANIFEST.json"))
    observer = manifest["keys"]["observer"]
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(observer["publicKey"]))
    ptype = manifest["payloadType"].encode()

    def pae(body):
        return b"DSSEv1 %d %s %d %s" % (len(ptype), ptype, len(body), body)

    def judge(env_bytes, variant):
        try:
            env = json.loads(env_bytes)
            payload = base64.b64decode(env["payload"])
            if variant == "dupguard":
                stmt = json.loads(payload, object_pairs_hook=reject_duplicate_keys)
            elif strict_parse is not None:
                stmt = strict_parse(payload)
            else:
                stmt = json.loads(payload)
        except JwsGuardError as e:
            return "malformed", "intake", str(e)
        except RecursionError:
            return "malformed", "intake", "recursion limit"
        except ValueError as e:
            return "malformed", "intake", str(e)[:120]
        try:
            require_safe_integers(stmt)
        except ValueError:
            return "malformed", "integers", "integer outside +-(2**53-1)"
        try:
            canon = canonicalize(stmt)
        except RecursionError:
            return "malformed", "canonicalize", "recursion limit"
        sigs = env.get("signatures") or []
        if len(sigs) != 1:
            return "malformed", "signature", f"{len(sigs)} signatures"
        try:
            pub.verify(base64.b64decode(sigs[0]["sig"]), pae(canon))
        except (InvalidSignature, ValueError):
            return "malformed", "signature", "does not verify over PAE(JCS(statement))"
        return "valid", "-", "no further check in our code"

    tsv = ["draftId\tid\texpected\tours\tstage\tours_dupguard\tstage\tconforms\tcause_class\tcause"]
    conform = {"evaluate": 0, "dupguard": 0}
    classes = {"O": 0, "R": 0, "J": 0}
    for v in manifest["vectors"]:
        raw = open(f"{corpus}/{v['file']}", "rb").read()
        exp = v["expected"]
        # An indeterminate member carries its conforming verdicts in readings.
        conforming = ([exp["verdict"]] if "verdict" in exp
                      else [r["verdict"] for r in v.get("readings", [])])
        assert conforming, v["draftId"]
        e = judge(raw, "evaluate")
        g = judge(raw, "dupguard")
        ok = e[0] in conforming
        conform["evaluate"] += ok
        conform["dupguard"] += g[0] in conforming
        cls, why = ("", "") if ok else CAUSE[v["draftId"]]
        if cls:
            classes[cls] += 1
        tsv.append("\t".join([v["draftId"], v["id"], exp.get("verdict") or "|".join(conforming),
                              e[0], e[1], g[0], g[1], "yes" if ok else "no", cls, why]))

    # Negative control: one flipped payload byte in A1 must not come back valid.
    a1 = next(v for v in manifest["vectors"] if v["draftId"] == "A1")
    env = json.loads(open(f"{corpus}/{a1['file']}", "rb").read())
    p = bytearray(base64.b64decode(env["payload"]))
    p[p.index(b'"permit"') + 2] = ord("q")
    env["payload"] = base64.b64encode(bytes(p)).decode()
    control = judge(json.dumps(env).encode(), "evaluate")
    assert control[0] != "valid", control

    with open(out, "w", newline="\n") as f:
        f.write("\n".join(tsv) + "\n")
    n = len(manifest["vectors"])
    print(f"corpusDigest {manifest['corpusDigest']}")
    print(f"evaluate: {conform['evaluate']} of {n} conform; dupguard: {conform['dupguard']} of {n}")
    print("not conforming by cause:", classes)
    print("control (A1, one byte flipped):", control)


if __name__ == "__main__":
    main(*sys.argv[1:4])
