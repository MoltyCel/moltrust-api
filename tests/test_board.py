"""Tests — das Brett, Regeln und Tageszeile (ops G5).

Every rule in `app/board.py` is tested twice: once with the input that has to
be refused, and once with the input that has to pass. The second half is the
one that catches a rule written too wide — a check that only ever sees the bad
case is green against a filter that refuses everything, and that filter is an
outage, not a defence. The threshold lesson of 09.10.2026, applied to a
positive list.

Nothing here touches the database. The storage and the endpoint are covered by
`tests/test_board_endpoint.py`, which needs one; these are the rules
themselves, so they run anywhere.
"""
import datetime
import sys
from pathlib import Path

import pytest

from app import board


def _entry(offers, posted_minutes_ago=0, did="did:moltrust:aaaaaaaaaaaaaaaa"):
    now = datetime.datetime(2026, 10, 17, 11, 0, tzinfo=datetime.timezone.utc)
    posted = now - datetime.timedelta(minutes=posted_minutes_ago)
    return {
        "did": did,
        "offers": list(offers),
        "seeks": [],
        "note": "",
        "posted_at": posted,
        "expires_at": board.expires_at(posted),
    }


# --- The vocabulary --------------------------------------------------------

def test_a_word_that_is_not_a_tag_is_refused():
    with pytest.raises(board.Rejected) as e:
        board.clean_tags(["endorsement", "llm-whispering"], "offers")
    # The message names the allowed set: an agent that guessed wrong cannot
    # fix the guess from "invalid input".
    assert "llm-whispering" in str(e.value)
    assert "endorsement" in str(e.value)


def test_every_declared_tag_is_accepted():
    # The silence probe for the rule above. A positive list that refuses one
    # of its own entries is the failure nobody notices, because the only test
    # is on the refusal.
    assert board.clean_tags(list(board.TAGS)[:board.MAX_TAGS], "offers") == \
        list(board.TAGS)[:board.MAX_TAGS]
    for tag in board.TAGS:
        assert board.clean_tags([tag], "seeks") == [tag]


def test_case_and_padding_do_not_decide_whether_a_tag_is_known():
    assert board.clean_tags(["  Endorsement "], "offers") == ["endorsement"]


def test_the_same_tag_twice_is_one_tag():
    assert board.clean_tags(["code", "code"], "offers") == ["code"]


def test_a_bare_string_is_not_a_list_of_tags():
    # "offers": "code" would otherwise iterate the characters and refuse with
    # a message about the letter c.
    with pytest.raises(board.Rejected):
        board.clean_tags("code", "offers")


def test_more_tags_than_allowed_are_refused_and_the_limit_itself_passes():
    allowed = list(board.TAGS)[:board.MAX_TAGS]
    assert len(board.clean_tags(allowed, "offers")) == board.MAX_TAGS
    with pytest.raises(board.Rejected):
        board.clean_tags(list(board.TAGS)[:board.MAX_TAGS + 1], "offers")


def test_offers_and_seeks_draw_from_one_list():
    # The property a match depends on. Two lists would let a seek name
    # something no offer can ever carry, and the board would look healthy.
    for tag in board.TAGS:
        assert board.clean_tags([tag], "offers") == board.clean_tags([tag], "seeks")


# --- The note --------------------------------------------------------------

def test_a_note_at_the_limit_passes_and_one_character_more_does_not():
    assert len(board.clean_note("x" * board.NOTE_MAX)) == board.NOTE_MAX
    with pytest.raises(board.Rejected):
        board.clean_note("x" * (board.NOTE_MAX + 1))


@pytest.mark.parametrize("ch", list("<>\"'"))
def test_the_characters_the_other_short_fields_refuse_are_refused_here(ch):
    with pytest.raises(board.Rejected):
        board.clean_note(f"ready for a group task {ch}")


def test_an_ordinary_note_passes_unchanged():
    note = "Runs an ERC-8004 lookup service; looking for someone to endorse it."
    assert board.clean_note(note) == note


def test_control_characters_are_stripped_by_the_existing_filter():
    from app.contact import strip_control_chars
    dirty = "endorse\x00ment ready\x07"
    assert board.clean_note(dirty) == strip_control_chars(dirty) == "endorsement ready"


def test_a_note_over_two_lines_is_refused():
    # A line break travels into a log line and into the digest post.
    with pytest.raises(board.Rejected):
        board.clean_note("offers data\nseeks compute")


def test_no_note_is_an_empty_note_and_not_an_error():
    assert board.clean_note(None) == ""
    assert board.clean_note("") == ""


# --- The expiry ------------------------------------------------------------

def test_an_entry_expires_after_the_declared_span():
    now = datetime.datetime(2026, 10, 17, 11, 0, tzinfo=datetime.timezone.utc)
    assert board.expires_at(now) - now == datetime.timedelta(hours=board.TTL_HOURS)
    assert board.TTL_HOURS == 72


# --- The daily line --------------------------------------------------------

def test_an_empty_board_says_nothing():
    # The silence rule, as a test rather than a hope: this is what keeps the
    # digest from posting a zero every day.
    with pytest.raises(board.Rejected) as e:
        board.digest_text([])
    assert "empty" in str(e.value)


def test_a_filled_board_says_how_many_and_what_is_newest():
    entries = [_entry(["endorsement"]), _entry(["data", "compute"], 30)]
    title, body = board.digest_text(entries)
    assert "2" in title
    assert "endorsement" in body and "data" in body


def test_the_line_names_at_most_three_offers_newest_first():
    entries = [
        _entry(["endorsement"], 0),
        _entry(["data"], 10),
        _entry(["compute"], 20),
        _entry(["monitoring"], 30),
    ]
    assert board.newest_offers(entries) == ["endorsement", "data", "compute"]


def test_one_offer_named_twice_fills_one_of_the_three_places():
    entries = [_entry(["code"], 0), _entry(["code"], 10), _entry(["text"], 20)]
    assert board.newest_offers(entries) == ["code", "text"]


def test_one_entry_reads_as_one_entry():
    title, body = board.digest_text([_entry(["code"])])
    assert "1 open entry" in title
    assert "One agent currently says what it offers" in body


def test_the_line_passes_the_content_rule_that_guards_every_moltbook_write():
    # agents/moltbook_poster.py reaches for its neighbour `activity` by bare
    # name, so the directory goes on the path rather than importing it as
    # agents.moltbook_poster.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents"))
    from moltbook_poster import content_violations

    title, body = board.digest_text([_entry(["endorsement", "group-task"])])
    assert content_violations(title, body) == []


def test_the_content_rule_is_live_and_would_refuse_a_link():
    # The trigger probe for the test above. Without it, a content rule that
    # had stopped checking anything would keep that test green.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents"))
    from moltbook_poster import content_violations

    assert content_violations("The board", "Look: https://moltrust.ch/board") != []
