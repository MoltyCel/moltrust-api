"""The filters and the claim guard — the two places a bad draft is stopped early.

Both were tightened on 23.09.2026 after the first live replies: 6, 8 and 0
impressions under targets with 2,870, 1,494 and 9,162, and three drafts in one
afternoon all leaning on "EU AI Act Article 12".
"""
import datetime
import json

import pytest

from agents import reply_radar


# Every fixture post is on topic on purpose. An account we have not tiered is
# tier 4, and tier 4 is only answered when the post is about agent identity,
# x402 or ERC-8004 — so an off-topic fixture would be filtered by that rule and
# prove nothing about the one under test.
ON_TOPIC = "agent identity"


def post(text=f"a post long enough to carry an argument about {ON_TOPIC}",
         source="search", impressions=5000, followers=5000, **kw):
    t = {"id": "1", "text": text, "lang": "en", "_source": source,
         "author_id": "9", "_author": "someone", "_author_followers": followers,
         "public_metrics": {"impression_count": impressions}}
    t.update(kw)
    return t


SINCE = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)


def ok(t, state=None):
    return reply_radar.worth_answering(t, state or {}, SINCE, {})


# ── point 2: thresholds and post shapes ──

def test_a_small_account_is_skipped_but_never_a_list_member():
    assert not ok(post(followers=499))
    assert ok(post(followers=500))
    # Curated by hand, and the ones that matter most to us are the small ones.
    assert ok(post(source="list", followers=3, impressions=1))


def test_an_unknown_follower_count_does_not_silently_skip():
    """None is "we did not get the expansion", not "a tiny account"."""
    assert ok(post(followers=None))


@pytest.mark.parametrize("text", [
    "$SOL is ripping and agent identity is next, here is why it matters today",
    "our fund is long $ETH and short everything else in agent identity land",
])
def test_a_cashtag_post_is_skipped(text):
    assert not ok(post(text=text))


def test_a_price_in_dollars_is_not_a_cashtag():
    assert ok(post(text="the whole agent identity run cost $21 in USDC fees "
                        "across 74 calls, which is what surprised us most"))


@pytest.mark.parametrize("text", [
    "3/7 agent identity is not the same question as what the agent may do",
    "(2/9) the second problem is that nobody can recompute agent identity later",
    "2 of 5 — the agent identity chain breaks the moment a sub-agent appears",
])
def test_one_instalment_of_a_thread_is_skipped(text):
    assert not ok(post(text=text))


def test_a_ratio_in_prose_is_not_a_thread_counter():
    assert ok(post(text="only 2 of 114 agent identity records ever carried an "
                        "authenticated call, which is the measurement here"))


# ── point 3: one core claim per 48 hours, across every draft ──

def test_the_guard_sees_a_two_digit_article_number():
    """The old guard looked for three digits, so "Article 12" was invisible."""
    assert "eu ai act article 12" in reply_radar.claim_marks(
        "EU AI Act Article 12 sets record-keeping obligations")


def test_a_year_is_not_a_core_claim():
    assert reply_radar.claim_marks("this applies from 2 Aug 2026") == []


def test_a_thousands_separated_number_is_one_claim():
    """Tried before the bare run of digits, or "17,000" reads as "000"."""
    assert reply_radar.claim_marks("17,000 agents registered") == ["17,000"]


def test_the_claims_of_one_sentence_are_all_found():
    assert set(reply_radar.claim_marks(
        "242 registrations, ERC-8004 ids, 21 USDC of escrow")) == {
        "242", "erc-8004", "21 usdc"}


def test_legacy_junk_does_not_occupy_the_window():
    """The live state held '2026,', '2027.', '17,000', '2027'. One is a claim."""
    state = {"recent_claims": ["2026,", "2027.", "17,000", "2027"]}
    assert set(reply_radar.claim_history(state)) == {"17,000"}


def test_the_same_claim_is_refused_for_two_days(monkeypatch, tmp_path):
    monkeypatch.setattr(reply_radar, "BLOCKLIST_FILE", str(tmp_path / "none.json"))
    now = datetime.datetime.now(datetime.timezone.utc)
    state = {}
    text = "ERC-8004 registries answer identity, not authority"
    assert reply_radar.claim_conflict(state, text, now) is None
    reply_radar.remember_claims(state, text)

    clash = reply_radar.claim_conflict(state, text, now)
    assert clash and "erc-8004" in clash

    # Still blocked at 47 hours, free at 49.
    assert reply_radar.claim_conflict(state, text, now + datetime.timedelta(hours=47))
    assert reply_radar.claim_conflict(
        state, text, now + datetime.timedelta(hours=49)) is None


def test_a_different_claim_is_unaffected(monkeypatch, tmp_path):
    monkeypatch.setattr(reply_radar, "BLOCKLIST_FILE", str(tmp_path / "none.json"))
    now = datetime.datetime.now(datetime.timezone.utc)
    state = {}
    reply_radar.remember_claims(state, "ERC-8004 registries answer identity")
    assert reply_radar.claim_conflict(state, "RFC 8785 fixes the canonical form",
                                      now) is None


