"""The measurement of Lars's own replies (ops CM5, docs/reply-radar-lars.md).

X is faked at requests.get, under x_meter.get, so the ledger rows these runs
book are the real ones: that is what proves the reads land under their own
source and never in the send cap.
"""
import datetime
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents import x_meter  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "lars_reply_metrics", ROOT / "scripts" / "lars_reply_metrics.py")
lrm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lrm)

UTC = datetime.timezone.utc
LARS_ID = "4242"
START = datetime.datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


class _Resp:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeX:
    """Answers the three reads the script makes and records each call."""

    def __init__(self, replies=None, followers=100, own_posts=0, author=LARS_ID):
        self.calls = []
        self.replies = replies or {}
        self.followers = followers
        self.own_posts = own_posts
        self.author = author

    def __call__(self, url, params=None, auth=None, timeout=None, **kw):
        self.calls.append((url, dict(params or {}), auth))
        if url.endswith("/users/me"):
            return _Resp({"data": {"id": LARS_ID, "username": "larsk",
                                   "public_metrics": {"followers_count": self.followers}}})
        if url.endswith(f"/users/{LARS_ID}/tweets"):
            return _Resp({"data": [{"id": f"own{i}"} for i in range(self.own_posts)],
                          "meta": {"result_count": self.own_posts}})
        if url.endswith("/2/tweets"):
            ids = params["ids"].split(",")
            return _Resp({"data": [
                {"id": i, "author_id": self.author,
                 "created_at": self.replies.get(i, {}).get(
                     "created_at", START.strftime("%Y-%m-%dT%H:%M:%S.000Z")),
                 "public_metrics": {"impression_count":
                                    self.replies.get(i, {}).get("impr", 40),
                                    "like_count": 1, "reply_count": 0},
                 "non_public_metrics": {"user_profile_clicks":
                                        self.replies.get(i, {}).get("clicks", 1),
                                        "engagements": 3}}
                for i in ids]})
        raise AssertionError(f"unexpected X call {url}")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(x_meter, "LEDGER", str(tmp_path / "data" / "x_meter.jsonl"))
    monkeypatch.setattr(x_meter, "BREAKER_FLAG", str(tmp_path / "data" / "x_reads_paused"))
    x_meter._live.update({"at": 0.0, "day": "", "usd": 0.0})
    for k in lrm.LARS_TOKEN_VARS:
        monkeypatch.setenv(k, "t")
    sent = []
    monkeypatch.setattr(lrm, "notify", type("N", (), {
        "STATS": "stats",
        "send_telegram": staticmethod(lambda text, **k: sent.append(text) or True),
        "silence_http_request_logs": staticmethod(lambda: None)}))
    return tmp_path, sent


def _fake(monkeypatch, fake):
    import requests
    monkeypatch.setattr(requests, "get", fake)
    return fake


def _rows(tmp_path, name="reply_radar_lars_metrics.jsonl"):
    p = tmp_path / "data" / name
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


# ── the window ──────────────────────────────────────────────────────────────

def test_no_reply_sent_means_no_window_and_no_call(env, monkeypatch):
    fake = _fake(monkeypatch, FakeX())
    assert lrm.measure(now=START) == 0
    assert fake.calls == []
    assert not (env[0] / "data" / "x_meter.jsonl").exists()


def test_a_copied_reply_does_not_open_the_window(env, monkeypatch):
    fake = _fake(monkeypatch, FakeX())
    lrm.record_copied("https://x.com/larsk/status/700")
    assert lrm.window_start() is None
    assert lrm.measure(now=START) == 0
    assert fake.calls == []


def test_the_first_sent_reply_opens_the_window(env):
    lrm.record_sent("701", "601", (START + datetime.timedelta(days=1)).isoformat())
    lrm.record_sent("700", "600", START.isoformat())
    assert lrm.window_start() == START


# ── criterion 3: Lars's tokens, one row per reply and day, kept apart ───────

def test_missing_lars_tokens_never_fall_back_to_moltrust(env, monkeypatch):
    for k in ("X_LARS_ACCESS_TOKEN", "X_LARS_ACCESS_SECRET"):
        monkeypatch.delenv(k)
    monkeypatch.setenv("X_ACCESS_TOKEN", "moltrust")
    monkeypatch.setenv("X_ACCESS_SECRET", "moltrust")
    fake = _fake(monkeypatch, FakeX())
    lrm.record_sent("700", "600", START.isoformat())
    assert lrm.measure(now=START + datetime.timedelta(hours=9)) == 1
    assert fake.calls == [], "nothing may be read with the @MolTrust pair"
    assert _rows(env[0]) == []


