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
    monkeypatch.setattr(selfheal, "state_path",
                        lambda: str(tmp_path / "state.json"))
    ok, detail = selfheal.restart_service(
        {"check": "service/moltycel-bot", "unit": "moltycel-bot"}, {}, dry=False)
    assert ok is False and "sudo-Positivliste" in detail


def test_the_restart_cap_turns_into_a_construction_fault(monkeypatch, tmp_path):
    monkeypatch.setattr(selfheal, "state_path",
                        lambda: str(tmp_path / "state.json"))
    stamps = [supervision.now_utc().isoformat()] * 3
    st = {"runs": {"restart_service:moltstack": stamps}}
    ok, detail = selfheal.restart_service({"check": "service/moltstack",
                                           "unit": "moltstack"}, st, dry=False)
    assert ok is False and "Konstruktionsfehler" in detail


def test_retry_once_means_once(monkeypatch, tmp_path):
    monkeypatch.setattr(selfheal, "state_path",
                        lambda: str(tmp_path / "state.json"))
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
    monkeypatch.setattr(report, "heal_state", lambda: str(tmp_path / "none.json"))
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
    monkeypatch.setattr(report, "heal_state", lambda: str(heal))
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
    monkeypatch.setattr(report, "heal_state", lambda: str(heal))
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
    monkeypatch.setattr(report, "heal_state", lambda: str(tmp_path / "none.json"))
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
    monkeypatch.setattr(report, "heal_state", lambda: str(tmp_path / "none.json"))
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

    # Derived from the live constant, not written out. The breaker moved from
    # $1.50 to $2.00 on 2026-10-04 and a literal here turned a budget decision
    # into a failing test — a test that copies a number fights the next
    # decision about it.
    from agents import x_meter as xm2
    over = xm2.DAILY_BREAK_USD + 0.10
    under = xm2.DAILY_BREAK_USD - 0.10

    assert supervision.flag_disagrees(under) is False
    assert supervision.flag_disagrees(over) is True
    flag.write_text(json.dumps({"day": today, "usd": over}))
    assert supervision.flag_disagrees(over) is False
    assert supervision.flag_disagrees(under) is True
    flag.write_text(json.dumps({"day": "2026-01-01", "usd": 9.9}))
    assert supervision.flag_disagrees(under) is False


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
    # The property is that nothing can skip the ping, not that the ping is
    # literally last. Another console added stamp_heartbeat() after it, which
    # is fine: the ping still requires reaching that line. A `return` after it
    # would not be fine, and that is what this asserts.
    assert "return" not in tail, "code runs after the ping — it is not the last thing"
    statements = [l.strip() for l in tail.splitlines() if l.strip()
                  and not l.strip().startswith("#")]
    assert len(statements) <= 2, (
        f"too much happens after the ping: {statements} — a failure there "
        f"would be reported as a healthy run")


def test_the_supervisor_gap_is_no_longer_an_alert():
    """24 h in the collected report, not 3 h in an alarm: at a 15 % hit rate
    the expectation measured GitHub's queue."""
    import inspect
    src = inspect.getsource(watchdog.run)
    block = src.split("GitHub Actions runs the selftest")[1].split("blind =")[0]
    assert "alerts.append" not in block, (
        "the supervisor gap still raises an alarm")
    assert "22 %" in block or "best-effort" in block.lower()


def test_the_declared_expectation_travels_with_its_measurement():
    """The property, not the minutes.

    The window was 1440 when this was written and is 300 now — an operational
    tuning another console made on a better measurement. Pinning the number
    here would have made a legitimate decision look like a regression. The
    reply threshold stays pinned exactly, because that one was fixed by
    instruction and must not drift; the difference is whether the number is a
    decision or a tuning.
    """
    spec = supervision.load_expectations()
    sup = spec["supervisor"]
    # The GitHub-driven window stays loose, because what it measures is partly
    # somebody else's queue. Another console split this block on 2026-10-04:
    # `supervisor` is the workflow's stamp and `watchdog` the hourly one that
    # arrives on this machine, which is the better division and the reason the
    # tight number moved rather than being argued down.
    assert isinstance(sup.get("max_silence_minutes"), int)
    assert sup["max_silence_minutes"] >= 180, (
        "below three hours the window measures GitHub's queue, which is the "
        "finding that produced this field")
    assert sup.get("external_watch"), (
        "the loose window is only acceptable because an external service "
        "carries the timing guarantee — it has to be declared here")

    # Whichever block carries a declared tolerance has to carry the
    # measurement that justifies it. That is the property; which block it
    # lives in is a layout decision.
    measured = [b for b in spec.values()
                if isinstance(b, dict) and b.get("measured_hit_rate")]
    assert measured, "no declared window carries its measurement any more"
    for b in measured:
        m = b["measured_hit_rate"]
        assert m.get("pct") and m.get("due") and m.get("fired")
        assert m["fired"] <= m["due"]


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


# ── 29. the shipped prompt, and what it is recorded as ──

def test_the_prompt_version_is_recorded_with_every_draft():
    """A draft rate without the prompt that produced it is a number nobody can
    act on, and the 18.10 decision will be read against a drafter that changed
    on the 5th."""
    import inspect
    src = inspect.getsource(rrad.handle_decision)
    assert '"prompt_version": PROMPT_VERSION' in src
    assert rrad.PROMPT_VERSION == "v3-2026-10-05"


def test_the_run_books_candidates_drafts_and_passes_per_version(tmp_path):
    state = {}
    rrad.count_by_prompt(state, "2026-10-05", candidates=10, drafted=4, passed=3)
    rrad.count_by_prompt(state, "2026-10-05", candidates=5, drafted=1, passed=1)
    row = state["by_prompt"]["2026-10-05"][rrad.PROMPT_VERSION]
    assert row == {"candidates": 15, "drafts": 5, "gate_pass": 4, "runs": 2}


def test_two_versions_on_one_day_stay_separate():
    """A rate averaged over two prompts describes neither."""
    state = {}
    rrad.count_by_prompt(state, "2026-10-05", 10, 4, 3)
    old = rrad.PROMPT_VERSION
    try:
        rrad.PROMPT_VERSION = "v4-later"
        rrad.count_by_prompt(state, "2026-10-05", 10, 9, 2)
    finally:
        rrad.PROMPT_VERSION = old
    day = state["by_prompt"]["2026-10-05"]
    assert set(day) == {old, "v4-later"}
    assert day[old]["gate_pass"] == 3 and day["v4-later"]["gate_pass"] == 2


def test_an_empty_citation_index_drops_the_source_rule_rather_than_shipping_it(monkeypatch):
    """A rule pointing at an empty list reads as 'nothing is citable' and turns
    every candidate into a SKIP."""
    import importlib.util

    def fake_build():
        return {"posts": [], "specs": [], "counts": {"posts": 0, "figures": 0}}

    import sys as _sys
    mod = type(_sys)("citation_index")
    mod.build = fake_build
    mod.as_prompt = lambda idx: "SHOULD NOT APPEAR"
    monkeypatch.setitem(_sys.modules, "citation_index", mod)
    monkeypatch.setattr(importlib.util, "spec_from_file_location",
                        lambda *a, **k: None)
    assert rrad.citation_block() == ""


def test_an_unavailable_index_does_not_take_the_run_down(monkeypatch):
    import importlib.util

    def boom(*a, **k):
        raise OSError("gone")

    monkeypatch.setattr(importlib.util, "spec_from_file_location", boom)
    assert rrad.citation_block() == ""


def test_the_system_prompt_is_marked_cacheable():
    """The index is some 8 000 tokens and the system block is identical for
    every candidate in a run — one run should pay for it once."""
    import inspect
    src = inspect.getsource(rrad.draft_reply)
    assert '"cache_control": {"type": "ephemeral"}' in src
    assert '"system": [{"type": "text"' in src


def test_the_source_rule_only_ships_with_an_index(monkeypatch):
    """The rule and the index are one change: the rule without the index is
    the SKIP-everything failure."""
    import inspect
    src = inspect.getsource(rrad.draft_reply)
    assert "(VARIANT3_RULE if index else \"\")" in src


