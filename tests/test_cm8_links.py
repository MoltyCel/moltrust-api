"""No reply leaves with a link, and the ledger prices the same cases (CM8).

A reply with a link costs $0.200 at X against $0.015 without — thirteen
times — so two replies a day stay under the weekly $0.25 only as long as none
of them carries one. Three places have to agree about what a link is: the
drafter must not write one, the button must refuse one, and `x_meter` must
book one that got out anyway at the higher price. They agree by sharing
`x_meter.URL_RE`; these tests hold the pattern and both refusals against the
four forms X linkifies.
"""
import json

import pytest

from agents import reply_radar as rr
from agents import x_meter


# The four forms, one per shape X turns into a link.
LINKS = ["https://moltrust.ch/kya", "http://example.test/a", "t.co/abc123",
         "moltrust.ch", "github.com/x"]

# The silence probe. Every one of these has looked like a link to a pattern at
# some point: the price itself, a section number, a decimal, an abbreviation.
PLAIN = ["EU AI Act Article 50 applies from 2 Aug 2026.",
         "Eine Antwort ohne Link kostet 0,015 $, mit Link 0,200 $.",
         "ERC-8004 has 3 registries. Art. 50 is the one that bites.",
         "Score 50 earns the 20 % discount, withheld pays full price.",
         "No. 5 of the series, i.e. the last one."]


@pytest.mark.parametrize("text", LINKS)
def test_the_pattern_finds_every_form_x_linkifies(text):
    assert x_meter.link_in(f"delegation is a record question {text} really")


@pytest.mark.parametrize("text", PLAIN)
def test_the_pattern_stays_silent_on_plain_text(text):
    assert x_meter.link_in(text) == ""


def test_the_refusal_names_what_it_found():
    assert x_meter.link_in("read it at moltrust.ch today") == "moltrust.ch"


# ── the ledger books what the guard refuses (criterion 4) ──

@pytest.mark.parametrize("text", LINKS)
def test_the_ledger_books_the_link_price_for_every_form(tmp_path, monkeypatch, text):
    monkeypatch.setattr(x_meter, "LEDGER", str(tmp_path / "x_meter.jsonl"))
    x_meter.record_write("1", f"a reply that slipped out: {text}", source="reply")
    row = json.loads((tmp_path / "x_meter.jsonl").read_text().splitlines()[0])
    assert row["with_url"] is True, "a sent reply with a link would be priced at $0.015"


def test_a_reply_without_a_link_is_booked_at_the_plain_price(tmp_path, monkeypatch):
    monkeypatch.setattr(x_meter, "LEDGER", str(tmp_path / "x_meter.jsonl"))
    x_meter.record_write("1", PLAIN[0], source="reply")
    row = json.loads((tmp_path / "x_meter.jsonl").read_text().splitlines()[0])
    assert row["with_url"] is False


# ── the drafter writes no link (criterion 3) ──

class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def drafter(monkeypatch, tmp_path):
    """The drafter as it ran until 17.10.2026, network replaced."""
    def install(raw):
        body = {"content": [{"type": "text", "text": raw}],
                "stop_reason": "end_turn", "usage": {"output_tokens": 40}}
        monkeypatch.setattr(rr.httpx, "post",
                            lambda *a, **k: _Resp(body))
    monkeypatch.setattr(rr, "reply_branch_retired", lambda now=None: False)
    monkeypatch.setattr(rr, "load_anthropic_key", lambda: "k")
    monkeypatch.setattr(rr.voice_gate, "load_voice_docs",
                        lambda: {"anti_ki_sprech": "", "my_voice_en": ""})
    monkeypatch.setattr(rr, "citation_block", lambda: "")
    monkeypatch.setattr(rr, "kb_digest", lambda kb: "")
    monkeypatch.setattr(rr, "REFUSALS_FILE", str(tmp_path / "refusals.jsonl"))
    return install


TWEET = {"id": "1", "_author": "a", "_source": "mention", "text": "x"}


