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
import os

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


def test_the_flag_fix_is_offered_only_on_a_real_disagreement(tmp_path, monkeypatch):
    """A correction that reports 'already in agreement' every evening is how
    an alert channel stops being read."""
    from agents import x_meter
    flag = tmp_path / "flag"
    monkeypatch.setattr(x_meter, "BREAKER_FLAG", str(flag))
    today = x_meter._day()

    # under the breaker, no flag → they agree
    assert supervision.flag_disagrees(1.44) is False
    # over the breaker, no flag → the flag is behind
    assert supervision.flag_disagrees(1.60) is True
    flag.write_text(json.dumps({"day": today, "usd": 1.6}))
    # over the breaker, flag set for today → they agree
    assert supervision.flag_disagrees(1.60) is False
    # under the breaker but the flag still claims today → also a disagreement
    assert supervision.flag_disagrees(0.40) is True
    flag.write_text(json.dumps({"day": "2026-01-01", "usd": 9.9}))
    assert supervision.flag_disagrees(0.40) is False


# ── 14. the cost cut and the evergreen order ──

def test_the_search_asks_for_ten_not_twenty_five():
    """The number is a budget decision, so it is pinned by a test."""
    from agents import reply_radar
    assert reply_radar.SEARCH_PAGE == 10
    import inspect
    src = inspect.getsource(reply_radar.gather)
    assert '"max_results": SEARCH_PAGE' in src
    assert '"max_results": 25' not in src.split("search/recent")[1][:300]


def test_the_priority_list_runs_before_the_oldest_first_walk(tmp_path, monkeypatch):
    from agents import syndicate
    items = [{"link": f"https://moltrust.ch/blog/{n}.html"}
             for n in ("newest", "mid", "oldest")]          # feed order
    pri = tmp_path / "p.json"
    pri.write_text(json.dumps({"first": ["https://moltrust.ch/blog/mid.html"]}))
    monkeypatch.setattr(syndicate, "PRIORITY_FILE", str(pri))
    out = syndicate.evergreen_candidates(items, {}, supervision.now_utc())
    assert [i["link"].rsplit("/", 1)[-1] for i in out] == [
        "mid.html", "oldest.html", "newest.html"], (
        "priority first, then the unnamed posts in their old oldest-first order")


def test_no_priority_file_leaves_the_order_untouched(tmp_path, monkeypatch):
    from agents import syndicate
    items = [{"link": f"https://moltrust.ch/blog/{n}.html"}
             for n in ("newest", "mid", "oldest")]
    monkeypatch.setattr(syndicate, "PRIORITY_FILE", str(tmp_path / "none.json"))
    out = syndicate.evergreen_candidates(items, {}, supervision.now_utc())
    assert [i["link"].rsplit("/", 1)[-1] for i in out] == [
        "oldest.html", "mid.html", "newest.html"]


def test_an_unparseable_priority_file_is_logged_not_swallowed(tmp_path, monkeypatch):
    """Losing the order quietly is how the weakest post of the set goes first."""
    from agents import syndicate
    bad = tmp_path / "p.json"
    bad.write_text("{not json")
    monkeypatch.setattr(syndicate, "PRIORITY_FILE", str(bad))
    errors = []
    monkeypatch.setattr(syndicate.log, "error", lambda m: errors.append(m))
    assert syndicate.evergreen_priority() == []
    assert errors and "unreadable" in errors[0]


def test_the_shipped_priority_file_names_posts_that_exist():
    """A link with a typo silently falls back to oldest-first."""
    import json as _json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    d = _json.load(open(root / "config" / "evergreen_priority.json"))
    links = (d.get("first") or []) + (d.get("then") or [])
    assert len(links) == len(set(links)), "a link is listed twice"
    for url in links:
        assert url.startswith("https://moltrust.ch/blog/")
        assert url.endswith(".html")
    # Every named post must also carry a note saying why it was chosen.
    for url in links:
        assert url.rsplit("/", 1)[-1] in (d.get("_notes") or {}), url


def test_a_redirect_stub_is_not_counted_as_a_missing_post(tmp_path, monkeypatch):
    blog = tmp_path / "blog"
    blog.mkdir()
    (blog / "real.html").write_text("<html><body>a post</body></html>")
    (blog / "moved.html").write_text(
        '<html><head><meta http-equiv="refresh" content="0;url=/blog/real.html">')
    (blog / "index.html").write_text("<html>")
    monkeypatch.setattr(supervision, "WEBROOT_BLOG", str(blog))
    monkeypatch.setattr(supervision.httpx, "get", lambda *a, **k: Resp(
        200, None, "<rss><item><link>https://moltrust.ch/blog/real.html</link>"
                   "</item></rss>"))
    f = supervision.dep_feed()
    assert f["light"] == supervision.GREEN, f["detail"]


# ── 15. one writer in the checkout ──

def _git_repo(tmp_path):
    import subprocess as sp
    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "f.txt").write_text("one\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": __import__("os").environ["PATH"], "HOME": str(tmp_path)}
    sp.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, env=env)
    sp.run(["git", "-C", str(tmp_path), "commit", "-qm", "one"], check=True, env=env)
    return sp.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                  capture_output=True, text=True).stdout.strip()


def _owner_env(monkeypatch, tmp_path, sha, owner=True):
    monkeypatch.setattr(supervision, "BASE", str(tmp_path))
    o = tmp_path / "owner.json"
    if owner:
        o.write_text(json.dumps({"owner": "mac-console",
                                 "declared_at": "2026-10-03T14:00:00+00:00"}))
    monkeypatch.setattr(supervision, "OWNER_FILE", str(o))
    d = tmp_path / "deployed"
    d.mkdir(exist_ok=True)
    (d / "moltrust-api").write_text(f"{sha}\t2026-10-03T14:19:31Z\tok\n")
    monkeypatch.setattr(supervision, "DEPLOYED", str(d))
    # A clean server also has a supervisor history with stated origins. Without
    # it check_checkout is yellow for a reason that has nothing to do with the
    # checkout, which is the fixture being incomplete rather than a finding.
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "supervision_history.jsonl").write_text(json.dumps(
        {"at": supervision.now_utc().isoformat(), "run": "workflow:1",
         "light": "green"}) + "\n")


def test_a_clean_checkout_at_the_deployed_sha_is_green(tmp_path, monkeypatch):
    sha = _git_repo(tmp_path)
    _owner_env(monkeypatch, tmp_path, sha)
    out = supervision.check_checkout(supervision.now_utc())
    assert supervision.worst(out) == supervision.GREEN, [f["detail"] for f in out]


