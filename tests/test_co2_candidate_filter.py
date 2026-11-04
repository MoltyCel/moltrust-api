"""CO2: the candidate filter removes obvious noise only.

Herkunft: ops CO2 (Freigabe Lars 04.11.2026). Kein Text, reine Werbung,
fremde Sprache ohne Bezug. Ob ein Post zum Thema passt, entscheidet der
Entwurfsteil, nicht der Filter.

  K2  tier 4 "off topic" is no reason any more; every remaining rule is one
      of the three noise kinds or bookkeeping
  K4  the watch message counts the filter's reasons like the drafter's
  K5  the daily cap stays at 3

Each rule has its Gegenprobe: the noise is dropped, the normal post stays.
"""
import datetime

import pytest

from agents import reply_radar as rr
from agents import watchdog as wd
from agents import x_meter

UTC = datetime.timezone.utc
SINCE = datetime.datetime(2026, 11, 3, 14, 28, tzinfo=UTC)
# A tier-4 account: not in the targets, so tier_of() gives 4.
TARGETS: dict = {}


def post(text, **kw):
    t = {"id": "2117000000000000009", "text": text, "lang": "en", "_source": "list",
         "author_id": "9", "_author": "polymarket",
         "created_at": "2026-11-04T05:00:00.000Z",
         "public_metrics": {"impression_count": 120000}}
    t.update(kw)
    return t


def why(t, state=None):
    return rr.drop_reason(t, state or {}, SINCE, TARGETS)


# ── K2: Stufe 4 entfaellt, der Rest ist Laerm ──

def test_a_tier_four_post_off_our_subject_is_now_a_candidate():
    t = post("JUST IN: Trump declares \"we got Greenland\"")
    assert rr.tier_of(t, TARGETS) >= 4
    assert not rr.ON_TOPIC_RE.search(t["text"])
    assert why(t) is None


@pytest.mark.parametrize("text", [
    "Live odds:  https://t.co/O0a1hVhYbC",
    "https://t.co/AykhWtXR4P",
    "@someone #tag https://t.co/x",
])
def test_a_post_without_text_is_noise(text):
    assert why(post(text)) == rr.NOISE_NO_TEXT


@pytest.mark.parametrize("text", [
    "Agents now pay https://t.co/x",
    "Trump veto forecast:  https://t.co/zufRmaVGnZ",
])
def test_three_words_of_text_are_text(text):
    """Gegenprobe: a short post that says something stays a candidate; the
    drafter decides whether there is anything checkable in it."""
    assert why(post(text)) is None


def test_a_retweet_is_no_text_of_its_own():
    t = post("RT @EFDevcon: Day 1 at Devcon 8, in 28 seconds.",
             referenced_tweets=[{"type": "retweeted", "id": "1"}])
    assert why(t) == rr.NOISE_NO_TEXT


def test_lang_zxx_is_no_text():
    assert why(post("https://t.co/a https://t.co/b nothing", lang="zxx")) == rr.NOISE_NO_TEXT


@pytest.mark.parametrize("text", [
    "Airdrop is live, claim your tokens before Friday",
    "Use code AGENT for 20% off the first month",
    "Presale opens tonight, whitelist spots are limited",
])
def test_pure_advertisement_is_noise(text):
    assert why(post(text)) == rr.NOISE_PURE_AD


@pytest.mark.parametrize("text", [
    "No presale, no airdrop, just the audit numbers from the last 30 days",
    "We launched without a giveaway: 12,000 agents registered in a week",
    "The #BlueMove hacker address bridged another 193K $USDC to Ethereum",
])
def test_a_negated_ad_word_or_a_cashtag_is_not_an_ad(text):
    """Gegenprobe and Negativfall (CLAUDE.md, Verneinung): the negation is
    removed before the ad words are searched; a cashtag alone is not an ad."""
    assert why(post(text)) is None


def test_a_foreign_post_without_bearing_is_noise():
    assert why(post("Hoy el mercado cerró en alza tras el anuncio", lang="es")) == rr.NOISE_FOREIGN


