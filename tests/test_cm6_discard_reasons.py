"""CM6: every draft ends as one line, and a discard carries its reason.

Herkunft: ops CM6 (Freigabe Lars 22.10.2026). Nach vier Wochen soll sich
trennen lassen, ob die Entwuerfe schwach sind oder der Kanal. Dafuer:

  K2  nach Verwerfen fragt Telegram nach dem Grund, mit vier Kurzknoepfen
      und Freitext; ohne Antwort binnen zwoelf Stunden steht "ohne Grund"
  K3  eine Zeile je Entwurf in data/reply_radar_decisions.jsonl
  K4  der CM5-Zwischenstand nennt je Ergebnis die Anzahl und die Gruende
      der Verwuerfe nach Haeufigkeit

Jede Pruefung hat ihre Gegenprobe: der Befund loest aus, und der
Normalbetrieb schweigt.
"""
import datetime
import json

import pytest

from agents import reply_decisions as rd
from agents import reply_radar as rr
from app import telegram_inbox

T0 = datetime.datetime(2026, 10, 27, 6, 5, tzinfo=datetime.timezone.utc)
TWEET = {"id": "1983000000000000001", "_author": "owasp", "_source": "list",
         "text": "Wer prueft, was ein Agent darf?"}


def at(hours: float) -> datetime.datetime:
    return T0 + datetime.timedelta(hours=hours)


@pytest.fixture
def files(monkeypatch, tmp_path):
    """Both CM6 files in a directory of this test alone."""
    monkeypatch.setattr(rd, "open_path", lambda: str(tmp_path / "open.json"))
    monkeypatch.setattr(rd, "decisions_path", lambda: str(tmp_path / "decisions.jsonl"))
    return tmp_path


def lines(tmp_path) -> list[dict]:
    p = tmp_path / "decisions.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture
def telegram(monkeypatch):
    """The Telegram side without a network: what was sent, what was edited."""
    seen = {"sent": [], "edits": [], "claims": []}

    def fake_send(text, **kw):
        seen["sent"].append({"text": text, "markup": kw.get("reply_markup")})
        return 500 + len(seen["sent"])

    monkeypatch.setattr(rr.notify, "send_telegram_message", fake_send)
    monkeypatch.setattr(rr, "edit_message",
                        lambda chat, mid, text: seen["edits"].append((mid, text)))
    return seen


def draft_message(hours: float = 0) -> dict:
    return {"message_id": 42, "chat": {"id": 7},
            "date": int(at(hours).timestamp()),
            "text": ("\U0001f4dd Reply-Entwurf 1\n"
                     f"Quelle (list): https://x.com/owasp/status/{TWEET['id']}\n"
                     "Entwurf (20/280):\n<pre>Ein Mandat.</pre>\n\n"
                     "Voice-Gate: bestanden\n")}


def press(verb: str, message: dict | None = None) -> dict:
    return {"id": "cb", "data": f"rr|{verb}|{TWEET['id']}",
            "from": {"username": "lars"}, "message": message or draft_message()}


# ── K2: die Frage nach Verwerfen ──

def test_verwerfen_asks_with_four_short_keys_and_free_text(files, telegram):
    rd.register(TWEET, True, [], 42, chat_id=7, now=T0)
    rr.handle_decision({}, {}, None, press("drop"), "drop", TWEET["id"],
                       "2026-10-27", dry_run=False)

    assert len(telegram["sent"]) == 1
    q = telegram["sent"][0]
    assert "warum" in q["text"] and "ohne Grund" in q["text"]
    labels = [k["text"] for row in q["markup"]["inline_keyboard"] for k in row]
    assert labels[:4] == ["Ton", "Inhalt falsch", "Zielpost unpassend", "Timing"]
    assert "Freitext" in labels[4]
    assert len(labels) == 5
    # Every key is a callback within Telegram's 64 bytes, none a link.
    for row in q["markup"]["inline_keyboard"]:
        for k in row:
            assert "url" not in k and len(k["callback_data"].encode()) <= 64
    # Asked, not yet closed: no line before the answer.
    assert lines(files) == []


def test_senden_asks_nothing(files, telegram, monkeypatch):
    """Gegenprobe: nur Verwerfen fragt nach dem Grund."""
    monkeypatch.setattr(rr, "reply_branch_retired", lambda: True)
    rr.handle_decision({}, {}, None, press("post"), "post", TWEET["id"],
                       "2026-10-27", dry_run=False)
    assert telegram["sent"] == []