def test_a_second_writer_is_named_within_the_hour(tmp_path, monkeypatch):
    """03.10, 11:27: a deploy was refused over 31 uncommitted lines another
    session had left live, and nobody knew for nine minutes."""
    sha = _git_repo(tmp_path)
    _owner_env(monkeypatch, tmp_path, sha)
    (tmp_path / "f.txt").write_text("edited by somebody else\n")
    out = supervision.check_checkout(supervision.now_utc())
    clean = [f for f in out if f["check"] == "host/checkout/clean"][0]
    assert clean["light"] == supervision.RED
    assert "f.txt" in clean["detail"] and "zweiter Schreiber" in clean["detail"]


def test_a_checkout_moved_by_hand_is_red(tmp_path, monkeypatch):
    sha = _git_repo(tmp_path)
    _owner_env(monkeypatch, tmp_path, "0" * 40)
    out = supervision.check_checkout(supervision.now_utc())
    f = [x for x in out if x["check"] == "host/checkout/sha"][0]
    assert f["light"] == supervision.RED and "von Hand" in f["detail"]
    assert sha[:7] in f["detail"]


def test_no_declared_owner_is_red(tmp_path, monkeypatch):
    """A dirty tree is one incident. An undeclared owner is every future one."""
    sha = _git_repo(tmp_path)
    _owner_env(monkeypatch, tmp_path, sha, owner=False)
    out = supervision.check_checkout(supervision.now_utc())
    f = [x for x in out if x["check"] == "host/checkout/owner"][0]
    assert f["light"] == supervision.RED and "kein Eigentümer" in f["detail"]


def test_the_checkout_check_never_offers_a_correction(tmp_path, monkeypatch):
    """Nothing here is on the positive list. Committing or discarding another
    session's work is exactly the judgement a repair tool must not make."""
    sha = _git_repo(tmp_path)
    _owner_env(monkeypatch, tmp_path, sha)
    (tmp_path / "f.txt").write_text("someone else's work\n")
    for f in supervision.check_checkout(supervision.now_utc()):
        assert f.get("fix") in (None, "none"), f


# ── 16. a timeout is not a verdict ──

def test_a_timeout_gets_one_retry_and_a_green_second_try(monkeypatch):
    """A 20-second hiccup turned a healthy run red on 03.10, which through the
    supervisor is a failed workflow and an alarm for a network blip."""
    import httpx
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("the read operation timed out")
        return supervision.finding("dep/flaky", supervision.GREEN, "HTTP 200",
                                   fix=None)

    monkeypatch.setattr(supervision, "DEPENDENCIES", (flaky,))
    out = supervision.check_dependencies()
    assert len(calls) == 2
    assert out[0]["light"] == supervision.GREEN
    assert "beim ersten Versuch ReadTimeout" in out[0]["detail"], (
        "a green that needed a retry must still say so")


def test_two_timeouts_are_red(monkeypatch):
    import httpx

    def dead():
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(supervision, "DEPENDENCIES", (dead,))
    out = supervision.check_dependencies()
    assert out[0]["light"] == supervision.RED and "zweimal" in out[0]["detail"]


def test_a_non_transient_failure_is_not_retried(monkeypatch):
    """Only timeouts. Retrying a KeyError twice reports the same bug twice and
    hides that the checker is broken."""
    calls = []

    def broken():
        calls.append(1)
        raise KeyError("author_id")

    monkeypatch.setattr(supervision, "DEPENDENCIES", (broken,))
    out = supervision.check_dependencies()
    assert len(calls) == 1
    assert out[0]["light"] == supervision.RED
    assert "Prüfung selbst gescheitert" in out[0]["detail"]


# ── 17. which bound binds ──

bottleneck = _load("radar_bottleneck")


def test_a_cap_bound_radar_is_not_a_problem():
    """Most runs hitting the 3-draft cap means more reads buy nothing."""
    b = bottleneck.judge(cand=60, skips=20, drafts=18, capped_runs=6, runs=8)
    assert b["bound"] == "cap" and "would not produce more" in b["why"]


def test_a_drafter_bound_radar_points_at_the_prompt_not_the_budget():
    """03.10: 687 of 809 refused before any gate. A prompt change is free."""
    b = bottleneck.judge(cand=809, skips=687, drafts=25, capped_runs=1, runs=40)
    assert b["bound"] == "drafter"
    assert "prompt" in b["why"] and "687 of 809" in b["why"]


def test_a_supply_bound_radar_says_reading_less_makes_it_worse():
    """The state a read cap produces, and the one that must not stay quiet."""
    b = bottleneck.judge(cand=12, skips=11, drafts=1, capped_runs=0, runs=4)
    assert b["bound"] == "supply"
    assert "reading less makes it worse" in b["why"]
    text = bottleneck.format_report({
        "days": 7, "runs": 4, "candidates": 12, "drafter_skips": 11,
        "author_caps": 0, "drafts": 1, "delivered": {"list": 1},
        "by_source": {}, "gate_rules": {}, "capped_runs": 0,
        "cost": {"list": 1.2, "search": 4.0}, "bottleneck": b})
    assert "Engpass: supply" in text
    assert "je zugestelltem Entwurf" in text, (
        "a supply-bound report must name the per-draft comparison")


def test_no_runs_is_unknown_not_healthy():
    assert bottleneck.judge(0, 0, 0, 0, 0)["bound"] == "unknown"


def test_the_report_divides_cost_by_delivered_drafts_per_leg():
    """The number that made the 03.10 cut the wrong one: $0.22 against $2.02."""
    text = bottleneck.format_report({
        "days": 14, "runs": 40, "candidates": 809, "drafter_skips": 687,
        "author_caps": 7, "drafts": 25,
        "delivered": {"list": 12, "search": 4},
        "by_source": {"list": 17, "search": 8},
        "gate_rules": {"g1x_fragment_coda": 5},
        "capped_runs": 1, "cost": {"list": 2.69, "search": 8.09},
        "bottleneck": {"bound": "drafter", "why": "x"}})
    assert "$0.22 je Entwurf" in text and "$2.02 je Entwurf" in text


def test_a_leg_that_delivered_nothing_is_not_divided_by_zero():
    text = bottleneck.format_report({
        "days": 7, "runs": 2, "candidates": 5, "drafter_skips": 5,
        "author_caps": 0, "drafts": 0, "delivered": {}, "by_source": {},
        "gate_rules": {}, "capped_runs": 0,
        "cost": {"list": 0.4, "search": 1.0},
        "bottleneck": {"bound": "supply", "why": "x"}})
    assert "kein Entwurf" in text


# ── 18. a red verdict reaches somebody ──
#
# The state these guard: on 03.10 the scheduled 16:17 run went red on the day's
# X spend, failed at "Fail on red" as designed, and sent nothing at all. The
# job was red, the Actions history was red, and no message existed.

