"""BY5: the reply radar as a reading list only (scripts/check_radar_reading_list.py)."""
import datetime as dt
import json
import pathlib

from scripts import check_radar_reading_list as c

UTC = dt.timezone.utc
DUE = dt.datetime(2026, 10, 18, 6, 37, tzinfo=UTC)   # first hourly selftest after the run

QUIET_RUN = [
    "[2026-10-18T06:05:02] INFO: ============================================================",
    "[2026-10-18T06:05:02] INFO: REPLY RADAR — 2026-10-18 06:05 UTC",
    "[2026-10-18T06:05:02] INFO: Window: 06:05 -> 06:05 UTC (24.0 h)  ·  list + mentions only",
    "[2026-10-18T06:05:02] INFO: Targets configured: 71  ·  reading list only (reply branch retired from 2026-10-18 (Lars, BW1))",
    "[2026-10-18T06:05:04] INFO: Candidates after filtering: 6",
    "[2026-10-18T06:05:04] INFO: reading list: 4 posts of 6 candidates sent",
    "[2026-10-18T06:10:02] INFO: No decisions waiting",
]


def _read(at, posts=(), users=()):
    return {"at": at, "kind": "read", "source": "tweets", "posts": list(posts), "users": list(users)}


SENT = [{"ts": "2026-10-18T06:05:04+00:00", "kanal": "stats", "erfolg": True, "http_status": 200,
         "text_anfang": "\U0001f4d6 Reply-Radar — Leseliste, 4 von 6 Kandidaten\nFenster"}]


def _setup(tmp_path, log=QUIET_RUN, state=None, posts=(), meter=None, sent=SENT):
    p = {"log_path": tmp_path / "reply_radar.log", "state_path": tmp_path / "state.json",
         "posts_path": tmp_path / "x_posts.jsonl", "meter_path": tmp_path / "x_meter.jsonl",
         "sent_path": tmp_path / "telegram-sent.jsonl"}
    p["sent_path"].write_text("".join(json.dumps(r) + "\n" for r in sent))
    p["log_path"].write_text("\n".join(log) + "\n")
    p["state_path"].write_text(json.dumps(state or {"per_day": {"2026-10-17": 2}}))
    p["posts_path"].write_text("".join(json.dumps(r) + "\n" for r in posts))
    if meter is None:
        meter = [_read("2026-10-18T06:05:03+00:00", [str(i) for i in range(30)])]
    p["meter_path"].write_text("".join(json.dumps(r) + "\n" for r in meter))
    return {k: str(v) for k, v in p.items()}


# --- not due -----------------------------------------------------------------

def test_nothing_is_due_before_the_first_run(tmp_path):
    before = dt.datetime(2026, 10, 18, 6, 22, tzinfo=UTC)
    assert c.run(before, **_setup(tmp_path)) is None
    assert "erste Prüfung" in c.lines(before, **_setup(tmp_path))[0]


def test_the_retirement_date_comes_from_the_radar_source():
    assert c.retired_from() == dt.date(2026, 10, 18)


# --- silence: a normal day passes all four -----------------------------------

def test_a_normal_reading_list_day_passes(tmp_path):
    r = c.run(DUE, **_setup(tmp_path))
    assert r["failed"] == [], r
    line = c.lines(DUE, **_setup(tmp_path))[0]
    assert "18.10. 06:05: OK" in line and "Leseliste 4 von 6" in line and "$0.150" in line


def test_drafts_counted_before_the_date_do_not_count(tmp_path):
    log = ["[2026-10-17T16:05:05] INFO:   tokens in=132 out=37 cache_read=0", *QUIET_RUN]
    r = c.run(DUE, **_setup(tmp_path, log=log, state={"per_day": {"2026-10-17": 3}}))
    assert r["checks"]["drafter"]["ok"], r


def test_no_candidates_and_breaker_are_declared_quiet_states(tmp_path):
    for log in (QUIET_RUN[:4] + ["[2026-10-18T06:05:04] INFO: Candidates after filtering: 0"],
                QUIET_RUN[:3] + ["[2026-10-18T06:05:03] WARNING: Reads paused: X reads paused: $2.01 spent today"]):
        r = c.run(DUE, **_setup(tmp_path, log=log, sent=[]))
        assert r["failed"] == [], (log[-1], r)


def test_other_kinds_of_x_post_are_not_reply_writes(tmp_path):
    posts = [{"at": "2026-10-18T09:00:00+00:00", "id": "1", "kind": "syndication-part"},
             {"at": "2026-10-18T09:00:00+00:00", "id": "2", "kind": "digest"}]
    assert c.run(DUE, **_setup(tmp_path, posts=posts))["checks"]["x_write"]["ok"]


def test_reads_outside_the_run_window_are_not_the_runs_cost(tmp_path):
    meter = [_read("2026-10-18T06:05:03+00:00", [str(i) for i in range(40)]),
             _read("2026-10-18T07:00:11+00:00", [f"p{i}" for i in range(100)])]
    r = c.run(DUE, **_setup(tmp_path, meter=meter))
    assert r["checks"]["kosten"]["ok"], r


# --- trigger: each check fails on its own finding ----------------------------

def test_a_missing_run_fails(tmp_path):
    r = c.run(DUE, **_setup(tmp_path, log=["[2026-10-17T06:05:02] INFO: REPLY RADAR — 2026-10-17 06:05 UTC"]))
    assert "leseliste" in r["failed"]


def test_a_run_without_list_or_reason_fails(tmp_path):
    r = c.run(DUE, **_setup(tmp_path, log=QUIET_RUN[:5]))
    assert "leseliste" in r["failed"]


