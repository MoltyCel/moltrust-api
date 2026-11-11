"""Session tokens reach the caller intact.

Every JSON response passes scrub_secrets() (app/main.py), which replaces what
looks like a leaked secret with "[REDACTED]". One of its patterns is a run of
exactly 40 base64 characters. A secrets.token_urlsafe(32) value contains such
a run about 1.8 % of the time, so roughly one admin login, reseller login or
reseller-admin elevation in fifty handed out a token with "[REDACTED]" in the
middle that matched no session. It showed up as an intermittent 401 in
tests/test_reseller_admin.py, and tests/test_reseller_portal.py had grown a
retry for "a corrupted response yields a short one".

The issuers now use token_hex(32): 256 bits as before, and no hex string can
match the 40-character pattern or the "sk-" one.
"""
import re
import secrets
from pathlib import Path

from app.main import scrub_secrets

ROOT = Path(__file__).resolve().parents[1]
ISSUERS = ("app/admin_auth.py", "app/reseller.py", "app/reseller_admin.py")


def test_a_hex_token_is_never_redacted():
    for _ in range(5000):
        tok = secrets.token_hex(32)
        assert scrub_secrets({"token": tok}) == {"token": tok}


def test_the_old_format_was_redacted_sometimes():
    """The negative case: without it the test above would pass against a
    scrubber that redacts nothing."""
    hits = sum(scrub_secrets({"token": t})["token"] != t
               for t in (secrets.token_urlsafe(32) for _ in range(5000)))
    assert hits > 0


def test_the_session_issuers_use_hex():
    for rel in ISSUERS:
        src = (ROOT / rel).read_text()
        assert "secrets.token_hex(32)" in src, rel
        assert not re.search(r"token\s*=\s*secrets\.token_urlsafe", src), rel