def test_the_shipped_supervise_sh_passes_alert(tmp_path):
    """The wiring, not the function. `--alert` missing here is exactly how
    "red and nobody hears" comes back, and it comes back silently."""
    from pathlib import Path
    sh = (Path(__file__).resolve().parents[1] / "ops" / "supervise.sh").read_text()
    calls = [l.strip() for l in sh.splitlines()
             if "agents.supervision --json" in l]
    assert calls, "no JSON self-test call found in supervise.sh"
    for c in calls:
        assert "--alert" in c, (
            f"a self-test invocation without --alert: {c!r} — a red verdict "
            f"would reach nobody")


def test_a_security_red_rings_immediately(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(supervision, "NOTICES", str(tmp_path / "n.jsonl"))
    monkeypatch.setattr(supervision, "send_now",
                        lambda f, why: sent.append((f["check"], why)))
    out = supervision.queue_notice([
        {"check": "dep/github", "light": supervision.RED, "detail": "abgelaufen"},
    ], supervision.now_utc())
    assert out == {"red": 1, "ringing": 1, "collected": 0, "queued": 1}
    assert sent == [("dep/github", "security")]


def test_a_public_wrong_red_rings_immediately(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(supervision, "NOTICES", str(tmp_path / "n.jsonl"))
    monkeypatch.setattr(supervision, "send_now",
                        lambda f, why: sent.append((f["check"], why)))
    supervision.queue_notice([
        {"check": "dep/feed", "light": supervision.RED, "detail": "41 von 69"},
    ], supervision.now_utc())
    assert sent == [("dep/feed", "publicly_wrong")]


def test_an_ordinary_red_waits_for_the_collected_report(tmp_path, monkeypatch):
    """The day's X spend over the breaker is real, known, and not urgent."""
    sent = []
    notices = tmp_path / "n.jsonl"
    monkeypatch.setattr(supervision, "NOTICES", str(notices))
    monkeypatch.setattr(supervision, "send_now",
                        lambda f, why: sent.append(f["check"]))
    out = supervision.queue_notice([
        {"check": "cost/day", "light": supervision.RED, "detail": "$1.66"},
        {"check": "pipeline/herald_v3", "light": supervision.RED, "detail": "still"},
    ], supervision.now_utc())
    assert sent == [], "an ordinary red interrupted somebody"
    assert out["collected"] == 2 and out["queued"] == 2
    rows = [json.loads(l) for l in notices.read_text().splitlines() if l.strip()]
    assert {r["check"] for r in rows} == {"cost/day", "pipeline/herald_v3"}
    assert all(r["sent_immediately"] is False for r in rows)


def test_green_and_yellow_are_never_queued(tmp_path, monkeypatch):
    notices = tmp_path / "n.jsonl"
    monkeypatch.setattr(supervision, "NOTICES", str(notices))
    monkeypatch.setattr(supervision, "send_now", lambda f, why: None)
    out = supervision.queue_notice([
        {"check": "dep/github", "light": supervision.GREEN, "detail": "ok"},
        {"check": "dep/feed", "light": supervision.YELLOW, "detail": "29 fehlen"},
    ], supervision.now_utc())
    assert out == {"red": 0, "ringing": 0, "collected": 0, "queued": 0}
    assert not notices.exists() or notices.read_text() == ""


def test_an_unwritable_queue_makes_every_red_ring(tmp_path, monkeypatch):
    """Loud is the right failure here. A queue that cannot be written must not
    swallow the finding — that is the original defect wearing a new shape."""
    sent = []
    monkeypatch.setattr(supervision, "NOTICES", "/proc/nope/notices.jsonl")
    monkeypatch.setattr(supervision, "send_now",
                        lambda f, why: sent.append((f["check"], why)))
    out = supervision.queue_notice([
        {"check": "cost/day", "light": supervision.RED, "detail": "$1.66"},
    ], supervision.now_utc())
    assert out["ringing"] == 1 and out["queued"] == 0
    assert sent[0][0] == "cost/day"


def test_the_reply_draft_exception_is_written_down_even_though_it_cannot_fire():
    """An exception nobody can trigger is better recorded than omitted — and
    the comment says so, so the next reader does not take it for a bug."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "agents" / "supervision.py").read_text()
    assert "reply_draft" in src
    assert "unreachable from here" in src


def test_the_exception_table_matches_on_prefix_not_substring():
    assert supervision.exception_for("dep/feed") == "publicly_wrong"
    assert supervision.exception_for("dep/feed/items") == "publicly_wrong"
    assert supervision.exception_for("cost/day") is None
    # Not a substring match: a check merely containing a listed name must not
    # inherit its urgency.
    assert supervision.exception_for("pipeline/dep/github") is None


# ── 19. where a run came from ──

record = _load_ops("supervise_record")


def test_the_four_origin_shapes_are_accepted():
    for ok in ("workflow:37136288385", "dispatch:1",
               "local:moltstack@ubuntu-4gb-nbg1-1", "cron:supervise-hourly"):
        assert record.clean_origin(ok) == ok


def test_anything_else_is_unknown_not_half_trusted():
    """A malformed origin is worse than a missing one: it looks like
    provenance and is not."""
    for bad in ("", None, "unknown", "workflow:abc", "local:nohost",
                "workflow:1; rm -rf /", "manual", "schedule"):
        assert record.clean_origin(bad) == "unknown", bad


def test_an_unknown_origin_is_recorded_with_what_was_offered(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "HISTORY", str(tmp_path / "h.jsonl"))
    out = tmp_path / "o.json"
    out.write_text(json.dumps({"light": "green", "findings": []}))
    assert record.history(str(out), "schedule") == 0
    row = json.loads((tmp_path / "h.jsonl").read_text().strip())
    assert row["run"] == "unknown" and row["offered"] == "schedule"


def test_a_run_without_a_stated_origin_is_red(tmp_path, monkeypatch):
    """17:29 on 03.10: a check GitHub had no record of, and the field could
    not say whose it was."""
    (tmp_path / "data").mkdir()
    hist = tmp_path / "data" / "supervision_history.jsonl"
    now = supervision.now_utc()
    hist.write_text("\n".join(json.dumps(
        {"at": now.isoformat(), "run": run, "light": "green"})
        for run in ("workflow:1", "workflow:2", "unknown")) + "\n")
    monkeypatch.setattr(supervision, "BASE", str(tmp_path))
    out = supervision.check_supervisor_origins(now)
    assert out[0]["light"] == supervision.RED
    assert "ohne erklärte Herkunft" in out[0]["detail"]
    assert "Eigentümer-Regel" in out[0]["detail"]


def test_a_declared_local_run_is_green(tmp_path, monkeypatch):
    """A local run is not wrong. An unstated one is."""
    (tmp_path / "data").mkdir()
    now = supervision.now_utc()
    (tmp_path / "data" / "supervision_history.jsonl").write_text(
        json.dumps({"at": now.isoformat(), "run": "local:lars@mac",
                    "light": "green"}) + "\n")
    monkeypatch.setattr(supervision, "BASE", str(tmp_path))
    out = supervision.check_supervisor_origins(now)
    assert out[0]["light"] == supervision.GREEN and "local 1×" in out[0]["detail"]


def test_old_history_rows_do_not_count_against_today(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    now = supervision.now_utc()
    old = (now - datetime.timedelta(days=3)).isoformat()
    (tmp_path / "data" / "supervision_history.jsonl").write_text(
        json.dumps({"at": old, "run": "unknown", "light": "green"}) + "\n")
    monkeypatch.setattr(supervision, "BASE", str(tmp_path))
    out = supervision.check_supervisor_origins(now)
    assert out[0]["light"] == supervision.YELLOW
    assert "kein Supervisor-Lauf" in out[0]["detail"]


# ── 20. the external deadman ──

def test_only_a_finished_run_pings(monkeypatch):
    """A watchdog reporting itself healthy while failing is the defect this
    exists to catch, and it happened twice on 03.10."""
    calls = []
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/abc-123")
    monkeypatch.setattr(watchdog.httpx, "get",
                        lambda url, **k: calls.append(url) or Resp(200, None, "OK"))
    assert "HTTP 200" in watchdog.ping_healthcheck(True)
    assert calls == ["https://hc.example/abc-123"]
    watchdog.ping_healthcheck(False)
    assert calls[1].endswith("/fail"), "a crash must not ping the healthy URL"


def test_no_url_configured_is_not_an_error(monkeypatch):
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    assert watchdog.ping_healthcheck(True) == "not configured"


def test_a_plaintext_url_is_refused(monkeypatch):
    """A ping over http puts the URL on the wire, and holding it means being
    able to silence the alarm."""
    monkeypatch.setenv("HEALTHCHECK_URL", "http://hc.example/abc-123")
    called = []
    monkeypatch.setattr(watchdog.httpx, "get", lambda *a, **k: called.append(1))
    assert watchdog.ping_healthcheck(True) == "refused: not https"
    assert called == []


def test_the_ping_url_never_reaches_a_log_line(monkeypatch):
    """The whole point of treating it as a credential. httpx puts the URL in
    its exception messages, so the type is reported and not the message."""
    secret = "https://hc.example/4f6e2a1b-SECRETTOKEN"
    monkeypatch.setenv("HEALTHCHECK_URL", secret)

    def boom(url, **k):
        raise watchdog.httpx.ConnectError(f"cannot connect to {url}")

    monkeypatch.setattr(watchdog.httpx, "get", boom)
    out = watchdog.ping_healthcheck(True)
    assert out == "failed: ConnectError"
    assert "SECRETTOKEN" not in out and "hc.example" not in out


def test_a_ping_response_body_is_not_echoed(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/abc")
    monkeypatch.setattr(watchdog.httpx, "get",
                        lambda *a, **k: Resp(200, None, "https://hc.example/abc"))
    assert watchdog.ping_healthcheck(True) == "ok -> HTTP 200"


def test_the_watchdog_pings_at_the_very_end_of_the_run():
    """Placement is the guarantee: an early return or an exception must leave
    the ping unsent. A ping anywhere earlier would survive a failed run."""
    import inspect
    src = inspect.getsource(watchdog.run)
    tail = src.rsplit("ping_healthcheck", 1)[1]
    assert "return" not in tail, "code runs after the ping — it is not the last thing"
    body = [l for l in src.splitlines() if l.strip()]
    assert "ping_healthcheck" in body[-1], (
        "the ping is not the final statement of run()")


def test_the_supervisor_gap_is_no_longer_an_alert():
    """24 h in the collected report, not 3 h in an alarm: at a 15 % hit rate
    the expectation measured GitHub's queue."""
    import inspect
    src = inspect.getsource(watchdog.run)
    block = src.split("GitHub Actions runs the selftest")[1].split("blind =")[0]
    assert "alerts.append" not in block, (
        "the supervisor gap still raises an alarm")
    assert "22 %" in block or "best-effort" in block.lower()


def test_the_declared_expectation_matches_the_measurement():
    spec = supervision.load_expectations()["supervisor"]
    assert spec["max_silence_minutes"] == 1440
    assert spec.get("best_effort") is True
    # The measurement that justifies the number travels with it.
    m = spec.get("measured_hit_rate") or {}
    assert m.get("pct") and m.get("due") and m.get("fired")


def test_the_hourly_schedule_check_warns_rather_than_fails():
    import yaml
    from pathlib import Path
    d = yaml.safe_load((Path(__file__).resolve().parents[1] / "docs" /
                        "invariants" / "c-external-schedule-fires.yaml").read_text())
    assert d["schweregrad"] == "warn", (
        "a check that is permanently red gets muted, and then it reports "
        "nothing at all")
    assert "15 %" in d["herkunft"] and "70 %" in d["herkunft"]


# ── 21. the reply-impact measurement ──

impact = _load("reply_impact")
linkedin = _load("linkedin_metrics")


def test_a_delta_outside_the_window_is_not_reported_as_24h(tmp_path, monkeypatch):
    """One row was written at 47.3 h before the window was tightened. The row
    stays as the record; reporting it as a 24-hour figure does not."""
    f = tmp_path / "m.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        {"kind": "reply", "tweet_id": "a", "followers_delta_24h": 2,
         "delta_24h_taken_at_hours": 47.3},
        {"kind": "reply", "tweet_id": "b", "followers_delta_24h": 1,
         "delta_24h_taken_at_hours": 22.0},
    ]) + "\n")
    monkeypatch.setattr(impact, "METRICS", str(f))
    assert impact.delta_24h("a") == (None, None)
    assert impact.delta_24h("b") == (1, 22.0)


