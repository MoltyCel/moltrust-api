"""ThreadWatch 🎯 action-implied flag + pinned-comment snippet.

Covers detect_action / first_sentences and the analyze_pinned + fmt_report render.
The module imports yaml/requests at top and parses argv at import — so we
importorskip those deps (keeps CI's collect-only green when they're absent) and
neutralise sys.argv before importing.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

pytest.importorskip("yaml")
pytest.importorskip("requests")

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "scripts"))
_saved_argv = sys.argv
sys.argv = ["threadwatch"]  # module runs argparse.parse_args() at import
try:
    import threadwatch as TW
finally:
    sys.argv = _saved_argv


CONFIG = {
    "moltrust_identities": ["MoltyCel", "VCOne-AI", "vcone-ai", "moltrust-agent", "moltguard_v1"],
    "mention_keywords": ["moltrust", "did:moltrust", "moltybridge", "moltrust.ch", "@moltycel", "@vcone-ai"],
    "action_keywords": ["lead", "take you up on", "please", "can you", "could you", "would you",
                        "assign", "assigned", "you're on", "own the", "drive", "point person",
                        "nominate", "rfc owner"],
    "thresholds": {"max_per_category": 10},
}
IDS = CONFIG["moltrust_identities"]
MKW = CONFIG["mention_keywords"]
AKW = CONFIG["action_keywords"]

# The real bigblackcoder comment on w3c-cg/atp#1, 2026-07-06 (verbatim). Note it
# never says "@MoltyCel" — the assignment is directed via "your background at
# MolTrust … lead" and buried in the 4th paragraph.
BBC_COMMENT = (
    "Lars, you’re absolutely right, and I appreciate you sharing this insight. The core "
    "finding remains valid: counting successes and endorsements without considering time or "
    "detecting collusion results in a score that measures volume rather than trustworthiness. "
    "This is a significant gap, not a minor oversight.\n\n"
    "Here’s the revised plan:\n\n"
    "1. **Activity Decay:** Old activity will be given less weight over time and will become an "
    "integral part of the specification.\n"
    "2. **Score Confidence:** Scores will be accompanied by confidence labels to distinguish "
    "between low and high interaction counts.\n"
    "3. **Score Protection:** The “score never goes down” rule will be refined to protect "
    "only genuine, filtered activity, not raw volume.\n\n"
    "You offered to assist in building adversarial test cases, and I’d like to take you up on "
    "that directly. Given your background at MolTrust, you’re well-suited to lead this specific "
    "aspect: developing the anti-gaming test vectors that form the basis of our conformance suite. "
    "Please note that this is a contributor role, not an editor-of-the-whole-spec role, to clarify "
    "the scope. However, it’s a crucial role, and I’d be delighted if you could lead it."
)


# ── Test 1: assignment in the second sentence → flagged + snippet visible ──────
def test_assignment_in_second_sentence_flagged():
    body = ("Thanks for the notes on MolTrust. @MoltyCel could you lead the anti-gaming vector "
            "workstream for the conformance suite?")
    implied, snippet = TW.detect_action(body, IDS, MKW, AKW)
    assert implied is True
    assert "lead" in snippet.lower() and "anti-gaming" in snippet.lower()
    assert snippet.startswith("@MoltyCel could you lead")   # the 2nd sentence, not the 1st
    assert "anti-gaming" in TW.first_sentences(body).lower()  # also visible in the context snippet


# ── Test 2: plain waiting, no action verb → not flagged ───────────────────────
def test_plain_waiting_not_flagged():
    body = ("Thanks, this is helpful context on MolTrust. I will update the spec draft and post "
            "the diff sometime next week for everyone to look over.")
    implied, snippet = TW.detect_action(body, IDS, MKW, AKW)
    assert implied is False
    assert snippet == ""


def test_verb_without_directed_mention_not_flagged():
    # action verb present, but nothing points it at us
    body = "Could you lead the triage rotation next sprint? Thanks all."
    implied, _ = TW.detect_action(body, IDS, MKW, AKW)
    assert implied is False


# ── Test 3: regression — the real atp#1 comment must fire 🎯 and show the ball ──
def test_regression_bigblackcoder_atp1():
    implied, snippet = TW.detect_action(BBC_COMMENT, IDS, MKW, AKW)
    assert implied is True, "the real atp#1 assignment must be flagged"
    assert "lead" in snippet.lower()
    assert "anti-gaming test vectors" in snippet.lower(), "snippet must show the actual assignment"
    # The buried ball is NOT in the first 2-3 sentences — proves why we surface the
    # action sentence, not just the opening context.
    assert "anti-gaming" not in TW.first_sentences(BBC_COMMENT).lower()

    # analyze_pinned end-to-end (this is atp#1's real code path).
    item = {
        "number": 1, "title": "Anti-gaming vectors for the ATP conformance suite",
        "html_url": "https://github.com/w3c-cg/atp/issues/1", "state": "open",
        "user": {"login": "bigblackcoder"},
        "created_at": "2026-07-01T00:00:00Z", "updated_at": "2026-07-06T05:42:46Z",
        "body": "Original issue text about scoring.",
    }
    comments = [{"user": {"login": "bigblackcoder"},
                 "created_at": "2026-07-06T05:42:46Z", "body": BBC_COMMENT}]
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    entry = TW.analyze_pinned("w3c-cg/atp", 1, item, comments, CONFIG, now, note="ATP lead ask")
    assert entry["action_implied"] is True
    assert "anti-gaming test vectors" in entry["action_snippet"].lower()
    assert entry["waiting"] is True  # they spoke last — still a waiting thread

    # Rendered roster line shows the 🎯 assignment text.
    rendered = "\n".join(TW.fmt_roster_line(entry))
    assert "\U0001F3AF" in rendered  # 🎯
    assert "anti-gaming test vectors" in rendered.lower()

    # Full digest: 🎯 section appears ABOVE the pinned roster.
    report = TW.fmt_report({"urgent": [], "active": [], "stale": []}, [], now, CONFIG, roster=[entry])
    assert "ACTION IMPLIED" in report
    assert "(heuristic)" in report
    assert report.index("ACTION IMPLIED") < report.index("PINNED ROSTER")
    assert "anti-gaming test vectors" in report.lower()


# ── Pinned entry with no action still shows a 2-3 sentence context snippet ─────
def test_pinned_snippet_without_action():
    item = {
        "number": 9, "title": "Some tracked thread",
        "html_url": "https://github.com/x/y/issues/9", "state": "open",
        "user": {"login": "someone"},
        "created_at": "2026-07-01T00:00:00Z", "updated_at": "2026-07-05T00:00:00Z",
        "body": "Original.",
    }
    body = ("We looked at the moltrust integration notes. The mapping is clear now. We will circle "
            "back after the next release with a summary of the remaining questions.")
    comments = [{"user": {"login": "someone"}, "created_at": "2026-07-05T00:00:00Z", "body": body}]
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    entry = TW.analyze_pinned("x/y", 9, item, comments, CONFIG, now)
    assert entry["action_implied"] is False
    assert entry["newest_comment_snippet"]  # non-empty context snippet
    rendered = "\n".join(TW.fmt_roster_line(entry))
    assert "\U0001F3AF" not in rendered  # no 🎯 when not flagged
    assert "circle back" in rendered.lower()


# ── /unpin on a config pin names the file the entry sits in ──────────────────
# Regression: aae-conformance-vectors#2 stayed in the roster after /unpin
# because it was a tracked_threads entry, and the handler answered
# "No dynamic pin" — which reads as "already gone".

TRACKED_CONFIG = {
    "tracked_threads": [
        {"repo": "MoltyCel/aae-conformance-vectors", "number": 2, "note": "vector-schema field"},
        {"repo": "a2aproject/A2A", "number": 1628},
    ]
}


def test_config_pin_keys_collects_repo_and_number():
    assert TW.config_pin_keys(TRACKED_CONFIG) == {
        "MoltyCel/aae-conformance-vectors#2",
        "a2aproject/A2A#1628",
    }


def test_config_pin_keys_tolerates_empty_config():
    assert TW.config_pin_keys(None) == set()
    assert TW.config_pin_keys({}) == set()
    assert TW.config_pin_keys({"tracked_threads": None}) == set()
    assert TW.config_pin_keys({"tracked_threads": [{"repo": "x/y"}]}) == set()


def _run_unpin(monkeypatch, key, state, config):
    """Drive process_ack_commands with one /unpin message; return what was sent."""
    sent = []
    monkeypatch.setattr(TW, "telegram_get_updates", lambda secrets, offset: [
        {"update_id": 1, "message": {"chat": {"id": 42}, "text": f"/unpin {key}"}}
    ])
    monkeypatch.setattr(TW, "telegram_send",
                        lambda secrets, text, dry=False: sent.append(text))
    TW.process_ack_commands({"TELEGRAM_CHAT_ID": "42"}, state, config)
    return "\n".join(sent)


def test_unpin_config_pin_names_the_yaml(monkeypatch):
    out = _run_unpin(monkeypatch, "MoltyCel/aae-conformance-vectors#2",
                     {"pinned": {}}, TRACKED_CONFIG)
    assert "Config-Pin" in out
    assert "threadwatch_config.yaml" in out
    assert "tracked_threads" in out
    assert "No dynamic pin" not in out


def test_unpin_unknown_key_keeps_the_old_message(monkeypatch):
    out = _run_unpin(monkeypatch, "some/repo#9", {"pinned": {}}, TRACKED_CONFIG)
    assert "No dynamic pin" in out
    assert "Config-Pin" not in out


def test_unpin_dynamic_pin_still_removes_it(monkeypatch):
    """The pinning behaviour itself is unchanged — only the miss-message differs."""
    state = {"pinned": {"in-toto/attestation#554": {"repo": "in-toto/attestation",
                                                    "number": 554}}}
    out = _run_unpin(monkeypatch, "in-toto/attestation#554", state, TRACKED_CONFIG)
    assert "Unpinned" in out
    assert state["pinned"] == {}


def test_unpin_without_config_falls_back_to_old_message(monkeypatch):
    """config defaults to None, so callers that don't pass it keep working."""
    out = _run_unpin(monkeypatch, "MoltyCel/aae-conformance-vectors#2",
                     {"pinned": {}}, None)
    assert "No dynamic pin" in out
