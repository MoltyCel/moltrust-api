"""`moltrust-verify` — Kommandozeile fuer die unabhaengige Pruefung eines AER-Records.

    moltrust-verify --input decision.json --trust-list sources.json

`decision.json` traegt `record`, `bundle`, `mandate` und `transaction`; jedes davon laesst
sich mit einer eigenen Datei ueberschreiben. Die Trust-List sagt, welchen Quellen der
Pruefende glaubt — sie gehoert ihm, nicht dem Entscheider, und wird deshalb immer getrennt
uebergeben.

Exit-Code 0 heisst: alle vier Pruefungen halten. 1 heisst: mindestens eine faellt. 2 heisst,
dass die Eingabe schon nicht lesbar war — auch das ist kein bestandener Lauf.

Netzfrei per Konstruktion: das Modul importiert keinen HTTP-Client und oeffnet keine
Verbindung. Der Lauf funktioniert auf einer Maschine ohne Route ins Netz, Tage nach der
Entscheidung.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

from .verify import AerVerifyResult, verify_record

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INPUT = 2

_PARTS = ("record", "bundle", "mandate", "transaction")


def _load(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _collect(args: argparse.Namespace) -> Dict[str, Any]:
    """Die vier Eingaben zusammensuchen: erst aus `--input`, dann von Einzeldateien
    ueberschrieben."""
    parts: Dict[str, Any] = {}
    if args.input is not None:
        blob = _load(args.input)
        if not isinstance(blob, dict):
            raise ValueError("--input must contain a JSON object")
        for name in _PARTS:
            if name in blob:
                parts[name] = blob[name]
    for name in _PARTS:
        path = getattr(args, name)
        if path is not None:
            parts[name] = _load(path)
    missing = [name for name in _PARTS if name not in parts]
    if missing:
        raise ValueError("missing input(s): " + ", ".join(missing))
    return parts


def _render(result: AerVerifyResult) -> str:
    lines = []
    for name in ("V1", "V2", "V3", "V4"):
        check = result.checks.get(name, {"result": "FAIL", "reason": "check did not run"})
        lines.append(f"{name} {check['result']:<4} {check['reason']}")
    lines.append("")
    lines.append("PASS" if result.ok else "FAIL")
    if result.ok and result.recomputed_verdict:
        lines[-1] = f"PASS — recomputed verdict {result.recomputed_verdict}"
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moltrust-verify",
        description="Verify a MolTrust AER verdict record offline (V1 integrity, "
                    "V2 authenticity, V3 freshness, V4 recomputation).")
    parser.add_argument("--input", metavar="FILE",
                        help="JSON object holding record, bundle, mandate and transaction")
    for name in _PARTS:
        parser.add_argument(f"--{name}", metavar="FILE",
                            help=f"JSON file holding the {name} (overrides --input)")
    parser.add_argument("--trust-list", metavar="FILE", required=True,
                        dest="trust_list",
                        help="JSON trust list mapping source ids to ed25519 public keys")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="write the full result as JSON instead of a summary")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        parts = _collect(args)
        trust_list = _load(args.trust_list)
    except (OSError, ValueError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return EXIT_INPUT

    result = verify_record(parts["record"], parts["bundle"], parts["mandate"],
                           parts["transaction"], trust_list)
    if args.as_json:
        print(json.dumps({"ok": result.ok, "checks": result.checks,
                          "failures": list(result.failures),
                          "recomputed_verdict": result.recomputed_verdict},
                         indent=2, sort_keys=True))
    else:
        print(_render(result))
    return EXIT_PASS if result.ok else EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