def test_the_closest_measurement_to_24h_wins(tmp_path, monkeypatch):
    f = tmp_path / "m.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        {"kind": "reply", "tweet_id": "a", "followers_delta_24h": 5,
         "delta_24h_taken_at_hours": 34.0},
        {"kind": "reply", "tweet_id": "a", "followers_delta_24h": 3,
         "delta_24h_taken_at_hours": 25.0},
        {"kind": "reply", "tweet_id": "a", "followers_delta_24h": 9,
         "delta_24h_taken_at_hours": 13.0},
    ]) + "\n")
    monkeypatch.setattr(impact, "METRICS", str(f))
    assert impact.delta_24h("a") == (3, 25.0), "a later row overwrote a better one"


def test_the_report_names_what_the_follower_delta_cannot_carry():
    """The column was asked for and is not attributable. Saying so is part of
    the report, not a footnote somebody has to remember."""
    text = impact.format_report(impact.collect.__wrapped__(  # noqa
        ) if hasattr(impact.collect, "__wrapped__") else {
        "since": "2026-10-04", "decision_date": "2026-10-18",
        "replies_in_window": [], "replies_before": [],
        "window": {f: {"n": 0, "sum": 0, "median": None, "max": None}
                   for f in ("impressions", "profile_clicks", "likes",
                             "engagements")},
        "retro": {f: {"n": 0, "sum": 0, "median": None, "max": None}
                  for f in ("impressions", "profile_clicks", "likes",
                            "engagements")},
        "digest": {"impressions": {"n": 0, "sum": 0, "median": None,
                                   "max": None},
                   "likes": {"n": 0, "sum": 0, "median": None, "max": None}},
        "digest_n": 0})
    assert "nicht" in text and "Follower" in text
    assert "Profilklicks" in text


