"""The two did:moltrust syntax patterns: what we issue, and what we resolve.

Section 2.2 of the method specification defines the method-specific identifier
as sixteen lowercase hex characters. That is the issuance pattern: an
identifier this registry hands out must match it.

The resolution pattern is wider by one form, `ext_` followed by sixteen hex.
One registered agent carries it, `did:moltrust:ext_516a656bafa39e5c`, bridged
from agentnexus on 2026-04-22, and it stays resolvable. Section 6 describes the
bridge without an `ext_` form, so the form is accepted where an existing
identifier is read and refused where a new one is issued.

One module, so main.py and the enforcement modules cannot drift apart; until
2026-10-08 the resolution pattern was copied into three files.
"""

import re

DID_ISSUE_PATTERN = re.compile(r"^did:moltrust:[a-f0-9]{16}$")
DID_RESOLVE_PATTERN = re.compile(r"^did:moltrust:(?:ext_)?[a-f0-9]{16}$")
