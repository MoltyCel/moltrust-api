"""Score the 34 members added to vectors-w3c-report since v0.17.4 through the
three RFC 8785 paths of moltrust-api, per path.

usage: venv/bin/python tests/conformance/score_w3c_jcs.py <api-checkout> <corpus-dir> <old-manifest.json>

Per member and path:
  * every check object in subject.checks goes through the path;
  * a declared refusal class -> verdict "reject" (refused: <class>);
  * otherwise the flat root sha256(sha256(leaf_1) || ... || sha256(leaf_n)),
    the construction in canonical.*.command, is held against
    subject.check-set.sha256: equal -> "accept", different -> "reject"
    (digests-differ);
  * the leaf bytes are also held against canonical.conforming.leaves[].hex,
    which says whether the path produced the RFC 8785 bytes the member expects.
Members without a check-set (the run-metrics member) are not judgeable by a
canonicaliser and are reported as n/a.
"""

import hashlib
import json
import sys
from pathlib import Path

api, corpus, old_manifest = (Path(a).resolve() for a in sys.argv[1:4])
sys.path.insert(0, str(api))

from tests.conformance import a2a_jcs_paths  # noqa: E402
from lib.agent_card_verify import CardVerificationError  # noqa: E402

PATHS = {
    "raw": (a2a_jcs_paths.canonicalize, (UnicodeEncodeError, ValueError)),
    "signing": (a2a_jcs_paths.signing_path, (UnicodeEncodeError, ValueError)),
    "verify": (a2a_jcs_paths.verify_path, (CardVerificationError,)),
}


def judge(fn, refusals, member):
    subject = member["subject"]
    check_set = subject.get("check-set")
    if not check_set:
        return {"verdict": "n/a", "why": "no check-set"}
    leaves = []
    for check in subject["checks"]:
        try:
            leaves.append(fn(check))
        except Exception as exc:  # exact class, as the a2a-tck runner matches
            if type(exc) in refusals:
                return {"verdict": "reject", "why": f"refused {type(exc).__name__}"}
            return {"verdict": "error", "why": f"{type(exc).__name__}: {exc}"}
    root = hashlib.sha256(b"".join(hashlib.sha256(x).digest() for x in leaves)).hexdigest()
    conforming = [bytes.fromhex(l["hex"]) for l in member["canonical"]["conforming"].get("leaves", [])]
    bytes_ok = (leaves == conforming) if conforming else None
    if root == check_set["sha256"]:
        return {"verdict": "accept", "why": "root matches", "bytes": bytes_ok}
    return {"verdict": "reject", "why": "digests-differ", "bytes": bytes_ok}


manifest = json.loads((corpus / "MANIFEST.json").read_text(encoding="utf-8"))
old_ids = {v["id"] for v in json.loads(old_manifest.read_text(encoding="utf-8"))["vectors"]}
moved = {"w3c-f-disensor", "w3c-f-16"}
new = [v for v in manifest["vectors"] if v["id"] not in old_ids and v["family"] not in moved]
rows = []
for entry in sorted(new, key=lambda v: (v["family"], v["kind"], v["id"])):
    member = json.loads((corpus / entry["file"]).read_text(encoding="utf-8"))
    row = {
        "id": entry["id"],
        "family": entry["family"],
        "expected": entry["expected"]["verdict"],
        "rejects": entry["expected"].get("rejects", []),
        "cause": entry["expected"].get("cause"),
        "encoding": member["subject"].get("check-set", {}).get("digest-encoding"),
    }
    for name, (fn, refusals) in PATHS.items():
        row[name] = judge(fn, refusals, member)
    rows.append(row)
print(json.dumps({"members": len(rows), "rows": rows}, indent=1, ensure_ascii=True))
