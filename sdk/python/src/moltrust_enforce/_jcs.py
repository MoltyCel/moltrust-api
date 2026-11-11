"""RFC 8785 (JCS) canonicalization that refuses integers outside +-(2**53-1).

jcs 0.2.1 writes every number through a double. An integer that is not an
exact double comes back as a different value: 9007199254740993 as
9007199254740992, 10**21+1 as 1e+21. Two transactions that differ only in such
an integer then share one digest. Upstream keeps the behaviour
(titusz/jcs#1). The server's ``app.signature.canonicalize`` refuses these
integers since moltrust-api #788/#819; this is the same rule, so the vendored
core reaches the same verdict as the server on the same input.
"""
from __future__ import annotations

from typing import Any

import jcs

# RFC 7493 section 2.2 (I-JSON): the interoperable integer range.
MAX_SAFE_INTEGER = 2**53 - 1


def require_safe_integers(value: Any) -> None:
    """Raise ValueError for any integer outside +-(2**53-1), anywhere in ``value``."""
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            if abs(v) > MAX_SAFE_INTEGER:
                raise ValueError(
                    f"integer {v} lies outside the I-JSON number range "
                    f"(RFC 7493 section 2.2: |n| <= 2**53-1 = {MAX_SAFE_INTEGER}); "
                    "RFC 8785 would serialize it as a different double, so it "
                    "is refused instead of signed"
                )
        elif isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, (list, tuple)):
            stack.extend(v)


def canonicalize(obj: Any) -> bytes:
    """RFC 8785 bytes of ``obj``; integers outside +-(2**53-1) raise ValueError."""
    require_safe_integers(obj)
    return jcs.canonicalize(obj)
