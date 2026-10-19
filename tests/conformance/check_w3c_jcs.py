"""Hold a score_w3c_jcs.py run to the per-member results it was published with.

usage: python -I tests/conformance/check_w3c_jcs.py --scored <out.json> \
           --expected tests/conformance/w3c_jcs_33_members.tsv \
           --manifest <corpus-dir>/MANIFEST.json --digest <corpusDigest>

The expected file is the TSV of the 33-member run at vectors-w3c-report v0.18.1
(sha256 a7cefa02...7591 with LF line endings). For every member and each of the
three paths the run must give the same verdict and reason, written as
"<verdict> (<why>)". Members the scorer reports as n/a must be absent from the
expected file. The MANIFEST must declare the pinned corpusDigest.

Exit 0 when every cell matches, 1 when any differs, 2 on unusable input.
--self-test changes one expected cell and requires the comparison to fail, so a
checker that matches everything cannot pass.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

PATH_COLUMNS = {
    "raw": "app/signature.py canonicalize",
    "signing": "app/signature.py sign_agent_card",
    "verify": "lib/agent_card_verify.py verify_agent_card",
}


def load_expected(path):
    text = Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")
    rows = list(csv.DictReader(text.splitlines(), delimiter="\t"))
    missing = [c for c in ("id", "expected", *PATH_COLUMNS.values()) if c not in rows[0]]
    if missing:
        raise SystemExit(f"expected file lacks columns: {missing}")
    return rows


def compare(scored, expected):
    """Return (differences, per-path agreement with the corpus's expected verdict)."""
    by_id = {r["id"]: r for r in scored["rows"]}
    diffs = []
    agree = {p: 0 for p in PATH_COLUMNS}
    for row in expected:
        got = by_id.get(row["id"])
        if got is None:
            diffs.append(f"{row['id']}: not scored")
            continue
        for path, column in PATH_COLUMNS.items():
            cell = f"{got[path]['verdict']} ({got[path]['why']})"
            if cell != row[column]:
                diffs.append(f"{row['id']} {path}: got {cell!r}, published {row[column]!r}")
            if got[path]["verdict"] == row["expected"]:
                agree[path] += 1
    listed = {r["id"] for r in expected}
    for member_id, got in by_id.items():
        if member_id in listed:
            continue
        verdicts = {got[p]["verdict"] for p in PATH_COLUMNS}
        if verdicts != {"n/a"}:
            diffs.append(f"{member_id}: judged {sorted(verdicts)} but absent from the expected file")
    return diffs, agree


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scored", required=True)
    ap.add_argument("--expected", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--digest", required=True)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    declared = json.loads(Path(args.manifest).read_text(encoding="utf-8")).get("corpusDigest")
    if declared != args.digest:
        print(f"corpusDigest {declared} is not the pinned {args.digest}")
        return 2
    scored = json.loads(Path(args.scored).read_text(encoding="utf-8"))
    expected = load_expected(args.expected)

    if args.self_test:
        flipped = [dict(r) for r in expected]
        column = PATH_COLUMNS["raw"]
        flipped[0][column] = "reject (digests-differ)" if flipped[0][column].startswith("accept") else "accept (root matches)"
        diffs, _ = compare(scored, flipped)
        if not diffs:
            print("self-test: a changed expected cell went unnoticed")
            return 1
        print(f"self-test: a changed expected cell is caught ({len(diffs)} difference)")
        return 0

    diffs, agree = compare(scored, expected)
    print(f"corpusDigest {declared}; {len(expected)} members judged, {scored['members']} scored")
    for path, n in agree.items():
        print(f"  {path:8s} agrees with the expected verdict on {n}/{len(expected)}")
    for d in diffs:
        print(f"  DIFFERS {d}")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
