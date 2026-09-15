"""content_scout discovery resilience: guards, feed freshness, zero-intake alarm.

Pure unit tests: no network, no DB, no Telegram. The DB connection, Anthropic
client and Telegram module are replaced; all file paths point into tmp_path.
"""
import asyncio
import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from workers.content_scout import config, db, health, llm, pipeline, pull, telegram  # noqa: E402

UTC = dt.timezone.utc


def T(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


class FakeConn:
    def __init__(self):
        self.inserted = []
        self.closed = False

    async def fetch(self, sql, *a):
        return []

    async def execute(self, sql, *a):
        if "INSERT INTO content_review_queue" in sql:
            self.inserted.append(a[1])  # source_ref
        return "INSERT 0 1"

    async def close(self):
        self.closed = True


@pytest.fixture
def env(tmp_path, monkeypatch):
    feed = tmp_path / "discovery_candidates.json"
    hfile = tmp_path / "discovery_health.json"
    monkeypatch.setattr(config, "DISCOVERY_FEED", feed)
    monkeypatch.setattr(config, "DISCOVERY_HEALTH", hfile)
    monkeypatch.setattr(config, "RUNS_STATE", tmp_path / "state" / "content_scout_runs.json")
    monkeypatch.setattr(config, "NEWSSCOUT_ARTIFACT", tmp_path / "no_news.json")
    monkeypatch.setattr(config, "load_secrets", lambda: {})
    monkeypatch.setattr(config, "anthropic_key", lambda s: "test")
    monkeypatch.setattr(llm, "make_client", lambda k: object())
    monkeypatch.setattr(llm, "balance_ok", lambda c: True)
    monkeypatch.setattr(llm, "point", lambda *a, **k: ("a point", config.MODEL_CLASSIFY))
    conn = FakeConn()

    async def connect(secrets):
        return conn

    monkeypatch.setattr(db, "connect", connect)
    sent, summaries = [], []
    monkeypatch.setattr(telegram, "send_message",
                        lambda secrets, text, label="": sent.append(text) or [])
    monkeypatch.setattr(telegram, "send_summary",
                        lambda secrets, text: summaries.append(text))
    health._run.update(started_at=None, recorded=False, feed=None)

    class E:
        pass

    e = E()
    e.feed, e.health, e.conn, e.sent, e.summaries, e.tmp = feed, hfile, conn, sent, summaries, tmp_path

    def write_feed(n=2):
        feed.write_text(json.dumps({"candidates": [
            {"repo": "acme/w", "number": i, "title": f"t{i}",
             "url": f"https://github.com/acme/w/issues/{i}", "added_at": "2026-09-15"}
            for i in range(1, n + 1)]}))

    def write_health(**kw):
        h = {"consecutive_failures": 0, "last_ok": "2026-09-15",
             "last_ok_at": health._iso(health.utcnow()), "last_error": None}
        h.update(kw)
        hfile.write_text(json.dumps(h))

    e.write_feed, e.write_health = write_feed, write_health
    return e


def alerts(sent):
    return [s for s in sent if s.startswith(health.ALERT_PREFIX)]


def recoveries(sent):
    return [s for s in sent if s.startswith(health.RECOVERY_PREFIX)]


# --- guards -------------------------------------------------------------------
def test_classify_error_leaves_item_for_retry(env, monkeypatch):
    env.write_feed(2)
    env.write_health()
    calls = {"n": 0}

    def classify(client, system, text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("overloaded")
        return {"verdict": "DROP", "reason": "meh"}

    monkeypatch.setattr(llm, "classify", classify)
    tally = asyncio.run(pipeline.run())
    assert tally["classify_errors"] == 1 and tally["classified"] == 1
    # the failed item is NOT persisted (no DROP/discarded row) -> retried next run
    assert env.conn.inserted == ["https://github.com/acme/w/issues/2"]
    assert not alerts(env.sent)
    assert "errors: 1 classify" in tally["summary"]
    assert not tally.get("exit_code")


def test_all_classify_errors_alert_and_exit_1(env, monkeypatch):
    env.write_feed(3)
    env.write_health()
    monkeypatch.setattr(llm, "classify", lambda *a: (_ for _ in ()).throw(RuntimeError("401")))
    tally = asyncio.run(pipeline.run())
    assert env.conn.inserted == []
    assert tally["exit_code"] == 1
    a = alerts(env.sent)
    assert len(a) == 1 and "classify failed for ALL 3" in a[0]


def test_db_connect_failure_alerts_and_exits_2(env, monkeypatch):
    env.write_feed(1)
    env.write_health()

    async def boom(secrets):
        raise OSError("connection refused")

    monkeypatch.setattr(db, "connect", boom)
    monkeypatch.setattr(sys, "argv", ["pipeline"])
    with pytest.raises(SystemExit) as exc:
        pipeline.main()
    assert exc.value.code == 2
    a = alerts(env.sent)
    assert len(a) == 1 and "DB connect failed" in a[0]
    runs = json.loads(config.RUNS_STATE.read_text())["runs"]
    assert runs[-1]["fatal"].startswith("db.connect")


def test_corrupt_feed_alerts_and_aborts(env, monkeypatch):
    env.write_health()
    env.feed.write_text("{not json")
    monkeypatch.setattr(llm, "classify", lambda *a: {"verdict": "DROP", "reason": ""})
    with pytest.raises(health.FatalRunError):
        asyncio.run(pipeline.run())
    assert any("feed unreadable" in s for s in alerts(env.sent))


def test_pull_error_alerts_and_skips_lead(env, monkeypatch):
    env.write_feed(1)
    env.write_health()
    monkeypatch.setattr(llm, "classify", lambda *a: {"verdict": "PASS", "reason": "good"})

    def bad_pull(url, token):
        raise RuntimeError("github down")

    monkeypatch.setattr(pull, "pull_discovery", bad_pull)
    tally = asyncio.run(pipeline.run())
    assert env.conn.inserted == []
    a = alerts(env.sent)
    assert len(a) == 1 and "content pull failed for 1" in a[0] and "github down" in a[0]


def test_unexpected_crash_alerts_and_exits_2(env, monkeypatch):
    env.write_feed(1)
    env.write_health()

    async def seen(conn):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(db, "seen_refs", seen)
    monkeypatch.setattr(sys, "argv", ["pipeline"])
    with pytest.raises(SystemExit) as exc:
        pipeline.main()
    assert exc.value.code == 2
    assert any("run crashed" in s for s in alerts(env.sent))


# --- feed freshness -----------------------------------------------------------
def test_healthy_feed_no_alert_and_fresh_summary_line(env, monkeypatch):
    env.write_feed(1)
    env.write_health()
    monkeypatch.setattr(llm, "classify", lambda *a: {"verdict": "DROP", "reason": ""})
    tally = asyncio.run(pipeline.run())
    assert alerts(env.sent) == []
    assert env.summaries and "\nfeed: fresh (discovery ok " in env.summaries[0]


def test_stale_feed_alerts_and_summary_says_stale(env, monkeypatch):
    env.write_feed(0)
    env.write_health(last_ok="2026-08-10", last_ok_at="2026-08-10T06:00:40+00:00",
                     consecutive_failures=70, last_error="GitHub credential unusable")
    monkeypatch.setattr(llm, "classify", lambda *a: {"verdict": "DROP", "reason": ""})
    tally = asyncio.run(pipeline.run())
    a = alerts(env.sent)
    assert len(a) == 1 and "STALE" in a[0] and "2026-08-10 06:00 UTC" in a[0]
    assert "feed: STALE — discovery stale since 2026-08-10 06:00 UTC" in env.summaries[0]
    assert "FAILED (70x in a row" in env.summaries[0]


def test_missing_health_file_alerts(env):
    env.write_feed(1)
    st = health.start({}, now=T("2026-09-15T06:30:00"))
    assert st["health_missing"] and not st["fresh"]
    a = alerts(env.sent)
    assert len(a) == 1 and "discovery_health.json missing" in a[0]
    assert health.feed_line(st).startswith("feed: UNKNOWN")


def test_date_only_health_uses_feed_mtime(env):
    env.write_feed(1)
    mtime = T("2026-09-15T06:00:57").timestamp()
    os.utime(env.feed, (mtime, mtime))
    env.health.write_text(json.dumps({"consecutive_failures": 0, "last_ok": "2026-09-15",
                                      "last_error": None}))
    st = health.feed_status(now=T("2026-09-15T17:30:00"))
    assert st["fresh"] and health.feed_line(st) == \
        "feed: fresh (discovery ok 2026-09-15 06:00 UTC, 1 in feed)"
    # next day, discovery did not run: stale by the evening run (> 26h)
    assert health.feed_status(now=T("2026-09-16T06:30:00"))["fresh"]
    assert not health.feed_status(now=T("2026-09-16T17:30:00"))["fresh"]


def test_stale_alarm_throttle_and_recovery(env):
    env.write_feed(0)
    env.write_health(last_ok_at="2026-09-10T06:00:00+00:00")
    t0 = T("2026-09-15T06:30:00")
    health.start({}, now=t0)
    health.start({}, now=t0 + dt.timedelta(hours=11))            # 17:30, throttled
    assert len(alerts(env.sent)) == 1
    health.start({}, now=t0 + dt.timedelta(hours=24, seconds=-5))  # next 06:30, jitter
    assert len(alerts(env.sent)) == 2
    env.write_health(last_ok_at="2026-09-16T06:00:00+00:00")
    now = T("2026-09-16T17:30:00")
    os.utime(env.feed, (now.timestamp(), now.timestamp()))
    health.start({}, now=now)
    assert len(recoveries(env.sent)) == 1 and "fresh again" in recoveries(env.sent)[0]
    health.start({}, now=now + dt.timedelta(hours=13))
    assert len(recoveries(env.sent)) == 1 and len(alerts(env.sent)) == 2


# --- zero-intake alarm --------------------------------------------------------
def _schedule(start, days):
    """Scout cron: 06:30 and 17:30 UTC."""
    d0 = T(start)
    for d in range(days):
        day = d0 + dt.timedelta(days=d)
        yield day.replace(hour=6, minute=30)
        yield day.replace(hour=17, minute=30)


def _run_at(now, cands, classified=None):
    health._run.update(started_at=now, recorded=False, feed={"fresh": True})
    return health.finish_run({}, candidates=cands,
                             classified=cands if classified is None else classified, now=now)


def test_normal_evening_zero_never_alarms(env):
    # 10 days of normal operation: morning intake, evening 0.
    for t in _schedule("2026-09-01T00:00:00", 10):
        _run_at(t, 12 if t.hour == 6 else 0)
    assert alerts(env.sent) == []


def test_one_quiet_discovery_day_does_not_alarm(env):
    runs = list(_schedule("2026-09-01T00:00:00", 3))
    for t in runs:
        # day 2 morning: discovery found nothing new; day 3 morning: intake again
        _run_at(t, 0 if (t.hour == 17 or t.day == 2) else 9)
    assert alerts(env.sent) == []


def test_zero_intake_alarm_fires_after_window_throttles_and_recovers(env):
    times = list(_schedule("2026-09-01T00:00:00", 6))
    fired_at = None
    for t in times:
        live = t < T("2026-09-02T00:00:00")          # upstream dies after day 1
        res = _run_at(t, 14 if (live and t.hour == 6) else 0)
        if res["action"] == "alert" and fired_at is None:
            fired_at = t
    # Last intake day1 06:30. At day2 17:30 that run is still inside the 36h
    # window, so no alarm. At day3 06:30 the window holds day2 06:30, day2 17:30,
    # day3 06:30: 3 runs, 24h span, all zero -> first alert. (The feed freshness
    # alarm covers the faster path: it fires once discovery is > 26h stale.)
    assert fired_at == T("2026-09-03T06:30:00")
    a = alerts(env.sent)
    # 6 days: alert day2 17:30, re-alerts at most every 24h (-jitter)
    assert 3 <= len(a) <= 5
    assert all("zero intake" in s for s in a)
    state = json.loads(config.RUNS_STATE.read_text())
    last = [health.parse_ts(s) for s in [state["alarms"]["zero_intake"]["last_alert"]]][0]
    assert last is not None
    # intake resumes -> exactly one recovery, alarm cleared
    _run_at(times[-1] + dt.timedelta(hours=13), 5)
    assert len(recoveries(env.sent)) == 1 and "intake resumed" in recoveries(env.sent)[0]
    assert "zero_intake" not in json.loads(config.RUNS_STATE.read_text())["alarms"]


def test_zero_intake_realert_gap_is_at_least_a_day(env):
    times = list(_schedule("2026-09-01T00:00:00", 8))
    stamps = []
    for t in times:
        if _run_at(t, 0)["action"] in ("alert", "realert"):
            stamps.append(t)
    assert len(stamps) >= 2
    gaps = [(b - a).total_seconds() / 3600 for a, b in zip(stamps, stamps[1:])]
    assert all(g >= 24 - 0.5 for g in gaps)


def test_manual_run_burst_does_not_alarm(env):
    base = T("2026-09-15T09:00:00")
    for i in range(6):  # six manual runs in 5 hours, all zero
        _run_at(base + dt.timedelta(hours=i), 0)
    assert alerts(env.sent) == []


def test_run_history_is_bounded(env, monkeypatch):
    monkeypatch.setattr(config, "RUNS_HISTORY_MAX", 10)
    for t in _schedule("2026-09-01T00:00:00", 10):
        _run_at(t, 1)
    assert len(json.loads(config.RUNS_STATE.read_text())["runs"]) == 10


def test_summary_line_reports_errors_and_freshness(env):
    env.write_feed(1)
    env.write_health()
    health.start({})
    s = health.summary_lines({"classify_errors": 2, "pull_errors": ["x"]})
    assert s.splitlines()[0].startswith("feed: fresh (discovery ok ")
    assert s.splitlines()[1] == "errors: 2 classify, 1 pull (left for retry)"


def test_pull_discovery_raises_on_auth_error(monkeypatch):
    import httpx

    def handler(request):
        return httpx.Response(401, json={"message": "Bad credentials"})

    real_client = httpx.Client
    monkeypatch.setattr(pull.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(httpx.HTTPStatusError):
        pull.pull_discovery("https://github.com/acme/w/issues/1", "expired")


def test_zero_intake_tolerates_cron_jitter(env):
    # day2 06:30:05 -> day3 06:30:01 is a few seconds short of 24h; must still fire.
    _run_at(T("2026-09-01T06:30:00"), 12)
    _run_at(T("2026-09-01T17:30:00"), 0)
    _run_at(T("2026-09-02T06:30:05"), 0)
    _run_at(T("2026-09-02T17:30:02"), 0)
    res = _run_at(T("2026-09-03T06:30:01"), 0)
    assert res["action"] == "alert"