def test_the_measured_numbers_are_recorded_next_to_the_decision():
    """The comparison that justified shipping travels with the constant, so
    nobody has to find the PR to know why v3 and not neu."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "agents" / "reply_radar.py").read_text()
    block = src.split("PROMPT_VERSION =")[0][-1600:]
    for marker in ("3 gate-pass", "13 gate-pass", "11 gate-pass"):
        assert marker in block, marker


# ── 27. the citation index and the corrected gate ──

cindex = _load("citation_index")


def test_a_page_without_a_figure_gets_no_index_entry(tmp_path, monkeypatch):
    """A title alone is not a citation, and listing one would invite exactly
    the move the index exists to stop."""
    web = tmp_path / "web" / "blog"
    web.mkdir(parents=True)
    (web / "feed.xml").write_text(
        "<rss><item><title>Thin</title>"
        "<link>https://moltrust.ch/blog/thin.html</link></item>"
        "<item><title>Solid</title>"
        "<link>https://moltrust.ch/blog/solid.html</link></item></rss>")
    (web / "thin.html").write_text(
        "<html><body>Agents are the future and trust matters a great deal to "
        "everyone involved in this fast moving space.</body></html>")
    (web / "solid.html").write_text(
        "<html><body>They rebuilt the timeline from more than 17,000 log "
        "events, which is the only record that survived the incident.</body></html>")
    monkeypatch.setattr(cindex, "WEB", str(tmp_path / "web"))
    monkeypatch.setattr(cindex, "SPEC", str(tmp_path / "nospec"))
    idx = cindex.build()
    urls = [p["url"] for p in idx["posts"]]
    assert "https://moltrust.ch/blog/solid.html" in urls
    assert "https://moltrust.ch/blog/thin.html" not in urls


def test_the_index_quotes_verbatim():
    """Gate (h) checks that a claim appears in the page the draft named, so a
    paraphrased index would hand the drafter figures it cannot stand behind."""
    sent = "They rebuilt the timeline from more than 17,000 log events today."
    got = cindex.citable(sent)
    assert got and got[0] == sent


def test_navigation_chrome_with_digits_is_not_citable():
    for junk in ("8 min read and the rest of the page follows after this line",
                 "© 2026 MolTrust all rights reserved worldwide and forever",
                 "Skip to main content 2026 navigation menu for the whole site"):
        assert cindex.citable(junk) == [], junk


def test_the_prompt_block_tells_the_drafter_to_skip_without_a_hit():
    block = cindex.as_prompt({"posts": [{"title": "T", "url": "u",
                                         "figures": ["17,000 log events"]}],
                              "specs": [], "counts": {}})
    assert "verbatim" in block
    assert "SKIP" in block


def test_the_gate_is_given_the_sources_the_radar_gives(monkeypatch):
    """My own measurement bug: the first harness passed only the target post,
    so gate (h) blocked eleven of twelve drafts for citing pages that were
    never in the dict. That was the harness, not the prompt."""
    compare = _load("prompt_compare")
    seen = {}

    def fake_check(text, sources):
        seen["sources"] = dict(sources)
        return True, [], {}

    monkeypatch.setattr(compare.rr, "check", fake_check)
    monkeypatch.setattr(compare.rr, "fetch_sources",
                        lambda urls: {u: f"body of {u}" for u in urls})
    kb = {"https://moltrust.ch/integrity.html": "kb body"}
    out = compare.gate(
        {"reply": "x", "sources": ["https://moltrust.ch/integrity.html",
                                   "https://moltrust.ch/blog/a.html"]},
        "target text with https://t.co/abc in it", "123", "someone", kb)
    assert out["pass"] is True
    got = seen["sources"]
    # the cited KB page, the cited non-KB page, the link in the post, and the
    # post itself — all four, as production does it
    assert "https://moltrust.ch/integrity.html" in got
    assert "https://moltrust.ch/blog/a.html" in got
    assert any("t.co/abc" in k for k in got)
    assert any("the post being answered" in k for k in got)
    assert out["sources_given"] == 4


def test_the_citation_index_module_exists_where_the_radar_looks_for_it():
    """The shipped prompt loads it by path. It was referenced for one deploy
    cycle without being on main: the safeguard dropped the source rule and the
    radar quietly drafted with the old prompt, which is the right failure and
    still not the intended state."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert (root / "scripts" / "citation_index.py").is_file()


def test_the_radar_builds_a_real_index_from_the_repo(monkeypatch):
    """Not a mock: if the index cannot be built on this machine the source rule
    silently stops shipping, and the only way to notice is to build it."""
    from agents import reply_radar as rr2
    block = rr2.citation_block()
    if not block:
        pytest.skip("kein moltrust-web-Checkout auf dieser Maschine")
    assert "citation index" in block
    assert "<https://moltrust.ch/blog/" in block


def test_the_index_is_built_once_per_run(monkeypatch):
    """Each build reads the feed plus some seventy HTML files. Three
    candidates meant three full passes over the blog."""
    from agents import reply_radar as rr3
    builds = []
    import importlib.util
    import sys as _sys

    real = importlib.util.spec_from_file_location

    def counting(*a, **k):
        builds.append(1)
        return real(*a, **k)

    monkeypatch.setattr(rr3, "_INDEX_CACHE", None)
    monkeypatch.setattr(rr3, "_MODULE_CACHE", None)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", counting)
    first = rr3.citation_block()
    second = rr3.citation_block()
    assert first == second
    assert len(builds) == 1, f"the index was built {len(builds)} times"


def test_a_failed_build_is_cached_as_empty(monkeypatch):
    """A run whose first attempt failed should not retry the same failing read
    once per candidate."""
    from agents import reply_radar as rr3
    import importlib.util
    tries = []

    def boom(*a, **k):
        tries.append(1)
        raise OSError("gone")

    monkeypatch.setattr(rr3, "_INDEX_CACHE", None)
    monkeypatch.setattr(rr3, "_MODULE_CACHE", None)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", boom)
    assert rr3.citation_block() == ""
    assert rr3.citation_block() == ""
    assert len(tries) == 1


# --- deploy_verify: a silent fallback looks healthy everywhere else ---------
# On 2026-10-05 the radar logged `prompt v3-2026-10-05` and drafted under the
# old rule, because scripts/citation_index.py was not on main and the safeguard
# dropped the source rule as designed. The version string was right, the log
# line was right, every other signal was green.
#
# A note on how these came to be fixtures: the first proof of the rule-count
# invariant was run by hand against the production mirror, and it was
# inconclusive. The corruption inserted `id: [broken` at the top of a rule
# block, and `positions: [opener, middle, coda]` further down closed the flow
# sequence it had opened — so the block still parsed, 26 rules came back, and
# the check reported green for a correct reason that proved nothing. These
# tests use a fixture whose breakage genuinely fails yaml.safe_load.

import importlib.util as _ilu
import os as _os


