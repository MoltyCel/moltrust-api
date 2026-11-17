"""Answers to decision messages straight from Telegram (ops MELD-RUECKKANAL).

Origin: job MELD-RUECKKANAL, 17.11.2026. Every message that asks Lars to
decide carries the code ENTSCH-YYYYMMDD and one line with the answer format;
"ENTSCH-20261117 1A 2A 3B" sent to the bot is filed where the Console tab
reads it (app/entscheidungen.py). These cases cover the code and the line in
the bundle and in scripts/zugang_ablauf.py, numbering across two messages of
one day, the parse, the filing, and the consumer from a telegram_inbox update
to the filed answer and the bot's reply.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import pathlib

import pytest

from app import entscheidungen as E
from app import notify

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 11, 17, 8, 0, tzinfo=dt.timezone.utc)
LARS = "424242"


@pytest.fixture
def reports(tmp_path, monkeypatch):
    d = tmp_path / "reports"
    monkeypatch.setenv("MOLTRUST_REPORTS_DIR", str(d))
    return d


@pytest.fixture
def lars_chat(monkeypatch):
    monkeypatch.setattr(notify, "chat_id_for", lambda ch: LARS)


def _bundle(sent, now=NOW):
    return E.senden(now, send=lambda t, **kw: sent.append((t, kw)) or True)


def _update(text, sender=LARS, chat=LARS, mid=900):
    return {"update_id": 1, "message": {"message_id": mid, "text": text,
                                        "from": {"id": int(sender), "is_bot": False},
                                        "chat": {"id": int(chat), "type": "private"}}}


def test_the_bundle_carries_the_code_and_the_answer_line(reports):
    E.vormerken("discovery-thread", "Thread a: eins.")
    E.vormerken("reseller-antrag", "Antrag b: zwei.", empfehlung="B")
    sent = []
    assert _bundle(sent) is True
    kopf = sent[0][0].splitlines()[0]
    assert "ENTSCH-20261117" in kopf
    assert kopf.endswith("Antwort hier im Chat: „ENTSCH-20261117 1A 2A“.")
    assert "Console-Tab" not in kopf


def test_numbers_run_on_through_the_day(reports):
    E.vormerken("discovery-thread", "Thread a: eins.")
    sent = []
    _bundle(sent)
    E.vormerken("discovery-thread", "Thread b: zwei.")
    E.vormerken("discovery-thread", "Thread c: drei.")
    _bundle(sent, NOW.replace(hour=20))
    zweite = sent[1][0].splitlines()
    assert "„ENTSCH-20261117 2A 3A“" in zweite[0]
    assert zweite[1].startswith("2. Thread b") and zweite[2].startswith("3. Thread c")
    # a new day starts at 1 again
    E.vormerken("discovery-thread", "Thread d: vier.")
    _bundle(sent, NOW + dt.timedelta(days=1))
    assert "„ENTSCH-20261118 1A“" in sent[2][0]


@pytest.mark.parametrize("text,erwartet", [
    ("ENTSCH-20261117 1A 2A 3B", ("ENTSCH-20261117", [(1, "A"), (2, "A"), (3, "B")], [])),
    ("  entsch-20261117 1a, 2b", ("ENTSCH-20261117", [(1, "A"), (2, "B")], [])),
    ("ENTSCH-20261117 1A bitte", ("ENTSCH-20261117", [(1, "A")], ["bitte"])),
    ("ENTSCH-20261117", ("ENTSCH-20261117", [], [])),
])
def test_parse(text, erwartet):
    assert E.zerlegen(text) == erwartet


@pytest.mark.parametrize("text", ["1A 2B", "Hallo", "ENTSCH-2026 1A", "/start", ""])
def test_not_an_answer(text):
    assert E.zerlegen(text) is None


def test_an_answer_is_filed_with_option_and_full_text(reports):
    E.vormerken("discovery-thread", "Thread a: eins.", volltext="Volltext A mit Entwurf")
    E.vormerken("reseller-antrag", "Antrag b: zwei.", empfehlung="B")
    _bundle([])
    erg = E.verarbeiten("ENTSCH-20261117 1A 2B", message_id=900, now=NOW)
    assert erg["abgelegt"] == [(1, "A"), (2, "B")] and erg["geschrieben"]
    zeilen = [json.loads(z) for z in open(E.antworten_pfad())]
    assert [(z["nr"], z["wahl"], z["art"]) for z in zeilen] == \
        [(1, "A", "discovery-thread"), (2, "B", "reseller-antrag")]
    assert zeilen[0]["option"] == "ein Console-Tab übernimmt"
    ablage = (reports / "2026-11-17.md").read_text()
    assert "entscheidungs-antwort · stats · ENTSCH-20261117" in ablage
    assert "ENTSCH-20261117 1A: ein Console-Tab übernimmt" in ablage
    assert "Volltext A mit Entwurf" in ablage


def test_unknown_numbers_and_options_are_named_not_filed(reports):
    E.vormerken("discovery-thread", "Thread a: eins.")
    _bundle([])
    erg = E.verarbeiten("ENTSCH-20261117 1Z 7A quatsch", now=NOW)
    assert erg["abgelegt"] == [] and erg["unbekannt"] == [7] and erg["ungueltig"] == ["1Z"]
    text = E.bestaetigung(erg)
    assert text.splitlines()[0] == "ENTSCH-20261117: nichts abgelegt."
    assert "keinen Punkt 7" in text and "1Z" in text and "quatsch" in text
    assert len(text.splitlines()) <= 4
    with pytest.raises(FileNotFoundError):
        open(E.antworten_pfad())


def test_consumer_from_inbox_update_to_filing_and_reply(reports, lars_chat):
    E.vormerken("discovery-thread", "Thread a: eins.")
    _bundle([])
    calls, replies = [], []

    def claim(consumer, kinds, limit=100, **kw):
        calls.append((consumer, kinds, kw))
        return [_update("ENTSCH-20261117 1B", mid=901)]

    n = E.konsumieren(claim=claim, send=lambda t, **kw: replies.append((t, kw)) or 1)
    assert n == 1
    assert calls == [("entscheidungen", ["message"], {"text_prefix": "ENTSCH-"})]
    assert replies == [("ENTSCH-20261117: abgelegt 1B.",
                        {"channel": notify.STATS, "reply_to": 901, "art": "befehl-antwort"})]
    assert json.loads(open(E.antworten_pfad()).readline())["wahl"] == "B"


def test_consumer_ignores_other_senders(reports, lars_chat):
    E.vormerken("discovery-thread", "Thread a: eins.")
    _bundle([])
    replies = []
    n = E.konsumieren(claim=lambda *a, **k: [_update("ENTSCH-20261117 1A", sender="7")],
                      send=lambda t, **kw: replies.append(t))
    assert n == 0 and replies == []


def test_the_reply_kind_is_delivered():
    from app import meldungsarten as MA
    assert MA.ARTEN["befehl-antwort"].klasse == MA.HANDELN


def test_zugang_ablauf_message_carries_code_and_number(reports, tmp_path):
    spec = importlib.util.spec_from_file_location("zugang_ablauf", ROOT / "scripts" / "zugang_ablauf.py")
    za = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(za)
    reg = tmp_path / "zugaenge.yaml"
    reg.write_text("zugaenge:\n  - name: X_TOKEN\n    beschreibung: Testzugang\n"
                   "    ablauf: 2026-11-24\n    erneuern: neu anlegen\n", encoding="utf-8")
    E.vormerken("discovery-thread", "Thread a: eins.")
    _bundle([])                               # takes number 1 of the day
    sent = []
    za.main(["--register", str(reg)], jetzt=NOW.replace(hour=9),
            send=lambda t, **kw: sent.append(t) or True)
    letzte = sent[0].splitlines()[-1]
    assert letzte == ("Antwort hier im Chat: „ENTSCH-20261117 2A“ "
                      "(A ein Console-Tab übernimmt, B liegen lassen)")
    erg = E.verarbeiten("ENTSCH-20261117 2A", now=NOW.replace(hour=10))
    assert erg["abgelegt"] == [(2, "A")]
    assert json.loads(open(E.antworten_pfad()).readline())["art"] == "zugang-abgelaufen"