def test_a_list_the_send_log_does_not_have_fails(tmp_path):
    failed = dict(SENT[0], erfolg=False, http_status=400)
    for sent in ([], [failed], [dict(SENT[0], text_anfang="Reply-Radar draft 1")]):
        r = c.run(DUE, **_setup(tmp_path, sent=sent))
        assert "leseliste" in r["failed"], sent
        assert "nicht im Sendeprotokoll" in r["checks"]["leseliste"]["text"]


def test_a_run_with_search_fails(tmp_path):
    log = [l.replace("  ·  list + mentions only", "") for l in QUIET_RUN]
    assert "takt" in c.run(DUE, **_setup(tmp_path, log=log))["failed"]


def test_a_retired_run_time_firing_fails(tmp_path):
    log = QUIET_RUN + ["[2026-10-18T10:05:02] INFO: REPLY RADAR — 2026-10-18 10:05 UTC"]
    r = c.run(DUE.replace(hour=11), **_setup(tmp_path, log=log))
    assert "takt" in r["failed"] and "Lauf 10:05" in r["checks"]["takt"]["text"]


def test_the_retired_run_times_before_the_date_do_not_count(tmp_path):
    log = ["[2026-10-17T16:05:02] INFO: REPLY RADAR — 2026-10-17 16:05 UTC", *QUIET_RUN]
    assert c.run(DUE, **_setup(tmp_path, log=log))["checks"]["takt"]["ok"]


def test_a_list_page_over_forty_fails_and_forty_passes(tmp_path):
    forty = [_read("2026-10-18T06:05:03+00:00", [str(i) for i in range(40)])]
    assert c.run(DUE, **_setup(tmp_path, meter=forty))["checks"]["takt"]["ok"]
    fifty = [_read("2026-10-18T06:05:03+00:00", [str(i) for i in range(41)])]
    assert "takt" in c.run(DUE, **_setup(tmp_path, meter=fifty))["failed"]


def test_a_posted_per_day_entry_after_the_date_fails(tmp_path):
    r = c.run(DUE, **_setup(tmp_path, state={"posted_per_day": {"2026-10-18": 1}}))
    assert "x_write" in r["failed"]


def test_a_model_call_after_the_date_fails(tmp_path):
    log = QUIET_RUN + ["[2026-10-18T10:05:05] INFO:   tokens in=132 out=37 cache_read=0"]
    assert "drafter" in c.run(DUE, **_setup(tmp_path, log=log))["failed"]


def test_a_draft_counter_after_the_date_fails(tmp_path):
    r = c.run(DUE, **_setup(tmp_path, state={"per_day": {"2026-10-18": 1}}))
    assert "drafter" in r["failed"]


def test_a_reply_posted_after_the_date_fails(tmp_path):
    posts = [{"at": "2026-10-18T11:00:00+00:00", "id": "9", "kind": "reply", "reply_to": "8"}]
    assert "x_write" in c.run(DUE, **_setup(tmp_path, posts=posts))["failed"]


def test_a_reply_write_in_the_meter_fails(tmp_path):
    meter = [_read("2026-10-18T06:05:03+00:00", ["1"]),
             {"at": "2026-10-18T11:00:00+00:00", "kind": "write", "source": "reply", "id": "9"}]
    assert "x_write" in c.run(DUE, **_setup(tmp_path, meter=meter))["failed"]


def test_a_run_over_the_cost_cap_fails(tmp_path):
    # 40 posts = $0.20 (the page cap) passes; 40 posts plus 6 profiles = $0.26 fails.
    ok = [_read("2026-10-18T06:05:03+00:00", [str(i) for i in range(40)])]
    assert c.run(DUE, **_setup(tmp_path, meter=ok))["checks"]["kosten"]["ok"]
    over = [_read("2026-10-18T06:05:03+00:00", [str(i) for i in range(40)], [f"u{i}" for i in range(6)])]
    assert "kosten" in c.run(DUE, **_setup(tmp_path, meter=over))["failed"]


def test_a_failure_is_named_in_the_digest_line(tmp_path):
    posts = [{"at": "2026-10-18T11:00:00+00:00", "id": "9", "kind": "reply"}]
    line = c.lines(DUE, **_setup(tmp_path, posts=posts))[0]
    assert "ABWEICHUNG x_write" in line and "X-Antworten 1" in line


# --- unreadable --------------------------------------------------------------

def test_a_missing_log_is_unreadable_not_a_pass(tmp_path):
    p = _setup(tmp_path)
    p["log_path"] = str(tmp_path / "missing.log")
    assert "nicht lesbar" in c.lines(DUE, **p)[0]


# --- a forced rotation the same afternoon (23.10.2026, 16:07) ----------------

def test_the_run_is_found_in_the_rotated_log(tmp_path):
    p = _setup(tmp_path)
    log = pathlib.Path(p["log_path"])
    log.rename(str(log) + "-20261018")
    log.write_text("[2026-10-18T16:10:02] INFO: No decisions waiting\n")
    assert c.run(DUE, **p)["failed"] == []


def test_the_run_is_found_in_a_compressed_rotated_log(tmp_path):
    import gzip
    p = _setup(tmp_path)
    log = pathlib.Path(p["log_path"])
    with gzip.open(str(log) + "-20261018.gz", "wt", encoding="utf-8") as f:
        f.write(log.read_text())
    log.write_text("")
    assert c.run(DUE, **p)["failed"] == []


def test_without_the_rotated_copy_the_missing_run_still_fails(tmp_path):
    p = _setup(tmp_path)
    pathlib.Path(p["log_path"]).write_text("[2026-10-18T16:10:02] INFO: No decisions waiting\n")
    assert "leseliste" in c.run(DUE, **p)["failed"]