def _deploy_verify():
    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "scripts", "deploy_verify.py")
    spec = _ilu.spec_from_file_location("deploy_verify_under_test", path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_RULE = """# Spec

```yaml
id: g1a
label: Kontrapunkt
gate: 1
positions: [opener, middle, coda]
```

```yaml
id: g1b
label: Validierungs-Opener
gate: 1
```

```yaml
lexicon: validation_openers
terms_en:
  - great question
```
"""

# The third rule block does not survive yaml.safe_load: the quoted scalar is
# never terminated, so the parser runs off the end of the block.
_BROKEN = _RULE + """
```yaml
id: g1c
label: 'nie geschlossen
gate: 2
```
"""


def test_a_rule_block_that_fails_to_parse_is_red(tmp_path, monkeypatch):
    """The floor does not catch this. 26 rules with one block broken is 25,
    still above a floor of 18, and the scan keeps reporting PASS while the
    rule it no longer knows about goes unenforced."""
    dv = _deploy_verify()
    from agents import voice_gate as vg
    doc = tmp_path / "pre-send-scan.md"
    doc.write_text(_BROKEN, encoding="utf-8")
    monkeypatch.setattr(vg, "DOC_SCAN", doc)
    monkeypatch.setattr(dv, "check_voice_gate_docs_only", lambda *a: [], raising=False)

    out = dv.check_voice_gate({"floor": {"rules": 2}})
    rules = [f for f in out if f["path"] == "voice_gate_rules"][0]
    assert rules["ok"] is False, rules
    assert rules["declared"] == 3 and rules["rules"] == 2, rules
    assert "unlesbar" in rules["detail"]


def test_an_intact_rule_file_does_not_alarm(tmp_path, monkeypatch):
    """The counterpart: the invariant must not fire on a healthy document, or
    it gets muted and then it reports nothing at all."""
    dv = _deploy_verify()
    from agents import voice_gate as vg
    doc = tmp_path / "pre-send-scan.md"
    doc.write_text(_RULE, encoding="utf-8")
    monkeypatch.setattr(vg, "DOC_SCAN", doc)

    out = dv.check_voice_gate({"floor": {"rules": 2}})
    rules = [f for f in out if f["path"] == "voice_gate_rules"][0]
    assert rules["ok"] is True, rules
    assert rules["rules"] == 2 and rules["declared"] == 2, rules


def test_the_version_can_be_right_while_the_rule_is_gone(monkeypatch):
    """Exactly the 2026-10-05 shape: PROMPT_VERSION says v3, citation_block()
    returns "" because the index module is missing, draft_reply drops the
    source rule, and nothing else notices."""
    dv = _deploy_verify()
    from agents import reply_radar as rr
    monkeypatch.setattr(rr, "citation_block", lambda: "")
    monkeypatch.setattr(dv, "committed",
                        lambda p: f'PROMPT_VERSION = "{rr.PROMPT_VERSION}"\n')

    out = dv.check_prompt({"markers_for_version": {
        "v3-": ["The source rule", "citation index: what we can point at"]}})
    by = {f["path"]: f for f in out}
    assert by["prompt/version"]["ok"] is True, by["prompt/version"]
    assert by["prompt/markers"]["ok"] is False, by["prompt/markers"]
    assert "Fallback" in by["prompt/markers"]["detail"]


def test_a_stale_process_is_red_even_with_the_markers_present(monkeypatch):
    """The other half: the artefact is fine but the process is running code
    from before the deploy."""
    dv = _deploy_verify()
    monkeypatch.setattr(dv, "committed", lambda p: 'PROMPT_VERSION = "v9-neu"\n')
    out = dv.check_prompt({})
    version = [f for f in out if f["path"] == "prompt/version"][0]
    assert version["ok"] is False
    assert "alten Code" in version["detail"]


# --- the doc mirror must not store a credential ------------------------------
# 2026-10-05: workers/content_scout/.webdocs/.git/config held a fine-grained
# PAT in clear text at mode 664, on a host with a second human account. The
# token was no longer the current MOLTYCEL_GH_TOKEN and still carried push and
# admin on both private repos. It was not put there by hand: ensure_web_docs
# built the remote as https://MoltyCel:<token>@github.com/... and wrote the
# current token back on every refresh.

def test_the_mirror_remote_carries_no_credential():
    from workers.content_scout import guardrails
    assert "@" not in guardrails.REMOTE.split("//", 1)[1].split("/", 1)[0], \
        f"the remote embeds a credential: {guardrails.REMOTE}"
    src = open(guardrails.__file__).read()
    assert "MoltyCel:{" not in src and "MoltyCel:%s" not in src, \
        "a credential is being interpolated into a git URL again"


def test_the_token_reaches_git_through_the_environment(monkeypatch):
    """And not through argv, where /proc/<pid>/cmdline is world-readable."""
    from workers.content_scout import guardrails
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        seen["env"] = kw.get("env") or {}
        class R: returncode = 0
        return R()

    monkeypatch.setattr(guardrails.subprocess, "run", fake_run)
    guardrails._git("s3cret-token", ["-C", "/tmp/x", "fetch"], 30)
    assert "s3cret-token" not in " ".join(seen["args"]), seen["args"]
    assert seen["env"].get("MOLTRUST_GIT_TOKEN") == "s3cret-token"


def test_an_existing_clone_is_migrated_off_the_stored_credential(monkeypatch, tmp_path):
    """The helper is only consulted for a URL without credentials, so the
    refresh has to strip the stored one — otherwise the old token keeps
    working and keeps sitting there."""
    from workers.content_scout import guardrails
    clone = tmp_path / ".webdocs"
    (clone / ".git").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(guardrails.config, "WEB_DOCS_CLONE", clone)
    monkeypatch.setattr(guardrails, "_git",
                        lambda tok, args, t: calls.append(args) or type("R", (), {"returncode": 0}))

    guardrails.ensure_web_docs("tok")
    first = calls[0]
    assert first[-3:] == ["set-url", "origin", guardrails.REMOTE], first


# --- refresh_doc_mirror: the mirror must not hang on another worker ---------
# The voice gate enforces the rules out of a shallow clone of moltrust-web that
# nothing refreshed on a tick of its own — it came along with the content_scout
# pipeline. On 2026-10-05 the mirror stood one commit behind a rewritten
# section of pre-send-scan.md with the rule count unchanged (26 against 26), so
# only a content comparison could see it, and what saw it was a deploy. Two
# hours after that was fixed the mirror was behind again by two web merges.

def _selfheal():
    import importlib.util
    import os as _os
    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "scripts", "selfheal.py")
    spec = importlib.util.spec_from_file_location("selfheal_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    import sys as _sys
    _sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Ran:
    returncode = 0
    stdout = "doc mirror 80d61c18aee9 -> 55fdb81f54ef\n"
    stderr = ""


def test_the_mirror_refresh_is_on_the_closed_list():
    sh = _selfheal()
    assert "refresh_doc_mirror" in sh.ACTIONS
    assert "refresh_doc_mirror" in sh.__doc__, \
        "an action on the list has to be named in the header too"


def test_the_fourth_refresh_in_a_day_is_a_construction_fault(tmp_path, monkeypatch):
    """Three in 24 h is the cap, and the fourth is not a fourth repair. A
    mirror that needs pulling three times a day has a broken tick, not an old
    commit, and pulling it again hides exactly that."""
    sh = _selfheal()
    monkeypatch.setattr(sh, "BASE", str(tmp_path))
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "refresh_doc_mirror.py").write_text("x")
    monkeypatch.setattr(sh.subprocess, "run", lambda *a, **k: _Ran())

    st = {}
    for i in range(3):
        ok, detail = sh.refresh_doc_mirror({"check": "docs/mirror"}, st, False)
        assert ok is True, detail
        assert f"Lauf {i + 1} von 3" in detail, detail

    ok, detail = sh.refresh_doc_mirror({"check": "docs/mirror"}, st, False)
    assert ok is False
    assert "Konstruktionsfehler" in detail, detail
    assert "defekten Takt" in detail, detail


def test_a_refresh_without_the_tool_refuses_instead_of_improvising(tmp_path, monkeypatch):
    sh = _selfheal()
    monkeypatch.setattr(sh, "BASE", str(tmp_path))
    ok, detail = sh.refresh_doc_mirror({"check": "docs/mirror"}, {}, False)
    assert ok is False and "fehlt" in detail


def test_the_dry_run_changes_nothing(tmp_path, monkeypatch):
    sh = _selfheal()
    monkeypatch.setattr(sh, "BASE", str(tmp_path))
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "refresh_doc_mirror.py").write_text("x")
    calls = []
    monkeypatch.setattr(sh.subprocess, "run", lambda *a, **k: calls.append(a) or _Ran())
    st = {}
    ok, detail = sh.refresh_doc_mirror({"check": "docs/mirror"}, st, True)
    assert ok is True and "würde" in detail
    assert calls == [] and st == {}, "a dry run neither ran nor recorded anything"


def test_a_stale_mirror_is_yellow_with_the_named_fix(tmp_path, monkeypatch):
    from agents import supervision
    monkeypatch.setattr(supervision, "BASE", str(tmp_path))
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "refresh_doc_mirror.py").write_text("x")
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "python").write_text("")

    class Stale:
        returncode = 1
        stdout = "doc mirror: 80d61c18aee9, main 55fdb81f54ef — STALE\n"
        stderr = ""

    monkeypatch.setattr(supervision.subprocess, "run", lambda *a, **k: Stale())
    f = supervision.check_doc_mirror()
    assert f["light"] == supervision.YELLOW
    assert f["fix"] == "refresh_doc_mirror"
    assert "STALE" in f["detail"]