def test_the_old_list_shaped_state_still_guards(monkeypatch, tmp_path):
    """Migration: a bare list from before the clock existed must still block."""
    monkeypatch.setattr(reply_radar, "BLOCKLIST_FILE", str(tmp_path / "none.json"))
    now = datetime.datetime.now(datetime.timezone.utc)
    state = {"recent_claims": ["erc-8004"]}
    assert reply_radar.claim_conflict(
        state, "ERC-8004 registries answer identity", now)


def test_expired_claims_are_dropped_rather_than_accumulating(monkeypatch, tmp_path):
    monkeypatch.setattr(reply_radar, "BLOCKLIST_FILE", str(tmp_path / "none.json"))
    old = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(hours=72)).isoformat()
    state = {"recent_claims": {"rfc 8785": old}}
    reply_radar.remember_claims(state, "ERC-8004 is the registry standard")
    assert "rfc 8785" not in state["recent_claims"]
    assert "erc-8004" in state["recent_claims"]


def test_a_hand_blocked_claim_is_refused_until_its_date(monkeypatch, tmp_path):
    f = tmp_path / "blocklist.json"
    f.write_text(json.dumps({"eu ai act article 12": "2026-09-26T00:00:00Z"}))
    monkeypatch.setattr(reply_radar, "BLOCKLIST_FILE", str(f))
    text = "EU AI Act Article 12 sets record-keeping obligations"

    before = datetime.datetime(2026, 9, 25, 12, tzinfo=datetime.timezone.utc)
    clash = reply_radar.claim_conflict({}, text, before)
    assert clash and "blocked by hand" in clash

    after = datetime.datetime(2026, 9, 26, 1, tzinfo=datetime.timezone.utc)
    assert reply_radar.claim_conflict({}, text, after) is None


def test_the_shipped_blocklist_covers_article_12():
    """The file Lars actually edits, read as the radar reads it."""
    blocked = reply_radar.load_blocklist()
    assert "eu ai act article 12" in blocked
    assert "article 12" in blocked, "a draft naming only the article must not slip"


# ── point 1: one draft per author per run, two per day ──

def test_a_second_post_by_the_same_author_is_skipped_in_one_run():
    state = {}
    t = post(source="list")
    t["_author"] = "owasp"
    assert reply_radar.author_cap_reason(state, t, set(), "2026-09-23") is None
    assert reply_radar.author_cap_reason(state, t, {"owasp"}, "2026-09-23")


def test_the_daily_cap_is_two_per_author():
    t = post(source="search")
    t["_author"] = "owasp"
    state = {"authors_per_day": {"2026-09-23": {"owasp": 1}}}
    assert reply_radar.author_cap_reason(state, t, set(), "2026-09-23") is None
    state["authors_per_day"]["2026-09-23"]["owasp"] = 2
    clash = reply_radar.author_cap_reason(state, t, set(), "2026-09-23")
    assert clash and "2 drafts today" in clash


def test_yesterdays_count_does_not_carry_over():
    t = post(source="list")
    t["_author"] = "owasp"
    state = {"authors_per_day": {"2026-09-22": {"owasp": 5}}}
    assert reply_radar.author_cap_reason(state, t, set(), "2026-09-23") is None


def test_a_mention_is_never_capped():
    """Someone who addressed us is owed an answer, including the second time."""
    t = post(source="mention")
    t["_author"] = "owasp"
    state = {"authors_per_day": {"2026-09-23": {"owasp": 9}}}
    assert reply_radar.author_cap_reason(state, t, {"owasp"}, "2026-09-23") is None


def test_counting_an_author_drops_older_days():
    t = post(source="list")
    t["_author"] = "owasp"
    state = {"authors_per_day": {"2026-09-20": {"someone": 3}}}
    reply_radar.count_author(state, t, "2026-09-23")
    assert state["authors_per_day"] == {"2026-09-23": {"owasp": 1}}


# ── point 2: the branch counter feeding the Sunday comparison ──

def test_a_draft_is_booked_to_its_branch():
    state = {}
    reply_radar.count_source(state, post(source="list"), "2026-09-23")
    reply_radar.count_source(state, post(source="search"), "2026-09-23")
    reply_radar.count_source(state, post(source="search"), "2026-09-23")
    assert state["drafts_by_source"]["2026-09-23"] == {"list": 1, "search": 2}


def test_the_search_floor_is_a_hundred():
    assert reply_radar.MIN_IMPRESSIONS == 100
    assert ok(post(impressions=100))
    assert not ok(post(impressions=99))
    # The other three filters are untouched by the lower floor.
    assert not ok(post(impressions=5000, followers=100))
    assert not ok(post(text="$SOL and agent identity, 9000 impressions of it here"))
