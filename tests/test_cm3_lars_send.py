"""CM3: Senden posts from Lars's own account, and from no other.

Herkunft: ops CM3 (Freigabe Lars 22.10.2026). Kriterium 3: kein Rueckfall auf
@MolTrust, belegt durch einen Lauf, in dem nur die @MolTrust-Tokens gesetzt
sind und nichts gesendet wird. Kriterium 4: fehlt die Freischaltung, antwortet
Senden mit "Account nicht freigeschaltet", dem Entwurf zum Kopieren und dem
Link auf den Zielpost.

Jede Pruefung hat ihre Gegenprobe: der Befund loest aus, und der
Normalbetrieb schweigt.
"""

import datetime

import pytest

from agents import reply_radar as rr

APP = {"X_CONSUMER_KEY": "app-key", "X_CONSUMER_SECRET": "app-secret"}
MOLTRUST = {"X_ACCESS_TOKEN": "moltrust-token", "X_ACCESS_SECRET": "moltrust-secret"}
LARS = {"X_LARS_ACCESS_TOKEN": "lars-token", "X_LARS_ACCESS_SECRET": "lars-secret"}
DRAFT = "Article 50 applies to the deployer as well."


def _env(monkeypatch, *pairs):
    for k in list(APP) + list(MOLTRUST) + list(LARS):
        monkeypatch.delenv(k, raising=False)
    for pair in pairs:
        for k, v in pair.items():
            monkeypatch.setenv(k, v)


@pytest.fixture
def button(monkeypatch, tmp_path):
    """One press on Senden, every check in front of the post passed, armed."""
    calls = {"posted": [], "edits": [], "booked": [], "http": []}
    monkeypatch.setattr(rr, "DAILY_MAX", 8)
    monkeypatch.setattr(rr, "reply_branch_retired", lambda now=None: False)
    monkeypatch.setattr(rr, "edit_message",
                        lambda c, m, t, **kw: calls["edits"].append(t))
    monkeypatch.setattr(rr, "target_ok",
                        lambda a, t: (True, "", {"id": t, "_author": "owasp",
                                                 "text": "the post"}))
    monkeypatch.setattr(rr, "fetch_sources", lambda urls: {})
    monkeypatch.setattr(rr, "check", lambda t, s: (True, [], {}))
    monkeypatch.setattr(rr.x_meter, "sends_paused", lambda: "")
    monkeypatch.setattr(rr, "followers_now", lambda a: 27)
    monkeypatch.setattr(rr.reply_decisions, "close", lambda *a, **k: None)
    monkeypatch.setenv(rr.ARM_FLAG, "1")
    monkeypatch.setattr(rr.x_post, "post",
                        lambda text, **kw: calls["posted"].append((text, kw)) or "999")
    # Nothing in this file may reach X by any other road either.
    monkeypatch.setattr(rr.x_post.requests, "post",
                        lambda *a, **k: calls["http"].append(a) or None)
    from scripts import lars_reply_metrics
    monkeypatch.setattr(lars_reply_metrics, "record_sent",
                        lambda *a, **k: calls["booked"].append(a))
    return calls


def press() -> dict:
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    return {"id": "cb1", "data": "rr|post|111",
            "from": {"username": "LarsOnMoltrust"},
            "message": {"message_id": 42, "chat": {"id": 7}, "date": now,
                        "text": f"""📝 Reply-Entwurf 1
Quelle (mention): https://x.com/owasp/status/111
9 Impressionen · 0 Likes

Entwurf ({len(DRAFT)}/280):
{DRAFT}

Freigabe postet die Reply innerhalb von fünf Minuten."""}}


def run(state=None) -> dict:
    decisions = {}
    rr.handle_decision(state if state is not None else {}, decisions, "auth",
                       press(), "post", "111", "2026-10-28", False)
    return decisions["111"]


# ── Kriterium 3: nur Lars' Tokens, kein Rueckfall ──

def test_k3_fires_moltrust_tokens_alone_send_nothing(button, monkeypatch):
    _env(monkeypatch, APP, MOLTRUST)
    assert rr.x_auth() is not None, "the @MolTrust pair must be complete here"
    result = run()
    assert button["posted"] == [], "Senden went out without Lars's tokens"
    assert button["http"] == []
    assert result["result"] == "lars_not_unlocked"