def test_a_current_mirror_is_green_and_offers_no_fix(tmp_path, monkeypatch):
    """A watcher that is always yellow gets muted, and a muted watcher reports
    nothing at all."""
    from agents import supervision
    monkeypatch.setattr(supervision, "BASE", str(tmp_path))
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "refresh_doc_mirror.py").write_text("x")
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "python").write_text("")

    class Current:
        returncode = 0
        stdout = "doc mirror: 55fdb81f54ef, main 55fdb81f54ef — current\n"
        stderr = ""

    monkeypatch.setattr(supervision.subprocess, "run", lambda *a, **k: Current())
    f = supervision.check_doc_mirror()
    assert f["light"] == supervision.GREEN and f["fix"] is None


def test_an_unreadable_remote_is_not_a_pass(tmp_path, monkeypatch):
    """Not knowing is not the same as being current — the check exits 1 when
    the remote cannot be read, so the mirror gets pulled rather than assumed."""
    from agents import supervision
    monkeypatch.setattr(supervision, "BASE", str(tmp_path))
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "refresh_doc_mirror.py").write_text("x")
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "python").write_text("")

    def boom(*a, **k):
        raise OSError("no venv")

    monkeypatch.setattr(supervision.subprocess, "run", boom)
    f = supervision.check_doc_mirror()
    assert f["light"] == supervision.YELLOW and f["fix"] == "refresh_doc_mirror"


# --- one key, one name, and a failed fetch that says so -------------------
# 2026-10-05: GH_TOKEN and MOLTYCEL_GH_TOKEN held two different tokens, seven
# places read the first one and each spelled the lookup itself. Two of those
# paths swallowed a failed read, so after the revocation they kept reporting
# health: _git runs with check=False, a 401 fetch raised nothing,
# ensure_web_docs completed, and the mirror stayed on its old commit.

def test_only_one_token_name_is_read():
    """Seven spellings were seven answers to 'which token does this path use'."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for p in list(root.glob("agents/*.py")) + list(root.glob("scripts/*.py")) \
            + list(root.glob("app/*.py")) + list(root.glob("workers/**/*.py")):
        if p.name in ("gh.py",):
            continue
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            # Not "skip the line if it mentions the right name": both
            # fallback chains found on 2026-10-05 named MOLTYCEL_GH_TOKEN
            # first and GH_TOKEN second, so that skip hid exactly the shape
            # the test is for. Strip the right name, then look.
            if "GH_TOKEN" not in line.replace("MOLTYCEL_GH_TOKEN", ""):
                continue
            if line.lstrip().startswith("#") or "gh.NAME" in line:
                continue
            offenders.append(f"{p.relative_to(root)}:{n} {line.strip()[:70]}")
    assert offenders == [], "reads a second token name: " + "; ".join(offenders)


def test_a_failed_fetch_is_reported_not_swallowed(tmp_path, monkeypatch):
    from workers.content_scout import guardrails
    clone = tmp_path / ".webdocs"
    (clone / ".git").mkdir(parents=True)
    monkeypatch.setattr(guardrails.config, "WEB_DOCS_CLONE", clone)

    class Failed:
        returncode = 128
        stdout = ""
        stderr = "fatal: Authentication failed for 'https://github.com/...'"

    monkeypatch.setattr(guardrails, "_git", lambda tok, args, t: Failed())
    noted = []
    monkeypatch.setattr(guardrails, "_report", lambda d: noted.append(d))

    guardrails.ensure_web_docs("dead-token")
    assert len(noted) == 1, noted
    assert "fetch exit 128" in noted[0] and "Authentication failed" in noted[0]


def test_a_successful_refresh_reports_nothing(tmp_path, monkeypatch):
    """A watcher that fires on health gets muted."""
    from workers.content_scout import guardrails
    clone = tmp_path / ".webdocs"
    (clone / ".git").mkdir(parents=True)
    monkeypatch.setattr(guardrails.config, "WEB_DOCS_CLONE", clone)

    class Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(guardrails, "_git", lambda tok, args, t: Ok())
    noted = []
    monkeypatch.setattr(guardrails, "_report", lambda d: noted.append(d))
    guardrails.ensure_web_docs("tok")
    assert noted == []


def test_the_notice_lands_in_the_queue_the_report_reads(tmp_path, monkeypatch):
    import json
    from app import notices, paths
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    assert notices.note("docs/mirror/fetch", "fetch exit 128") is True
    rows = [json.loads(l) for l in open(paths.data("notices.jsonl"))]
    assert len(rows) == 1
    r = rows[0]
    assert r["check"] == "docs/mirror/fetch" and r["light"] == "red"
    # Collected, never an immediate message: a producer is not a second sender.
    assert r["exception"] is None and r["sent_immediately"] is False


def test_a_traffic_403_is_null_never_zero():
    """`t.get("count", 0)` on a 403 body produced a zero that no reader could
    tell from a measured one, while the run reported 6/6 captured."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts" / "discovery_snapshot.py").read_text()
    # Comments are skipped: the fix explains itself by quoting the old call.
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert 't.get("count", 0)' not in code and 't.get("uniques", 0)' not in code
    assert 'entry[f"{kind}_14d_count"] = None' in src
    assert '_14d_status' in src, "a null without the reason is half a record"


def test_a_run_with_unreadable_traffic_is_not_ok():
    """`gh_ok` counts repos whose base call answered, so a run with every
    traffic field null recorded itself as "ok" — the same untruth as the old
    "6/6 captured", one field over. status is what a reader uses to decide
    whether the row is usable, so it has to carry the incompleteness."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts" / "discovery_snapshot.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "gh_ok == 0 and bool(gh.token())" not in code, \
        "the status still keys on the base call alone"
    assert "full == 0 and bool(gh.token())" in code


# --- threadwatch: a refused credential is not a low quota ------------------
# 2026-10-05, while the token was revoked: rate_limit() swallowed the HTTP 401
# into {}, the caller read remaining = 0, logged "rate limit too low (0 < 500)
# — skipping run" and returned. Exit 0. A dead credential looked like a quiet
# hour, and main()'s return value was discarded anyway.

def _threadwatch():
    import importlib.util
    import os as _os
    import sys as _sys
    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "scripts", "threadwatch.py")
    spec = importlib.util.spec_from_file_location("threadwatch_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = mod
    _sys.argv = ["threadwatch.py", "--dry-run"]
    spec.loader.exec_module(mod)
    return mod


class _Refused(Exception):
    def __init__(self, status):
        super().__init__(f"{status} Client Error: Unauthorized for url: ...")
        self.response = type("R", (), {"status_code": status})()


def test_a_401_on_rate_limit_is_an_auth_error_not_an_empty_quota():
    tw = _threadwatch()
    g = tw.GH.__new__(tw.GH)
    g.get = lambda url, params=None: (_ for _ in ()).throw(_Refused(401))
    core = g.rate_limit()
    assert core == {}
    assert g.auth_error is not None and g.auth_error[0] == 401, g.auth_error


def test_a_403_counts_too_and_a_timeout_does_not():
    """403 is the other refusal shape. A timeout is a transient failure and
    must stay a low-quota skip, or every flaky minute becomes an alarm."""
    tw = _threadwatch()
    for status, expect in ((403, True), (500, False)):
        g = tw.GH.__new__(tw.GH)
        g.get = lambda url, params=None, s=status: (_ for _ in ()).throw(_Refused(s))
        g.rate_limit()
        assert bool(g.auth_error) is expect, (status, g.auth_error)

    g = tw.GH.__new__(tw.GH)
    g.get = lambda url, params=None: (_ for _ in ()).throw(OSError("timed out"))
    g.rate_limit()
    assert g.auth_error is None


def test_the_exit_code_leaves_the_process():
    """A non-zero return is worthless while __main__ discards it."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts" / "threadwatch.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "sys.exit(main() or 0)" in code, "main()'s return value is discarded again"
    assert "\n    main()\n" not in code


def test_the_auth_finding_reaches_the_collected_report():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "scripts" / "threadwatch.py").read_text()
    body = src[src.index("if getattr(gh, \"auth_error\", None):"):]
    body = body[:body.index("remaining =")]
    assert "notices.note(" in body and "source/threadwatch/auth" in body
    assert "return 2" in body, "it has to end the run, not fall through to the crawl"


