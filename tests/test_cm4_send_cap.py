"""CM4 (Lars, 22.10.2026): two drafts a UTC day, $0.50 a week for sending.

The cap counts sending only, and when it trips it pauses sending and nothing
else. Each test below is one of the five proofs the job asks for, and the
silence proofs are the point: a reading list that costs more than the cap must
not close the send path, and a closed send path must not stop the measurement.
"""

import datetime
import json

from agents import reply_radar, x_meter

# A Thursday, so the week under test is Monday 19.10 to Sunday 25.10.
NOW = datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.timezone.utc)


def _ledger(tmp_path, rows):
    p = tmp_path / "x_meter.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(p)


def _read(day, posts):
    return {"at": f"{day}T06:05:00+00:00", "kind": "read", "source": "tweets",
            "posts": posts, "users": []}


def _send(day, source="reply", with_url=False):
    return {"at": f"{day}T09:00:00+00:00", "kind": "write", "source": source,
            "id": f"{day}-{source}", "with_url": with_url}


def test_week_window_runs_monday_to_sunday():
    assert x_meter.week_window(NOW) == ("2026-10-19", "2026-10-25")
    monday = datetime.datetime(2026, 10, 19, 0, 0, tzinfo=datetime.timezone.utc)
    sunday = datetime.datetime(2026, 10, 25, 23, 59, tzinfo=datetime.timezone.utc)
    # Monday belongs to its own week, and Sunday does not spill into the next.
    assert x_meter.week_window(monday) == ("2026-10-19", "2026-10-25")
    assert x_meter.week_window(sunday) == ("2026-10-19", "2026-10-25")
    assert x_meter.next_monday(NOW) == "2026-10-26"


def test_reads_over_the_cap_do_not_pause_sending(tmp_path):
    """Criterion 2: a ledger of reads worth more than $0.50 and no send row."""
    # 300 distinct posts at $0.005 is $1.50 — three times the cap, and the
    # measured reading list of the week of 19.10 was $0.78 of that shape.
    rows = [_read("2026-10-20", [f"p{i}" for i in range(300)])]
    led = _ledger(tmp_path, rows)
    assert x_meter.send_spend(NOW, led)["usd"] == 0.0
    assert x_meter.sends_paused(NOW, led) is None


def test_other_write_sources_do_not_count(tmp_path):
    """The digest and the syndication thread are out of scope for this cap."""
    rows = [_send("2026-10-20", "digest"), _send("2026-10-20", "digest-part", True),
            _send("2026-10-21", "syndication"), _send("2026-10-21", "proof", True)]
    led = _ledger(tmp_path, rows)
    # $0.43 of writes in the week, none of them a reply.
    assert x_meter.send_spend(NOW, led)["usd"] == 0.0
    assert x_meter.sends_paused(NOW, led) is None


def test_daily_draft_cap_is_two_and_leaves_room_for_two(tmp_path):
    """Criterion 3, the arithmetic: room = min(limit, DAILY_MAX - used)."""
    assert x_meter.DAILY_DRAFT_CAP == 2
    assert reply_radar.DAILY_MAX == 2
    candidates = 5
    room = min(candidates, max(0, reply_radar.DAILY_MAX - 0))
    assert room == 2
    assert min(candidates, max(0, reply_radar.DAILY_MAX - 2)) == 0


RUN_AT = datetime.datetime(2026, 10, 22, 6, 5, tzinfo=datetime.timezone.utc)
LONG = "agents need a verifiable credential before they move money for anyone"


def _candidate(id_, author):
    return {"id": id_, "author_id": f"u{id_}", "lang": "en", "_source": "list",
            "_author": author, "_author_followers": 900,
            "created_at": "2026-10-22T05:00:00Z", "text": f"{LONG} — {author}",
            "public_metrics": {"impression_count": 100, "like_count": 0}}


