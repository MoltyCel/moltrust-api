"""Score this repository's three RFC 8785 paths against the a2a-jcs-v01 corpus.

Runs the corpus's own runner, unmodified, once per path, and fails unless:

* the corpus is the one pinned (``--digest``), so a moved corpus cannot pass;
* each path passes every vector of the target it provides;
* the negative control fails all four rule-3 vectors, so a green run is known
  to come from a harness that can go red.

Each runner record is written to ``--out`` and a per-path table goes to the job
summary when ``GITHUB_STEP_SUMMARY`` is set.

    python tests/conformance/score_a2a_jcs.py --corpus <a2a-jcs-v01 dir> --digest <sha256> --out <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ADAPTER = "tests.conformance.a2a_jcs_paths"
REPO_ROOT = Path(__file__).resolve().parents[2]

# app.signature.canonicalize forwards to the jcs library and raises nothing of
# its own, so the raw and signing paths refuse with whatever that library
# raises. Measured against a2a-jcs-v01, both paths and every reject vector:
# UnicodeEncodeError on the five lone-surrogate rejects (A3-REJECT-011/012,
# A4-REJECT-013/014/015) and ValueError on the three number rejects
# (A5-REJECT-016/017/018). Both are needed and neither is redundant: the runner
# of a2aproject/a2a-tck#228 classifies with `type(exc) in refusals`, exact
# identity, so declaring ValueError does not cover UnicodeEncodeError even
# though it is a subclass. That is also why the verify path declares only
# CardVerificationError — there it is the one class the code itself raises.
LIBRARY_REFUSALS = [
    "--refusal",
    "builtins:UnicodeEncodeError",
    "--refusal",
    "builtins:ValueError",
]

# name, runner options, what it is scored as
PATHS = [
    (
        "raw canonicalizer (app/signature.py canonicalize)",
        "raw",
        ["--canonicalize", f"{ADAPTER}:canonicalize", *LIBRARY_REFUSALS],
    ),
    (
        "card signing (app/signature.py sign_agent_card)",
        "signing",
        ["--signing-bytes", f"{ADAPTER}:signing_path", *LIBRARY_REFUSALS],
    ),
    (
        "card verify (lib/agent_card_verify.py verify_agent_card)",
        "verify",
        [
            "--signing-bytes",
            f"{ADAPTER}:verify_path",
            "--refusal",
            "lib.agent_card_verify:CardVerificationError",
        ],
    ),
]
CONTROL = (
    "negative control: signing with the rule-3 strip removed",
    "control",
    ["--signing-bytes", f"{ADAPTER}:signing_path_without_strip", *LIBRARY_REFUSALS],
)
RULE3 = {"A2-001", "A2-002", "A2-REJECT-003", "A2-REJECT-004"}


def run(corpus: Path, options: list[str], out: Path) -> tuple[int, dict]:
    """Run the corpus runner with ``options`` and keep its record."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.run(
        [sys.executable, str(corpus / "run_python.py"), str(corpus), *options],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    out.write_text(proc.stdout, encoding="utf-8")
    if proc.returncode == 2 or not proc.stdout.strip():
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"runner could not run {options}: exit {proc.returncode}")
    return proc.returncode, json.loads(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    rows: list[str] = []
    for label, name, options in PATHS:
        code, record = run(args.corpus, options, args.out / f"{name}.json")
        if record["corpusDigest"] != args.digest:
            failures.append(
                f"{name}: corpus digest {record['corpusDigest']} is not the pinned {args.digest}"
            )
        for target, tally in record["targets"].items():
            rows.append(
                f"| {label} | `{target}` | {tally['passed']}/{tally['vectors']} |"
            )
            if tally["passed"] != tally["vectors"]:
                bad = [
                    f"{r['id']} {r['outcome']}"
                    for r in record["results"]
                    if r["outcome"] != "pass"
                ]
                failures.append(
                    f"{name} on {target}: {tally['passed']}/{tally['vectors']}: {', '.join(bad)}"
                )
        if code != 0:
            failures.append(f"{name}: runner exited {code}")

    label, name, options = CONTROL
    code, record = run(args.corpus, options, args.out / f"{name}.json")
    caught = {r["id"] for r in record["results"] if r["outcome"] != "pass"}
    tally = record["targets"]["card-signing-input"]
    rows.append(
        f"| {label} | `card-signing-input` | {tally['passed']}/{tally['vectors']} (must fail all four rule-3 vectors) |"
    )
    if code != 1 or caught != RULE3:
        failures.append(
            f"{name}: the harness did not go red as it must (exit {code}, failed {sorted(caught)})"
        )

    table = "\n".join(["| path | target | passed |", "| --- | --- | --- |", *rows])
    summary = f"### a2a-jcs-v01, corpus `{args.digest[:12]}`\n\n{table}\n"
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(summary)
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