def test_a_draft_that_names_its_source_in_the_text_is_refused(drafter):
    drafter(json.dumps({"reply": "Article 50 applies from 2 Aug 2026, see moltrust.ch/kya",
                        "sources": ["https://example.test/a"]}))
    assert rr.draft_reply(TWEET, {}) is None
    assert rr._LAST_REFUSAL["kind"] == "link"
    assert "moltrust.ch" in rr._LAST_REFUSAL["detail"]


def test_the_sources_stay_with_the_draft_and_out_of_the_text(drafter):
    """The pages the drafter used come back beside the text, not inside it."""
    drafter(json.dumps({"reply": "Article 50 applies from 2 Aug 2026.",
                        "sources": ["https://example.test/a", "https://example.test/b"]}))
    text, sources = rr.draft_reply(TWEET, {})
    assert x_meter.link_in(text) == ""
    assert sources == ["https://example.test/a", "https://example.test/b"]


def test_the_prompt_tells_the_drafter_where_the_pages_go():
    assert "No link and no domain in the reply itself" in rr.SYSTEM_PROMPT


# ── the button refuses (criterion 2) ──

@pytest.fixture
def button(monkeypatch):
    """One button press, with X and Telegram replaced and nothing else."""
    calls = {"posted": [], "edits": []}
    monkeypatch.setattr(rr, "DAILY_MAX", 8)
    monkeypatch.setattr(rr, "reply_branch_retired", lambda now=None: False)
    monkeypatch.setattr(rr, "edit_message",
                        lambda c, m, t: calls["edits"].append(t))
    monkeypatch.setattr(rr, "target_ok",
                        lambda a, t: (True, "", {"id": t, "_author": "owasp",
                                                 "text": "the post said 42"}))
    monkeypatch.setattr(rr, "fetch_sources", lambda urls: {})
    monkeypatch.setattr(rr, "check", lambda t, s: (True, [], {}))
    monkeypatch.setattr(rr.x_meter, "sends_paused", lambda: "")
    monkeypatch.setattr(rr, "followers_now", lambda a: 27)
    monkeypatch.setenv(rr.ARM_FLAG, "1")
    monkeypatch.setattr(rr.x_post, "post",
                        lambda *a, **k: calls["posted"].append(a) or "999")
    return calls


def press(text: str) -> dict:
    return {"id": "cb1", "data": "rr|post|111",
            "from": {"username": "LarsOnMoltrust"},
            "message": {"message_id": 42, "chat": {"id": 7}, "text": f"""📝 Reply-Entwurf 1
Quelle (mention): https://x.com/owasp/status/111
9 Impressionen · 0 Likes

Entwurf ({len(text)}/280):
{text}

Freigabe postet die Reply innerhalb von fünf Minuten."""}}


@pytest.mark.parametrize("link", LINKS)
def test_the_button_refuses_every_form_and_says_why(button, link):
    decisions = {}
    rr.handle_decision({}, decisions, "auth", press(f"Article 50 applies. {link}"),
                       "post", "111", "2026-10-23", False)
    assert button["posted"] == [], "a reply with a link reached X"
    assert decisions["111"]["result"] == "link_in_draft"
    assert any("Link" in e for e in button["edits"]), "the button said nothing"
    assert any(link.split("/")[0] in e for e in button["edits"]), \
        "the refusal did not name what it found"


def test_the_refusal_hands_the_text_back_for_the_manual_route(button):
    rr.handle_decision({}, {}, "auth", press("Article 50 applies. moltrust.ch"),
                       "post", "111", "2026-10-23", False)
    assert any("Article 50 applies." in e for e in button["edits"])


def test_a_reply_without_a_link_still_goes_out(button):
    decisions = {}
    rr.handle_decision({}, decisions, "auth", press(PLAIN[0]),
                       "post", "111", "2026-10-23", False)
    assert len(button["posted"]) == 1, "the guard swallowed a clean reply"
    assert decisions["111"]["result"] != "link_in_draft"
