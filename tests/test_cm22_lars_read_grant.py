"""Read-only grant for @LarsOnMoltrust and the posted-reply match (ops CM22/CM23).

X is faked at requests.get/requests.post, under x_meter.get, so the ledger rows
are the real ones. The clock is passed in everywhere: a draft is offered at T0,
the sweep runs at T0 + 12 h + 1 min, and the replies sit before and after that.
"""
import datetime
import json
import os
import stat
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents import lars_reply_match as lrmatch  # noqa: E402
from agents import reply_decisions as rd  # noqa: E402
from agents import reply_radar as rr  # noqa: E402
from agents import x_meter  # noqa: E402
from app import x_lars_oauth2 as grant  # noqa: E402
from scripts import lars_reply_metrics as lrm  # noqa: E402
from scripts import x_oauth2_read_grant as grant_cli  # noqa: E402

UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 11, 2, 6, 5, tzinfo=UTC)
LARS_ID = "4242"
TARGET = "1900000000000000001"
OTHER = "1900000000000000999"
SWEEP = T0 + datetime.timedelta(hours=12, minutes=1)
ACCESS = "acc-SECRET-value-1"
REFRESH = "ref-SECRET-value-1"


class _Resp:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FakeX:
    """users/me and Lars's timeline; records every call with its auth."""

    def __init__(self, posts=(), handle="LarsOnMoltrust"):
        self.calls = []
        self.posts = list(posts)
        self.handle = handle

    def __call__(self, url, params=None, auth=None, timeout=None, **kw):
        self.calls.append((url, dict(params or {}), auth))
        if url.endswith("/users/me"):
            return _Resp({"data": {"id": LARS_ID, "username": self.handle}})
        if url.endswith(f"/users/{LARS_ID}/tweets"):
            return _Resp({"data": self.posts, "meta": {"result_count": len(self.posts)}})
        raise AssertionError(f"unexpected X call {url}")