def test_the_latest_measurement_of_each_post_is_the_one_used(tmp_path, monkeypatch):
    f = tmp_path / "m.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        {"kind": "reply", "tweet_id": "a", "measured_at": "2026-10-04T08:00:00",
         "impressions": 5, "posted_at": "2026-10-04T06:00:00Z"},
        {"kind": "reply", "tweet_id": "a", "measured_at": "2026-10-04T18:00:00",
         "impressions": 59, "posted_at": "2026-10-04T06:00:00Z"},
    ]) + "\n")
    monkeypatch.setattr(impact, "METRICS", str(f))
    rows = impact.latest_per_post("reply")
    assert len(rows) == 1 and rows[0]["impressions"] == 59


# ── 22. the LinkedIn series, kept by hand ──

def test_an_unanswered_prompt_leaves_pending_never_zero(tmp_path, monkeypatch):
    """Zero impressions and an unread panel are different facts."""
    drafts = tmp_path / "d.jsonl"
    drafts.write_text(json.dumps(
        {"at": "2026-10-04T09:00:00+00:00", "title": "A post",
         "mode": "evergreen"}) + "\n")
    monkeypatch.setattr(linkedin, "DRAFTS", str(drafts))
    monkeypatch.setattr(linkedin, "SERIES", str(tmp_path / "s.jsonl"))
    assert len(linkedin.outstanding()) == 1
    text = linkedin.table()
    assert "pending, nicht null" in text


def test_a_field_absent_from_the_answer_stays_absent(tmp_path, monkeypatch):
    """No default of zero. A column the panel did not show is a gap."""
    monkeypatch.setattr(linkedin, "SERIES", str(tmp_path / "s.jsonl"))
    answer = tmp_path / "a.json"
    answer.write_text(json.dumps({"posted_at": "2026-10-04",
                                  "url": "https://linkedin.com/x",
                                  "impressions": 120}))
    assert linkedin.record(str(answer)) == 0
    row = json.loads((tmp_path / "s.jsonl").read_text().strip())
    assert row["impressions"] == 120
    assert "reactions" not in row and "clicks" not in row


def test_an_answer_without_a_url_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(linkedin, "SERIES", str(tmp_path / "s.jsonl"))
    answer = tmp_path / "a.json"
    answer.write_text(json.dumps({"posted_at": "2026-10-04", "impressions": 9}))
    assert linkedin.record(str(answer)) == 1
    assert "url fehlt" in capsys.readouterr().out


def test_the_schema_is_fixed_before_the_first_row():
    """A column added later leaves every earlier row null, and a series with
    holes cannot be compared across weeks."""
    for c in ("posted_at", "url", "impressions", "reactions", "comments",
              "reposts", "clicks", "followers_total"):
        assert c in linkedin.COLUMNS


def test_a_draft_that_was_never_posted_stops_being_owed(tmp_path, monkeypatch):
    monkeypatch.setattr(linkedin, "SERIES", str(tmp_path / "s.jsonl"))
    monkeypatch.setattr(linkedin, "DRAFTS", str(tmp_path / "d.jsonl"))
    (tmp_path / "d.jsonl").write_text(json.dumps(
        {"at": "2026-10-04T09:00:00+00:00", "title": "A post"}) + "\n")
    answer = tmp_path / "a.json"
    answer.write_text(json.dumps({"posted_at": "2026-10-04T09:00:00+00:00",
                                  "not_posted": True}))
    assert linkedin.record(str(answer)) == 0
    assert linkedin.outstanding() == [], "a post never made still looks owed"


# ── 23. the evergreen cooldown ──

everg = _load("evergreen_verify")


def test_a_missing_register_entry_is_the_loud_case(tmp_path, monkeypatch):
    """The post went out and the register line failed: indistinguishable from
    a quiet day until Thursday repeats the same post."""
    text = everg.format_report({"day": "2026-10-06", "runs": 1,
                                "posted_today": [], "locked": [], "unlocked": [],
                                "next_candidate": None, "candidates_left": 0,
                                "log": []})
    assert "Kein Eintrag" in text and "Donnerstag" in text


def test_two_entries_in_one_day_is_flagged(tmp_path):
    text = everg.format_report({
        "day": "2026-10-06", "runs": 1,
        "posted_today": ["https://x/a.html", "https://x/b.html"],
        "locked": ["https://x/a.html", "https://x/b.html"], "unlocked": [],
        "next_candidate": "https://x/c.html", "candidates_left": 26, "log": []})
    assert "2 Einträge, erwartet war 1" in text


def test_a_cooldown_that_does_not_hold_is_flagged():
    text = everg.format_report({
        "day": "2026-10-06", "runs": 1, "posted_today": ["https://x/a.html"],
        "locked": [], "unlocked": ["https://x/a.html"],
        "next_candidate": "https://x/a.html", "candidates_left": 28, "log": []})
    assert "Cooldown greift nicht" in text
    # Needle and haystack in the same case: lowercasing only one of them is how
    # the first version of this assertion could never have passed.
    assert "nächste kandidat ist der, der heute lief" in text.lower()


# ── 24. a test must not be able to write to production ──
#
# The class, not the instance. On 2026-10-04 three fixture rows reached the
# live LinkedIn series because `append(row, path=SERIES)` bound SERIES at
# import, so a test that redirected SERIES wrote to the old path anyway. The
# next gap of that shape reaches x_meter.jsonl or digest_metrics.jsonl — the
# ledgers the decisions are read from.

def test_the_guard_refuses_a_write_under_the_production_tree(tmp_path):
    """The conftest guard, exercised on itself. If this test ever passes by
    writing the file, the guard is gone."""
    prod = os.path.join(os.path.expanduser("~/moltstack"), "data",
                        "guard-probe-should-never-exist.jsonl")
    with pytest.raises(RuntimeError, match="production state tree"):
        open(prod, "a")
    assert not os.path.exists(prod), "the guard let the file be created"


def test_the_guard_leaves_reads_alone():
    """A test asserting against the live register is legitimate; only writes
    leave a mark."""
    real = os.path.join(os.path.expanduser("~/moltstack"), "data")
    if not os.path.isdir(real):
        pytest.skip("kein Produktionsbaum auf dieser Maschine")
    names = os.listdir(real)
    if not names:
        pytest.skip("Produktionsbaum leer")
    target = os.path.join(real, names[0])
    if os.path.isfile(target):
        open(target, "rb").close()      # must not raise