def test_a_short_key_records_the_reason(files, telegram):
    rd.register(TWEET, True, [], 42, chat_id=7, now=T0)
    rr.handle_decision({}, {}, None, press("drop"), "drop", TWEET["id"],
                       "2026-10-27", dry_run=False)
    question = {"message_id": 501, "chat": {"id": 7}, "text": "warum?"}
    rr.handle_reason(press("why-ziel", question), "why-ziel", TWEET["id"], False)

    [row] = lines(files)
    assert row["result"] == "verworfen"
    assert row["reason"] == "Zielpost unpassend"
    assert (501, "\U0001f5d1 Verworfen — Grund: Zielpost unpassend") in telegram["edits"]

    # A second press on the same question writes nothing more.
    rr.handle_reason(press("why-ton", question), "why-ton", TWEET["id"], False)
    assert len(lines(files)) == 1


def test_free_text_arrives_as_a_reply_and_is_the_reason(files, telegram, monkeypatch):
    rd.register(TWEET, True, [], 42, chat_id=7, now=T0)
    rr.handle_decision({}, {}, None, press("drop"), "drop", TWEET["id"],
                       "2026-10-27", dry_run=False)
    question = {"message_id": 501, "chat": {"id": 7}}
    rr.handle_reason(press("why-frei", question), "why-frei", TWEET["id"], False)
    # The prompt asks for a reply; Telegram's client opens the reply field.
    prompt = telegram["sent"][-1]
    assert prompt["markup"]["force_reply"] is True
    assert set(rd.awaiting_reason()) == {501, 502}

    asked = {}

    def fake_claim(consumer, kinds, limit=100, **kw):
        asked.update(consumer=consumer, kinds=kinds, **kw)
        return [{"update_id": 9, "message": {
            "chat": {"id": 7}, "text": "  Zahl stimmt nicht mit der Quelle  ",
            "reply_to_message": {"message_id": 502}}}]

    monkeypatch.setattr(rr.telegram_inbox, "claim", fake_claim)
    assert rr.consume_reason_texts() == 1
    # Only replies to the two open messages are claimed, nothing else typed.
    assert asked["kinds"] == ["message"]
    assert sorted(asked["reply_to"]) == [501, 502]

    [row] = lines(files)
    assert row["result"] == "verworfen"
    assert row["reason"] == "Zahl stimmt nicht mit der Quelle"
    assert rd.awaiting_reason() == {}


def test_nothing_waiting_claims_nothing(files, monkeypatch):
    """Schweigeprobe: ohne offene Frage wird keine Nachricht beansprucht."""
    monkeypatch.setattr(rr.telegram_inbox, "claim",
                        lambda *a, **k: pytest.fail("claimed without a question open"))
    assert rr.consume_reason_texts() == 0


def test_twelve_hours_without_an_answer_is_ohne_grund(files, telegram):
    rd.register(TWEET, True, [], 42, chat_id=7, now=T0)
    rd.mark_asked(TWEET["id"], draft_message(), 501, now=at(1))

    assert rd.due(now=at(12.9)) == []          # 11.9 h after the question
    assert lines(files) == []

    closed = rd.due(now=at(13.1))              # 12.1 h after the question
    assert [(c[1], c[2]) for c in closed] == [("verworfen", "ohne Grund")]
    [row] = lines(files)
    assert row["reason"] == "ohne Grund"


def test_the_sweep_says_so_on_the_question(files, telegram, monkeypatch):
    rd.register(TWEET, True, [], 42, chat_id=7, now=T0)
    rd.mark_asked(TWEET["id"], draft_message(), 501, now=T0)
    monkeypatch.setattr(rd, "_now", lambda: at(13))
    assert rr.sweep_decisions() == 1
    assert telegram["edits"][0][0] == 501
    assert "ohne Grund" in telegram["edits"][0][1]


# ── K3: eine Zeile je Entwurf ──

def test_a_draft_nobody_pressed_expires_after_twelve_hours(files):
    rd.register(TWEET, False, ["gate 2 (h): unsourced 42"], 42, chat_id=7, now=T0)
    assert rd.due(now=at(11)) == []
    rd.due(now=at(13))
    [row] = lines(files)
    assert row == {
        "draft_id": f"rr-{TWEET['id']}",
        "target": f"https://x.com/owasp/status/{TWEET['id']}",
        "at": at(13).isoformat(),
        "offered_at": T0.isoformat(),
        "result": "verfallen",
        "reason": None,
        "voice_gate": "Befunde",
        "voice_gate_findings": ["gate 2 (h): unsourced 42"],
    }


