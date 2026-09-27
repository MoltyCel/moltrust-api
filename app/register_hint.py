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

DOCS_URL = "https://api.moltrust.ch/docs"
PROBE_URL = "https://github.com/MoltyCel/moltrust-api/blob/main/scripts/gate_probe.py"

# Two lines. Any longer and it stops being read.
HINT_TEXT = (
    "A MolTrust identity takes two calls and no account: "
    "POST /identity/register-pop, then POST /identity/bind to prove a wallet. "
    f"Docs {DOCS_URL} · scripts/gate_probe.py --register does both."
)

# The same thing for a parser. `steps` is ordered; a client can walk it without
# reading the sentence above.
HINT_OBJECT = {
    "why": "An identified caller is priced separately on endpoints that offer it.",
    "cost": "free",
    "account_required": False,
    "steps": [
        {"method": "POST", "path": "/identity/register-pop",
         "note": "proof of work plus an Ed25519 key you generate and keep"},
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
    """
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    out.setdefault("register_hint", dict(HINT_OBJECT, **({"via": source} if source else {})))
    if "message" in out and isinstance(out["message"], str):
        out["message"] = f"{out['message']} {HINT_TEXT}"
    else:
        out.setdefault("how_to_register", HINT_TEXT)
    return out
