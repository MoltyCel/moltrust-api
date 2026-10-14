"""Answer the agent-evidence-vectors a2a-jcs-v01 contract for one path of this repository.

The agent-evidence-vectors Action replays its vendored copy of a2a-jcs-v01 by
running a command once per vector:

    python -m tests.conformance.a2a_jcs_contract <path> <target> <input.json>

``<path>`` is a name from the table in ``score_a2a_jcs.py`` (``raw``,
``signing``, ``verify`` or the negative ``control``), and that table is the
only place a path's function and refusal classes are declared: this module
reads it rather than repeating it, so the a2a-tck job and the Action job score
the same functions under the same classes. ``<target>`` is the corpus target
the harness asks for and must be the one the path provides.

The answer follows the contract in agent-evidence-vectors
docs/reference/corpus-readers.md:

* exit 0 and ``{"canonical_utf8_hex": "<hex>"}`` on the last stdout line when
  the path returned bytes;
* exit 1 when it raised one of the path's declared refusal classes, matched by
  exact type, the way the a2a-tck#228 runner matches them, so a declared
  ValueError does not cover a UnicodeEncodeError;
* exit 2 for anything else (an undeclared exception, a wrong target, input
  that does not parse, an import that fails, a usage error), which the harness
  scores as errored. Python exits 1 on an uncaught exception, so ``main`` catches
  every exception and exits 2: a crash must never read as a refusal, or every
  reject vector would pass.

The input file holds the vector's JSON text, parsed here with ``json.loads``
as the a2a-tck runner parses it.
"""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any

# The runner option that names a path's function, and the corpus target it scores.
TARGET_OF_OPTION = {"--canonicalize": "rfc8785", "--signing-bytes": "card-signing-input"}


def _load(spec: str) -> Any:
    """Import ``module:attribute`` and return the attribute."""
    module_name, _, attribute = spec.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


def _class_name(exc: BaseException) -> str:
    """Name an exception's class as ``module:qualname``, the form the runner declares."""
    return f"{type(exc).__module__}:{type(exc).__qualname__}"


def path_table() -> dict[str, tuple[str, str, list[str]]]:
    """Map each path name to its target, its function and its refusal classes.

    Read from the runner options in ``score_a2a_jcs.PATHS`` and ``CONTROL``.
    """
    from tests.conformance import score_a2a_jcs

    table: dict[str, tuple[str, str, list[str]]] = {}
    for _label, name, options in [*score_a2a_jcs.PATHS, score_a2a_jcs.CONTROL]:
        pairs = list(zip(options[::2], options[1::2]))
        [(target, function)] = [
            (TARGET_OF_OPTION[option], value)
            for option, value in pairs
            if option in TARGET_OF_OPTION
        ]
        refusals = [value for option, value in pairs if option == "--refusal"]
        table[name] = (target, function, refusals)
    return table


def answer(path: str, target: str, text: str) -> tuple[int, str]:
    """Run ``path`` on the JSON ``text`` and return the exit status and stdout line."""
    table = path_table()
    if path not in table:
        return 2, json.dumps({"error": f"no path {path!r}; one of {sorted(table)}"})
    provided, function, refusal_specs = table[path]
    if target != provided:
        return 2, json.dumps(
            {"error": f"path {path!r} provides target {provided!r}, not {target!r}"}
        )
    refusals = tuple(_load(spec) for spec in refusal_specs)
    implementation = _load(function)
    value = json.loads(text)
    try:
        out = implementation(value)
    except refusals as exc:
        if type(exc) in refusals:
            return 1, json.dumps({"refused": _class_name(exc)})
        raise  # a subclass of a declared class is not that class; main exits 2
    if not isinstance(out, (bytes, bytearray)):
        return 2, json.dumps({"error": f"returned {type(out).__name__}, not bytes"})
    return 0, json.dumps({"canonical_utf8_hex": bytes(out).hex()})


def main(argv: list[str] | None = None) -> int:
    """Parse ``<path> <target> <input.json>``, answer, and return the exit status."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3:
        print(
            "usage: python -m tests.conformance.a2a_jcs_contract <path> <target> <input.json>",
            file=sys.stderr,
        )
        return 2
    path, target, input_path = args
    try:
        with open(input_path, encoding="utf-8", errors="surrogatepass") as handle:
            text = handle.read()
        status, line = answer(path, target, text)
    # Deliberately broad: whatever is not a declared refusal must exit 2, and an
    # uncaught exception would exit 1, which the harness reads as a refusal.
    except Exception as exc:  # noqa: BLE001
        status = 2
        line = json.dumps({"error": f"{_class_name(exc)}: {exc}"})
    print(line)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