def test_k3_fires_lars_auth_never_reads_the_moltrust_pair(monkeypatch):
    _env(monkeypatch, APP, MOLTRUST)
    assert rr.lars_auth() is None


def test_k3_fires_half_an_unlock_is_no_unlock(button, monkeypatch):
    _env(monkeypatch, APP, MOLTRUST, {"X_LARS_ACCESS_TOKEN": "lars-token"})
    assert run()["result"] == "lars_not_unlocked"
    assert button["posted"] == []


def test_k3_quiet_with_lars_tokens_it_posts_signed_as_lars(button, monkeypatch):
    _env(monkeypatch, APP, MOLTRUST, LARS)
    result = run()
    assert len(button["posted"]) == 1
    text, kw = button["posted"][0]
    assert text == DRAFT
    assert kw["reply_to"] == "111"
    signer = kw["auth"].client
    assert signer.resource_owner_key == "lars-token"
    assert signer.resource_owner_key != MOLTRUST["X_ACCESS_TOKEN"]
    assert result["result"] == "posted"
    assert result["account"] == "LarsOnMoltrust"
    assert result["link"] == "https://x.com/LarsOnMoltrust/status/999"


def test_k3_quiet_the_sent_reply_opens_the_cm5_window(button, monkeypatch):
    _env(monkeypatch, APP, LARS)
    run()
    assert len(button["booked"]) == 1
    reply_id, target_id, _posted_at = button["booked"][0]
    assert (reply_id, target_id) == ("999", "111")


def test_k3_quiet_a_failed_booking_keeps_the_post(button, monkeypatch):
    _env(monkeypatch, APP, LARS)
    from scripts import lars_reply_metrics

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(lars_reply_metrics, "record_sent", broken)
    assert run()["result"] == "posted"


# ── Kriterium 4: die Antwort auf einen Druck ohne Freischaltung ──

def test_k4_fires_the_answer_names_the_unlock_the_text_and_the_post(button, monkeypatch):
    _env(monkeypatch, APP, MOLTRUST)
    run()
    assert len(button["edits"]) == 1
    answer = button["edits"][0]
    assert "Account nicht freigeschaltet" in answer
    assert f"<pre>{DRAFT}</pre>" in answer
    assert "https://x.com/i/status/111" in answer


def test_k4_fires_ahead_of_the_other_holds(button, monkeypatch):
    """The same answer whatever else would hold the draft — here the send cap."""
    _env(monkeypatch, APP)
    monkeypatch.setattr(rr.x_meter, "sends_paused", lambda: "Wochendeckel erreicht")
    assert run()["result"] == "lars_not_unlocked"


def test_k4_quiet_an_unlocked_press_says_nothing_about_an_unlock(button, monkeypatch):
    _env(monkeypatch, APP, LARS)
    run()
    assert not any("nicht freigeschaltet" in e for e in button["edits"])


def test_k4_a_failed_post_hands_the_text_back(button, monkeypatch):
    _env(monkeypatch, APP, LARS)
    monkeypatch.setattr(rr.x_post, "post", lambda text, **kw: None)
    assert run()["result"] == "post_failed"
    answer = button["edits"][-1]
    assert f"<pre>{DRAFT}</pre>" in answer
    assert "https://x.com/i/status/111" in answer


# ── Was CM3 nicht aendert ──

def test_the_retired_branch_still_answers_first(button, monkeypatch):
    """From 18.10 Senden is refused before Lars's tokens are asked (BW1, CM13).
    Unlocking the account does not turn the button back on."""
    _env(monkeypatch, APP, LARS)
    monkeypatch.setattr(rr, "reply_branch_retired", lambda now=None: True)
    assert run()["result"] == "retired"
    assert button["posted"] == []


def test_unarmed_sends_nothing_even_when_unlocked(button, monkeypatch):
    _env(monkeypatch, APP, LARS)
    monkeypatch.delenv(rr.ARM_FLAG, raising=False)
    assert run()["result"].startswith("not posted")
    assert button["posted"] == []
