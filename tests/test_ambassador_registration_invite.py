"""The one invitation a reply may carry: asked how to register (ops AMB-1).

Replies never invite — comment_gate c0, the content rule in moltbook_poster and
RULES.md "first contact = no CTA" all say so, and they stay as they are. The
exception is a comment that asks for the way in. Then, and only after the
drafted part has passed every gate, post_reply adds one fixed line that the
model never sees: REGISTRATION_LINE.

Two halves:
  - the detector, positives and negatives, negations included (CLAUDE.md
    "Eine Verneinung ist keine Bejahung");
  - the run: question -> line appended, ordinary comment -> no line, and a
    model draft carrying a URL is still withheld when the question is explicit.

The run half uses the harness from tests/test_ambassador_withheld_retry.py,
which stubs every outward call and fails a run that never reads a comment.
"""
import pytest

from agents.comment_gate import asks_how_to_register
from tests.test_ambassador_withheld_retry import (  # noqa: F401  (fixture)
    CID,
    POST_ID,
    ambassador,
    harness,
    seen,
)

LINE = ("Registration: https://api.moltrust.ch/join?ref=did:moltrust:214298170282752d"
        "&src=moltbook")


# ── the detector ──

@pytest.mark.parametrize("text", [
    "How do I register with MolTrust?",
    "Where can my agent get a DID from you?",
    "Can we sign up for your registry?",
    "Interesting thread. How would I onboard my agent with MolTrust?",
    "how to register an agent with moltrust",
    "Is there a way for me to register my agent with you?",
    "What are the steps to sign-up with MolTrust?",
    "Can I register at MolTrust without a wallet?",
])
def test_an_explicit_registration_question_is_detected(text):
    assert asks_how_to_register(text), text


@pytest.mark.parametrize("text", [
    # negations: the opposite sentence has to stay untouched
    "I don't want to register with MolTrust?",
    "No need to sign up with you, right?",
    "We never registered with MolTrust, so why would that matter?",
    "Can't I just skip registration with you?",
    # a question about the mechanism, not the way in
    "How does MolTrust registration resist Sybil attacks?",
    "Why would anyone register with MolTrust?",
    # not a question
    "We registered with MolTrust last week and it went fine.",
    "I registered with you yesterday.",
    # a question without the act, or without us
    "How do you score agents?",
    "How do I register a domain name?",
    "",
])
def test_everything_else_is_not(text):
    assert not asks_how_to_register(text), text


def test_the_line_is_the_constant_and_names_the_ambassador_and_the_channel():
    assert ambassador.REGISTRATION_LINE == LINE
    assert ambassador.AMBASSADOR_DID in ambassador.REGISTRATION_LINE


# ── the run ──

ASKED = ("How do I register my agent with MolTrust to get a DID and a "
         "verifiable credential?")
ORDINARY = "Does the anchor survive a key rotation when the DID document changes?"


@pytest.fixture
def bodies(harness, monkeypatch):  # noqa: F811
    sent = []

    def _post(client, path, body):
        harness.calls["posted"] += 1
        sent.append(body["content"])
        return harness.post_result
    monkeypatch.setattr(ambassador, "moltbook_post", _post)
    logged = []
    monkeypatch.setattr(ambassador, "write_log_entry", lambda t, m: logged.append(t))
    harness.logged = logged
    return sent


def comment(text):
    return [{"id": CID, "content": text, "author": {"name": "someone"}, "author_id": "a1"}]


def test_a_registration_question_gets_the_line_after_the_draft(harness, bodies):  # noqa: F811
    harness.comments = comment(ASKED)
    ambassador.cmd_run(harness.state)
    assert len(bodies) == 1
    assert bodies[0].endswith("\n\n" + LINE)
    assert bodies[0].startswith(harness.draft)
    assert "INVITE" in harness.logged
    assert CID in seen(harness.state)


def test_an_ordinary_comment_gets_no_line(harness, bodies):  # noqa: F811
    harness.comments = comment(ORDINARY)
    ambassador.cmd_run(harness.state)
    assert len(bodies) == 1
    assert "join?ref=" not in bodies[0] and "Registration:" not in bodies[0]
    assert "INVITE" not in harness.logged


@pytest.mark.parametrize("draft", [
    "Register at https://moltrust.ch/for-agents.html and you are in.",
    "Use the link: " + LINE,
])
def test_a_draft_with_a_url_is_withheld_even_when_registration_was_asked(harness, bodies, draft):  # noqa: F811
    harness.comments = comment(ASKED)
    harness.draft = draft
    ambassador.cmd_run(harness.state)
    assert bodies == [], "a model-written URL went out beside the exemption"
    assert CID not in seen(harness.state)


def test_post_reply_checks_the_draft_and_not_the_constant(monkeypatch):
    sent = []
    monkeypatch.setattr(ambassador, "moltbook_post",
                        lambda c, p, body: sent.append(body["content"]) or None)
    ambassador.post_reply(None, POST_ID, "A plain answer.", CID, invite=True)
    assert sent == ["A plain answer.\n\n" + LINE]
    with pytest.raises(ambassador.ReplyWithheld):
        ambassador.post_reply(None, POST_ID, "See www.example.com/x for more.", CID,
                              invite=True)