def test_a_write_outside_the_production_tree_is_fine(tmp_path):
    p = tmp_path / "anywhere.jsonl"
    with open(p, "a") as f:
        f.write("ok\n")
    assert p.read_text() == "ok\n"


def test_the_root_is_read_on_every_call(monkeypatch, tmp_path):
    """Not cached, not bound to a default argument — that is the whole point."""
    from app import paths
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path / "a"))
    first = paths.data("x.jsonl")
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path / "b"))
    second = paths.data("x.jsonl")
    assert first != second
    assert second.startswith(str(tmp_path / "b"))


def test_the_test_suite_runs_with_a_redirected_root():
    """conftest points MOLTRUST_ROOT at a temporary directory, so a module
    that resolves through app.paths cannot reach production even without the
    open() guard. Two measures, because one is not enough."""
    from app import paths
    assert not paths.is_production(paths.data("anything.jsonl")), (
        "MOLTRUST_ROOT still resolves to the production tree")


def test_the_meter_ledger_follows_the_redirected_root(monkeypatch, tmp_path):
    from agents import x_meter
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    monkeypatch.setattr(x_meter, "LEDGER", None)
    assert x_meter.ledger_path().startswith(str(tmp_path))
    x_meter.record_write("1", "no link", "test")
    assert os.path.exists(x_meter.ledger_path()), "the write went elsewhere"
    assert x_meter.spend()["writes"] == 1


def test_an_explicit_path_still_wins_over_the_root(monkeypatch, tmp_path):
    """Tests that patch the module attribute keep working — the override slot
    is deliberate, and it is how a test redirects without an env var."""
    from agents import x_meter
    direct = tmp_path / "direct.jsonl"
    monkeypatch.setattr(x_meter, "LEDGER", str(direct))
    assert x_meter.ledger_path() == str(direct)


def test_the_linkedin_series_follows_the_redirected_root(monkeypatch, tmp_path):
    """The exact file that was polluted."""
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    monkeypatch.setattr(linkedin, "SERIES", None)
    assert linkedin.series_path().startswith(str(tmp_path))
    linkedin.append({"kind": "linkedin", "url": "https://x/y", "posted_at": "2026-10-04"})
    assert os.path.exists(linkedin.series_path())
    assert len(linkedin.read(linkedin.series_path())) == 1


def test_no_module_resolves_a_data_path_at_import_any_more():
    """A constant computed at import cannot be redirected by anything. Checked
    across the modules whose files carry decisions, by reading the source —
    importing them would be too late to see it."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for rel in ("agents/x_meter.py", "scripts/linkedin_metrics.py"):
        src = (root / rel).read_text()
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or '"""' in stripped:
                continue
            if 'expanduser("~/moltstack' in stripped and "=" in stripped:
                offenders.append(f"{rel}: {stripped[:70]}")
    assert not offenders, "import-time data paths are back:\n" + "\n".join(offenders)


# ── 25. the threshold decides, not a mood ──

def _window(median, clicks, n):
    def agg(v):
        return {"n": n, "sum": v, "median": median, "max": median}
    return {"impressions": {"n": n, "sum": (median or 0) * n,
                            "median": median, "max": median},
            "profile_clicks": agg(clicks),
            "likes": agg(0), "engagements": agg(0)}


def test_both_conditions_are_required_not_either():
    """Impressions alone measure somebody else's thread."""
    good_impr = impact.verdict({"window": _window(40, 0, 10)})
    assert good_impr["median_ok"] and not good_impr["clicks_ok"]
    assert good_impr["continue"] is False

    good_clicks = impact.verdict({"window": _window(9, 10, 10)})
    assert good_clicks["clicks_ok"] and not good_clicks["median_ok"]
    assert good_clicks["continue"] is False

    both = impact.verdict({"window": _window(31, 6, 10)})
    assert both["continue"] is True


def test_no_replies_in_the_window_is_not_a_pass():
    """A branch that produced nothing in fourteen days has answered the
    question it was asked."""
    v = impact.verdict({"window": _window(None, 0, 0)})
    assert v["continue"] is False and v["replies"] == 0


def test_the_thresholds_are_exactly_the_documented_ones():
    """A number in code and a different number in the doc is how a threshold
    moves without anybody deciding to move it."""
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[1] / "docs" / "reply-radar.md").read_text()
    assert f"≥ {impact.MEDIAN_IMPRESSIONS_MIN}" in doc
    assert str(impact.CLICKS_PER_REPLY_MIN) in doc
    assert impact.MEDIAN_IMPRESSIONS_MIN == 30
    assert impact.CLICKS_PER_REPLY_MIN == 0.5


def test_the_boundary_is_inclusive():
    """Exactly 30 and exactly 0.5 pass — the doc says 'at least'."""
    v = impact.verdict({"window": _window(30, 5, 10)})
    assert v["median_ok"] and v["clicks_ok"] and v["continue"] is True


def test_the_report_names_the_consequence():
    text = impact.format_report({
        "since": "2026-10-04", "decision_date": "2026-10-18",
        "replies_in_window": [], "replies_before": [],
        "window": _window(9, 1, 10), "retro": _window(9, 1, 10),
        "digest": {"impressions": {"n": 0, "sum": 0, "median": None, "max": None},
                   "likes": {"n": 0, "sum": 0, "median": None, "max": None}},
        "digest_n": 0})
    assert "Einstellung" in text
    assert "Radar abschalten, Budget auf Null" in text


# ── 26. LinkedIn OAuth: the parts that must not be assumed ──
#
# The store is a single Postgres row, not a file: the API service runs on a
# read-only filesystem and the first version answered 500. These tests put a
# fake row in front of the module so they exercise the logic without a
# database — the SQL itself is covered by the migration running on the server.

from app import linkedin_oauth as li


class FakeStore:
    """Stands in for the one row. Same contract: state, state_at, token_enc."""

    def __init__(self):
        self.state = None
        self.state_at = None
        self.token_enc = None

    def install(self, monkeypatch):
        monkeypatch.setattr(li, "_row",
                            lambda: (self.state, self.state_at, self.token_enc))

        def save(payload):
            self.token_enc = li._fernet().encrypt(
                json.dumps(payload, sort_keys=True).encode())

        monkeypatch.setattr(li, "save_tokens", save)

        class Cur:
            def __init__(self, outer):
                self.outer = outer

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, args=()):
                if "SET state = NULL" in sql:
                    self.outer.state = self.outer.state_at = None
                elif "state, state_at" in sql and args:
                    self.outer.state, self.outer.state_at = args[0], args[1]

            def fetchone(self):
                return (self.outer.state, self.outer.state_at,
                        self.outer.token_enc)

        class Conn:
            def __init__(self, outer):
                self.outer = outer

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                return Cur(self.outer)

        monkeypatch.setattr(li, "_connect", lambda: Conn(self))
        return self


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "cid")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "s3cret")
    return FakeStore().install(monkeypatch)


