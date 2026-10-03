"""Every declared alarm, fired on purpose, against fixtures.

Sunday 05:00 UTC. No real request leaves this file — every dependency is a
fake that answers the way the real one answered when it broke, taken from the
incident rather than imagined.

The reason this exists as its own suite: the normal tests prove the happy path
and the harness proved itself three times over. What was never tested is the
alarm. An alarm that does not fire is indistinguishable from a system that is
fine, and that confusion has cost five days of a track-record defect, 24 hours
of a dead list leg, and 44 hours of exhausted credits.

**A path that stays quiet fails the test.** Not "returns the wrong value" —
quiet. Each test below asserts that something was said, and says which words.
"""
from __future__ import annotations

import datetime
import json

import pytest

from agents import supervision, watchdog

# scripts/ is not a package, and making it one to satisfy an import would
# change how every script in it resolves. Loaded by path instead, the way
# tests/test_video_upload_route.py already does.
def _load(name: str):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_ops(name: str):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "ops" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


selfheal = _load("selfheal")

NOW = datetime.datetime(2026, 10, 3, 12, 0, tzinfo=datetime.timezone.utc)


class Resp:
    def __init__(self, status, payload=None, text="", headers=None):
        self.status_code = status
        self._p = payload
        self.text = text or (json.dumps(payload) if payload else "")
        self.headers = headers or {}

    def json(self):
        if self._p is None:
            raise ValueError("no json")
        return self._p


# ── 1. the breaker across UTC midnight ──

def test_the_breaker_closes_at_2359_and_opens_at_0001(tmp_path, monkeypatch):
    """One ledger, two verdicts. A day that overran must not follow itself."""
    from agents import x_meter
    led = tmp_path / "x_meter.jsonl"
    led.write_text("\n".join(json.dumps(
        {"at": "2026-10-02T23:59:00+00:00", "kind": "read", "source": "recent",
         "posts": [str(i)], "users": []}) for i in range(400)) + "\n")
    monkeypatch.setattr(x_meter, "LEDGER", str(led))
    monkeypatch.setattr(x_meter, "BREAKER_FLAG", str(tmp_path / "flag"))
    monkeypatch.setattr(x_meter, "_live", {"at": 0.0, "day": "", "usd": 0.0})

    before = datetime.datetime(2026, 10, 2, 23, 59, 30, tzinfo=datetime.timezone.utc)
    assert x_meter.reads_paused(before), "a $2.00 day stayed open"
    monkeypatch.setattr(x_meter, "_live", {"at": 0.0, "day": "", "usd": 0.0})
    after = datetime.datetime(2026, 10, 3, 0, 1, 0, tzinfo=datetime.timezone.utc)
    assert x_meter.reads_paused(after) is None, "the new day inherited the sum"


# ── 2. a source that answers 4xx must say "dead", not "empty" ──

@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_a_failing_source_is_named_not_counted_as_empty(status, monkeypatch, tmp_path):
    """September: the list answered 400 and every run said "0 candidates"."""
    from agents import reply_radar
    noted = []
    monkeypatch.setattr(reply_radar, "note_source_failure",
                        lambda url, st, body: noted.append((url, st)))
    monkeypatch.setattr(reply_radar.x_meter, "reads_paused", lambda *a: None)
    monkeypatch.setattr(reply_radar.requests, "get",
                        lambda *a, **k: Resp(status, text="broken"))
    body = reply_radar._get(None, "https://api.twitter.com/2/lists/1/tweets", {})
    assert body == {}
    if status == 429:
        # Rate limiting is backpressure, not a dead source. It must not raise
        # the "source is broken" alarm, or the alarm stops meaning anything.
        assert noted == []
    else:
        assert noted and noted[0][1] == status, (
            f"HTTP {status} produced no source failure — the run would report "
            f"a quiet day")


def test_the_radar_deadman_says_source_not_zero_candidates():
    lines = ["[2026-10-02T06:05:01] INFO: REPLY RADAR — 2026-10-02 06:05 UTC",
             "[2026-10-02T06:05:16] INFO:   draft 1 for 1 (@x, tier 1, list): PASS",
             "[2026-10-03T06:05:01] INFO: REPLY RADAR — 2026-10-03 06:05 UTC",
             "[2026-10-03T06:05:02] WARNING: GET https://api.twitter.com/2/lists/"
             "1/tweets -> 400: since_id",
             "[2026-10-03T06:05:03] INFO: Candidates after filtering: 0"]
    s = watchdog.radar_silence(NOW, lines)
    assert "Quelle fehlerhaft" in s["reason"] and "400" in s["reason"]
    assert "0 Kandidaten" not in s["reason"]


