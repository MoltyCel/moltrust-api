"""The radar's silence, reported by something other than the radar.

Three incidents, one shape: the component that knew was the one that had gone
quiet. September, the list answered HTTP 400 for 24 hours and every run said
"0 candidates". The same week, the account ran out of credits. On 2026-10-02 the
breaker closed at 11:00 UTC and cancelled the 14:05 and 18:05 runs — logged as
a WARNING, reported once, and then 24 hours of nothing.

The reason has to come out of the run's own evidence, which is why these tests
feed real log shapes rather than a mock.
"""
import datetime
import json

from agents import watchdog as wd

NOW = datetime.datetime(2026, 10, 3, 10, 42, tzinfo=datetime.timezone.utc)


def log(*lines: str) -> list:
    return list(lines)


def run_header(ts: str) -> str:
    return f"[{ts}] INFO: REPLY RADAR — {ts[:10]} {ts[11:16]} UTC"


def test_a_draft_within_the_window_is_not_a_finding():
    lines = log(run_header("2026-10-03T06:05:01"),
                "[2026-10-03T06:05:02] INFO: Candidates after filtering: 4",
                "[2026-10-03T06:05:16] INFO:   draft 1 for 21 (@x, tier 1, list): PASS",
                "[2026-10-03T06:06:01] INFO: Done: 1 drafts this run")
    s = wd.radar_silence(NOW, lines)
    assert s["hours"] < wd.DEADMAN_HOURS


def test_a_blocked_draft_counts_as_delivered():
    """It reaches Telegram and gets read there. A skip does not."""
    lines = log(run_header("2026-10-03T06:05:01"),
                "[2026-10-03T06:05:34] INFO:   draft 2 for 99 (@y, tier 2, list): BLOCKED")
    assert wd.radar_silence(NOW, lines)["hours"] < wd.DEADMAN_HOURS


def test_the_breaker_is_named_as_the_reason():
    lines = log(run_header("2026-10-02T10:05:02"),
                "[2026-10-02T10:05:44] INFO:   draft 1 for 21 (@x, tier 4, search): PASS",
                run_header("2026-10-03T06:05:01"),
                "[2026-10-03T06:05:01] WARNING: Reads paused: X reads paused: "
                "$1.75 spent today (limit $1.50) — paused until 00:00 UTC")
    s = wd.radar_silence(NOW, lines)
    assert s["hours"] > wd.DEADMAN_HOURS
    assert "Breaker zu" in s["reason"] and "$1.75" in s["reason"]


def test_a_broken_source_is_named_with_its_status():
    """The September failure: a 400 that every run reported as a quiet day."""
    lines = log(run_header("2026-10-02T06:05:01"),
                "[2026-10-02T06:05:16] INFO:   draft 1 for 21 (@x, tier 1, list): PASS",
                run_header("2026-10-03T06:05:01"),
                "[2026-10-03T06:05:02] WARNING: GET https://api.twitter.com/2/lists/"
                "2101805022954557791/tweets -> 400: {\"detail\":\"since_id\"}",
                "[2026-10-03T06:05:03] INFO: Candidates after filtering: 0",
                "[2026-10-03T06:05:03] INFO: Done: 0 drafts this run")
    s = wd.radar_silence(NOW, lines)
    assert "Quelle fehlerhaft" in s["reason"] and "400" in s["reason"]
    assert "0 Kandidaten" not in s["reason"], "the 400 outranks the empty result"


def test_zero_candidates_says_so_rather_than_blaming_a_source():
    lines = log(run_header("2026-10-02T06:05:01"),
                "[2026-10-02T06:05:16] INFO:   draft 1 for 21 (@x, tier 1, list): PASS",
                run_header("2026-10-03T06:05:01"),
                "[2026-10-03T06:05:03] INFO: Candidates after filtering: 0",
                "[2026-10-03T06:05:03] INFO: Done: 0 drafts this run")
    assert "0 Kandidaten" in wd.radar_silence(NOW, lines)["reason"]


