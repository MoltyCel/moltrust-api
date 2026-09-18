"""Contact blocklist: silent, domain-aware, and incapable of breaking the form."""
import importlib
import os

import pytest


def _reload(value):
    os.environ["CONTACT_BLOCKLIST"] = value
    import app.main as m
    importlib.reload(m)
    return m


class TestMatching:
    def test_empty_blocklist_blocks_nobody(self):
        m = _reload("")
        assert m.is_blocked_sender("anyone@example.com") is False

    def test_full_address_matches(self):
        m = _reload("sales@spam.example")
        assert m.is_blocked_sender("sales@spam.example") is True
        assert m.is_blocked_sender("other@spam.example") is False

    def test_bare_domain_matches_every_local_part(self):
        """One entry retires a sender that rotates its local part weekly."""
        m = _reload("spam.example")
        for local in ("sales", "hello", "no-reply", "a.b+c"):
            assert m.is_blocked_sender(f"{local}@spam.example") is True

    def test_bare_domain_covers_subdomains(self):
        m = _reload("spam.example")
        assert m.is_blocked_sender("x@mail.spam.example") is True
        assert m.is_blocked_sender("x@a.b.spam.example") is True

    def test_does_not_match_a_lookalike_suffix(self):
        """notspam.example must not be caught by an entry for spam.example."""
        m = _reload("spam.example")
        assert m.is_blocked_sender("x@notspam.example") is False
        assert m.is_blocked_sender("x@spam.example.org") is False

    def test_case_and_whitespace_insensitive(self):
        m = _reload("  SPAM.Example , other.test ")
        assert m.is_blocked_sender("X@Spam.Example") is True
        assert m.is_blocked_sender("y@OTHER.TEST") is True

    def test_leading_at_is_tolerated_in_config(self):
        m = _reload("@spam.example")
        assert m.is_blocked_sender("x@spam.example") is True


class TestCannotBreakTheForm:
    @pytest.mark.parametrize("value", [None, "", "not-an-address", "@", "a@", "@b"])
    def test_never_raises(self, value):
        m = _reload("spam.example")
        assert m.is_blocked_sender(value) in (True, False)

    def test_unset_env_is_not_an_error(self):
        os.environ.pop("CONTACT_BLOCKLIST", None)
        import app.main as m
        importlib.reload(m)
        assert m.is_blocked_sender("x@y.z") is False


class TestSilence:
    def test_blocked_answer_is_the_accepted_answer(self):
        """Byte-identical to a genuine submission. A sender who learns they are
        blocked changes domain."""
        src = open(_reload("spam.example").__file__).read()
        blk = src[src.index("if is_blocked_sender(body.email):"):][:400]
        assert "CONTACT_ACCEPTED_RESPONSE" in blk

    def test_nothing_is_stored_for_a_blocked_sender(self):
        src = open(_reload("spam.example").__file__).read()
        i = src.index("if is_blocked_sender(body.email):")
        blk = src[i:src.index("if not db_pool:", i)]
        assert "INSERT" not in blk.upper()

    def test_checked_before_the_database(self):
        """Scoped to the contact handler: `if not db_pool:` appears dozens of
        times in the file, and the first one is nowhere near here."""
        src = open(_reload("spam.example").__file__).read()
        handler = src[src.index("async def contact_submit"):]
        assert handler.index("is_blocked_sender(body.email)") < handler.index("if not db_pool:")