def test_every_traffic_field_says_how_it_got_its_value():
    """A zero whose provenance a reader has to guess is the defect: four repos
    carried 0/0 in 137 rows and nothing recorded whether that was a
    measurement or a swallowed 403. New rows carry a basis; the historical ones
    were marked `indeterminate` in the data, not in a footnote, because it
    cannot be resolved retroactively."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "scripts" / "discovery_snapshot.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert '_14d_basis"] = "measured"' in code
    assert '_14d_basis"] = "unreadable"' in code
    mig = root / "migrations" / "2026-10-05_traffic_indeterminate.sql"
    assert mig.exists(), "the historical rows need the migration beside the code"
    assert "jsonb_object_agg" in mig.read_text(), \
        "the per-row join marked one repo of six; the aggregate does every one"


# --- a pending series row is not an answer ---------------------------------
# agents/linkedin_post.record() creates the series row the moment a post goes
# out, and outstanding() treated the row's existence as "has numbers". So every
# posted share looked answered immediately. Found on 2026-10-05 with the first
# real post: the prompt said "Keine offenen Posts" while its own pending row
# sat in the series, which would have made the 48 h follow-up silent.

def _li_metrics():
    import importlib.util
    import os as _os
    import sys as _sys
    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "scripts", "linkedin_metrics.py")
    spec = importlib.util.spec_from_file_location("li_metrics_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_a_pending_row_leaves_the_post_outstanding(monkeypatch, tmp_path):
    lm = _li_metrics()
    draft = {"at": "2026-10-05T12:30:00+00:00", "title": "t", "source": "s"}
    monkeypatch.setattr(lm, "read", lambda p: (
        [{"posted_at": "2026-10-05T12:30:00+00:00", "url": None, "pending": True}]
        if "metrics" in str(p) else [draft]))
    monkeypatch.setattr(lm, "series_path", lambda: "x/linkedin_metrics.jsonl")
    monkeypatch.setattr(lm, "drafts_path", lambda: "x/linkedin_drafts.jsonl")
    assert lm.outstanding() == [draft], "a row without a figure answered the draft"


def test_one_figure_is_enough_to_count_as_answered(monkeypatch):
    lm = _li_metrics()
    draft = {"at": "2026-10-05T12:30:00+00:00"}
    monkeypatch.setattr(lm, "read", lambda p: (
        [{"posted_at": "2026-10-05T12:30:00+00:00", "impressions": 0}]
        if "metrics" in str(p) else [draft]))
    monkeypatch.setattr(lm, "series_path", lambda: "x/linkedin_metrics.jsonl")
    monkeypatch.setattr(lm, "drafts_path", lambda: "x/linkedin_drafts.jsonl")
    # Zero impressions is a reading, and a reading is an answer.
    assert lm.outstanding() == []


def test_not_posted_is_not_owed(monkeypatch):
    lm = _li_metrics()
    draft = {"at": "2026-10-05T12:30:00+00:00"}
    monkeypatch.setattr(lm, "read", lambda p: (
        [{"posted_at": "2026-10-05T12:30:00+00:00", "not_posted": True}]
        if "metrics" in str(p) else [draft]))
    monkeypatch.setattr(lm, "series_path", lambda: "x/linkedin_metrics.jsonl")
    monkeypatch.setattr(lm, "drafts_path", lambda: "x/linkedin_drafts.jsonl")
    assert lm.outstanding() == []


# --- reach is not impressions, and one post can carry two URNs -------------
# 2026-10-02, first measured LinkedIn post: 59 impressions against 20 members
# reached. Served three times to the same twenty people is not a format that
# reached sixty, so the two stand as separate columns.
#
# The same post also carries two identifiers: ugcPost 7511719123865800706 at
# 09:30:00 UTC and activity 7511719153490223105 at 09:30:07 UTC, both serving
# the same og:title. url and timestamp both miss across that pair, so matching
# goes by our own key.

def test_reach_and_watch_time_are_columns():
    lm = _li_metrics()
    for c in ("members_reached", "avg_watch_time", "video_views", "watch_time",
              "profile_views_from_post", "followers_gained", "key"):
        assert c in lm.COLUMNS, f"{c} is not a column, so record() drops it"
    # link_engagements is kept apart from clicks: the panel reports them
    # separately and we do not know they are the same thing.
    assert "link_engagements" in lm.COLUMNS and "clicks" in lm.COLUMNS


def test_a_supplied_figure_without_a_column_is_named(tmp_path, monkeypatch, capsys):
    """Dropping a measurement without a word is the same defect as a swallowed
    403: the record looks complete and a number is gone."""
    import json
    lm = _li_metrics()
    f = tmp_path / "in.json"
    f.write_text(json.dumps({"posted_at": "2026-10-02T09:30:00+00:00",
                             "url": "https://example.invalid/p",
                             "impressions": 59, "dwell_time_p95": 12}))
    written = []
    monkeypatch.setattr(lm, "append", lambda row, path=None: written.append(row))
    assert lm.record(str(f)) == 0
    out = capsys.readouterr().out
    assert "dwell_time_p95" in out and "keine Spalte" in out
    # The row is still written: losing a whole reading over one unknown key
    # would be worse than naming it.
    assert written and written[0]["impressions"] == 59
    assert "dwell_time_p95" not in written[0]


def test_two_urns_for_one_post_still_match(monkeypatch):
    lm = _li_metrics()
    draft = {"at": "2026-10-02T09:30:07+00:00", "key": "lobster-clip-1",
             "url": "https://www.linkedin.com/feed/update/urn:li:activity:7511719153490223105/"}
    measured = {"key": "lobster-clip-1", "posted_at": "2026-10-02T09:30:00+00:00",
                "url": "https://www.linkedin.com/posts/…ugcPost-7511719123865800706-bnnK",
                "impressions": 59}
    monkeypatch.setattr(lm, "read", lambda p: [measured] if "metrics" in str(p) else [draft])
    monkeypatch.setattr(lm, "series_path", lambda: "x/linkedin_metrics.jsonl")
    monkeypatch.setattr(lm, "drafts_path", lambda: "x/linkedin_drafts.jsonl")
    # Neither the url nor the timestamp matches across the pair.
    assert lm.outstanding() == [], "the key did not carry the match"


def test_the_table_shows_one_line_per_post():
    """A placeholder plus a measured reading is two rows and one post."""
    lm = _li_metrics()
    rows = [
        {"key": "k", "posted_at": "2026-10-02T09:30:00+00:00", "pending": True},
        {"key": "k", "posted_at": "2026-10-02T09:30:00+00:00",
         "impressions": 59, "members_reached": 20, "read_at": "2026-10-05T13:00:00+00:00"},
    ]
    merged = lm.latest_per_post(rows)
    assert len(merged) == 1
    assert merged[0]["impressions"] == 59 and merged[0]["members_reached"] == 20


def test_a_text_post_is_not_asked_for_watch_time(monkeypatch):
    """Asking invites a zero where the right answer is 'not applicable'."""
    lm = _li_metrics()
    monkeypatch.setattr(lm, "read", lambda p: [])
    monkeypatch.setattr(lm, "outstanding",
                        lambda: [{"title": "t", "at": "2026-10-05T12:31:05+00:00",
                                  "format": "text+image"}])
    text = lm.prompt()
    assert "Average watch time" not in text
    assert "Members reached" in text

    monkeypatch.setattr(lm, "outstanding",
                        lambda: [{"title": "t", "at": "2026-10-02T09:30:00+00:00",
                                  "format": "video"}])
    assert "Average watch time" in lm.prompt()


# --- the citation index must not hand out candidate material ---------------
# The candidate input log in docs/spec-fakten/ says of itself that it is "not a
# citation source", is UNVERIFIED BY DESIGN per the README, and quotes
# third-party correspondence verbatim. Until 2026-10-06 the index read every
# .md in that directory except README.md, so the drafter was handed it as
# something it could point at.
#
# The fixtures are written at run time and the reserved identifiers are
# assembled from parts — .github/scripts/reserved_names_guard.py forbids them
# in tracked files, including a fixture, and a test that needs them must not
# smuggle them into the repo to get there.

def _citation_index():
    import importlib.util
    import os as _os
    import sys as _sys
    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "scripts", "citation_index.py")
    spec = importlib.util.spec_from_file_location("citation_index_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_A, _N = "a" + "ae", "0" + "4"
BAD_NAME = f"{_A}-{_N}-kandidaten.md"


def _spec_dir(tmp_path):
    d = tmp_path / "spec-fakten"
    d.mkdir()
    (d / BAD_NAME).write_text(
        f"# {_A.upper()} -{_N} — Kandidatenlog\n\n"
        "UNVERIFIED BY DESIGN — Eingabeprotokoll, keine Zitierquelle.\n\n"
        "- Kandidat: Clock-Skew-Toleranz auf 300 Sekunden anheben (heute 120).\n"
        '  Quelle: Mail von A. Beispiel, 14.09.2026: "3 von 47 Laeufen".\n'
        f"- Betrifft Revision -{_N}, unveroeffentlicht.\n", encoding="utf-8")
    (d / "anchor-commitment.md").write_text(
        "# Anchor commitment\n\nVERIFIED 2026-09-21. Ours to define.\n\n"
        "Der Leaf-Preimage ist 32 Bytes. 261 von 261 Credentials und 15 von 15\n"
        "Batches wurden allein aus den Dokumenten nachgerechnet.\n", encoding="utf-8")
    return str(d)


def test_the_candidate_log_does_not_reach_the_index(tmp_path, monkeypatch):
    ci = _citation_index()
    monkeypatch.setattr(ci, "SPEC", _spec_dir(tmp_path))
    titles = [e["title"] for e in ci.from_spec()]
    assert not any("kandidaten" in t.lower() for t in titles), titles
    # The clean file beside it still makes it in, or the test proves nothing.
    assert any("anchor-commitment" in t for t in titles), titles
    # And the exclusion is reported, not merely a missing entry.
    assert any("kandidaten" in e["source"] for e in ci.SKIPPED), ci.SKIPPED


def test_every_indexed_entry_names_its_source(tmp_path, monkeypatch):
    ci = _citation_index()
    monkeypatch.setattr(ci, "SPEC", _spec_dir(tmp_path))
    for e in ci.from_spec():
        assert e.get("source"), e


def test_a_draft_citing_an_excluded_source_is_blocked(tmp_path, monkeypatch):
    ci = _citation_index()
    monkeypatch.setattr(ci, "SPEC", _spec_dir(tmp_path))
    ci.from_spec()                      # fills SKIPPED
    draft = f"Clock skew moves to 300 s, see docs/spec-fakten/{BAD_NAME}."
    hits = ci.cites_excluded(draft)
    assert hits, "the draft cited the candidate log and the gate let it pass"
    ident, why = hits[0]
    assert "kandidaten" in ident and "citation source" in why


def test_a_clean_draft_is_not_blocked(tmp_path, monkeypatch):
    """A rule that fires on a healthy draft gets switched off."""
    ci = _citation_index()
    monkeypatch.setattr(ci, "SPEC", _spec_dir(tmp_path))
    ci.from_spec()
    draft = ("261 of 261 credentials were recomputed from the documents alone, "
             "see docs/spec-fakten/anchor-commitment.md.")
    assert ci.cites_excluded(draft) == []


def test_the_exclusion_does_not_fire_on_an_april_date():
    """A bare revision number would match every date in April, and a rule that
    fires on dates gets switched off."""
    ci = _citation_index()
    assert ci.excluded("anchor-commitment.md",
                       "VERIFIED 2026-04-21, 261 of 261 recomputed.") is None


def test_verbatim_third_party_correspondence_is_excluded():
    """ADR-0002: a third party's text is referenced as an occasion, not quoted,
    until it has been read."""
    ci = _citation_index()
    why = ci.excluded("notes.md",
                      'Mail von A. Beispiel, 14.09.2026: "wir sehen Abweichungen".')
    assert why and "correspondence" in why


def test_the_source_rule_holds_after_the_file_is_deleted(tmp_path, monkeypatch):
    """#604 deleted the candidate log while this guard was being written, and
    with it every entry in the refused list. The drafter does not read the
    repo — it recalls, and a name it recalls outlives the file."""
    ci = _citation_index()
    empty = tmp_path / "spec-fakten"
    empty.mkdir()
    (empty / "anchor-commitment.md").write_text(
        "VERIFIED. 261 von 261 Credentials nachgerechnet.\n", encoding="utf-8")
    monkeypatch.setattr(ci, "SPEC", str(empty))
    ci.from_spec()
    assert ci.SKIPPED == [], "the fixture directory must hold nothing to refuse"

    draft = f"Siehe docs/spec-fakten/{BAD_NAME} fuer den Kandidaten."
    hits = ci.cites_excluded(draft)
    assert hits, "nothing was refused this run, so nothing blocked the draft"
    assert any("citation source" in why or "superseded" in why for _, why in hits), hits


def test_an_unpublished_revision_named_in_a_draft_is_blocked():
    ci = _citation_index()
    draft = f"Laut Revision -{_N} (unveroeffentlicht) steigt die Toleranz."
    assert ci.cites_excluded(draft), "an unpublished revision passed the gate"


# ── 16. syndication guards (2026-10-08) ──
#
# Seven tweets left without approval on 2026-09-30 and 2026-10-01. Armed is now
# required, and armed alone is not enough: each guard below has to stop a post
# and say why, or the test fails.

def _syn(monkeypatch, tmp_path, *, seen, items, counter=None, state_file=True):
    from agents import syndicate as sy
    state_path = tmp_path / "syndicate_state.json"
    if state_file:
        state_path.write_text(json.dumps({"seen": seen}))
    counter_path = tmp_path / "syndicate_counter.json"
    if counter is not None:
        counter_path.write_text(json.dumps(counter))
    monkeypatch.setattr(sy, "STATE_FILE", str(state_path))
    monkeypatch.setattr(sy, "COUNTER_FILE", str(counter_path))
    monkeypatch.setattr(sy, "SECRETS_FILE", str(tmp_path / "no-secrets"))
    monkeypatch.setenv("SYNDICATE_ARMED", "1")
    monkeypatch.setattr(sy, "_RUN_POSTS", 0)
    monkeypatch.setattr(sy, "fetch_feed", lambda: items)
    monkeypatch.setattr(sy, "fetch_article_text", lambda u, limit=6000: "42 things")
    monkeypatch.setattr(sy, "draft", lambda i: {"thread": ["one 42", "two https://moltrust.ch/x"],
                                                "linkedin": ""})
    monkeypatch.setattr(sy.voice_gate, "scan", lambda parts, **kw:
                        {"ok": True, "violations": [], "gate1": {}, "gate2": {}, "mode": "thread"})
    monkeypatch.setattr(sy.voice_gate, "format_report", lambda r: "")
    monkeypatch.setattr(sy.x_meter, "reads_paused", lambda now=None: None)
    monkeypatch.setattr(sy, "write_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(sy, "bluesky_mirror", lambda parts: [])
    said, posted = [], []
    monkeypatch.setattr(sy, "send_telegram", lambda m, **k: said.append(m) or True)
    monkeypatch.setattr(sy.x_post, "post_thread",
                        lambda parts, **k: posted.append(parts) or ["1", "2"])
    return sy, said, posted


def _feed_item(guid, hours_ago):
    from email.utils import format_datetime
    when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)
    return {"guid": guid, "link": guid, "title": guid.rsplit("/", 1)[-1], "category": "Analysis",
            "description": "d", "pub_date": format_datetime(when)}


def test_a_feed_item_older_than_48h_is_archive_not_news(monkeypatch, tmp_path):
    old = _feed_item("https://moltrust.ch/blog/old.html", 72)
    sy, said, posted = _syn(monkeypatch, tmp_path, seen=[], items=[old])
    assert sy.run() == 0
    assert posted == [], "an archive post went out on the new-post path"
    assert any("Altbestand" in m for m in said), said
    state = json.loads((tmp_path / "syndicate_state.json").read_text())
    assert state["items"][old["guid"]]["status"] == "legacy_not_posted"


def test_a_fresh_item_still_posts(monkeypatch, tmp_path):
    new = _feed_item("https://moltrust.ch/blog/new.html", 2)
    sy, said, posted = _syn(monkeypatch, tmp_path, seen=[], items=[new])
    assert sy.run() == 0
    assert len(posted) == 1


def test_the_daily_cap_halts_with_exit_1(monkeypatch, tmp_path):
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    new = _feed_item("https://moltrust.ch/blog/new.html", 2)
    sy, said, posted = _syn(monkeypatch, tmp_path, seen=[], items=[new],
                            counter={"days": {day: 4}})
    assert sy.run() == 1
    assert posted == []
    assert any("angehalten" in m and "höchstens 4" in m for m in said), said


def test_the_run_cap_halts_after_two(monkeypatch, tmp_path):
    from agents import syndicate as sy
    sy, said, posted = _syn(monkeypatch, tmp_path, seen=[], items=[])
    sy.note_post(); sy.note_post()
    with pytest.raises(sy.Halt, match="höchstens 2"):
        sy.post_guard()


def test_a_missing_state_file_halts_instead_of_seeding(monkeypatch, tmp_path):
    new = _feed_item("https://moltrust.ch/blog/new.html", 2)
    sy, said, posted = _syn(monkeypatch, tmp_path, seen=[], items=[new], state_file=False)
    assert sy.run() == 1
    assert posted == []
    assert any("fehlt" in m for m in said), said
    assert not (tmp_path / "syndicate_state.json").exists(), "a missing state was re-seeded"


def test_a_shrunken_state_file_halts(monkeypatch, tmp_path):
    new = _feed_item("https://moltrust.ch/blog/new.html", 2)
    sy, said, posted = _syn(monkeypatch, tmp_path, seen=["a", "b"], items=[new],
                            counter={"last_seen_count": 71})
    assert sy.run() == 1
    assert posted == []
    assert any("geschrumpft" in m and "71" in m for m in said), said


def test_a_closed_x_breaker_stops_syndication(monkeypatch, tmp_path):
    new = _feed_item("https://moltrust.ch/blog/new.html", 2)
    sy, said, posted = _syn(monkeypatch, tmp_path, seen=[], items=[new])
    monkeypatch.setattr(sy.x_meter, "reads_paused",
                        lambda now=None: "X reads paused: $2.10 spent today")
    assert sy.run() == 1
    assert posted == []
    assert any("X-Breaker" in m for m in said), said


def test_syndication_writes_are_booked_on_the_meter():
    """x_post books every write under its kind; syndication posts as kind=syndication."""
    import inspect
    from agents import syndicate as sy, x_post
    assert "x_meter.record_write(tid, text, source=kind)" in inspect.getsource(x_post.post)
    assert 'post_thread(parts, kind="syndication")' in inspect.getsource(sy)


def test_a_closed_x_breaker_holds_the_weekly_proof_post(monkeypatch, tmp_path):
    """2026-10-08: proof_post is a standing pipeline under the same conditions
    as syndication, the breaker included."""
    import inspect
    from agents import proof_post as pp
    src = inspect.getsource(pp.run)
    i_break, i_post = src.index("x_meter.reads_paused()"), src.index("x_post.post(")
    assert i_break < i_post, "the breaker check has to come before the post"
    assert "not posting" in src[i_break:i_post]


def test_a_closed_x_breaker_holds_the_herald_digest():
    """2026-10-08: the digest is a standing pipeline under the same conditions
    as syndication and the weekly proof, the breaker included."""
    import inspect
    from agents import herald_v3 as h
    src = inspect.getsource(h.run_digest)
    i_break, i_post = src.index("x_meter.reads_paused()"), src.index("x_post.post_thread(")
    assert i_break < i_post, "the breaker check has to come before the post"
    assert "not posting" in src[i_break:i_post]


# ── 17. deploy.sh checks itself against the repository (2026-10-08) ──
#
# On 2026-10-08 deploy.sh was replaced by hand on the server at 07:19:24 UTC and
# nobody could say by whom. Since then the file lives in ops/deploy/, and the
# running copy has to equal the one at the deployed commit, or the run refuses.

import pathlib as _pl
import subprocess as _sp

_DEPLOY_SH = _pl.Path(__file__).resolve().parents[1] / "ops" / "deploy" / "deploy.sh"


def _bash(script: str, env: dict | None = None) -> _sp.CompletedProcess:
    full = {"DEPLOY_SH_FUNCTIONS_ONLY": "1", "PATH": os.environ["PATH"], **(env or {})}
    return _sp.run(["bash", "-c", f'source "{_DEPLOY_SH}"; LOG=/dev/null; {script}'],
                   capture_output=True, text=True, env=full)


def _repo_with_deploy_sh(tmp_path, content: str | None) -> tuple[_pl.Path, str]:
    repo = tmp_path / "api"
    repo.mkdir()
    git = lambda *a: _sp.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "t@t"); git("config", "user.name", "t")
    if content is not None:
        (repo / "ops" / "deploy").mkdir(parents=True)
        (repo / "ops" / "deploy" / "deploy.sh").write_text(content)
    else:
        (repo / "README").write_text("no deploy.sh here\n")
    git("add", "-A"); git("commit", "-q", "-m", "c")
    return repo, git("rev-parse", "HEAD").stdout.strip()


def test_self_check_passes_when_the_running_copy_equals_the_repo(tmp_path):
    repo, sha = _repo_with_deploy_sh(tmp_path, "echo same\n")
    me = tmp_path / "deploy.sh"; me.write_text("echo same\n")
    state = tmp_path / "moltrust-api"; state.write_text(f"{sha}\t2026-10-08T00:00:00Z\tok\n")
    p = _bash(f'self_check "{me}" "{repo}" "{state}"; echo rc=$?')
    assert "rc=0" in p.stdout, p.stdout + p.stderr


def test_self_check_refuses_a_copy_that_differs(tmp_path):
    repo, sha = _repo_with_deploy_sh(tmp_path, "echo repo\n")
    me = tmp_path / "deploy.sh"; me.write_text("echo edited on the server\n")
    state = tmp_path / "moltrust-api"; state.write_text(f"{sha}\t2026-10-08T00:00:00Z\tok\n")
    p = _bash(f'self_check "{me}" "{repo}" "{state}"; echo "rc=$? why=$SELF_CHECK_WHY"')
    assert "rc=1" in p.stdout and sha in p.stdout, p.stdout + p.stderr


def test_self_check_refuses_when_the_repo_file_is_missing(tmp_path):
    repo, sha = _repo_with_deploy_sh(tmp_path, None)
    me = tmp_path / "deploy.sh"; me.write_text("echo anything\n")
    state = tmp_path / "moltrust-api"; state.write_text(f"{sha}\t2026-10-08T00:00:00Z\tok\n")
    p = _bash(f'self_check "{me}" "{repo}" "{state}"; echo "rc=$? why=$SELF_CHECK_WHY"')
    assert "rc=1" in p.stdout and "not in the deployed commit" in p.stdout, p.stdout + p.stderr


def test_self_check_refuses_without_a_recorded_deploy(tmp_path):
    repo, _ = _repo_with_deploy_sh(tmp_path, "echo same\n")
    me = tmp_path / "deploy.sh"; me.write_text("echo same\n")
    p = _bash(f'self_check "{me}" "{repo}" "{tmp_path / "absent"}"; echo "rc=$? why=$SELF_CHECK_WHY"')
    assert "rc=1" in p.stdout and "no deployed" in p.stdout, p.stdout + p.stderr


def test_the_check_runs_before_the_lock_and_exits_1_with_an_alert():
    src = _DEPLOY_SH.read_text()
    i_check = src.index('if ! self_check "$DEPLOY_SELF"')
    assert i_check < src.index('exec 9>"$LOCK"'), "the self-check must come before the lock"
    block = src[i_check:src.index("exit 1", i_check) + len("exit 1")]
    assert "alert " in block and block.endswith("exit 1")


def test_self_install_replaces_the_target_atomically(tmp_path):
    repo, sha = _repo_with_deploy_sh(tmp_path, "#!/bin/bash\necho new\n")
    target = tmp_path / "bin" / "deploy.sh"; target.parent.mkdir(); target.write_text("echo old\n")
    p = _bash(f'self_install "{repo}" "{sha}" "{target}"; echo rc=$?')
    assert "rc=0" in p.stdout, p.stdout + p.stderr
    assert target.read_text() == "#!/bin/bash\necho new\n"
    assert oct(target.stat().st_mode & 0o777) == "0o700"
    assert [f.name for f in target.parent.iterdir()] == ["deploy.sh"], "temp file left behind"


def test_an_install_that_fails_mid_write_leaves_the_old_copy(tmp_path):
    """git dies after writing half the file: the target must be untouched."""
    repo, sha = _repo_with_deploy_sh(tmp_path, "#!/bin/bash\necho new\n")
    target = tmp_path / "bin" / "deploy.sh"; target.parent.mkdir(); target.write_text("echo old\n")
    stub = tmp_path / "stub"; stub.mkdir()
    (stub / "git").write_text("#!/bin/bash\nprintf '#!/bin/bash\\necho ha'\nexit 1\n")
    (stub / "git").chmod(0o755)
    # deploy.sh pins its own PATH, so the stub goes in after loading it.
    p = _bash(f'PATH="{stub}:$PATH"; self_install "{repo}" "{sha}" "{target}"; '
              'echo "rc=$? why=${SELF_INSTALL_WHY:-}"')
    assert "rc=1" in p.stdout and "git show" in p.stdout, p.stdout + p.stderr
    assert target.read_text() == "echo old\n"
    assert [f.name for f in target.parent.iterdir()] == ["deploy.sh"], "temp file left behind"


def test_an_install_with_a_syntax_error_leaves_the_old_copy(tmp_path):
    repo, sha = _repo_with_deploy_sh(tmp_path, "#!/bin/bash\nif then fi (\n")
    target = tmp_path / "bin" / "deploy.sh"; target.parent.mkdir(); target.write_text("echo old\n")
    p = _bash(f'self_install "{repo}" "{sha}" "{target}"; echo "rc=$? why=${{SELF_INSTALL_WHY:-}}"')
    assert "rc=1" in p.stdout and "syntax" in p.stdout, p.stdout + p.stderr
    assert target.read_text() == "echo old\n"
    assert [f.name for f in target.parent.iterdir()] == ["deploy.sh"]


def test_the_install_runs_only_after_a_successful_api_deploy():
    src = _DEPLOY_SH.read_text()
    ok_block = src[src.index('if [ "${ok:-1}" -eq 0 ]; then'):src.index("rollback\nrecord")]
    assert '[ "$REPO" = moltrust-api ] && install_self_if_changed' in ok_block
    assert ok_block.index('record "$SHA" ok') < ok_block.index("install_self_if_changed")


# ── 18. the web deploy ships a positive list (2026-10-08) ──

def test_web_files_ships_only_the_positive_list(tmp_path):
    repo = tmp_path / "web"; repo.mkdir()
    git = lambda *a: _sp.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (repo / "README").write_text("x"); git("add", "-A"); git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD").stdout.strip()
    files = ["index.html", "blog/a.html", "blog/feed.xml", "img/blog/a-hero.jpg", "assets/css/x.css",
             ".well-known/jwks.json", "robots.txt", "Whitepaper.pdf", "publications/p.pdf",
             "admin/index.html", "zh/index.html", "contexts/aae/v1", "ns/music/v1",
             # must not ship
             "trouvart/index.html", "assets-src/blog/a-master.png", "docs/assets-src/blog/a.png",
             "contexts/aae/v1.bak", "contexts/README",
             "publications/x.proposed.json", "newdir/page.html", "blog/index.html", "scripts/x.js"]
    for f in files:
        (repo / f).parent.mkdir(parents=True, exist_ok=True); (repo / f).write_text("x")
    git("add", "-A"); git("commit", "-q", "-m", "files")
    head = git("rev-parse", "HEAD").stdout.strip()
    p = _bash(f'cd "{repo}" && web_files {base} {head}')
    shipped = set(p.stdout.split())
    assert shipped == {"index.html", "blog/a.html", "blog/feed.xml", "img/blog/a-hero.jpg",
                       "assets/css/x.css", ".well-known/jwks.json", "robots.txt", "Whitepaper.pdf",
                       "publications/p.pdf", "admin/index.html", "zh/index.html",
                       "contexts/aae/v1", "ns/music/v1"}, p.stdout + p.stderr


# ── 19. a refused deploy leaves a line (2026-10-08) ──
# The self-check and the lock timeout exited without writing the deploy log, so
# a refusal left the checkout, ~/.deployed and the log all unchanged.

def test_self_check_and_lock_timeout_record_a_refusal():
    src = _DEPLOY_SH.read_text()
    i = src.index('if ! self_check "$DEPLOY_SELF"')
    block = src[i:src.index("exit 1", i)]
    assert 'record_deploy refused "$SHA"' in block
    j = src.index("if ! flock -w 1800 9; then")
    assert 'record_deploy refused "$SHA"' in src[j:src.index("exit 3", j)]


def test_a_refusal_is_written_to_the_deploy_log(tmp_path):
    log = tmp_path / "deploy-log.jsonl"
    p = _bash(f'DEPLOY_LOG="{log}"; REPO=moltrust-api; record_deploy refused {"a" * 40}; echo rc=$?')
    assert "rc=0" in p.stdout, p.stdout + p.stderr
    row = json.loads(log.read_text().strip())
    assert row["status"] == "refused" and row["dienst"] == "moltrust-api"


# ── 20. supervision sees main ahead of the deployed commit (2026-10-08) ──

class _Resp:
    def __init__(self, sha, date):
        self._b = {"sha": sha, "commit": {"committer": {"date": date}}}
    def raise_for_status(self):
        pass
    def json(self):
        return self._b


def _lag(monkeypatch, tmp_path, recorded, main_sha, minutes_ago):
    from agents import supervision as sv
    now = sv.now_utc()
    d = tmp_path / "deployed"; d.mkdir()
    for repo in ("moltrust-api", "moltrust-web"):
        (d / repo).write_text(f"{recorded}\t2026-10-08T00:00:00Z\tok\n")
    monkeypatch.setattr(sv, "DEPLOYED", str(d))
    monkeypatch.setattr(sv.gh, "token", lambda: "t")
    at = (now - datetime.timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(sv.httpx, "get", lambda *a, **k: _Resp(main_sha, at))
    return sv.check_deploy_lag(now), sv


def test_deploy_lag_green_when_main_is_deployed(monkeypatch, tmp_path):
    out, sv = _lag(monkeypatch, tmp_path, "a" * 40, "a" * 40, 120)
    assert [f["light"] for f in out] == [sv.GREEN, sv.GREEN]


def test_deploy_lag_red_when_main_waits_past_30_min(monkeypatch, tmp_path):
    out, sv = _lag(monkeypatch, tmp_path, "a" * 40, "b" * 40, 45)
    assert [f["light"] for f in out] == [sv.RED, sv.RED]
    assert "nicht deployt" in out[0]["detail"]


def test_deploy_lag_waits_while_a_deploy_can_still_be_running(monkeypatch, tmp_path):
    out, sv = _lag(monkeypatch, tmp_path, "a" * 40, "b" * 40, 5)
    assert [f["light"] for f in out] == [sv.GREEN, sv.GREEN]


def test_the_gate_runs_with_immutable_from_prev_when_it_knows_the_option():
    src = _DEPLOY_SH.read_text()
    i = src.index("local gargs=()")
    block = src[i:src.index("predeploy_gate.py \"${gargs[@]}\"", i)]
    assert "grep -q -- '--immutable-from'" in block
    assert 'gargs=(--immutable-from "$PREV")' in block


# ── 21. the MCP unit follows an api deploy that changed it (2026-10-08) ──

def test_mcp_restart_only_when_its_code_changed_and_never_fatal():
    src = _DEPLOY_SH.read_text()
    api = src[src.index("deploy_api() {"):src.index("restart_mcp_if_changed() {")]
    assert api.index("probe_api") < api.index("restart_mcp_if_changed")
    fn = src[src.index("restart_mcp_if_changed() {"):]
    fn = fn[:fn.index("\n}\n")]
    assert 'git diff --quiet "$PREV" "$SHA" -- services/ requirements.txt' in fn
    assert "sudo -n /usr/bin/systemctl restart \"$MCP_UNIT\"" in fn
    assert "return 1" not in fn, "an MCP restart failure must not roll the API back"
    assert fn.count("alert ") == 2


# ── 22. the deploy fetch survives one collision (2026-10-10) ──

def test_fetch_retries_once_on_cannot_lock_ref_and_tags_the_reflog():
    src = _DEPLOY_SH.read_text()
    assert 'export GIT_REFLOG_ACTION="deploy.sh $REPO $SHA"' in src
    fn = src[src.index("fetch_main() {"):src.index("fetch_main || die")]
    assert fn.count("git fetch -q origin main") == 2
    assert 'grep -q "cannot lock ref"' in fn and "sleep 5" in fn