def test_candidates_the_drafter_declined_are_not_reported_as_empty():
    """03.10: 15 candidates, 15 refusals. 'Quelle tot' would be wrong here."""
    lines = log(run_header("2026-10-02T10:05:02"),
                "[2026-10-02T10:05:44] INFO:   draft 1 for 21 (@x, tier 4, search): PASS",
                run_header("2026-10-03T10:05:02"),
                "[2026-10-03T10:05:04] INFO: Candidates after filtering: 5",
                *[f"[2026-10-03T10:05:0{i}] INFO:   skip 21{i} (@a) — "
                  f"nothing factual to say" for i in range(5)],
                "[2026-10-03T10:05:14] INFO: Done: 0 drafts this run")
    s = wd.radar_silence(NOW, lines)
    assert "alle verworfen" in s["reason"]
    assert "5 Kandidaten, 5 abgelehnt" in s["reason"]
    assert "Drafter" in s["reason"]


def test_a_job_that_never_ran_is_the_first_thing_checked():
    """A run cannot report that it did not happen. This is the case that needs
    an outside observer, and the only reason this check is not in the radar."""
    lines = log(run_header("2026-10-01T18:05:01"),
                "[2026-10-01T18:05:16] INFO:   draft 1 for 21 (@x, tier 1, list): PASS")
    s = wd.radar_silence(NOW, lines)
    assert s["reason"].startswith("Job lief nicht")


def test_an_empty_log_is_a_finding_not_a_pass():
    s = wd.radar_silence(NOW, [])
    assert s["hours"] is None
    assert s["reason"].startswith("Job lief nicht")


def test_an_unreadable_log_fires_rather_than_passing_silently(monkeypatch):
    def boom(*a, **k):
        raise OSError("no such file")
    monkeypatch.setattr(wd, "_radar_tail", boom)
    out = wd.check_radar_deadman(NOW)
    assert out["ok"] is False and out["fire"] is True
    assert "Log nicht lesbar" in out["detail"]


def test_the_alert_repeats_at_most_every_twelve_hours(tmp_path, monkeypatch):
    """The condition can last days. An hourly repeat is how a channel dies."""
    stamp = tmp_path / "radar_deadman_reported"
    monkeypatch.setattr(wd, "DEADMAN_STAMP", str(stamp))
    monkeypatch.setattr(wd, "_radar_tail", lambda *a, **k: [])

    first = wd.check_radar_deadman(NOW)
    assert first["fire"] is True
    wd._deadman_stamp(NOW)

    hour_later = NOW + datetime.timedelta(hours=1)
    assert wd.check_radar_deadman(hour_later)["fire"] is False
    half_day_later = NOW + datetime.timedelta(hours=12, minutes=1)
    assert wd.check_radar_deadman(half_day_later)["fire"] is True


def test_an_unwritable_stamp_does_not_suppress_the_next_alert(monkeypatch):
    monkeypatch.setattr(wd, "DEADMAN_STAMP", "/proc/nope/stamp")
    monkeypatch.setattr(wd, "_radar_tail", lambda *a, **k: [])
    wd._deadman_stamp(NOW)
    assert wd.check_radar_deadman(NOW)["fire"] is True


def test_the_timestamps_come_back_for_the_report():
    lines = log(run_header("2026-10-02T10:05:02"),
                "[2026-10-02T10:05:44] INFO:   draft 1 for 21 (@x, tier 4, search): PASS")
    s = wd.radar_silence(NOW, lines)
    assert s["last_run"] == "2026-10-02T10:05:02"
    assert s["last_draft"] == "2026-10-02T10:05:44"
    assert round(s["hours"], 1) == 24.6


def test_the_detail_names_the_reason_and_the_last_run(monkeypatch):
    monkeypatch.setattr(wd, "DEADMAN_STAMP", "/nonexistent/stamp")
    monkeypatch.setattr(wd, "_radar_tail", lambda *a, **k: log(
        run_header("2026-10-02T10:05:02"),
        "[2026-10-02T10:05:44] INFO:   draft 1 for 21 (@x, tier 4, search): PASS",
        run_header("2026-10-03T10:05:02"),
        "[2026-10-03T10:05:02] WARNING: Reads paused: X reads paused: $1.52 "
        "spent today (limit $1.50) — paused until 00:00 UTC"))
    out = wd.check_radar_deadman(NOW)
    assert out["ok"] is False and out["fire"] is True
    assert "kein Entwurf seit 24.6 h" in out["detail"]
    assert "Breaker zu" in out["detail"]
    assert "2026-10-03T10:05:02" in out["detail"]