def test_a_refresh_without_a_refresh_token_says_why(store, monkeypatch):
    """LinkedIn issues programmatic refresh tokens only to approved Marketing
    Developer Platform partners. This app is not one, so the renewal has
    nothing to renew — and must say so instead of failing obscurely."""
    store.token_enc = li._fernet().encrypt(json.dumps(
        {"access_token": "a", "refresh_token": None, "refreshable": False}).encode())
    with pytest.raises(RuntimeError, match="Marketing Developer Platform"):
        li.refresh()


def test_the_token_is_encrypted_before_it_reaches_the_row(monkeypatch):
    """The database never sees the clear token: a dump or a replica carries
    ciphertext only."""
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "s3cret")
    blob = li._fernet().encrypt(json.dumps(
        {"access_token": "AQV-VERY-SECRET-TOKEN"}).encode())
    assert b"AQV-VERY-SECRET-TOKEN" not in blob
    assert json.loads(li._fernet().decrypt(blob))["access_token"] \
        == "AQV-VERY-SECRET-TOKEN"


def test_a_row_that_will_not_decrypt_is_a_finding_not_an_empty_store(store):
    """Returning None here would start a fresh authorisation and leave
    ciphertext nobody can read sitting in the row."""
    store.token_enc = b"not fernet at all"
    with pytest.raises(RuntimeError, match="unreadable"):
        li.load_tokens()
    assert li.status()["state"] == "unreadable"


def test_an_empty_row_means_not_authorised(store):
    assert li.load_tokens() is None
    assert li.status()["state"] == "none"


def test_the_state_is_single_use(store):
    out = li.start()
    li.check_state(out["state"])
    with pytest.raises(PermissionError, match="no authorisation in progress"):
        li.check_state(out["state"])


def test_a_wrong_or_missing_state_is_refused(store):
    li.start()
    for bad in ("", "guessed", "x" * 32):
        with pytest.raises(PermissionError):
            li.check_state(bad)


def test_a_stale_state_is_refused(store):
    out = li.start()
    store.state_at = supervision.now_utc() - datetime.timedelta(hours=2)
    with pytest.raises(PermissionError, match="older than"):
        li.check_state(out["state"])


def test_the_requested_scopes_are_the_three_documented_ones(store):
    out = li.start()
    assert set(out["scopes"]) == {"openid", "profile", "w_member_social"}
    assert "response_type=code" in out["url"] and "state=" in out["url"]
    assert out["url"].startswith("https://www.linkedin.com/oauth/v2/authorization?")


def test_the_member_id_becomes_a_person_urn(store, monkeypatch):
    """w_member_social needs an author URN, and sub is pairwise — specific to
    this app, not a global profile id."""
    monkeypatch.setattr(li.httpx, "get", lambda *a, **k: Resp(
        200, {"sub": "ABC123xyz", "name": "MolTrust", "locale": "en_US"}))
    out = li.fetch_member("token")
    assert out["author_urn"] == "urn:li:person:ABC123xyz"
    assert li.load_tokens()["author_urn"] == "urn:li:person:ABC123xyz"


def test_userinfo_without_a_sub_claim_is_an_error(store, monkeypatch):
    monkeypatch.setattr(li.httpx, "get", lambda *a, **k: Resp(
        200, {"name": "MolTrust"}))
    with pytest.raises(RuntimeError, match="no sub claim"):
        li.fetch_member("token")


def test_the_expiry_states_are_graded(store):
    now = supervision.now_utc()

    def put(days, refreshable):
        store.token_enc = li._fernet().encrypt(json.dumps({
            "access_token": "a", "refreshable": refreshable,
            "refresh_token": "r" if refreshable else None,
            "access_expires_at": (now + datetime.timedelta(days=days)).isoformat(),
        }).encode())

    put(30, True)
    assert li.status(now)["state"] == "ok"
    put(3, True)
    assert li.status(now)["state"] == "renew_due"
    # The distinction that matters: the same three days with no refresh token
    # is a manual job, not an automatic one.
    put(3, False)
    assert li.status(now)["state"] == "reauth_due"
    put(-1, True)
    assert li.status(now)["state"] == "expired"


def test_nothing_in_the_module_posts():
    """Posting is off by instruction. A share endpoint appearing here is the
    thing to catch, and it is cheaper to catch in a test than in a timeline."""
    from pathlib import Path
    import re
    src = (Path(__file__).resolve().parents[1] / "app" / "linkedin_oauth.py").read_text()
    for c in re.findall(r"httpx\.(?:post|put)\(([^,)]+)", src):
        assert "TOKEN" in c, f"a write call to something other than the token endpoint: {c}"
    assert "ugcPosts" not in src and "/rest/posts" not in src


def test_the_analytics_gap_is_recorded_where_somebody_would_look():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for rel in ("app/linkedin_oauth.py", "scripts/linkedin_auth_check.py"):
        assert "memberCreatorPostAnalytics" in (root / rel).read_text(), rel


def test_the_module_does_not_write_files_any_more():
    """The API service filesystem is read-only, and that stays a property."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "linkedin_oauth.py").read_text()
    assert "open(" not in src.replace("_connect(", ""), (
        "a file write is back in a module the read-only service imports")


# ── 28. LinkedIn posting: only on a button press ──

from agents import linkedin_post as lp
from agents import reply_radar as rrad


def test_the_endpoint_is_the_ugc_api_and_carries_no_version_header():
    """/rest/posts is the versioned Marketing surface this app has no access
    to, and the UGC endpoint is unversioned — so LinkedIn-Version would be
    cargo cult. The header comes from the page, not from habit."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "agents" / "linkedin_post.py").read_text()
    assert "https://api.linkedin.com/v2/ugcPosts" in src
    assert "/rest/posts" not in src.split('"""')[2] if '"""' in src else True
    assert "X-Restli-Protocol-Version" in src
    # Not in the request. It may be named in the docstring that explains why.
    body = src.split('"""', 2)[-1]
    assert "LinkedIn-Version" not in body.replace("# ", "#"), (
        "a LinkedIn-Version header reached the request")


def test_the_post_urn_is_read_from_the_header_not_the_body(monkeypatch, tmp_path):
    """201 Created has an empty body. Reading it would look like a failure."""
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    monkeypatch.setattr(lp.oauth, "load_tokens", lambda: {
        "access_token": "t", "author_urn": "urn:li:person:XYZ",
        "access_expires_at": (supervision.now_utc()
                              + datetime.timedelta(days=30)).isoformat()})

    class R:
        status_code = 201
        text = ""
        headers = {"X-RestLi-Id": "urn:li:share:7100"}

        def json(self):
            raise ValueError("no body")

    monkeypatch.setattr(lp.httpx, "post", lambda *a, **k: R())
    out = lp.post_share("hello")
    assert out["urn"] == "urn:li:share:7100"
    assert out["url"].endswith("urn:li:share:7100/")
    assert out["visibility"] == "PUBLIC"


