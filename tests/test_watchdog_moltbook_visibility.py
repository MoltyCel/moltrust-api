"""A writer that posted is not a silent writer, and only Lars' step reaches Lars (ops MB-1107).

On 2026-11-07 ~06:39Z the watchdog reported "kein neuer Post von moltguard_v1
seit 37 h (max 36 h)". The run of 06.11. 13:00Z had posted: HTTP 201, id
90896ee8, verification solved, the post there when fetched by id. The author
listing left it out because Moltbook had classified it is_spam. From 25.10. to
10.11. every daily run posted; four of them were missing from the listing (two
is_spam, two without any score or comment).

Two parts, each with a firing test and a silence test:
  * when the listing looks silent, the ids the writer logged are fetched one
    by one; a post Moltbook holds and dates inside max_hours counts, a log
    line alone does not
  * NOTIFY-4: a writer's silence is filed (watchdog-befund, ABLAGE); a blocked
    account or a rejected key goes to Lars (moltbook-konto-gesperrt, HANDELN)
"""
import datetime

import pytest

from agents import watchdog as wd
from app import meldungsarten as MA, notify

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 7, 6, 39, tzinfo=UTC)
MG = next(w for w in wd.MOLTBOOK_WRITERS if w["surface"] == "Moltbook/MoltGuard")
ID_0611 = "90896ee8-696f-4379-8e15-d2e230ba33ac"
ID_OLD = "45aadb01-f5ab-4007-a400-3fa9bd87c125"


class HTTPError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = type("R", (), {"status_code": code})()


