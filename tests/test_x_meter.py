"""The X meter: what a day actually costs, with X's own deduplication."""
import datetime
import json

from agents import x_meter as xm


def ledger(tmp_path, rows):
    p = tmp_path / "x_meter.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(p)


def read(day, posts=(), users=(), source="list"):
    return {"at": f"{day}T10:00:00+00:00", "kind": "read", "source": source,
            "posts": list(posts), "users": list(users)}


def test_a_post_read_twice_in_one_day_is_charged_once(tmp_path):
    """X deduplicates resources within a UTC day. A meter that summed response
    sizes would overstate the bill — the radar re-reads much the same list."""
    path = ledger(tmp_path, [read("2026-09-27", posts=["1", "2"]),
                             read("2026-09-27", posts=["1", "2", "3"])])
    s = xm.spend("2026-09-27", path)
    assert s["posts"] == 3
    assert s["usd"] == round(3 * xm.USD_PER_POST_READ, 3)


def test_profiles_cost_twice_what_posts_do(tmp_path):
    path = ledger(tmp_path, [read("2026-09-27", posts=["1"], users=["u1"])])
    s = xm.spend("2026-09-27", path)
    assert s["usd"] == round(xm.USD_PER_POST_READ + xm.USD_PER_USER_READ, 3)


def test_a_post_with_a_url_costs_forty_times_a_plain_one(tmp_path):
    path = ledger(tmp_path, [
        {"at": "2026-09-27T10:00:00+00:00", "kind": "write", "id": "1",
         "with_url": False, "source": "digest"},
        {"at": "2026-09-27T10:01:00+00:00", "kind": "write", "id": "2",
         "with_url": True, "source": "digest"},
    ])
    s = xm.spend("2026-09-27", path)
    assert s["writes"] == 2 and s["writes_with_url"] == 1
    assert s["usd"] == round(xm.USD_PER_POST_WRITE
                             + xm.USD_PER_POST_WRITE_WITH_URL, 3)


def test_yesterday_is_not_todays_bill(tmp_path):
    path = ledger(tmp_path, [read("2026-09-26", posts=[str(i) for i in range(200)]),
                             read("2026-09-27", posts=["a"])])
    assert xm.spend("2026-09-27", path)["posts"] == 1


def test_the_biggest_source_is_named(tmp_path):
    path = ledger(tmp_path, [
        read("2026-09-27", posts=["1", "2", "3"], source="recent"),
        read("2026-09-27", posts=["9"], source="tweets"),
    ])
    s = xm.spend("2026-09-27", path)
    assert list(s["by_source"])[0] == "recent"


def test_a_day_over_the_limit_is_not_ok(tmp_path):
    many = [str(i) for i in range(300)]          # 300 × $0.005 = $1.50
    path = ledger(tmp_path, [read("2026-09-27", posts=many)])
    out = xm.check("2026-09-27", path)
    assert not out["ok"] and "1.50" in out["detail"]


def test_a_quiet_day_is_ok(tmp_path):
    path = ledger(tmp_path, [read("2026-09-27", posts=["1"])])
    assert xm.check("2026-09-27", path)["ok"]


def test_no_ledger_is_not_an_alarm(tmp_path):
    out = xm.check("2026-09-27", str(tmp_path / "missing.jsonl"))
    assert out["ok"] and "no meter rows" in out["detail"]


def test_a_write_carrying_a_link_is_detected_from_its_text(tmp_path, monkeypatch):
    rows = []
    monkeypatch.setattr(xm, "_write", rows.append)
    xm.record_write("1", "read it at https://moltrust.ch/blog/x.html", "digest")
    xm.record_write("2", "no link in this one, just 42", "digest")
    assert rows[0]["with_url"] and not rows[1]["with_url"]


def test_a_response_with_nothing_in_it_writes_no_row(tmp_path, monkeypatch):
    rows = []
    monkeypatch.setattr(xm, "_write", rows.append)
    xm.record_read({"meta": {"result_count": 0}}, "recent")
    assert rows == []


# ── the UTC day boundary ──
#
# On 2026-10-03 the reply radar had delivered nothing for 24 hours and the first
# suspect was the live breaker: a day sum that keeps counting past midnight
# would hold reads closed forever once a single day had overrun. It does not —
# measured live, 02.10 came out at $1.76 and 03.10 at $1.445 with the breaker
# open. These tests pin that, because the failure mode is invisible in
# production: everything simply stays quiet.