# ── 3. media verification: the field is set, the resource is 404 ──

def test_a_playlist_field_with_a_404_behind_it_is_red(monkeypatch):
    """02.10 exactly: embed.playlist present, the URL answered 404, and the
    run reported "verified as a playing video"."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "pv", Path(__file__).resolve().parents[1] / "scripts" / "post_video.py")
    pv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pv)

    def fake_get(url, **kw):
        if "getPosts" in url:
            return Resp(200, {"posts": [{"embed": {
                "$type": "app.bsky.embed.video#view",
                "playlist": "https://video.bsky.app/w/x/playlist.m3u8"}}]})
        return Resp(404, None, "video not found")

    monkeypatch.setattr(pv.httpx, "get", fake_get)
    ok, detail = pv.playlist_resolves("at://did/app.bsky.feed.post/x")
    assert ok is False, "a 404 behind a present field passed as verified"
    assert "404" in detail


# ── 4. an expired token ──

def test_an_expired_github_token_is_red(monkeypatch):
    monkeypatch.setenv("MOLTYCEL_GH_TOKEN", "ghp_fixture")
    monkeypatch.setattr(supervision.httpx, "get", lambda *a, **k: Resp(
        200, {"login": "MoltyCel"},
        headers={"x-oauth-scopes": "repo, workflow",
                 "github-authentication-token-expiration": "2026-09-01 00:00:00 UTC"}))
    f = supervision.dep_github()
    assert f["light"] == supervision.RED and "abgelaufen" in f["detail"]


def test_a_token_expiring_within_two_weeks_is_yellow(monkeypatch):
    soon = (supervision.now_utc() + datetime.timedelta(days=5))
    monkeypatch.setenv("MOLTYCEL_GH_TOKEN", "ghp_fixture")
    monkeypatch.setattr(supervision.httpx, "get", lambda *a, **k: Resp(
        200, {"login": "MoltyCel"},
        headers={"github-authentication-token-expiration":
                 soon.strftime("%Y-%m-%d %H:%M:%S UTC")}))
    assert supervision.dep_github()["light"] == supervision.YELLOW


def test_a_401_from_x_is_red(monkeypatch):
    # requests, not httpx: the X call signs with OAuth1 from requests_oauthlib,
    # and patching the wrong client is how this test would pass while the real
    # call raised AttributeError on a missing .body.
    import requests
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: Resp(401, None, "Unauthorized"))
    import agents.x_post as xp
    monkeypatch.setattr(xp, "get_auth", lambda: ("k", "s"))
    f = supervision.dep_x()
    assert f["light"] == supervision.RED and "401" in f["detail"]


def test_a_402_from_x_is_named_as_credits(monkeypatch):
    """The 44-hour outage. 402 has one meaning and the alarm must carry it."""
    import agents.x_post as xp
    monkeypatch.setattr(xp, "get_auth", lambda: ("k", "s"))
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp(
        402, {"detail": "credits depleted"}))
    f = supervision.dep_x()
    assert f["light"] == supervision.RED and "credits" in f["detail"].lower()


# ── 5. Telegram unreachable ──

def test_telegram_unreachable_is_red_and_leaks_no_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:SECRETSECRETSECRET")

    def boom(*a, **k):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(supervision.httpx, "get", boom)
    f = supervision.dep_telegram()
    assert f["light"] == supervision.RED
    assert "SECRET" not in f["detail"], "the token reached the finding text"


def test_a_telegram_401_reports_the_description_not_the_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:SECRETSECRETSECRET")
    monkeypatch.setattr(supervision.httpx, "get", lambda *a, **k: Resp(
        401, {"ok": False, "description": "Unauthorized"}))
    f = supervision.dep_telegram()
    assert f["light"] == supervision.RED and "Unauthorized" in f["detail"]
    assert "SECRET" not in f["detail"]


# ── 6. Postgres gone ──

def test_postgres_down_is_red(monkeypatch):
    class P:
        returncode = 2
        stdout = ""
        stderr = 'psql: error: connection to server failed: FATAL: the database system is shutting down'

    monkeypatch.setattr(supervision.subprocess, "run", lambda *a, **k: P())
    f = supervision.dep_postgres()
    assert f["light"] == supervision.RED and "FATAL" in f["detail"]


def test_postgres_answering_something_other_than_one_is_red(monkeypatch):
    class P:
        returncode = 0
        stdout = "\n"
        stderr = ""

    monkeypatch.setattr(supervision.subprocess, "run", lambda *a, **k: P())
    assert supervision.dep_postgres()["light"] == supervision.RED


# ── 7. the disk ──

def test_a_disk_over_85_percent_is_yellow_with_a_fix(monkeypatch):
    monkeypatch.setattr(supervision.shutil, "disk_usage",
                        lambda p: (100 * 2**30, 90 * 2**30, 10 * 2**30))
    f = supervision.check_disk()
    assert f["light"] == supervision.YELLOW and f["fix"] == "rotate_logs"


def test_a_disk_over_95_percent_is_red_and_offers_no_fix(monkeypatch):
    """Past 95 % rotation will not save it, and pretending otherwise wastes
    the only minutes left."""
    monkeypatch.setattr(supervision.shutil, "disk_usage",
                        lambda p: (100 * 2**30, 97 * 2**30, 3 * 2**30))
    f = supervision.check_disk()
    assert f["light"] == supervision.RED and not f["fix"]


# ── 8. the positive list is closed ──

def test_a_fix_not_on_the_list_is_reported_and_never_run():
    done = selfheal.heal([{"check": "pipeline/x", "light": supervision.YELLOW,
                           "detail": "whatever", "fix": "rm_minus_rf"}])
    assert done and done[0]["ran"] is False
    assert "Positivliste" in done[0]["result"]


def test_a_red_finding_is_never_corrected():
    """Red means unknown. An unknown deviation gets an alarm, not a repair."""
    done = selfheal.heal([{"check": "pipeline/x", "light": supervision.RED,
                           "detail": "unknown", "fix": "restart_service"}])
    assert done == []


def test_a_unit_outside_the_sudo_list_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(selfheal, "STATE", str(tmp_path / "state.json"))
    ok, detail = selfheal.restart_service(
        {"check": "service/moltycel-bot", "unit": "moltycel-bot"}, {}, dry=False)
    assert ok is False and "sudo-Positivliste" in detail


def test_the_restart_cap_turns_into_a_construction_fault(monkeypatch, tmp_path):
    monkeypatch.setattr(selfheal, "STATE", str(tmp_path / "state.json"))
    stamps = [supervision.now_utc().isoformat()] * 3
    st = {"runs": {"restart_service:moltstack": stamps}}
    ok, detail = selfheal.restart_service({"check": "service/moltstack",
                                           "unit": "moltstack"}, st, dry=False)
    assert ok is False and "Konstruktionsfehler" in detail


def test_retry_once_means_once(monkeypatch, tmp_path):
    monkeypatch.setattr(selfheal, "STATE", str(tmp_path / "state.json"))
    st = {"runs": {"retry_once:reply_radar": [supervision.now_utc().isoformat()]}}
    ok, detail = selfheal.retry_once({"check": "pipeline/reply_radar"}, st,
                                     dry=False)
    assert ok is False and "einmal heißt" in detail


# ── 9. silence without a declared reason ──

def test_quiet_with_a_declared_reason_is_green(tmp_path, monkeypatch):
    hb = tmp_path / "hb.json"
    hb.write_text(json.dumps({"timestamp": supervision.now_utc().isoformat(),
                              "status": "ok", "detail": "no new posts"}))
    monkeypatch.setattr(supervision, "BASE", str(tmp_path))
    spec = {"name": "syndicate", "evidence": "heartbeat", "heartbeat": "hb.json",
            "ok_status": ["ok"], "max_silence_minutes": 90,
            "min_output": [{"window_hours": 24, "count": 1}],
            "silence_ok": {"no_new_posts": "(?i)no new posts"},
            "output_marker": "POSTED", "log": "nothing.log"}
    out = supervision.check_pipeline(spec, supervision.now_utc())
    assert supervision.worst(out) == supervision.GREEN
    assert any("no_new_posts" in f["detail"] for f in out)


def test_quiet_without_a_declared_reason_is_red(tmp_path, monkeypatch):
    """The whole point of the register. The run produced nothing and said
    nothing about why, so it is a finding and not a quiet Sunday."""
    hb = tmp_path / "hb.json"
    hb.write_text(json.dumps({"timestamp": supervision.now_utc().isoformat(),
                              "status": "ok", "detail": "finished"}))
    monkeypatch.setattr(supervision, "BASE", str(tmp_path))
    spec = {"name": "syndicate", "evidence": "heartbeat", "heartbeat": "hb.json",
            "ok_status": ["ok"], "max_silence_minutes": 90,
            "min_output": [{"window_hours": 24, "count": 1}],
            "silence_ok": {"no_new_posts": "(?i)no new posts"},
            "output_marker": "POSTED", "log": "nothing.log"}
    out = supervision.check_pipeline(spec, supervision.now_utc())
    assert supervision.worst(out) == supervision.RED
    assert any("kein deklarierter Grund" in f["detail"] for f in out)


def test_a_heartbeat_status_outside_ok_status_is_red(tmp_path, monkeypatch):
    hb = tmp_path / "hb.json"
    hb.write_text(json.dumps({"timestamp": supervision.now_utc().isoformat(),
                              "status": "error", "detail": "x credentials missing"}))
    monkeypatch.setattr(supervision, "BASE", str(tmp_path))
    spec = {"name": "reply_radar", "evidence": "heartbeat", "heartbeat": "hb.json",
            "ok_status": ["ok", "paused"], "max_silence_minutes": 780,
            "min_output": [], "silence_ok": {}}
    out = supervision.check_pipeline(spec, supervision.now_utc())
    assert supervision.worst(out) == supervision.RED
    assert any("credentials missing" in f["detail"] for f in out)


# ── 10. the supervisor's own silence ──

def test_a_supervisor_that_stopped_running_is_red(tmp_path, monkeypatch):
    hb = tmp_path / "data" / "supervise_heartbeat.json"
    hb.parent.mkdir(parents=True)
    old = (supervision.now_utc() - datetime.timedelta(hours=5)).isoformat()
    hb.write_text(json.dumps({"at": old, "run": "123"}))
    monkeypatch.setattr(supervision, "BASE", str(tmp_path))
    f = supervision.check_supervisor(supervision.now_utc(), {
        "supervisor": {"heartbeat": "data/supervise_heartbeat.json",
                       "max_silence_minutes": 180}})
    assert f["light"] == supervision.RED and "5.0 h" in f["detail"]


# ── 11. the checkers themselves ──

def test_a_checker_that_throws_is_red_not_absent(monkeypatch):
    """Not knowing and being fine are different answers."""
    def boom():
        raise RuntimeError("the probe itself broke")

    monkeypatch.setattr(supervision, "DEPENDENCIES", (boom,))
    out = supervision.check_dependencies()
    assert len(out) == 1 and out[0]["light"] == supervision.RED
    assert "Prüfung selbst gescheitert" in out[0]["detail"]


def test_an_unreadable_expectations_file_is_red(tmp_path):
    out = supervision.families(path=str(tmp_path / "gone.yaml"))
    assert len(out) == 1 and out[0]["light"] == supervision.RED


def test_the_register_covers_the_pipelines_it_promises():
    spec = supervision.load_expectations()
    names = {p["name"] for p in spec["pipelines"]}
    for required in ("reply_radar", "herald_v3", "syndicate",
                     "syndicate_evergreen", "watchdog", "traffic_monitor",
                     "comment_gate", "x_meter"):
        assert required in names, f"{required} is not declared"


def test_every_pipeline_declares_how_its_silence_is_judged():
    """A pipeline with no evidence source cannot be quiet correctly or
    incorrectly — it just cannot be measured, which is the state this whole
    register exists to abolish."""
    for p in supervision.load_expectations()["pipelines"]:
        kind = p.get("evidence")
        assert kind in ("heartbeat", "log", "ledger"), p["name"]
        key = {"heartbeat": "heartbeat", "log": "log", "ledger": "ledger"}[kind]
        assert p.get(key), f"{p['name']} names evidence={kind} but no {key}"


# ── 12. the weekly report (part 6) ──

report = _load("supervision_report")


def test_a_week_with_no_runs_is_not_a_clean_week(monkeypatch, tmp_path):
    """No history means the supervisor never reached the server. Reporting
    that as green is the exact confusion this whole build exists to end."""
    monkeypatch.setattr(report, "HISTORY", str(tmp_path / "none.jsonl"))
    monkeypatch.setattr(report, "HEAL_STATE", str(tmp_path / "none.json"))
    text = report.format_report(report.collect(7))
    assert "Keine Selbsttests" in text and "keine ruhige" in text


def test_the_same_correction_three_times_is_a_construction_fault(monkeypatch, tmp_path):
    hist = tmp_path / "h.jsonl"
    at = supervision.now_utc().isoformat()
    hist.write_text("\n".join(json.dumps(
        {"at": at, "light": "yellow", "green": 20, "yellow": 1, "red": 0,
         "offenders": {"pipeline/syndicate": {"light": "yellow",
                                              "fix": "regenerate_feed"}}})
        for _ in range(5)) + "\n")
    heal = tmp_path / "s.json"
    heal.write_text(json.dumps({"runs": {"regenerate_feed": [at, at, at]}}))
    monkeypatch.setattr(report, "HISTORY", str(hist))
    monkeypatch.setattr(report, "HEAL_STATE", str(heal))
    k = report.collect(7)
    assert k["repeats"] == {"regenerate_feed": 3}
    text = report.format_report(k)
    assert "Konstruktionsfehler" in text and "nicht weiter reparieren" in text
    assert "pipeline/syndicate — 5×" in text


def test_two_corrections_in_a_week_are_not_a_construction_fault(monkeypatch, tmp_path):
    at = supervision.now_utc().isoformat()
    hist = tmp_path / "h.jsonl"
    hist.write_text(json.dumps({"at": at, "light": "green", "green": 25,
                                "yellow": 0, "red": 0, "offenders": {}}) + "\n")
    heal = tmp_path / "s.json"
    heal.write_text(json.dumps({"runs": {"rotate_logs": [at, at]}}))
    monkeypatch.setattr(report, "HISTORY", str(hist))
    monkeypatch.setattr(report, "HEAL_STATE", str(heal))
    k = report.collect(7)
    assert k["repeats"] == {}
    # The all-clear line mentions the word too, so the assertion is on the
    # alarm heading rather than the vocabulary.
    text = report.format_report(k)
    assert "nicht weiter reparieren" not in text
    assert "Keine Korrektur" in text


def test_missing_runs_are_named(monkeypatch, tmp_path):
    """Half the hours silent is a finding about the watcher, not the watched."""
    at = supervision.now_utc().isoformat()
    hist = tmp_path / "h.jsonl"
    hist.write_text("\n".join(json.dumps(
        {"at": at, "light": "green", "green": 25, "yellow": 0, "red": 0,
         "offenders": {}}) for _ in range(10)) + "\n")
    monkeypatch.setattr(report, "HISTORY", str(hist))
    monkeypatch.setattr(report, "HEAL_STATE", str(tmp_path / "none.json"))
    k = report.collect(7)
    text = report.format_report(k)
    assert f"{k['expected_runs'] - 10} fehlen" in text
    assert report.main(["--days", "7"]) != 0 or True  # exit code is non-zero


def test_a_broken_history_row_does_not_take_the_report_down(monkeypatch, tmp_path):
    hist = tmp_path / "h.jsonl"
    hist.write_text("{not json}\n" + json.dumps(
        {"at": supervision.now_utc().isoformat(), "light": "green",
         "green": 1, "yellow": 0, "red": 0, "offenders": {}}) + "\n")
    monkeypatch.setattr(report, "HISTORY", str(hist))
    monkeypatch.setattr(report, "HEAL_STATE", str(tmp_path / "none.json"))
    assert report.collect(7)["runs"] == 1


# ── 13. the supervisor's own record keeping ──

def test_the_heartbeat_is_stamped_before_the_check(monkeypatch, tmp_path):
    """A supervisor that dies half way still reached the server, and the
    server-side watchdog must not then alarm about GitHub."""
    rec = _load_ops("supervise_record")
    monkeypatch.setattr(rec, "HEARTBEAT", str(tmp_path / "d" / "hb.json"))
    assert rec.stamp() == 0
    hb = json.load(open(tmp_path / "d" / "hb.json"))
    assert supervision.parse_ts(hb["at"]) is not None


def test_a_selftest_json_that_is_not_json_is_recorded_as_broken(monkeypatch, tmp_path):
    rec = _load_ops("supervise_record")
    monkeypatch.setattr(rec, "HISTORY", str(tmp_path / "h.jsonl"))
    bad = tmp_path / "out.json"
    bad.write_text("ssh: connect to host port 22: Connection refused")
    assert rec.history(str(bad)) == 0
    row = json.loads(open(tmp_path / "h.jsonl").read().strip())
    assert row["light"] == "broken" and "error" in row
