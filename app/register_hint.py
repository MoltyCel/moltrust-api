"""One sentence about registering, in every place we answer without one.

Most callers reach us unauthenticated and leave that way. They get a 402, a
denial reason or an agent card, and none of those has ever said what to do about
it. The hint below is that missing line, written once so the wording cannot
drift across the six or seven places it appears.

Two rules it follows. It is two lines, because a paragraph in an error body is
marketing and gets skipped. And it ships a machine-readable twin under
`register_hint`, because the reader is usually a program: a human-readable
sentence a parser has to regex is a sentence written for the wrong audience.

Nothing here promises anything or asks for anything. It states the two calls and
names the script that does them.
"""
from __future__ import annotations

import copy

DOCS_URL ="https://api.moltrust.ch/docs"
PROBE_URL = "https://github.com/MoltyCel/moltrust-api/blob/main/scripts/gate_probe.py"

# Two lines. Any longer and it stops being read.
HINT_TEXT = (
    "A MolTrust identity takes two calls and no account: "
    "POST /identity/register-pop, then POST /identity/bind to prove a wallet. "
    f"Docs {DOCS_URL} · scripts/gate_probe.py --register does both."
)

# The example body, and why it is here (ops REG-POP-422). Over the two weeks to
# 30.10.2026 exactly two reasons made a real agent fail on the invited way, both
# read out of the journal: a call with no body at all (29 times, HTTP 422
# `body:missing`), and a `public_key` sent as base64 instead of hex (2 times,
# 422 `value_error` on `body.public_key` — 44 characters, the length of a 32-byte
# key in padded base64). Both are shape errors, so a body to copy answers both;
# the 490 crawler calls with an empty body are not addressed by anything here
# and are not meant to be. The placeholders are in angle brackets so a body sent
# verbatim is refused for the right reason instead of looking valid.
EXAMPLE_BODY = {
    "public_key": "469c5b9f3a3cf1295cd33bddbcb40fc03ad8e163f443218fac4fadf0951ee65f",
    "challenge": "<challenge from GET /identity/register-challenge>",
    "signature": "<base64url Ed25519 signature over that exact challenge string>",
    "pow_nonce": "<nonce solving pow.rule from the same response>",
    "display_name": "my-agent",
}

# The same thing for a parser. `steps` is ordered; a client can walk it without
# reading the sentence above.
HINT_OBJECT = {
    "why": "An identified caller is priced separately on endpoints that offer it.",
    "cost": "free",
    "account_required": False,
    "steps": [
        {"method": "POST", "path": "/identity/register-pop",
         "note": "proof of work plus an Ed25519 key you generate and keep",
         "challenge_from": "GET /identity/register-challenge",
         "example_body": EXAMPLE_BODY,
         "refused_when": [
             "no body is sent at all: all four fields are required, see example_body",
             "public_key is base64, base58 or multibase: it is 64 hex characters "
             "(32 bytes), and 44 characters is base64 of the same key",
         ]},
        {"method": "POST", "path": "/identity/bind",
         "note": "sign a nonce with the wallet to bind it to the DID"},
    ],
    "docs": DOCS_URL,
    "script": PROBE_URL,
    # Whoever arrives through a hint should be countable as having arrived
    # through it, or the channel cannot be told from the background.
    "source_hint": "pass source=<where you found this> to /identity/register-pop",
}


def with_hint(payload: dict, *, source: str | None = None) -> dict:
    """Add the hint to a response body, without overwriting what is there.

    `source` marks which surface the hint went out on, so the registration it
    produces can be attributed to it later.

    The copy is deep: since ops REG-POP-422 the hint carries a nested example
    body, and a shallow copy would hand every caller the same dict to edit.
    """
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    hint = copy.deepcopy(HINT_OBJECT)
    if source:
        hint["via"] = source
    out.setdefault("register_hint", hint)
    if "message" in out and isinstance(out["message"], str):
        out["message"] = f"{out['message']} {HINT_TEXT}"
    else:
        out.setdefault("how_to_register", HINT_TEXT)
    return out