def _at(ts, posts):
    return {"at": ts, "kind": "read", "source": "recent", "posts": list(posts),
            "users": []}


def test_a_day_that_overran_does_not_follow_itself_into_the_next(tmp_path, monkeypatch):
    """23:59 over the breaker, 00:01 open again, same ledger."""
    over = [str(i) for i in range(400)]        # 400 × $0.005 = $2.00
    path = ledger(tmp_path, [_at("2026-10-02T23:59:00+00:00", over),
                             _at("2026-10-03T00:01:00+00:00", ["x"])])
    monkeypatch.setattr(xm, "LEDGER", path)
    monkeypatch.setattr(xm, "_live", {"at": 0.0, "day": "", "usd": 0.0})

    before = datetime.datetime(2026, 10, 2, 23, 59, 30, tzinfo=datetime.timezone.utc)
    after = datetime.datetime(2026, 10, 3, 0, 1, 30, tzinfo=datetime.timezone.utc)

    assert xm.spend("2026-10-02", path)["usd"] == 2.0
    assert xm.spend("2026-10-03", path)["usd"] == round(xm.USD_PER_POST_READ, 3)
    assert xm.live_spend_usd(before) == 2.0
    assert xm.reads_paused(before), "a $2.00 day must be closed"
    monkeypatch.setattr(xm, "_live", {"at": 0.0, "day": "", "usd": 0.0})
    assert xm.live_spend_usd(after) == round(xm.USD_PER_POST_READ, 3)
    assert xm.reads_paused(after) is None, "the new day inherited the old sum"


def test_the_twenty_second_cache_cannot_carry_a_sum_across_midnight(tmp_path, monkeypatch):
    """The cache is keyed by day, so 00:00 is always a miss, not a stale hit."""
    path = ledger(tmp_path, [_at("2026-10-02T23:59:00+00:00",
                                 [str(i) for i in range(400)])])
    monkeypatch.setattr(xm, "LEDGER", path)
    monkeypatch.setattr(xm, "_live", {"at": 0.0, "day": "", "usd": 0.0})
    before = datetime.datetime(2026, 10, 2, 23, 59, 59, tzinfo=datetime.timezone.utc)
    after = datetime.datetime(2026, 10, 3, 0, 0, 1, tzinfo=datetime.timezone.utc)
    assert xm.live_spend_usd(before) == 2.0
    # One second later by the clock, a different UTC day — and no sleep, so the
    # 20-second window is still wide open. The day must win over the cache.
    assert xm.live_spend_usd(after) == 0.0


def test_a_stale_flag_from_yesterday_does_not_pause_today(tmp_path, monkeypatch):
    """The flag is a cache. The watchdog writes it hourly and it lags by design;
    on 2026-10-03 it still said 02.10 while reads were correctly open."""
    flag = tmp_path / "x_reads_paused"
    flag.write_text(json.dumps({"day": "2026-10-02", "usd": 1.54,
                                "at": "2026-10-02T11:00:08+00:00"}))
    path = ledger(tmp_path, [_at("2026-10-03T00:01:00+00:00", ["x"])])
    monkeypatch.setattr(xm, "LEDGER", path)
    monkeypatch.setattr(xm, "BREAKER_FLAG", str(flag))
    monkeypatch.setattr(xm, "_live", {"at": 0.0, "day": "", "usd": 0.0})
    now = datetime.datetime(2026, 10, 3, 10, 42, tzinfo=datetime.timezone.utc)
    assert xm.reads_paused(now) is None


def test_the_flag_still_holds_when_the_ledger_cannot_be_read(tmp_path, monkeypatch):
    """A breaker that fails open on a missing file is not a breaker."""
    flag = tmp_path / "x_reads_paused"
    now = datetime.datetime(2026, 10, 3, 10, 42, tzinfo=datetime.timezone.utc)
    flag.write_text(json.dumps({"day": "2026-10-03", "usd": 1.61,
                                "at": now.isoformat()}))
    monkeypatch.setattr(xm, "LEDGER", str(tmp_path / "gone.jsonl"))
    monkeypatch.setattr(xm, "BREAKER_FLAG", str(flag))
    monkeypatch.setattr(xm, "_live", {"at": 0.0, "day": "", "usd": 0.0})
    paused = xm.reads_paused(now)
    assert paused and "1.61" in paused