def test_one_row_per_reply_and_day_in_its_own_file(env, monkeypatch):
    tmp, _ = env
    fake = _fake(monkeypatch, FakeX(replies={"700": {"impr": 55, "clicks": 2}}))
    lrm.record_sent("700", "600", START.isoformat())
    lrm.record_sent("701", "601", START.isoformat())
    day1 = START + datetime.timedelta(hours=9)
    assert lrm.measure(now=day1) == 0
    assert lrm.measure(now=day1 + datetime.timedelta(days=1)) == 0

    replies = [r for r in _rows(tmp) if r["kind"] == "reply"]
    assert sorted((r["tweet_id"], r["measured_at"][:10]) for r in replies) == [
        ("700", "2026-11-02"), ("700", "2026-11-03"),
        ("701", "2026-11-02"), ("701", "2026-11-03")]
    r700 = [r for r in replies if r["tweet_id"] == "700"][0]
    assert r700["impressions"] == 55 and r700["profile_clicks"] == 2
    assert r700["copied"] is False and r700["route"] == "api"
    # Kept apart from the @MolTrust series.
    assert not (tmp / "data" / "digest_metrics.jsonl").exists()
    # Signed with Lars's pair: every call carried the auth built from it.
    assert all(c[2] is not None for c in fake.calls)
    assert "non_public_metrics" in [c for c in fake.calls
                                    if c[0].endswith("/2/tweets")][0][1]["tweet.fields"]


def test_reads_are_booked_under_their_own_source_and_not_as_sends(env, monkeypatch):
    tmp, _ = env
    _fake(monkeypatch, FakeX())
    lrm.record_sent("700", "600", START.isoformat())
    now = START + datetime.timedelta(hours=9)
    assert lrm.measure(now=now) == 0
    ledger = _rows(tmp, "x_meter.jsonl")
    assert ledger and {r["source"] for r in ledger} == {lrm.LEDGER_SOURCE}
    assert {r["kind"] for r in ledger} == {"read"}
    assert x_meter.send_spend(now)["usd"] == 0


def test_a_cost_pause_does_not_cut_the_measurement_off(env, monkeypatch):
    """CM4 criterion 5, CM5 criterion 3: both caps reached, the rows still come."""
    tmp, _ = env
    now = START + datetime.timedelta(hours=9)
    with open(x_meter.ledger_path(), "w") as f:
        for i in range(4):  # four radar replies with a link: $0.80 of sends
            f.write(json.dumps({"at": now.isoformat(), "kind": "write",
                                "source": "reply", "id": f"s{i}",
                                "with_url": True}) + "\n")
        f.write(json.dumps({"at": now.isoformat(), "kind": "read",
                            "source": "tweets", "users": [],
                            "posts": [f"p{i}" for i in range(500)]}) + "\n")
    assert x_meter.sends_paused(now), "precondition: sending is paused"
    assert x_meter.reads_paused(now), "precondition: the read breaker is tripped"

    _fake(monkeypatch, FakeX())
    lrm.record_sent("700", "600", START.isoformat())
    assert lrm.measure(now=now) == 0
    kinds = [r["kind"] for r in _rows(tmp)]
    assert kinds.count("reply") == 1 and kinds.count("followers") == 1


# ── followers per day without an own post ───────────────────────────────────

def test_followers_count_only_on_a_day_without_an_own_post(env, monkeypatch):
    tmp, _ = env
    lrm.record_sent("700", "600", START.isoformat())
    day = START + datetime.timedelta(hours=9)
    _fake(monkeypatch, FakeX(followers=100, own_posts=0))
    lrm.measure(now=day)
    _fake(monkeypatch, FakeX(followers=103, own_posts=0))
    lrm.measure(now=day + datetime.timedelta(days=1))
    _fake(monkeypatch, FakeX(followers=110, own_posts=1))
    lrm.measure(now=day + datetime.timedelta(days=2))

    f = [r for r in _rows(tmp) if r["kind"] == "followers"]
    assert [(r["new_followers"], r["own_posts"], r["counts"]) for r in f] == [
        (None, 0, True), (3, 0, True), (7, 1, False)]
    s = lrm.summary(START, START + datetime.timedelta(days=28))
    assert s["quiet_days"] == 1 and s["quiet_day_new_followers"] == 3


# ── criterion 4: copied replies ─────────────────────────────────────────────

def test_a_copied_reply_counts_once_reported_and_is_marked(env, monkeypatch):
    tmp, _ = env
    _fake(monkeypatch, FakeX())
    lrm.record_sent("700", "600", START.isoformat())
    assert lrm.main(["--add-copied", "https://x.com/larsk/status/702",
                     "--target", "602"]) == 0
    with pytest.raises(ValueError, match="already booked"):
        lrm.record_copied("https://x.com/larsk/status/702")
    assert lrm.main(["--add-copied", "https://x.com/larsk"]) == 2

    lrm.measure(now=START + datetime.timedelta(hours=9))
    rows = {r["tweet_id"]: r for r in _rows(tmp) if r["kind"] == "reply"}
    assert rows["702"]["copied"] is True and rows["702"]["route"] == "copied"
    assert rows["700"]["copied"] is False
    s = lrm.summary(START, START + datetime.timedelta(days=28))
    assert (s["replies"], s["sent"], s["copied"]) == (2, 1, 1)


