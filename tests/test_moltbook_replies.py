"""The reply templates, held to the rule and to the voice gate.

The advert pools were emptied because every template in them sold something,
and four of them passed the content rule anyway — the rule is a floor under
what may be sent, not a standard for what is worth sending. These tests put
both checks on the build: the shared rule, and the pre-send scan that reads
its patterns from moltrust-web.
"""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("heartbeat", ROOT / "moltbook" / "heartbeat.py")
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)

ALL_REPLIES = [t for group in hb.REPLY_FACTS.values() for t in group]


def test_there_are_replies_to_test():
    assert ALL_REPLIES, "REPLY_FACTS is empty"
    assert len(hb.REPLY_FACTS) >= 5


@pytest.mark.parametrize("text", ALL_REPLIES + [hb.POINTER])
def test_passes_the_shared_content_rule(text):
    assert hb.content_violations("", text) == []


@pytest.mark.parametrize("text", ALL_REPLIES)
def test_carries_no_url_and_no_install_line(text):
    low = text.lower()
    for token in ("http://", "https://", "moltrust.ch", "pip install", "npm install",
                  "free", "credits", "sign up", "register at"):
        assert token not in low, f"{token!r} in a reply template"


@pytest.mark.parametrize("text", ALL_REPLIES)
def test_does_not_open_with_the_product(text):
    """The opening clause belongs to the subject, not to us."""
    opening = text.split(".")[0].lower()
    for token in ("moltrust", "moltguard", "we ", "our ", "i "):
        assert not opening.startswith(token), f"reply opens with {token!r}"


def test_pointer_is_the_only_place_a_reader_is_sent_anywhere():
    for text in ALL_REPLIES:
        assert "repository" not in text.lower()


# --- behaviour ------------------------------------------------------------

def test_a_statement_gets_no_reply():
    assert hb.classify_question("Trust matters a lot in multi-agent systems.") is None


def test_a_question_without_a_topic_gets_no_reply():
    assert hb.classify_question("Anyone around here from Berlin?") is None


@pytest.mark.parametrize("text,group", [
    ("How do you know the caller is who the identifier says?", "identity"),
    ("What happens when a credential is revoked?", "revocation"),
    ("Why anchor anything on-chain at all?", "anchor"),
    ("Isn't a reputation score just self-reported?", "reputation"),
    ("How is the signature verified?", "verification"),
])
def test_questions_reach_the_right_group(text, group):
    assert hb.classify_question(text) == group


def test_offer_only_on_request():
    answer, _ = hb.build_reply("How do you know the caller is who they say?", None, 0)
    assert hb.POINTER not in answer

    asked, _ = hb.build_reply("How do you know the caller is who they say? Where can I read more?", None, 0)
    assert hb.POINTER in asked


def test_attestation_is_attached_only_where_identity_is_the_subject():
    token = "aaa.bbb.ccc"
    ident, _ = hb.build_reply("How do you bind an identifier to a key?", token, 0)
    assert token in ident

    other, _ = hb.build_reply("Why anchor anything on-chain at all?", token, 0)
    assert token not in other


def test_a_missing_attestation_does_not_stop_the_reply():
    answer, _ = hb.build_reply("How do you bind an identifier to a key?", None, 0)
    assert answer and "None" not in answer


def test_reply_mode_is_off_unless_switched_on():
    """Merging this must not change what the running service does."""
    assert hb.REPLY_ONLY is False or __import__("os").getenv("MOLTBOOK_REPLY_ONLY") == "true"
    assert hb.MAX_POSTS_PER_DAY == 1


# --- the switch itself -----------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("nein", False),
])
def test_flag_accepts_the_spellings_people_use(monkeypatch, value, expected):
    """MOLTBOOK_REPLY_ONLY=1 has to mean on.

    The first version compared against the literal "true", so setting it to 1
    switched nothing on and logged that the mode was off.
    """
    from agents.moltbook_poster import flag
    monkeypatch.setenv("MOLTBOOK_TEST_FLAG", value)
    assert flag("MOLTBOOK_TEST_FLAG") is expected


def test_flag_falls_back_to_the_secrets_file(monkeypatch, tmp_path):
    """The systemd unit carries no EnvironmentFile, and adding one needs root."""
    import agents.moltbook_poster as poster
    secrets = tmp_path / "secrets"
    secrets.write_text('OTHER=x\nMOLTBOOK_TEST_FLAG="yes"\n')
    monkeypatch.delenv("MOLTBOOK_TEST_FLAG", raising=False)
    monkeypatch.setattr(poster, "SECRETS_FILE", str(secrets))
    assert poster.flag("MOLTBOOK_TEST_FLAG") is True


def test_flag_is_off_when_nothing_sets_it(monkeypatch, tmp_path):
    import agents.moltbook_poster as poster
    monkeypatch.delenv("MOLTBOOK_TEST_FLAG", raising=False)
    monkeypatch.setattr(poster, "SECRETS_FILE", str(tmp_path / "absent"))
    assert poster.flag("MOLTBOOK_TEST_FLAG") is False
