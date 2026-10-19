"""ListWatch: every watched list has a roster, and a missing roster is loud.

Until 19.10.2026 the NEW SENDER trigger only knew the two W3C lists. On the
four IETF lists — agent2agent, agentproto, audit, dmsc — no roster existed, so
a first-time sender there was reported like any regular, and nothing said that
the mark was off (ops L8). The rosters now live in
config/listwatch_senders.json, built from six months of month indexes.

The trigger fails silent by construction: no roster means no mark. That is the
right direction for a decoration, and the wrong one for a decoration nobody
notices is gone — hence the two tests at the bottom, which keep a roster from
quietly disappearing.
"""
import importlib
import json
import os
import sys

import pytest

pytest.importorskip("requests")

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

SENDERS_JSON = os.path.join(_REPO, "config", "listwatch_senders.json")


@pytest.fixture
def lw(tmp_path, monkeypatch):
    # The module parses argv and opens its log under ~/moltstack at import.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["w3c_listwatch", "--dry-run"])
    sys.modules.pop("w3c_listwatch", None)
    mod = importlib.import_module("w3c_listwatch")
    monkeypatch.setattr(mod, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(mod, "load_secrets_into_env", lambda: None)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    yield mod
    sys.modules.pop("w3c_listwatch", None)


def test_every_watched_list_either_has_a_roster_or_says_why_not(lw):
    # A list without a roster is a legitimate state — it is how the trigger
    # avoids calling every sender new. What is not legitimate is a list that
    # has no roster and no reason: nobody would see the mark is off. So the
    # file names its own gaps under "ohne_roster".
    assert lw.load_known_senders() is None
    excused = json.load(open(SENDERS_JSON, encoding="utf-8")).get("ohne_roster", {})
    unexplained = [lst for lst, _ in lw.LISTS
                   if not lw.KNOWN_SENDERS.get(lst) and lst not in excused]
    assert not unexplained, (
        "no roster and no reason for " + ", ".join(unexplained) + ". Either add "
        "the roster to config/listwatch_senders.json, read off the month "
        "indexes, or record under ohne_roster why the list goes unmarked."
    )
    assert all(excused[lst].strip() for lst in excused), excused


def test_roster_names_are_stored_the_way_they_are_compared(lw):
    # triggers_for compares author.strip().lower(). A name stored with a
    # capital would match nobody, and the file is where that is visible.
    raw = json.load(open(SENDERS_JSON, encoding="utf-8"))["lists"]
    off = [(lst, n) for lst, names in raw.items() for n in names
           if n != n.strip().lower()]
    assert not off, off


@pytest.mark.parametrize("lst", ["audit", "agent2agent", "agentproto", "dmsc"])
def test_new_sender_fires_on_the_ietf_lists(lw, lst):
    lw.load_known_senders()
    assert "NEW SENDER" in lw.triggers_for(lst, "Someone Not In The Archive",
                                           "subject", "body")


@pytest.mark.parametrize("lst,known", [
    ("audit", "Mikhail Sergeev"),
    ("agent2agent", "Mikhail Sergeev"),
    ("dmsc", None),
])
def test_a_known_sender_stays_unmarked(lw, lst, known):
    # Schweigeprobe. For dmsc any roster name does, taken from the file itself
    # rather than written out here, so the test keeps working when it grows.
    lw.load_known_senders()
    name = known or sorted(lw.KNOWN_SENDERS[lst])[0]
    assert "NEW SENDER" not in lw.triggers_for(lst, name, "subject", "body")


def test_a_roster_file_that_cannot_be_read_is_named_in_the_report(
        lw, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lw, "SENDERS_FILE", tmp_path / "gone.json")
    monkeypatch.setattr(lw, "fetch", lambda url: (200, ""))
    assert lw.main() == 0
    out = capsys.readouterr().out
    assert "Known senders not loaded" in out
    assert "FileNotFoundError" in out
    assert lw.KNOWN_SENDERS == {}


def test_an_empty_list_block_counts_as_unreadable(lw, tmp_path, monkeypatch):
    bad = tmp_path / "empty.json"
    bad.write_text(json.dumps({"lists": {}}), encoding="utf-8")
    assert lw.load_known_senders(bad) is not None
    assert lw.KNOWN_SENDERS == {}