def test_a_sent_draft_is_one_line_gesendet(files, telegram, monkeypatch):
    monkeypatch.setattr(rr, "reply_branch_retired", lambda: False)
    monkeypatch.setattr(rr, "draft_expired", lambda msg, now=None: False)
    monkeypatch.setenv(rr.ARM_FLAG, "1")
    monkeypatch.setattr(rr, "DAILY_MAX", 8)
    monkeypatch.setattr(rr.x_meter, "sends_paused", lambda: None)
    monkeypatch.setattr(rr, "target_ok", lambda a, t: (True, "", {"id": t, "text": "x"}))
    monkeypatch.setattr(rr, "fetch_sources", lambda urls: {})
    monkeypatch.setattr(rr, "check", lambda t, s: (True, [], {}))
    monkeypatch.setattr(rr, "followers_now", lambda auth: 27)
    monkeypatch.setattr(rr.x_post, "post", lambda *a, **k: "999")
    msg = draft_message()
    msg["text"] = msg["text"].replace("Quelle (list)", "Quelle (mention)") \
        .replace("<pre>Ein Mandat.</pre>", "Ein Mandat.")
    rd.register(TWEET, True, [], 42, chat_id=7, now=T0)

    rr.handle_decision({}, {}, "auth", press("post", msg), "post", TWEET["id"],
                       "2026-10-27", dry_run=False)

    [row] = lines(files)
    assert row["result"] == "gesendet"
    assert row["voice_gate"] == "bestanden"
    # Closed: the sweep has nothing left to expire.
    assert rd.due(now=at(30)) == []
    assert len(lines(files)) == 1


def test_a_late_press_on_senden_is_one_line_verfallen(files, telegram, monkeypatch):
    monkeypatch.setattr(rr, "reply_branch_retired", lambda: False)
    rd.register(TWEET, True, [], 42, chat_id=7, now=T0)
    rr.handle_decision({}, {}, None, press("post", draft_message(-13)), "post",
                       TWEET["id"], "2026-10-27", dry_run=False)
    [row] = lines(files)
    assert row["result"] == "verfallen"
    assert row["reason"] is None


def test_close_twice_is_still_one_line(files):
    rd.register(TWEET, True, [], 42, chat_id=7, now=T0)
    assert rd.close(TWEET["id"], rd.DROPPED, reason="Ton", now=at(1)) is True
    assert rd.close(TWEET["id"], rd.EXPIRED, now=at(14)) is False
    assert [r["result"] for r in lines(files)] == ["verworfen"]


def test_a_draft_from_before_the_file_still_gets_its_line(files, telegram):
    """A Verwerfen on a message sent before CM6 was deployed."""
    rr.handle_decision({}, {}, None, press("drop"), "drop", TWEET["id"],
                       "2026-10-27", dry_run=False)
    rr.handle_reason(press("why-timing"), "why-timing", TWEET["id"], False)
    [row] = lines(files)
    assert row["target"] == f"https://x.com/owasp/status/{TWEET['id']}"
    assert row["voice_gate"] == "bestanden"
    assert row["reason"] == "Timing"