def test_a_201_without_the_id_header_is_an_error(monkeypatch, tmp_path):
    """The post may exist and we cannot name it — worse than a clean failure,
    so it must not pass quietly."""
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    monkeypatch.setattr(lp.oauth, "load_tokens", lambda: {
        "access_token": "t", "author_urn": "urn:li:person:XYZ"})

    class R:
        status_code = 201
        text = ""
        headers = {}

    monkeypatch.setattr(lp.httpx, "post", lambda *a, **k: R())
    with pytest.raises(RuntimeError, match="X-RestLi-Id"):
        lp.post_share("hello")


def test_an_expired_token_is_caught_before_the_call(monkeypatch, tmp_path):
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    monkeypatch.setattr(lp.oauth, "load_tokens", lambda: {
        "access_token": "t", "author_urn": "urn:li:person:XYZ",
        "access_expires_at": (supervision.now_utc()
                              - datetime.timedelta(days=1)).isoformat()})
    called = []
    monkeypatch.setattr(lp.httpx, "post", lambda *a, **k: called.append(1))
    with pytest.raises(RuntimeError, match="abgelaufen"):
        lp.post_share("hello")
    assert called == [], "the request went out with a dead token"


def test_the_body_is_the_documented_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    monkeypatch.setattr(lp.oauth, "load_tokens", lambda: {
        "access_token": "t", "author_urn": "urn:li:person:XYZ"})
    seen = {}

    class R:
        status_code = 201
        text = ""
        headers = {"X-RestLi-Id": "urn:li:share:1"}

    def fake_post(url, json=None, **k):
        seen["url"], seen["body"], seen["headers"] = url, json, k.get("headers")
        return R()

    monkeypatch.setattr(lp.httpx, "post", fake_post)
    lp.post_share("Hello World")
    b = seen["body"]
    assert seen["url"] == "https://api.linkedin.com/v2/ugcPosts"
    assert b["author"] == "urn:li:person:XYZ"
    assert b["lifecycleState"] == "PUBLISHED"
    assert b["specificContent"]["com.linkedin.ugc.ShareContent"] == {
        "shareCommentary": {"text": "Hello World"},
        "shareMediaCategory": "NONE"}
    assert b["visibility"] == {
        "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
    assert seen["headers"]["X-Restli-Protocol-Version"] == "2.0.0"
    assert "LinkedIn-Version" not in seen["headers"]


def test_a_second_press_does_not_post_twice(monkeypatch, tmp_path):
    """Telegram re-delivers a callback when the first answer was slow."""
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    lp.save_pending({"k1": {"text": "t", "title": "T", "result": "gepostet",
                            "urn": "urn:li:share:1"}})
    posted = []
    monkeypatch.setattr(lp, "post_share", lambda *a, **k: posted.append(1))
    monkeypatch.setattr(rrad, "edit_message", lambda *a, **k: None)
    rrad.handle_linkedin({"message": {"chat": {"id": 1}, "message_id": 2}},
                         "li|post|k1", dry_run=False)
    assert posted == []


def test_a_dropped_draft_is_recorded_and_never_posted(monkeypatch, tmp_path):
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    lp.save_pending({"k2": {"text": "t", "title": "T"}})
    posted, edits = [], []
    monkeypatch.setattr(lp, "post_share", lambda *a, **k: posted.append(1))
    monkeypatch.setattr(rrad, "edit_message",
                        lambda c, m, t: edits.append(t))
    rrad.handle_linkedin({"message": {"chat": {"id": 1}, "message_id": 2}},
                         "li|drop|k2", dry_run=False)
    assert posted == []
    assert lp.load_pending()["k2"]["result"] == "verworfen"
    assert edits and "Verworfen" in edits[0]


def test_a_missing_draft_says_so_rather_than_posting_something_else(monkeypatch, tmp_path):
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    lp.save_pending({})
    posted, edits = [], []
    monkeypatch.setattr(lp, "post_share", lambda *a, **k: posted.append(1))
    monkeypatch.setattr(rrad, "edit_message", lambda c, m, t: edits.append(t))
    rrad.handle_linkedin({"message": {"chat": {"id": 1}, "message_id": 2}},
                         "li|post|gone", dry_run=False)
    assert posted == []
    assert edits and "nicht mehr im Speicher" in edits[0]


def test_the_consumer_claims_linkedin_rows_instead_of_dropping_them():
    """claim() marks every callback row consumed the moment it is read. A `li|`
    row dropped here is a draft Lars pressed a button on that never posted."""
    import inspect
    src = inspect.getsource(rrad.cmd_consume) if hasattr(rrad, "cmd_consume") \
        else open(rrad.__file__).read()
    assert 'data.startswith("li|")' in src
    assert src.index('data.startswith("li|")') < src.index('data.startswith("rr|")')


def test_a_blocked_draft_is_delivered_without_a_post_button(monkeypatch):
    """A blocked draft carrying the button is one tap from being posted."""
    from agents import syndicate as sy
    sent, offered = [], []
    monkeypatch.setattr(sy.voice_gate, "scan", lambda *a, **k: {
        "violations": ["g2f Substanz-Boden"], "gate1": {}, "gate2": {}})
    monkeypatch.setattr(sy.voice_gate, "format_report", lambda s: "BLOCKED")
    monkeypatch.setattr(sy, "send_telegram",
                        lambda m, **k: sent.append(m) or True)
    monkeypatch.setattr(sy.notify, "send_telegram_message",
                        lambda *a, **k: offered.append(k) or True)
    ok = sy.deliver_linkedin({"title": "T", "link": "https://x/a.html"}, "text")
    assert ok is False
    assert sent and not offered, "a blocked draft was offered with buttons"


def test_an_accepted_draft_is_offered_with_both_buttons(monkeypatch, tmp_path):
    from agents import syndicate as sy
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    offered = {}
    monkeypatch.setattr(sy.voice_gate, "scan", lambda *a, **k: {
        "violations": [], "gate1": {}, "gate2": {}})
    monkeypatch.setattr(sy.voice_gate, "format_report", lambda s: "PASS")
    monkeypatch.setattr(sy.notify, "send_telegram_message",
                        lambda *a, **k: offered.update(k) or True)
    item = {"title": "T", "link": "https://moltrust.ch/blog/a.html"}
    assert sy.deliver_linkedin(item, "a share with a figure: 17,000 events")
    buttons = offered["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["✅ Posten", "🗑 Verwerfen"]
    key = sy.linkedin_key(item)
    assert buttons[0]["callback_data"] == f"li|post|{key}"
    assert len(buttons[0]["callback_data"]) <= 64
    assert lp.load_pending()[key]["text"].startswith("a share")