def _iso(hours_ago: float) -> str:
    return (NOW - datetime.timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


def _logline(hours_ago: float, text: str) -> str:
    when = NOW - datetime.timedelta(hours=hours_ago)
    return f"[{when:%Y-%m-%dT%H:%M:%S}] {text}"


def _posted(hours_ago: float, pid: str) -> list:
    # Every line twice, as on the server: file handler plus stdout redirect.
    line = _logline(hours_ago, f"INFO: Posted! ID: {pid}, verification: OK")
    return [line, line]


def _moltbook(listed_hours=37.6, single=None, profile=None, fetched=None):
    """moltrust-agent fresh everywhere; moltguard_v1's newest listed post
    listed_hours ago; single maps post id -> post dict or an exception."""
    single = single or {}
    profile = profile or {}

    def get(path, params):
        if path == "/agents/me/comments":
            return {"comments": [{"created_at": _iso(1)}]}
        if path == "/posts":
            who = params["author"]
            hours = 2.0 if who == "moltrust-agent" else listed_hours
            return {"posts": [{"author": {"name": who}, "created_at": _iso(hours)}]}
        if path.startswith("/posts/"):
            pid = path.rsplit("/", 1)[1]
            if fetched is not None:
                fetched.append(pid)
            got = single.get(pid, HTTPError(404))
            if isinstance(got, Exception):
                raise got
            return {"post": got}
        if path == "/agents/profile":
            got = profile.get(params["name"], {"is_active": True, "deleted_at": None})
            if isinstance(got, Exception):
                raise got
            return {"agent": got}
        raise AssertionError(path)

    return get


def _post(hours_ago, *, spam=False, author="moltguard_v1"):
    return {"author": {"name": author}, "created_at": _iso(hours_ago),
            "is_spam": spam, "is_deleted": False, "verification_status": "verified"}


def _mg(results):
    return next(r for r in results if r["surface"] == "Moltbook/MoltGuard")


def _log(lines):
    return lambda w: lines if w is MG else []


# --- the regex matches what moltguard.py actually writes ----------------------

def test_the_patterns_match_the_writer_log_format():
    posted = "[2026-11-12T13:00:16] INFO: Posted! ID: aa558445-cde7-498a-8f73-c64ba28f9de0, verification: OK"
    refused = '[2026-11-12T13:00:16] WARNING: POST /posts -> 401: {"error":"unauthorized"}'
    assert wd._log_stamps([posted], MG["posted_re"])[0][1] == "aa558445-cde7-498a-8f73-c64ba28f9de0"
    assert wd._log_stamps([refused], MG["rejected_re"])[0][1] == "401"
    # A 500 is Moltbook's outage, not a rejected key.
    assert wd._log_stamps([refused.replace("401", "500")], MG["rejected_re"]) == []


# --- visibility -------------------------------------------------------------

def test_the_case_of_0611_is_named_as_spam_not_as_silence():
    """Firing test: the run posted, Moltbook holds the post, the listing hides it."""
    get = _moltbook(single={ID_0611: _post(17.6, spam=True)})
    r = _mg(wd.check_moltbook_writers(NOW, get=get, read_log=_log(_posted(17.6, ID_0611))))
    assert r["ok"] is False and not r.get("handeln")
    assert "Lauf hat gepostet" in r["detail"] and "is_spam" in r["detail"]
    assert "seit" not in r["detail"]


def test_a_post_without_engagement_is_a_post():
    """27.10. and 30.10.: held, not spam, left out of the listing for want of a vote."""
    get = _moltbook(single={ID_0611: _post(17.6)})
    r = _mg(wd.check_moltbook_writers(NOW, get=get, read_log=_log(_posted(17.6, ID_0611))))
    assert r["ok"] is True
    assert "nicht in der Autorenliste" in r["detail"]


def test_a_logged_id_that_moltbook_dates_back_is_still_silence():
    """The fold of 11.10.: Moltbook answered with the id of an older post."""
    get = _moltbook(single={ID_OLD: _post(6 * 24)})
    r = _mg(wd.check_moltbook_writers(NOW, get=get, read_log=_log(_posted(17.6, ID_OLD))))
    assert r["ok"] is False
    assert "seit 38 h" in r["detail"] and "max 36 h" in r["detail"]


def test_a_logged_id_moltbook_does_not_know_is_still_silence():
    r = _mg(wd.check_moltbook_writers(NOW, get=_moltbook(),
                                      read_log=_log(_posted(17.6, ID_0611))))
    assert r["ok"] is False and "seit 38 h" in r["detail"]


def test_a_post_under_another_account_does_not_count():
    get = _moltbook(single={ID_0611: _post(17.6, author="someone-else")})
    r = _mg(wd.check_moltbook_writers(NOW, get=get, read_log=_log(_posted(17.6, ID_0611))))
    assert r["ok"] is False and "seit 38 h" in r["detail"]


def test_a_post_dated_after_now_does_not_count():
    """Found replaying 07.11. 06:39Z against the live log on 13.11.: the newest
    logged post (12.11.) counted with -126 h. Later lines and later posts are
    not evidence for an earlier moment."""
    later = "aa558445-cde7-498a-8f73-c64ba28f9de0"
    get = _moltbook(single={later: _post(-126.4), ID_0611: _post(17.6, spam=True)})
    lines = _posted(17.6, ID_0611) + _posted(-126.4, later)
    r = _mg(wd.check_moltbook_writers(NOW, get=get, read_log=_log(lines)))
    assert r["ok"] is False and "is_spam" in r["detail"]


def test_without_a_log_the_silence_verdict_stands():
    r = _mg(wd.check_moltbook_writers(NOW, get=_moltbook(), read_log=lambda w: []))
    assert r["ok"] is False and "seit 38 h" in r["detail"]


def test_an_unreadable_single_post_is_unmeasured_not_silence():
    get = _moltbook(single={ID_0611: HTTPError(503)})
    r = _mg(wd.check_moltbook_writers(NOW, get=get, read_log=_log(_posted(17.6, ID_0611))))
    assert r.get("unverifiable") is True


def test_normal_operation_reads_no_log_and_fetches_no_post():
    """Silence test: a listed post inside max_hours ends the question."""
    fetched = []

    def no_log(w):
        raise AssertionError("log read although the listing is fresh")

    out = wd.check_moltbook_writers(NOW, get=_moltbook(listed_hours=17.6, fetched=fetched),
                                    read_log=no_log)
    assert all(r["ok"] for r in out), out
    assert fetched == []


def test_the_doubled_log_lines_cost_one_fetch_per_post():
    fetched = []
    get = _moltbook(single={ID_0611: _post(17.6, spam=True)}, fetched=fetched)
    wd.check_moltbook_writers(NOW, get=get, read_log=_log(_posted(17.6, ID_0611)))
    assert fetched == [ID_0611]


# --- the account: what only Lars can fix -------------------------------------

def _konto(results, account="moltguard_v1"):
    return next(r for r in results if r["surface"] == f"Moltbook/Konto {account}")


def test_active_accounts_are_silent():
    out = wd.check_moltbook_accounts(NOW, get=_moltbook(),
                                     read_log=_log(_posted(17.6, ID_0611)))
    assert {r["surface"] for r in out} == {"Moltbook/Konto moltrust-agent",
                                           "Moltbook/Konto moltguard_v1"}
    assert all(r["ok"] and not r.get("handeln") for r in out), out


@pytest.mark.parametrize("profile", [
    {"is_active": False, "deleted_at": None},
    {"is_active": True, "deleted_at": "2026-11-06T13:00:00Z"},
    HTTPError(404),
    HTTPError(401),
])
def test_a_blocked_account_is_lars_step(profile):
    get = _moltbook(profile={"moltguard_v1": profile})
    r = _konto(wd.check_moltbook_accounts(NOW, get=get, read_log=lambda w: []))
    assert r["ok"] is False and r["handeln"] is True
    assert "moltguard_v1" in r["detail"] and r["advice"]


def test_an_unreachable_profile_is_unmeasured_not_lars_step():
    get = _moltbook(profile={"moltguard_v1": HTTPError(500)})
    r = _konto(wd.check_moltbook_accounts(NOW, get=get, read_log=lambda w: []))
    assert r.get("unverifiable") is True and not r.get("handeln")


def test_a_key_rejected_after_the_last_post_is_lars_step():
    lines = _posted(41.6, ID_OLD) + [
        _logline(17.6, 'WARNING: POST /posts -> 401: {"error":"unauthorized"}')]
    r = _konto(wd.check_moltbook_accounts(NOW, get=_moltbook(), read_log=_log(lines)))
    assert r["handeln"] is True and "401" in r["detail"]


def test_a_rejection_followed_by_a_post_is_over():
    lines = [_logline(41.6, "WARNING: POST /posts -> 403: {}")] + _posted(17.6, ID_0611)
    r = _konto(wd.check_moltbook_accounts(NOW, get=_moltbook(), read_log=_log(lines)))
    assert r["ok"] is True


# --- NOTIFY-4: which line goes where -------------------------------------------

@pytest.fixture
def posted(monkeypatch, tmp_path):
    """notify with the gate open, a token, and requests.post recorded."""
    monkeypatch.setenv("MOLTRUST_NOTIFY", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:" + "A" * 35)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100test")
    monkeypatch.setenv("MOLTRUST_NOTIFY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MOLTRUST_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(notify, "SENT_LOG", str(tmp_path / "state" / "sent.jsonl"))
    monkeypatch.setattr(notify, "FINGERPRINTS", str(tmp_path / "state" / "fp.json"))
    notify._ENV_CACHE.clear()
    calls = []

    class R:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"result": {"message_id": 41}}

    def post(url, data=None, timeout=None):
        calls.append(data)
        return R()

    monkeypatch.setattr(notify.requests, "post", post)
    return calls


def _lines(results, state=None):
    """The watchdog's own path from verdicts to lines: observe, then debounce."""
    obs, handeln = [], []
    for r in results:
        if r.get("unverifiable"):
            continue
        (handeln if r.get("handeln") else obs).append(
            {"key": r["surface"], "ok": r["ok"], "line": r["detail"],
             "detail": r["detail"], "advice": r.get("advice")})
    lines, st = wd.debounce(obs, state or {}, NOW)
    hl, _ = wd.debounce(handeln, st, NOW)
    return lines, hl


def test_the_classes_in_the_table():
    assert MA.einordnen("watchdog-befund", "x").klasse == MA.ABLAGE
    assert MA.einordnen("moltbook-konto-gesperrt", "x").klasse == MA.HANDELN


def test_a_writer_silence_is_filed_not_sent(posted, tmp_path):
    """The alarm of 07.11. 06:39Z, replayed: filed for a console, no Telegram call."""
    get = _moltbook()
    results = (wd.check_moltbook_writers(NOW, get=get, read_log=lambda w: [])
               + wd.check_moltbook_accounts(NOW, get=get, read_log=lambda w: []))
    lines, handeln = _lines(results)
    assert any("moltguard_v1 seit 38 h" in l for l in lines)
    assert handeln == []
    assert wd.deliver(lines, handeln, []) is True
    assert posted == []
    filed = "".join(p.read_text() for p in (tmp_path / "reports").glob("*.md"))
    assert "moltguard_v1 seit 38 h" in filed and "watchdog-befund" in filed


def test_a_post_hidden_as_spam_is_filed_not_sent(posted, tmp_path):
    get = _moltbook(single={ID_0611: _post(17.6, spam=True)})
    lines, handeln = _lines(wd.check_moltbook_writers(
        NOW, get=get, read_log=_log(_posted(17.6, ID_0611))))
    assert handeln == [] and any("is_spam" in l for l in lines)
    assert wd.deliver(lines, handeln, []) is True
    assert posted == []


def test_a_blocked_account_reaches_lars_in_four_lines(posted, tmp_path):
    get = _moltbook(profile={"moltguard_v1": {"is_active": False, "deleted_at": None}})
    results = (wd.check_moltbook_writers(NOW, get=get, read_log=lambda w: [])
               + wd.check_moltbook_accounts(NOW, get=get, read_log=lambda w: []))
    lines, handeln = _lines(results)
    assert len(handeln) == 1 and "moltguard_v1" in handeln[0]
    assert wd.deliver(lines, handeln, []) is True
    assert len(posted) == 1
    assert "moltguard_v1 kann nicht schreiben" in posted[0]["text"]
    assert len(notify.handeln_zeilen(posted[0]["text"])) <= 4