def test_a_foreign_post_on_our_subject_stays():
    """Gegenprobe: a foreign-language post that names the subject is kept."""
    t = post("Neue Studie zu x402 und Agentenzahlungen mit 40 Prozent Plus", lang="de")
    assert why(t) is None


def test_bookkeeping_reasons_stay():
    t = post("A post with enough words to be a real post here")
    assert why(t, {"seen": [t["id"]]}) == "seen"
    assert why(post(t["text"], author_id=rr.OUR_USER_ID)) == "ours"
    assert why(post(t["text"], created_at="2026-11-01T00:00:00.000Z")) == "older"


def test_every_reason_is_a_noise_kind_or_bookkeeping():
    """No other string can come out of drop_reason (criterion 2, the table)."""
    assert set(rr.FILTER_REASONS) == {"no-text", "pure-ad", "foreign-language",
                                      "seen", "ours", "older"}
    for t in [post("x"), post("Airdrop live now, claim yours"),
              post("Hoy el mercado cerró en alza", lang="es"),
              post("RT @a: b c d", referenced_tweets=[{"type": "retweeted"}])]:
        assert why(t) in rr.FILTER_REASONS


# ── K4: die Wach-Meldung zaehlt die Filtergruende ──

def test_the_filter_line_has_the_shape_of_the_reasons_line():
    line = rr.filter_line(39, 10, {"no-text": 12, "older": 17})
    assert line == "Filter: 39 read, 10 candidates · no-text 12 · older 17"
    assert rr.filter_line(5, 5, {}) == "Filter: 5 read, 5 candidates · nothing dropped"


RUN = [
    "[2026-11-05T06:05:01] INFO: REPLY RADAR — 2026-11-05 06:05 UTC",
    "[2026-11-05T06:05:02] INFO: Filter: 39 read, 12 candidates · no-text 9 · "
    "older 15 · pure-ad 1 · seen 2",
    "[2026-11-05T06:05:02] INFO: Candidates after filtering: 12 (by tier: {4: 12})",
    "[2026-11-05T06:05:06] INFO:   skip 1 (@a) — nothing factual to say "
    "[no-checkable-claim: SKIP: no-checkable-claim — x]",
    "[2026-11-05T06:05:09] INFO:   draft 1 for 2 (@b, tier 4, list): PASS",
]


def test_the_watch_message_counts_filter_reasons_like_drafter_reasons():
    s = wd.radar_silence(datetime.datetime(2026, 11, 5, 6, 10, tzinfo=UTC), RUN)
    assert s["filtered"] == {"no-text": 9, "older": 15, "pure-ad": 1, "seen": 2}
    assert ("Gründe: no-checkable-claim 1 · Filter: no-text 9 · older 15 · "
            "pure-ad 1 · seen 2") in s["run_summary"]


def test_a_run_with_no_candidates_names_the_filter_reasons():
    run = RUN[:2] + ["[2026-11-05T06:05:02] INFO: Candidates after filtering: 0 (by tier: {})"]
    run[1] = ("[2026-11-05T06:05:02] INFO: Filter: 39 read, 0 candidates · "
              "no-text 6 · older 33")
    s = wd.radar_silence(datetime.datetime(2026, 11, 5, 6, 10, tzinfo=UTC), run)
    assert "nichts kam durch den Filter · Filter: no-text 6 · older 33" in s["reason"]


def test_a_run_from_before_co2_says_filter_keine():
    """Gegenprobe: a log without a Filter line reads as no count, not a crash."""
    s = wd.radar_silence(datetime.datetime(2026, 11, 5, 6, 10, tzinfo=UTC),
                         [RUN[0], RUN[2], RUN[4]])
    assert s["filtered"] == {} and "Filter: keine" in s["run_summary"]


def test_only_the_last_run_is_counted():
    older = [l.replace("2026-11-05T06", "2026-11-04T06") for l in RUN]
    s = wd.radar_silence(datetime.datetime(2026, 11, 5, 6, 10, tzinfo=UTC),
                         older + [RUN[0]])
    assert s["filtered"] == {}


# ── K5: der Tagesdeckel bleibt ──

def test_the_daily_cap_stays_three():
    assert x_meter.DAILY_DRAFT_CAP == 3
    assert rr.DAILY_MAX == 3
