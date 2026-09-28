"""The reads the radar does not make, and the ones it stops making."""
import datetime
import json

import pytest

from agents import reply_radar as rr, x_meter as xm


NOW = datetime.datetime(2026, 9, 28, 14, 0, tzinfo=datetime.timezone.utc)


# ── the window follows the cadence ──

def test_the_window_starts_at_the_last_run_plus_an_overlap():
    state = {"last_run_at": "2026-09-28T10:00:00+00:00"}
    since = rr.lookback_since(state, NOW)
    assert since == datetime.datetime(2026, 9, 28, 9, 30,
                                      tzinfo=datetime.timezone.utc)


def test_a_first_run_falls_back_to_the_fixed_window():
    since = rr.lookback_since({}, NOW)
    assert since == NOW - datetime.timedelta(hours=rr.LOOKBACK_HOURS)


def test_a_long_outage_does_not_make_the_next_run_ask_for_a_week():
    state = {"last_run_at": "2026-09-21T10:00:00+00:00"}
    since = rr.lookback_since(state, NOW)
    assert since == NOW - datetime.timedelta(hours=rr.LOOKBACK_MAX_HOURS)


def test_an_unparseable_stamp_is_not_a_crash():
    assert rr.lookback_since({"last_run_at": "whenever"}, NOW) == \
        NOW - datetime.timedelta(hours=rr.LOOKBACK_HOURS)


# ── the breaker ──

def test_a_paused_day_reads_nothing(monkeypatch, tmp_path):
    flag = tmp_path / "paused"
    flag.write_text(json.dumps({"day": xm._day(), "usd": 1.7}))
    monkeypatch.setattr(xm, "BREAKER_FLAG", str(flag))

    def boom(*a, **k):
        raise AssertionError("called X while reads were paused")

    monkeypatch.setattr(rr.requests, "get", boom)
    assert rr._get("auth", "https://api.twitter.com/2/tweets/search/recent", {}) == {}


def test_yesterdays_flag_does_not_pause_today(monkeypatch, tmp_path):
    flag = tmp_path / "paused"
    flag.write_text(json.dumps({"day": "2020-01-01", "usd": 9.9}))
    monkeypatch.setattr(xm, "BREAKER_FLAG", str(flag))
    assert xm.reads_paused() is None


def test_the_breaker_is_written_once_a_day(tmp_path, monkeypatch):
    flag = tmp_path / "paused"
    monkeypatch.setattr(xm, "BREAKER_FLAG", str(flag))
    assert xm.trip_breaker(xm._day(), 1.6) is True
    assert xm.trip_breaker(xm._day(), 1.8) is False, "alarmed twice in one day"


def test_posting_is_never_paused(monkeypatch, tmp_path):
    """The breaker guards reads. A digest costs $0.015 and still goes out."""
    flag = tmp_path / "paused"
    flag.write_text(json.dumps({"day": xm._day(), "usd": 2.0}))
    monkeypatch.setattr(xm, "BREAKER_FLAG", str(flag))
    from agents import x_post
    source = (rr.os.path.dirname(rr.os.path.abspath(x_post.__file__)))
    text = open(x_post.__file__).read()
    assert "reads_paused" not in text, "the writer must not consult the read breaker"


# ── the list costs no profile reads ──

def test_the_list_request_asks_for_no_author_expansion(monkeypatch):
    calls = []

    def fake_get(auth, url, params):
        calls.append((url, params))
        return {}

    monkeypatch.setattr(rr, "_get", fake_get)
    rr.gather("auth", NOW - datetime.timedelta(hours=2), include_search=False)
    lists = [p for u, p in calls if "/lists/" in u]
    assert lists and "expansions" not in lists[0]
    mentions = [p for u, p in calls if "/mentions" in u]
    assert mentions and mentions[0]["expansions"] == "author_id"


def test_no_search_means_no_search_requests(monkeypatch):
    urls = []
    monkeypatch.setattr(rr, "_get", lambda a, u, p: urls.append(u) or {})
    rr.gather("auth", NOW - datetime.timedelta(hours=2), include_search=False)
    assert not any("search/recent" in u for u in urls)
    rr.gather("auth", NOW - datetime.timedelta(hours=2), include_search=True)
    assert any("search/recent" in u for u in urls)


def test_a_list_author_is_named_from_the_config(monkeypatch):
    """No expansion means no username in the response; the config has one."""
    body = {"data": [{"id": "1", "author_id": "16048357", "text": "x" * 60}]}
    monkeypatch.setattr(rr, "_get",
                        lambda a, u, p: body if "/lists/" in u else {})
    targets = {"owasp": {"user_id": "16048357", "followers": 218622, "tier": 1}}
    out = rr.gather("auth", NOW - datetime.timedelta(hours=2),
                    include_search=False, targets=targets)
    assert out[0]["_author"] == "owasp"
    assert out[0]["_author_followers"] == 218622


# ── a broken source must be heard (the 24-hour silence of 28.09.) ──

def test_the_list_request_carries_no_since_id(monkeypatch):
    """X: 'The query parameter [since_id] is not one of [id, max_results,
    pagination_token, post.fields]'. It answered 400 for 24 hours."""
    calls = []
    monkeypatch.setattr(rr, "_get", lambda a, u, p: calls.append((u, p)) or {})
    rr.gather("auth", NOW - datetime.timedelta(hours=2),
              since_id="2104137468916249064", include_search=False)
    lists = [p for u, p in calls if "/lists/" in u]
    assert lists and "since_id" not in lists[0]


def test_a_4xx_is_reported_not_just_logged(monkeypatch):
    sent = []
    monkeypatch.setattr(rr.notify, "send_telegram",
                        lambda text, **k: sent.append(text))
    monkeypatch.setattr(rr.x_meter, "reads_paused", lambda *a, **k: None)
    rr._SOURCE_FAILURES.clear()

    class R:
        status_code = 400
        text = '{"detail":"One or more parameters to your request was invalid."}'

    monkeypatch.setattr(rr.requests, "get", lambda *a, **k: R())
    rr._get("auth", "https://api.twitter.com/2/lists/1/tweets", {})
    state = {}
    rr.report_source_failures(state)
    assert sent and "Quelle antwortet nicht" in sent[0]
    assert "400" in sent[0]

    # The same failure four hours later is not a second message.
    sent.clear()
    rr.report_source_failures(state)
    assert sent == []
    rr._SOURCE_FAILURES.clear()


def test_a_429_is_not_treated_as_a_broken_source(monkeypatch):
    """Rate limiting is a wait, not a defect."""
    monkeypatch.setattr(rr.x_meter, "reads_paused", lambda *a, **k: None)
    rr._SOURCE_FAILURES.clear()

    class R:
        status_code = 429
        text = "Too Many Requests"

    monkeypatch.setattr(rr.requests, "get", lambda *a, **k: R())
    rr._get("auth", "https://api.twitter.com/2/lists/1/tweets", {})
    assert rr._SOURCE_FAILURES == []


def test_a_paused_run_says_so_once(monkeypatch):
    sent = []
    monkeypatch.setattr(rr.notify, "send_telegram",
                        lambda text, **k: sent.append(text))
    state = {}
    rr.report_reads_paused(state, "X reads paused: $1.63 spent today")
    rr.report_reads_paused(state, "X reads paused: $1.63 spent today")
    assert len(sent) == 1, "a suppressed run must be announced, and only once"
    assert "übersprungen" in sent[0]
