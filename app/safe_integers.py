"""The integer bound every JCS signer in this repository holds before signing.

jcs 0.2.1 writes every number through a double. Above 2**53-1 distinct integer
literals share one double, so the library returns bytes for a value the caller
never gave: 9007199254740993 comes out as 9007199254740992 and 10**21 as
1e+21. A signature over those bytes also covers the neighbour value, so two
different payloads verify under one signature. Upstream knows it and keeps the
behaviour (titusz/jcs#1, open since 2022-02-15).

``lib/agent_card_verify.py`` refuses the same input (RFC 7493 section 2.2).
Every path that hands a payload to ``jcs.canonicalize`` calls
``require_safe_integers`` first: ``app/signature.py``,
``app/crypto/hybrid.py`` and ``app/provenance/ipr.py``.
"""

# RFC 7493 section 2.2 (I-JSON), the same bound lib/agent_card_verify.py holds.
MAX_SAFE_INTEGER = 2**53 - 1


def require_safe_integers(value) -> None:
    """Refuse any integer outside +-(2**53-1), anywhere in ``value``.

    Plain ValueError on purpose: the a2a-tck runner classifies refusals by
    exact type (tests/conformance/score_a2a_jcs.py), and the HTTP handlers map
    ValueError to 400. Floats pass: a Python float already is a double, so
    jcs writes the value it was given.
    """
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
