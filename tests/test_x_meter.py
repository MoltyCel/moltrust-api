"""The X meter: what a day actually costs, with X's own deduplication."""
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
