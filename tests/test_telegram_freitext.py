"""Every message from Lars gets a reply (21.11.2026).

Origin: Lars approved CB-BAZAAR by Telegram in free text, "CB-BAZAAR 0,05 USDC
freigegeben", at 09:50Z on 21.11.2026. No consumer of telegram_inbox took it,
so nothing was filed and the bot said nothing. app/entscheidungen.py
frei_konsumieren() now takes what the narrower consumers left, files it and
replies. These cases cover the claim (finding and silence), the filing, the
reply, other senders, and that ENTSCH- answers keep their own consumer.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from app import entscheidungen as E
from app import notify, telegram_inbox

LARS = "424242"


@pytest.fixture
def reports(tmp_path, monkeypatch):
    monkeypatch.setenv("MOLTRUST_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("MOLTRUST_NOTIFY_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


@pytest.fixture
def lars_chat(monkeypatch):
    monkeypatch.setattr(notify, "chat_id_for", lambda ch: LARS)


def _update(text, sender=LARS, chat=LARS, mid=950):
    return {"update_id": 1, "message": {"message_id": mid, "text": text,
                                        "from": {"id": int(sender), "is_bot": False},
                                        "chat": {"id": int(chat), "type": "private"}}}


class _Cur:
    def __init__(self, log, rows=None):
        self.log, self.rows = log, rows or []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.log.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else (0,)


class _Conn(_Cur):
    def cursor(self):
        return _Cur(self.log, self.rows)

    def close(self):
        pass


@pytest.fixture
def sql_log(monkeypatch):
    log = []
    monkeypatch.setitem(sys.modules, "psycopg2",
                        types.SimpleNamespace(connect=lambda dsn: _Conn(log)))
    return log


def test_the_leftover_claim_takes_old_text_and_skips_commands(sql_log):
    telegram_inbox.claim("entscheidungen_frei", ["message"], limit=20, leftover=(600, 604800))
    sql, params = sql_log[0]
    assert "NOT LIKE '/%%'" in sql
    assert "ts < now() - make_interval(secs => %s)" in sql
    assert "ts > now() - make_interval(secs => %s)" in sql
    assert params == ("entscheidungen_frei", ["message"], 600, 604800, 20)


def test_a_plain_claim_has_no_age_window(sql_log):
    telegram_inbox.claim("reply_radar_post", ["callback_query"], limit=50)
    sql, _ = sql_log[0]
    assert "make_interval" not in sql


def test_free_text_from_lars_is_filed_and_answered(reports, lars_chat):
    calls, replies = [], []

    def claim(consumer, kinds, limit=100, **kw):
        calls.append((consumer, kinds, kw))
        return [_update("CB-BAZAAR 0,05 USDC freigegeben", mid=951)]

    n = E.frei_konsumieren(claim=claim, send=lambda t, **kw: replies.append((t, kw)) or 1)
    assert n == 1
    assert calls == [("entscheidungen_frei", ["message"],
                      {"leftover": (E.FREI_MIN_ALTER, E.FREI_MAX_ALTER)})]
    text, kw = replies[0]
    assert text.startswith("Erhalten und abgelegt: „CB-BAZAAR 0,05 USDC freigegeben“.")
    assert "#14" in text
    assert kw == {"channel": notify.STATS, "reply_to": 951, "art": "befehl-antwort"}
    zeile = json.loads(open(E.antworten_pfad()).readline())
    assert zeile["kennung"] == "FREI" and zeile["text"] == "CB-BAZAAR 0,05 USDC freigegeben"


def test_nothing_left_means_no_reply(reports, lars_chat):
    replies = []
    assert E.frei_konsumieren(claim=lambda *a, **k: [],
                              send=lambda t, **kw: replies.append(t)) == 0
    assert replies == []


def test_other_senders_get_nothing_filed(reports, lars_chat):
    replies = []
    n = E.frei_konsumieren(claim=lambda *a, **k: [_update("freigegeben", sender="7", chat="7")],
                           send=lambda t, **kw: replies.append(t))
    assert n == 0 and replies == []


def test_a_failed_filing_says_so(reports, lars_chat, monkeypatch):
    monkeypatch.setattr(E, "_anhaengen", lambda *a, **k: False)
    replies = []
    n = E.frei_konsumieren(claim=lambda *a, **k: [_update("ok")],
                           send=lambda t, **kw: replies.append(t))
    assert n == 0
    assert replies[0].startswith("Nicht abgelegt")


def test_a_long_message_is_quoted_short(reports, lars_chat):
    text = "x" * 500
    assert len(E.frei_bestaetigung(text, True).splitlines()[0]) < 160


def test_the_fallback_runs_after_the_entsch_consumer():
    src = open("agents/reply_radar.py", encoding="utf-8").read()
    assert src.index("entscheidungen.konsumieren()") < src.index("entscheidungen.frei_konsumieren()")


def test_the_watch_counts_and_stays_silent_at_zero(sql_log):
    assert E.liegengeblieben() == 0
    sql, params = sql_log[0]
    assert "consumed_by IS NULL" in sql and params == (1800, E.FREI_MAX_ALTER)
