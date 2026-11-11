"""Check an agent-evidence-vectors a2a-jcs-v01 report against this repository's pins.

The Action fails a job when a vector disagrees. This adds what the Action does
not know: that the corpus it replayed is the one pinned in the workflow, and,
for the negative control, that the harness went red on exactly the four
rule-3 vectors and nothing else.

    python -m tests.conformance.check_aev_report --report <json> --digest <sha256> [--control]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tests.conformance import score_a2a_jcs


def findings(report: dict, digest: str, control: bool) -> list[str]:
    """Return every way ``report`` misses the pins; empty when it meets them."""
    out: list[str] = []
    replayed = report.get("corpus", {}).get("corpusDigest")
    if replayed != digest:
        out.append(f"corpus digest {replayed} is not the pinned {digest}")
    totals = report.get("totals", {})
    executed = report.get("verifier", {}).get("vectorsExecuted")
    if executed != totals.get("vectors"):
        out.append(f"the path answered {executed} of {totals.get('vectors')} vectors")
    failed = {row["id"] for row in report.get("vectors", []) if row.get("status") != "PASS"}
    expected = score_a2a_jcs.RULE3 if control else set()
    if failed != expected:
        out.append(f"failed {sorted(failed)}, expected {sorted(expected)}")
    return out


def main() -> int:
    """Read the report and print each finding; exit 1 when there is one."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--control", action="store_true")
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    problems = findings(report, args.digest, args.control)
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        what = "failed exactly the four rule-3 vectors" if args.control else "passed"
        print(f"{report['target']}: {what}, corpus {args.digest[:12]}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