def test_the_run_registers_each_draft_it_sends(files, monkeypatch, tmp_path):
    """run() hands every draft it sends to the file, with its gate result."""
    from tests.test_cm4_send_cap import RUN_AT, _candidate
    monkeypatch.setattr(rr, "_utcnow", lambda: RUN_AT)
    monkeypatch.setattr(rr, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(rr, "HEARTBEAT_FILE", str(tmp_path / "hb.json"))
    monkeypatch.setattr(rr, "reply_branch_retired", lambda now=None: True)
    monkeypatch.setattr(rr, "drafter_retired", lambda now=None: False)
    monkeypatch.setattr(rr, "x_auth", lambda: "auth")
    monkeypatch.setattr(rr.x_meter, "reads_paused", lambda *a, **k: None)
    monkeypatch.setattr(rr, "load_targets", lambda: {
        a: {"tier": 1, "group": "builders"} for a in ("alice", "bob")})
    monkeypatch.setattr(rr, "gather", lambda *a, **k: [
        _candidate("1", "alice"), _candidate("2", "bob")])
    monkeypatch.setattr(rr, "load_kb", lambda: {})
    texts = iter(["ERC-8004 keeps its registry on chain.",
                  "The A2A agent card is served from .well-known."])
    monkeypatch.setattr(rr, "draft_reply", lambda t, kb, avoid=None: (next(texts), []))
    monkeypatch.setattr(rr, "fetch_sources", lambda urls: {})
    verdicts = iter([(True, [], {}), (False, ["gate 2 (f): no number"], {})])
    monkeypatch.setattr(rr, "check", lambda t, s: next(verdicts))
    monkeypatch.setattr(rr.notify, "send_telegram", lambda *a, **k: True)
    monkeypatch.setattr(rr.notify, "chat_id_for", lambda ch: 7)
    ids = iter([4242, 4243])
    monkeypatch.setattr(rr.notify, "send_telegram_message", lambda body, **kw: next(ids))

    rr.run()

    with rd._registry() as reg:
        drafts = dict(reg["drafts"])
    assert set(drafts) == {"1", "2"}
    assert drafts["1"]["voice_gate"] == "bestanden"
    assert drafts["1"]["message_id"] == 4242
    assert drafts["2"]["voice_gate"] == "Befunde"
    assert drafts["2"]["voice_gate_findings"] == ["gate 2 (f): no number"]
    # Today's keyless drafts (CM14) have nothing to press: twelve hours later
    # both are one line each, verfallen.
    rd.due(now=RUN_AT + datetime.timedelta(hours=13))
    assert sorted((r["draft_id"], r["result"]) for r in lines(files)) == [
        ("rr-1", "verfallen"), ("rr-2", "verfallen")]


# ── K4: der Zwischenstand aus CM5 ──

def test_the_summary_counts_results_and_ranks_reasons(files):
    rows = [("1", "verworfen", "Ton"), ("2", "verworfen", "Timing"),
            ("3", "verworfen", "Ton"), ("4", "gesendet", None),
            ("5", "verfallen", None), ("6", "verworfen", "ohne Grund")]
    for tid, result, reason in rows:
        rd.close(tid, result, reason=reason, now=at(24))
    # Outside the window: not counted.
    rd.close("7", "verworfen", reason="Inhalt falsch", now=at(24 * 40))

    s = rd.summary(T0, at(24 * 28))
    assert s["counts"] == {"gesendet": 1, "verworfen": 4, "verfallen": 1}
    assert s["reasons"] == [("Ton", 2), ("Timing", 1), ("ohne Grund", 1)]
    text = "\n".join(rd.format_summary(s))
    assert "gesendet 1, verworfen 4, verfallen 1" in text
    assert text.index("Ton 2") < text.index("Timing 1")


def test_the_cm5_interim_carries_the_decisions(files, monkeypatch, tmp_path):
    from scripts import lars_reply_metrics as lrm
    monkeypatch.setattr(lrm, "metrics_path", lambda: str(tmp_path / "m.jsonl"))
    rd.close("1", "verworfen", reason="Ton", now=at(24))
    rd.close("2", "verfallen", now=at(48))
    s = lrm.summary(T0, at(24 * 28))
    text = lrm.format_report("Zwischenstand nach vier Wochen", T0, at(24 * 28), s)
    assert "Entwürfe: gesendet 0, verworfen 1, verfallen 1" in text
    assert "Gründe der Verwürfe: Ton 1" in text


def test_no_discards_says_so(files):
    """Gegenprobe: ohne Verwurf steht keine erfundene Rangliste da."""
    rd.close("2", "verfallen", now=at(48))
    text = "\n".join(rd.format_summary(rd.summary(T0, at(24 * 28))))
    assert "keine Verwürfe" in text


# ── Die Inbox: ThreadWatch nimmt nur Befehle, der Radar nur Antworten ──

class _Cur:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.log.append((sql, params))

    def fetchall(self):
        return []


class _Conn(_Cur):
    def cursor(self):
        return _Cur(self.log)

    def close(self):
        pass


@pytest.fixture
def sql_log(monkeypatch):
    import sys
    import types
    log = []
    fake = types.SimpleNamespace(connect=lambda dsn: _Conn(log))
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    return log


def test_threadwatch_claims_commands_only(sql_log):
    telegram_inbox.claim("threadwatch", ["message"], commands_only=True)
    sql, params = sql_log[0]
    assert "LIKE '/%%'" in sql
    assert params == ("threadwatch", ["message"], 100)


def test_the_radar_claims_only_replies_to_its_questions(sql_log):
    telegram_inbox.claim("reply_radar_reason", ["message"], reply_to=[501, 502])
    sql, params = sql_log[0]
    assert "reply_to_message" in sql and "ANY(%s)" in sql
    assert params == ("reply_radar_reason", ["message"], ["501", "502"], 100)


def test_an_empty_reply_list_claims_nothing(sql_log):
    assert telegram_inbox.claim("reply_radar_reason", ["message"], reply_to=[]) == []
    assert sql_log == []


def test_a_plain_claim_is_unchanged(sql_log):
    telegram_inbox.claim("reply_radar_post", ["callback_query"], limit=50)
    sql, params = sql_log[0]
    assert "LIKE" not in sql and "reply_to_message" not in sql
    assert params == ("reply_radar_post", ["callback_query"], 50)


def test_reason_presses_are_acknowledged(monkeypatch):
    posted = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    import requests
    monkeypatch.setattr(requests, "post",
                        lambda url, data=None, timeout=None: posted.append((url, data)))
    telegram_inbox.acknowledge_callback({"callback_query": {
        "id": "c", "data": "rr|why-ton|1",
        "message": {"chat": {"id": 7}, "message_id": 501}}})
    assert posted and posted[0][1]["text"] == "✔️ Grund festgehalten"