class FakeToken:
    """The token endpoint. Answers with whatever scope it was built with."""

    def __init__(self, scope="tweet.read users.read offline.access", rotate=True,
                 status=200):
        self.calls = []
        self.scope = scope
        self.rotate = rotate
        self.status = status

    def __call__(self, url, data=None, headers=None, timeout=None, **kw):
        assert url == grant.TOKEN_URL
        self.calls.append((dict(data or {}), dict(headers or {})))
        if self.status != 200:
            return _Resp({"error": "invalid_request"}, self.status)
        n = len(self.calls) + 1
        body = {"token_type": "bearer", "expires_in": 7200,
                "access_token": f"acc-SECRET-value-{n}", "scope": self.scope}
        if self.rotate:
            body["refresh_token"] = f"ref-SECRET-value-{n}"
        return _Resp(body)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(x_meter, "LEDGER", str(tmp_path / "data" / "x_meter.jsonl"))
    secrets = tmp_path / "secrets"
    secrets.write_text("X_CONSUMER_KEY=ck\nX_ACCESS_TOKEN=moltrust\n"
                       "X_ACCESS_SECRET=moltrust\nX_OAUTH2_CLIENT_ID=cid\n"
                       "X_OAUTH2_CLIENT_SECRET='csecret'\n")
    secrets.chmod(0o600)
    monkeypatch.setenv("MOLTRUST_SECRETS_FILE", str(secrets))
    # The @MolTrust pair sits in the environment as it does under cron.
    monkeypatch.setenv("X_ACCESS_TOKEN", "moltrust")
    monkeypatch.setenv("X_ACCESS_SECRET", "moltrust")
    for k in ("X_LARS_ACCESS_TOKEN", "X_LARS_ACCESS_SECRET"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def _grant(env, expires=T0 + datetime.timedelta(days=30),
           scope="offline.access,tweet.read,users.read"):
    with open(env / "secrets", "a") as f:
        f.write(f"X_LARS_OAUTH2_ACCESS_TOKEN={ACCESS}\n"
                f"X_LARS_OAUTH2_REFRESH_TOKEN={REFRESH}\n"
                f"X_LARS_OAUTH2_EXPIRES_AT={expires:%Y-%m-%dT%H:%M:%SZ}\n"
                f"X_LARS_OAUTH2_SCOPE={scope}\n")


def _fake(monkeypatch, get=None, post=None):
    import requests
    if get is not None:
        monkeypatch.setattr(requests, "get", get)
    if post is not None:
        monkeypatch.setattr(requests, "post", post)
    return get, post


def _offer(tweet_id=TARGET, at=T0):
    rd.register({"id": tweet_id, "_author": "someone"}, True, [], 7223, chat_id=1, now=at)


def _reply(rid, to, at):
    return {"id": rid, "created_at": _iso(at),
            "referenced_tweets": [{"type": "replied_to", "id": to}]}


def _lines():
    try:
        with open(rd.decisions_path()) as f:
            return [json.loads(x) for x in f]
    except FileNotFoundError:
        return []


def _ledger(env):
    p = env / "data" / "x_meter.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# ── criterion 2/3: the grant is read-only and Lars's ────────────────────────

def test_authorize_url_asks_for_read_scopes_only_with_s256_pkce():
    verifier, challenge = grant.pkce_pair()
    url = grant.authorize_url("cid", grant_cli.REDIRECT_URI, "st", challenge)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert url.startswith("https://x.com/i/oauth2/authorize?")
    assert q["scope"] == ["tweet.read users.read offline.access"]
    assert "write" not in q["scope"][0]
    assert q["code_challenge_method"] == ["S256"]
    import base64
    import hashlib
    assert q["code_challenge"][0] == base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert 43 <= len(verifier) <= 128
    assert q["redirect_uri"] == ["http://127.0.0.1:8765/callback"]


def test_exchange_stores_the_grant_under_its_own_names(env, monkeypatch):
    _fake(monkeypatch, get=FakeX(), post=FakeToken())
    scopes = grant.exchange_code("code", "verifier", grant_cli.REDIRECT_URI, now=T0)
    assert scopes == ["offline.access", "tweet.read", "users.read"]
    text = (env / "secrets").read_text()
    # The @MolTrust pair and the client are untouched; Lars's names are added.
    assert "X_ACCESS_TOKEN=moltrust\n" in text and "X_OAUTH2_CLIENT_SECRET='csecret'" in text
    s = grant.read_secrets(grant.TOKEN_VARS)
    assert s[grant.ACCESS_VAR] == "acc-SECRET-value-2"
    assert s[grant.SCOPE_VAR] == "offline.access,tweet.read,users.read"
    assert stat.S_IMODE(os.stat(env / "secrets").st_mode) == 0o600


def test_confidential_client_uses_basic_auth_and_sends_the_verifier(env, monkeypatch):
    _, tok = _fake(monkeypatch, get=FakeX(), post=FakeToken())
    grant.exchange_code("code", "verifier", grant_cli.REDIRECT_URI, now=T0)
    form, headers = tok.calls[0]
    assert form == {"code": "code", "grant_type": "authorization_code",
                    "redirect_uri": grant_cli.REDIRECT_URI, "code_verifier": "verifier"}
    assert headers["Authorization"].startswith("Basic ")


@pytest.mark.parametrize("scope", ["tweet.read users.read offline.access tweet.write",
                                   "tweet.read users.read"])
def test_a_grant_that_is_not_exactly_read_only_is_not_stored(env, monkeypatch, scope):
    _fake(monkeypatch, get=FakeX(), post=FakeToken(scope=scope))
    before = (env / "secrets").read_text()
    with pytest.raises(grant.GrantError):
        grant.exchange_code("code", "verifier", grant_cli.REDIRECT_URI, now=T0)
    assert (env / "secrets").read_text() == before


def test_a_grant_from_another_account_is_not_stored(env, monkeypatch):
    _fake(monkeypatch, get=FakeX(handle="MolTrust"), post=FakeToken())
    before = (env / "secrets").read_text()
    with pytest.raises(grant.GrantError, match="MolTrust"):
        grant.exchange_code("code", "verifier", grant_cli.REDIRECT_URI, now=T0)
    assert (env / "secrets").read_text() == before


def test_the_grant_script_prints_scopes_and_handle_and_no_token(env, monkeypatch, capsys):
    _fake(monkeypatch, get=FakeX(), post=FakeToken())
    monkeypatch.setattr(grant_cli, "wait_for_callback", lambda state: "the-code")
    assert grant_cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Granted scopes: offline.access tweet.read users.read" in out
    assert "@LarsOnMoltrust" in out
    assert "SECRET" not in out and "the-code" not in out and "csecret" not in out


def test_the_callback_refuses_a_foreign_state():
    with pytest.raises(grant.GrantError):
        grant_cli.parse_callback("http://127.0.0.1:8765/callback?state=x&code=c", "y")
    assert grant_cli.parse_callback("/callback?state=y&code=c", "y") == "c"


# ── refresh ────────────────────────────────────────────────────────────────

def test_a_live_token_is_used_without_a_refresh(env, monkeypatch):
    _grant(env)
    _, tok = _fake(monkeypatch, post=FakeToken())
    a = grant.auth(now=T0)
    assert a is not None and tok.calls == []
    assert "SECRET" not in repr(a)


def test_an_expiring_token_is_refreshed_and_the_new_pair_stored(env, monkeypatch):
    _grant(env, expires=T0 + datetime.timedelta(minutes=5))
    _, tok = _fake(monkeypatch, post=FakeToken())
    a = grant.auth(now=T0)
    assert a is not None
    form, _ = tok.calls[0]
    assert form == {"grant_type": "refresh_token", "refresh_token": REFRESH}
    s = grant.read_secrets(grant.TOKEN_VARS)
    assert s[grant.ACCESS_VAR] == "acc-SECRET-value-2"
    assert s[grant.REFRESH_VAR] == "ref-SECRET-value-2"
    assert s[grant.EXPIRES_VAR] == "2026-11-02T08:05:00Z"
    # The next call within the two hours does not refresh again.
    grant.auth(now=T0 + datetime.timedelta(minutes=30))
    assert len(tok.calls) == 1


def test_a_refresh_without_a_new_refresh_token_keeps_the_old_one(env, monkeypatch):
    _grant(env, expires=T0 - datetime.timedelta(minutes=1))
    _fake(monkeypatch, post=FakeToken(rotate=False))
    assert grant.auth(now=T0) is not None
    assert grant.read_secrets(grant.TOKEN_VARS)[grant.REFRESH_VAR] == REFRESH


def test_a_refused_refresh_gives_no_auth_and_keeps_the_file(env, monkeypatch):
    _grant(env, expires=T0 - datetime.timedelta(minutes=1))
    before = (env / "secrets").read_text()
    _fake(monkeypatch, post=FakeToken(status=400))
    assert grant.auth(now=T0) is None
    assert (env / "secrets").read_text() == before


def test_a_stored_grant_with_a_write_scope_is_not_used(env, monkeypatch):
    _grant(env, scope="offline.access,tweet.read,tweet.write,users.read")
    assert grant.auth(now=T0) is None


def test_the_measurement_signs_with_the_oauth2_grant(env, monkeypatch):
    _grant(env)
    a = lrm.lars_auth()
    assert isinstance(a, grant.BearerAuth)


# ── CM23: a posted reply closes its draft ──────────────────────────────────

def test_reply_before_twelve_hours_closes_as_posted_and_never_expires(env, monkeypatch):
    _grant(env)
    _offer()
    fake, _ = _fake(monkeypatch, get=FakeX([_reply("801", TARGET, T0 + datetime.timedelta(hours=2))]))
    out = lrmatch.run(now=T0 + datetime.timedelta(hours=3))
    assert out["matched"] == {TARGET: "closed"}
    assert rd.due(now=SWEEP) == []
    lines = _lines()
    assert len(lines) == 1
    assert lines[0]["result"] == rd.SENT and lines[0]["route"] == "link"
    assert lines[0]["reply_id"] == "801"
    assert lines[0]["link"] == "https://x.com/LarsOnMoltrust/status/801"
    # Every call went out with Lars's bearer, none with the @MolTrust pair.
    assert fake.calls and all(isinstance(c[2], grant.BearerAuth) for c in fake.calls)


def test_reply_after_twelve_hours_overrides_expired(env, monkeypatch):
    _grant(env)
    _offer()
    assert [r[1] for r in rd.due(now=SWEEP)] == [rd.EXPIRED]
    _fake(monkeypatch, get=FakeX([_reply("802", TARGET, T0 + datetime.timedelta(hours=14))]))
    out = lrmatch.run(now=T0 + datetime.timedelta(hours=15))
    assert out["matched"] == {TARGET: "overridden"}
    lines = _lines()
    assert [x["result"] for x in lines] == [rd.EXPIRED, rd.SENT]
    assert lines[1]["overrides"] == rd.EXPIRED and lines[1]["route"] == "link"
    s = rd.summary(T0, T0 + datetime.timedelta(days=2))
    assert s["counts"] == {rd.SENT: 1, rd.DROPPED: 0, rd.EXPIRED: 0}
    # The draft is no longer a candidate, and a second find books nothing.
    later = T0 + datetime.timedelta(hours=16)
    assert lrmatch.run(now=later) == {"skipped": "no draft to match"}
    assert rd.close_posted(TARGET, "802", "l", None, now=later) is None
    assert len(_lines()) == 2


def test_no_reply_expires(env, monkeypatch):
    _grant(env)
    _offer()
    _fake(monkeypatch, get=FakeX([]))
    assert lrmatch.run(now=T0 + datetime.timedelta(hours=3))["matched"] == {}
    assert [r[1] for r in rd.due(now=SWEEP)] == [rd.EXPIRED]
    assert lrm.window_start() is None


def test_reply_to_a_different_post_does_not_match(env, monkeypatch):
    _grant(env)
    _offer()
    _fake(monkeypatch, get=FakeX([_reply("803", OTHER, T0 + datetime.timedelta(hours=1))]))
    assert lrmatch.run(now=T0 + datetime.timedelta(hours=3))["matched"] == {}
    assert [r[1] for r in rd.due(now=SWEEP)] == [rd.EXPIRED]


def test_a_reply_written_before_the_draft_is_not_the_drafts(env, monkeypatch):
    _grant(env)
    _offer()
    _fake(monkeypatch, get=FakeX([_reply("804", TARGET, T0 - datetime.timedelta(hours=1))]))
    assert lrmatch.run(now=T0 + datetime.timedelta(hours=3))["matched"] == {}


def test_a_reply_past_the_lookback_leaves_expired_standing(env, monkeypatch):
    _grant(env)
    _offer()
    rd.due(now=SWEEP)
    late = T0 + datetime.timedelta(hours=rd.LINK_LOOKBACK_HOURS + 2)
    assert rd.link_candidates(now=late) == {}
    assert rd.close_posted(TARGET, "805", "l", _iso(late), now=late) is None
    assert [x["result"] for x in _lines()] == [rd.EXPIRED]


def test_a_discarded_draft_is_not_turned_into_posted(env):
    _offer()
    rd.close(TARGET, rd.DROPPED, reason="Ton", now=T0 + datetime.timedelta(hours=1))
    assert rd.close_posted(TARGET, "806", "l", None, now=T0 + datetime.timedelta(hours=2)) is None


# ── criterion 5: the first matched reply opens the CM5 window ──────────────

def test_the_first_matched_reply_opens_the_window(env, monkeypatch):
    _grant(env)
    second = "1900000000000000002"
    _offer()
    _offer(second, T0 + datetime.timedelta(hours=1))
    first_at = T0 + datetime.timedelta(hours=2)
    _fake(monkeypatch, get=FakeX([_reply("808", second, T0 + datetime.timedelta(hours=5)),
                                  _reply("807", TARGET, first_at)]))
    lrmatch.run(now=T0 + datetime.timedelta(hours=6))
    assert lrm.window_start() == first_at
    booked = lrm.replies()
    assert {(r["reply_id"], r["route"]) for r in booked} == {("807", "link"), ("808", "link")}


def test_a_copied_reply_still_does_not_open_the_window(env):
    lrm.record_copied("https://x.com/LarsOnMoltrust/status/900")
    assert lrm.window_start() is None


# ── criterion 6: reads under their own source, not in the send cap ─────────

def test_reads_are_booked_under_their_own_source_not_the_send_cap(env, monkeypatch):
    _grant(env)
    _offer()
    _fake(monkeypatch, get=FakeX([_reply("809", TARGET, T0 + datetime.timedelta(hours=1))]))
    lrmatch.run(now=T0 + datetime.timedelta(hours=2))
    rows = _ledger(env)
    assert rows and {r["source"] for r in rows} == {"lars-reply-match"}
    assert all(r["kind"] == "read" for r in rows)
    assert all(x_meter._column(r) == "cm5_read" for r in rows)
    assert x_meter.send_spend(now=T0 + datetime.timedelta(hours=2))["sends"] == 0


def test_the_timeline_is_read_at_most_every_half_hour(env, monkeypatch):
    _grant(env)
    _offer()
    fake, _ = _fake(monkeypatch, get=FakeX([]))
    lrmatch.run(now=T0 + datetime.timedelta(hours=1))
    n = len(fake.calls)
    assert lrmatch.run(now=T0 + datetime.timedelta(hours=1, minutes=10)) == {
        "skipped": "checked recently"}
    assert len(fake.calls) == n


def test_nothing_is_read_without_an_open_draft(env, monkeypatch):
    _grant(env)
    fake, _ = _fake(monkeypatch, get=FakeX([]))
    assert lrmatch.run(now=T0) == {"skipped": "no draft to match"}
    assert fake.calls == []


# ── no grant: no fallback, nothing breaks ──────────────────────────────────

def test_missing_grant_reads_nothing_and_never_uses_the_moltrust_pair(env, monkeypatch):
    _offer()
    fake, post = _fake(monkeypatch, get=FakeX([_reply("810", TARGET, T0)]),
                       post=FakeToken())
    assert lrmatch.run(now=T0 + datetime.timedelta(hours=1)) == {"skipped": "no grant"}
    assert fake.calls == [] and post.calls == []
    assert _ledger(env) == []
    # The sweep still runs on the clock as before.
    assert [r[1] for r in rd.due(now=SWEEP)] == [rd.EXPIRED]
    assert lrm.lars_auth() is None


def test_consumer_matches_before_it_sweeps(env, monkeypatch):
    order = []
    monkeypatch.setattr(rr, "load_state", lambda: {})
    monkeypatch.setattr(rr, "save_state", lambda s: None)
    monkeypatch.setattr(rr, "x_auth", lambda: None)
    monkeypatch.setattr(lrmatch, "run", lambda: order.append("match"))
    monkeypatch.setattr(rr, "sweep_decisions", lambda: order.append("sweep"))
    monkeypatch.setattr(rr, "consume_reason_texts", lambda: None)
    monkeypatch.setattr(rr.telegram_inbox, "claim", lambda *a, **k: [])
    rr.consume_and_post()
    assert order == ["match", "sweep"]


def test_a_failing_match_does_not_stop_the_sweep(env, monkeypatch):
    order = []
    monkeypatch.setattr(rr, "load_state", lambda: {})
    monkeypatch.setattr(rr, "save_state", lambda s: None)
    monkeypatch.setattr(rr, "x_auth", lambda: None)

    def boom():
        raise RuntimeError("x down")
    monkeypatch.setattr(lrmatch, "run", boom)
    monkeypatch.setattr(rr, "sweep_decisions", lambda: order.append("sweep"))
    monkeypatch.setattr(rr, "consume_reason_texts", lambda: None)
    monkeypatch.setattr(rr.telegram_inbox, "claim", lambda *a, **k: [])
    rr.consume_and_post()
    assert order == ["sweep"]