def test_a_reported_post_from_another_account_is_skipped(env, monkeypatch):
    tmp, _ = env
    _fake(monkeypatch, FakeX(author="999"))
    lrm.record_sent("700", "600", START.isoformat())
    lrm.measure(now=START + datetime.timedelta(hours=9))
    assert [r for r in _rows(tmp) if r["kind"] == "reply"] == []


# ── the threshold ───────────────────────────────────────────────────────────

def _measured(tmp, figures):
    p = tmp / "data" / "reply_radar_lars_metrics.jsonl"
    with open(p, "a") as f:
        for i, (impr, clicks) in enumerate(figures):
            f.write(json.dumps({
                "kind": "reply", "tweet_id": str(800 + i), "copied": False,
                "posted_at": (START + datetime.timedelta(days=1)).isoformat(),
                "measured_at": (START + datetime.timedelta(days=3)).isoformat(),
                "impressions": impr, "profile_clicks": clicks}) + "\n")


def test_threshold_met_on_both(env):
    _measured(env[0], [(30, 1), (31, 0), (90, 0), (10, 1)])  # median 30.5, 0.5
    s = lrm.summary(START, START + datetime.timedelta(days=28))
    assert s["median_impressions"] == 30.5 and s["clicks_per_reply"] == 0.5
    assert s["passes_median"] and s["passes_clicks"]


def test_threshold_missed_by_either_one(env):
    _measured(env[0], [(29, 2), (29, 2), (200, 2)])  # median 29, clicks 2.0
    s = lrm.summary(START, START + datetime.timedelta(days=28))
    assert not s["passes_median"] and s["passes_clicks"]
    text = lrm.format_report("Zwischenstand", START,
                             START + datetime.timedelta(days=28), s)
    assert "Schwelle nicht erreicht." in text and "Median Aufrufe je Antwort: 29" in text


def test_the_newest_row_per_reply_decides(env):
    tmp = env[0]
    _measured(tmp, [(5, 0)])
    p = tmp / "data" / "reply_radar_lars_metrics.jsonl"
    with open(p, "a") as f:
        f.write(json.dumps({
            "kind": "reply", "tweet_id": "800", "copied": False,
            "posted_at": (START + datetime.timedelta(days=1)).isoformat(),
            "measured_at": (START + datetime.timedelta(days=5)).isoformat(),
            "impressions": 50, "profile_clicks": 1}) + "\n")
    s = lrm.summary(START, START + datetime.timedelta(days=28))
    assert s["replies"] == 1 and s["median_impressions"] == 50


# ── criterion 5: two reports, once each ─────────────────────────────────────

def test_interim_and_final_report_go_out_once_each(env, monkeypatch):
    tmp, sent = env
    _fake(monkeypatch, FakeX())
    lrm.record_sent("700", "600", START.isoformat())

    # Measured on its first day; after 14 days the reply is no longer read.
    lrm.measure(now=START + datetime.timedelta(hours=9))
    lrm.measure(now=START + datetime.timedelta(days=27, hours=23))
    assert sent == []
    lrm.measure(now=START + datetime.timedelta(days=28, hours=9))
    lrm.measure(now=START + datetime.timedelta(days=29, hours=9))
    assert len(sent) == 1 and "Zwischenstand nach vier Wochen" in sent[0]
    for key in ("Antworten: 1", "Median Aufrufe je Antwort", "Profilklicks je Antwort",
                "Schwelle ≥ 30", "Schwelle ≥ 0.5"):
        assert key in sent[0]

    lrm.measure(now=START + datetime.timedelta(days=56, hours=9))
    assert len(sent) == 2 and "Endstand nach acht Wochen" in sent[1]
    calls_before = len(_rows(tmp, "x_meter.jsonl"))
    assert lrm.measure(now=START + datetime.timedelta(days=57, hours=9)) == 0
    assert len(sent) == 2
    assert len(_rows(tmp, "x_meter.jsonl")) == calls_before, "closed window reads nothing"


def test_an_undelivered_report_is_tried_again(env, monkeypatch):
    tmp, sent = env
    _fake(monkeypatch, FakeX())
    lrm.record_sent("700", "600", START.isoformat())
    monkeypatch.setattr(lrm.notify, "send_telegram", staticmethod(lambda t, **k: False))
    assert lrm.measure(now=START + datetime.timedelta(days=28, hours=9)) == 1
    assert "interim_sent_at" not in lrm.load_state()
    monkeypatch.setattr(lrm.notify, "send_telegram",
                        staticmethod(lambda t, **k: sent.append(t) or True))
    lrm.measure(now=START + datetime.timedelta(days=29, hours=9))
    assert len(sent) == 1 and lrm.load_state().get("interim_sent_at")