def test_five_candidates_leave_the_run_with_two_drafts(monkeypatch, tmp_path):
    """Criterion 3: a run with five candidates sends two drafts and no third.

    The arithmetic above is not the proof the job asks for — this one drives
    `run()` down the drafting path with the real cap in place. The reply branch
    is retired (BW1), so the gate that would end the run before the cap is
    lifted here and nowhere else; what the cap does is what is under test.
    """
    rr = reply_radar
    asked, drafts = [], []
    monkeypatch.setattr(rr, "_utcnow", lambda: RUN_AT)
    monkeypatch.setattr(rr, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(rr, "HEARTBEAT_FILE", str(tmp_path / "hb.json"))
    monkeypatch.setattr(rr, "reply_branch_retired", lambda now=None: False)
    # CM1/CM13 split the gate in two; the cap sits behind the drafting one.
    monkeypatch.setattr(rr, "drafter_retired", lambda now=None: False)
    monkeypatch.setattr(rr, "x_auth", lambda: "auth")
    monkeypatch.setattr(rr.x_meter, "reads_paused", lambda *a, **k: None)
    monkeypatch.setattr(rr, "load_targets", lambda: {
        a: {"tier": 1, "group": "builders"}
        for a in ("alice", "bob", "carol", "dave", "erin")})
    monkeypatch.setattr(rr, "gather", lambda *a, **k: [
        _candidate("1", "alice"), _candidate("2", "bob"), _candidate("3", "carol"),
        _candidate("4", "dave"), _candidate("5", "erin")])
    monkeypatch.setattr(rr, "load_kb", lambda: {})

    # Five drafts with nothing in common, so the claim register never speaks
    # and what stops the run is the cap and only the cap.
    texts = ["ERC-8004 keeps its registry on chain.",
             "The A2A agent card is served from .well-known.",
             "A W3C status list answers revocation without a call home.",
             "An x402 receipt names the payer, not the wallet.",
             "A DID document can carry more than one verification method."]

    def fake_draft(tweet, kb, avoid=None):
        asked.append(tweet["id"])
        return (texts[len(asked) - 1], [])

    monkeypatch.setattr(rr, "draft_reply", fake_draft)
    monkeypatch.setattr(rr, "fetch_sources", lambda urls: {})
    monkeypatch.setattr(rr, "check", lambda t, s: (True, [], {}))
    monkeypatch.setattr(rr.notify, "send_telegram", lambda *a, **k: True)
    monkeypatch.setattr(rr.notify, "chat_id_for", lambda ch: 7)
    monkeypatch.setattr(rr.notify, "send_telegram_message",
                        lambda body, **kw: drafts.append(body) or 4242)

    rr.run()

    # Two drafts out of five candidates, and the model was asked exactly twice:
    # the cap has to bite before the spending, not after it.
    assert len(drafts) == 2, f"{len(drafts)} drafts for five candidates"
    assert len(asked) == 2, f"the drafter was asked {len(asked)} times"
    assert all("Reply-Entwurf" in d for d in drafts)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["per_day"]["2026-10-22"] == 2
    assert len(state["seen"]) == 2
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["detail"] == "2 drafts, 2/2 today"

    # The next run of the same day finds no room at all and asks nobody.
    asked.clear()
    drafts.clear()
    rr.run()
    assert drafts == [] and asked == []
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["detail"] == "daily cap reached (2/2)"


def test_sends_pause_when_the_week_is_spent(tmp_path):
    """Criterion 4: the cap trips, and the message says when it lifts."""
    rows = [_send(f"2026-10-{d}") for d in (19, 20, 21)]
    rows += [_send("2026-10-22", with_url=True)]   # $0.045 + $0.200 = $0.245
    rows += [_send("2026-10-22", "reply", True)]   # $0.445
    rows += [_send("2026-10-23", "reply", True)]   # $0.645, over the cap
    led = _ledger(tmp_path, rows)
    s = x_meter.send_spend(NOW, led)
    assert s["sends"] == 6 and s["usd"] > x_meter.WEEKLY_SEND_CAP_USD
    why = x_meter.sends_paused(NOW, led)
    assert why is not None
    assert "0.65" in why                      # what it stands at
    assert "2026-10-26" in why                # when it lifts
    assert "2026-10-19" in why                # which week it counted


def test_a_spent_week_leaves_reading_and_drafting_alone(tmp_path):
    """Criteria 4 and 5: the pause is sending only."""
    rows = [_send(f"2026-10-{d}", "reply", True) for d in (19, 20, 21)]
    rows += [_read("2026-10-22", [f"p{i}" for i in range(10)])]
    led = _ledger(tmp_path, rows)
    assert x_meter.sends_paused(NOW, led) is not None
    # The day's reads are what the reading list and the measurement ask about,
    # and they answer the same with the send path closed.
    day = x_meter.spend("2026-10-22", led)
    assert day["posts"] == 10 and day["usd"] == 0.05
    # The daily read breaker is a different number and is nowhere near tripping.
    assert day["usd"] < x_meter.DAILY_BREAK_USD


def test_a_missing_ledger_does_not_pause_sending(tmp_path):
    """A breaker that fails closed on a missing file stops work it never measured."""
    led = str(tmp_path / "absent.jsonl")
    assert x_meter.send_spend(NOW, led) == {
        "week": ("2026-10-19", "2026-10-25"), "usd": 0.0, "sends": 0, "ledger": False}
    assert x_meter.sends_paused(NOW, led) is None


def test_a_correction_row_is_not_a_send(tmp_path):
    """Corrections exist for missed reads; one must not close the send path."""
    rows = [{"at": "2026-10-20T10:00:00+00:00", "kind": "correction",
             "day": "2026-10-20", "usd": 5.0, "reason": "missed reads",
             "source": "manual"}]
    led = _ledger(tmp_path, rows)
    assert x_meter.send_spend(NOW, led)["usd"] == 0.0
    assert x_meter.sends_paused(NOW, led) is None
