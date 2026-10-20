"""Strict JSON intake for the request bodies of /vc/aae/evaluate and /vc/aae/submit (ops JI).

Starlette's request.json() is json.loads: a member name that occurs twice keeps
its last value without a word. On /vc/aae/evaluate that split one request into
two readings. The caller's RFC 9421 Content-Digest covers the raw bytes with both
values; the evaluator and the signed verdict record saw only the last one (live
20.10.2026: "domain" sent as evil.example.com and then as api.moltrust.ch was
evaluated as api.moltrust.ch and returned ALLOW). A third party holding the
request proof and the record reads two different requests.

This module refuses such a body before anything else looks at it, with its own
reason, and bounds the nesting depth, which json.loads only bounds by the
interpreter's recursion limit:

  duplicate-member   a member name occurs twice in one object, at any depth
  nesting-too-deep   more than MAX_INTAKE_DEPTH arrays/objects open at once
  not-json           not UTF-8, or not a JSON text
  not-an-object      the top level is not an object

The depth is counted on the raw bytes before parsing, so a deep document never
reaches the recursive parser.
"""
from __future__ import annotations

import json

from app.enforcement.envelope_store import MAX_JSON_DEPTH

# One limit for the AAE path: the envelope store already refuses mandate,
# constraints and validity nested deeper than this.
MAX_INTAKE_DEPTH = MAX_JSON_DEPTH


class JsonIntakeError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code


def nesting_depth(raw: bytes) -> int:
    """The largest number of arrays/objects open at once, brackets inside strings ignored.

    Stops counting as soon as MAX_INTAKE_DEPTH is exceeded: the answer is then
    MAX_INTAKE_DEPTH + 1, and the rest of a hostile document is not walked.
    """
    depth = deepest = 0
    in_string = escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):  # { [
            depth += 1
            if depth > deepest:
                deepest = depth
                if deepest > MAX_INTAKE_DEPTH:
                    return deepest
        elif byte in (0x7D, 0x5D):  # } ]
            depth -= 1
    return deepest


def _refuse_duplicates(pairs):
    seen = {}
    for name, value in pairs:
        if name in seen:
            raise JsonIntakeError("duplicate-member", f"member name {name!r} occurs twice")
        seen[name] = value
    return seen


def parse(raw: bytes) -> dict:
    """The body as a dict, or JsonIntakeError naming why not."""
    depth = nesting_depth(raw)
    if depth > MAX_INTAKE_DEPTH:
        raise JsonIntakeError(
            "nesting-too-deep",
            f"more than MAX_INTAKE_DEPTH={MAX_INTAKE_DEPTH} nested arrays/objects")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise JsonIntakeError("not-json", "body is not UTF-8") from None
    try:
        doc = json.loads(text, object_pairs_hook=_refuse_duplicates)
    except JsonIntakeError:
        raise
    except ValueError as e:
        raise JsonIntakeError("not-json", f"body is not a JSON text ({e.msg})") from None
    if not isinstance(doc, dict):
        raise JsonIntakeError("not-an-object", "the top level must be a JSON object")
    return doc
