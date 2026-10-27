"""The weekly payout of the standing round (ops G4-5).

Lars, 27.10.2026: payout only after his "ok" to the weekly Telegram message.
No database, no network, no key, no chain: Telegram, the payer, the web
result, the budget and the booking are injected. The payer here is a fake
that writes proof lines; the real one (scripts/dauerrunde_zahle.cjs) is only
syntax-checked and asked to refuse a plan without a release, which it does
before it touches a key.

What the tests prove:
- no payout without "ok" (no answer, wrong text, plain message, deadline);
- one "ok" pays exactly the proposed week, once - a second "ok", a rerun and
  a crashed run pay nothing more;
- expired, foreign and outdated answers do nothing;
- a week without activation asks nothing and pays nothing.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from scripts import dauerrunde_auszahlung as A
from scripts import dauerrunde_woche as W

KW = "2026-W45"
CHAT = "4242"
T0 = datetime(2026, 11, 9, 6, 20, tzinfo=timezone.utc)
ADR1 = "0x" + "1" * 40
ADR2 = "0x" + "2" * 40


def zeilen(*paare, solana=None):
    out = [{"adresse": a, "kette": "base", "betrag": f"{m / 1e6:.6f}",
            "betrag_mikro": m, "dids": [f"did:moltrust:{i:016d}"],
            "rollen": ["empfohlen"], "aktivierungen": [{"did": "x"}]}
           for i, (a, m) in enumerate(paare)]
    if solana:
        out.append({"adresse": solana, "kette": "solana", "betrag": "0.025000",
                    "betrag_mikro": 25000, "dids": ["did:moltrust:s"],
                    "rollen": ["empfehlend"], "aktivierungen": [{"did": "y"}]})
    return out


def woche(ausgang: pathlib.Path, rows, version=1, kw=KW):
    d = ausgang / kw
    d.mkdir(parents=True, exist_ok=True)
    (d / f"zeilen-v{version}.json").write_text(json.dumps(rows))
    doc = {"kw": kw, "version": version, "stand": "vorgeschlagen" if rows else
           "nichts_zu_zahlen", "abgleich": "2 von 2", "zeilen": []}
    (d / f"result-v{version}.json").write_text(json.dumps(doc))
    return d, doc


class Welt:
    """Everything outside the program, recorded."""

    def __init__(self, tmp_path, rows=None, version=1):
        self.ausgang = tmp_path / "dauerrunde"
        self.rows = zeilen((ADR1, 25000), (ADR2, 50000)) if rows is None else rows
        self.d, self.live = woche(self.ausgang, self.rows, version)
        self.gesendet, self.gemeldet, self.inbox = [], [], []
        self.zahlungen, self.gebucht, self.veroeffentlicht = [], [], []
        self.rest = 18_598_802
        self.zahler_rc = 0
        self.next_id = 100

    # Telegram
    def senden(self, text):
        self.next_id += 1
        self.gesendet.append((self.next_id, text))
        return self.next_id

    def melden(self, text, alarm=False):
        self.gemeldet.append((alarm, text))

    def antwort(self, text="ok", *, auf=None, datum=None, chat=CHAT, von=None,
                typ="private", bot=False):
        auf = auf if auf is not None else self.gesendet[-1][0]
        datum = datum or (T0 + timedelta(minutes=30))
        self.inbox.append({"update_id": 5000 + len(self.inbox), "message": {
            "message_id": 9000 + len(self.inbox),
            "date": int(datum.timestamp()), "text": text,
            "chat": {"id": int(chat), "type": typ},
            "from": {"id": int(von or chat), "is_bot": bot},
            "reply_to_message": {"message_id": auf}}})

    def holen(self, ids):
        mine = [u for u in self.inbox
                if u["message"]["reply_to_message"]["message_id"] in ids]
        self.inbox = [u for u in self.inbox if u not in mine]  # claimed = consumed
        return mine

    # Chain, web, budget
    def zahler(self, plan_pfad, nachweis):
        plan = json.loads(pathlib.Path(plan_pfad).read_text())
        self.zahlungen.append(plan)
        if self.zahler_rc == 3:
            return 3
        with open(nachweis, "a") as fh:
            for i, z in enumerate(plan["zeilen"]):
                fh.write(json.dumps({
                    "ts": "2026-11-09T07:00:00.000Z", "kw": plan["kw"],
                    "adresse": z["adresse"], "betrag_mikro": z["betrag_mikro"],
                    "betrag": f"{int(z['betrag_mikro']) / 1e6:.6f}",
                    "tx": "0x" + hashlib.sha256(f"{plan['kw']}{i}{len(self.zahlungen)}".encode()).hexdigest(),
                    "block": 1000 + i, "status": "success"}) + "\n")
        return self.zahler_rc

    def web(self, kw):
        return self.live

    def budget(self):
        return self.rest

    def buchen(self, kw, r, rest, d):
        self.gebucht.append((kw, r["tx"], rest))
        return A.budget_zeile(kw, r, rest, d)

    def veroeffentlichen(self, kw, plan):
        self.veroeffentlicht.append((kw, plan))
        return {"pr": 999, "merge": "abc", "version": plan["version"]}

    def lauf(self, jetzt=T0):
        return A.lauf(jetzt, ausgang=self.ausgang, senden=self.senden,
                      chat_id=CHAT, holen=self.holen, web=self.web,
                      budget_rest_mikro=self.budget, zahler=self.zahler,
                      buchen=self.buchen, veroeffentlichen=self.veroeffentlichen,
                      melden=self.melden)

    def anfrage(self):
        return json.loads((self.d / "anfrage.json").read_text())


@pytest.fixture
def welt(tmp_path):
    return Welt(tmp_path)


# --- Asking -------------------------------------------------------------------

def test_one_request_per_version_with_sum_recipients_and_deadline(welt):
    welt.lauf()
    welt.lauf(T0 + timedelta(minutes=10))
    assert len(welt.gesendet) == 1
    text = welt.gesendet[0][1]
    assert "0.075000 USDC an 2 Adressen" in text
    assert "0x1111…1111" in text and "0x2222…2222" in text
    assert ADR1 not in text and ADR2 not in text  # no full address in the chat
    assert "mit ok antworten, bis 16.11.2026 06:20 UTC" in text
    a = welt.anfrage()
    assert a["status"] == "offen" and a["summe_mikro"] == 75000
    assert welt.zahlungen == []


def test_a_week_without_activation_asks_nothing_and_pays_nothing(tmp_path):
    w = Welt(tmp_path, rows=[])
    w.lauf()
    w.lauf(T0 + timedelta(days=8))
    assert w.gesendet == [] and w.zahlungen == []
    assert not (w.d / "anfrage.json").exists()


def test_solana_rows_are_named_and_never_paid(tmp_path):
    w = Welt(tmp_path, rows=zeilen((ADR1, 25000), solana="So1anaAddre55xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"))
    w.lauf()
    assert "Nicht zahlbar auf Base" in w.gesendet[0][1]
    w.antwort("ok")
    w.lauf(T0 + timedelta(hours=1))
    assert [z["adresse"] for z in w.zahlungen[0]["zeilen"]] == [ADR1]


# --- No payout without ok ------------------------------------------------------

def test_no_answer_pays_nothing_and_the_deadline_closes_the_week(welt):
    welt.lauf()
    for h in (1, 24, 24 * 6):
        welt.lauf(T0 + timedelta(hours=h))
    assert welt.zahlungen == []
    welt.lauf(T0 + timedelta(days=7, minutes=1))
    assert welt.anfrage()["status"] == "abgelaufen"
    assert welt.zahlungen == []
    assert any("Frist ohne ok verstrichen" in t for _a, t in welt.gemeldet)


@pytest.mark.parametrize("text", ["nein", "okay?", "ok, aber spaeter", "o k", "", "/ok"])
def test_any_text_other_than_ok_pays_nothing(welt, text):
    welt.lauf()
    welt.antwort(text)
    welt.lauf(T0 + timedelta(hours=1))
    assert welt.zahlungen == []
    assert not (welt.d / "freigabe.json").exists()
    assert welt.anfrage()["status"] == "offen"


@pytest.mark.parametrize("text", ["ok", "OK", "Ok.", " ok! "])
def test_ok_in_its_plain_forms_releases(welt, text):
    welt.lauf()
    welt.antwort(text)
    welt.lauf(T0 + timedelta(hours=1))
    assert len(welt.zahlungen) == 1


def test_a_plain_ok_that_replies_to_nothing_is_never_claimed(welt):
    welt.lauf()
    welt.antwort("ok", auf=12345)  # not our message
    welt.lauf(T0 + timedelta(hours=1))
    assert welt.zahlungen == [] and len(welt.inbox) == 1  # left for others


# --- Exactly once ---------------------------------------------------------------

def test_one_ok_pays_exactly_the_proposed_week_once(welt):
    welt.lauf()
    welt.antwort("ok")
    erg = welt.lauf(T0 + timedelta(hours=1))
    assert len(welt.zahlungen) == 1
    plan = welt.zahlungen[0]
    assert plan["kw"] == KW and plan["version"] == 1
    assert [(z["adresse"], z["betrag_mikro"]) for z in plan["zeilen"]] == \
        [(ADR1, "25000"), (ADR2, "50000")]
    assert plan["zahler"] == A.ZAHLER and plan["freigabe"]["update_id"] == 5000
    assert plan["freigabe"]["zeilen_sha256"] == hashlib.sha256(
        (welt.d / "zeilen-v1.json").read_bytes()).hexdigest()
    assert "2 Zahlungen, zusammen 0.075000 USDC" in erg[0]

    # Booked: one pool_spend/budget line per tx with the falling rest.
    assert [rest for _kw, _tx, rest in welt.gebucht] == [18_573_802, 18_523_802]
    budget = [json.loads(z) for z in (welt.d / "budget-zeilen.jsonl").read_text().splitlines()]
    assert [b["rest"] for b in budget] == ["18.573802", "18.523802"]
    assert all(b["tx"].startswith("0x") for b in budget)

    # result.json replaced by a new version naming each payment.
    kw, pub = welt.veroeffentlicht[0]
    neu = pub["dateien"]["result.json"]
    assert pub["version"] == 2 and neu["ersetzt"] == "result-1.json"
    assert neu["stand"] == "ausgezahlt" and neu["auszahlung"]["weg"] == "direkt"
    assert [z["tx"] for z in neu["auszahlung"]["zahlungen"]] == [b["tx"] for b in budget]
    assert ADR1 not in json.dumps(neu)  # shortened in public
    assert pub["dateien"]["result-1.json"] == welt.live

    # A second ok, a rerun and many more runs pay nothing more.
    welt.antwort("ok")
    for h in (2, 3, 48):
        welt.lauf(T0 + timedelta(hours=h))
    assert len(welt.zahlungen) == 1 and len(welt.veroeffentlicht) == 1
    assert welt.anfrage()["status"] == "bezahlt"


def test_two_oks_in_the_same_run_pay_once(welt):
    welt.lauf()
    welt.antwort("ok")
    welt.antwort("ok", datum=T0 + timedelta(minutes=31))
    welt.lauf(T0 + timedelta(hours=1))
    assert len(welt.zahlungen) == 1
    assert any("Anfrage steht auf freigegeben" in t for _a, t in welt.gemeldet)


def test_a_crashed_payment_is_never_started_again(welt):
    welt.lauf()
    welt.antwort("ok")
    # Simulate a crash after the start marker: the payer never returned.
    orig = welt.zahler

    def absturz(plan_pfad, nachweis):
        welt.zahlungen.append("abgestuerzt")
        raise SystemExit("killed")

    welt.zahler = absturz
    with pytest.raises(SystemExit):
        welt.lauf(T0 + timedelta(hours=1))
    welt.zahler = orig
    welt.lauf(T0 + timedelta(hours=2))
    welt.lauf(T0 + timedelta(hours=3))
    assert welt.zahlungen == ["abgestuerzt"]
    assert any("begonnen und nicht abgeschlossen" in t for a, t in welt.gemeldet if a)


def test_a_partial_payment_is_reported_and_not_repeated(welt):
    welt.lauf()
    welt.antwort("ok")
    welt.zahler_rc = 1
    welt.lauf(T0 + timedelta(hours=1))
    welt.lauf(T0 + timedelta(hours=2))
    assert len(welt.zahlungen) == 1
    assert welt.veroeffentlicht == []
    assert any("unvollstaendig" in t or "nicht abgeschlossen" in t
               for a, t in welt.gemeldet if a)


# --- Expired, foreign, outdated ---------------------------------------------------

def test_an_ok_after_the_deadline_does_nothing(welt):
    welt.lauf()
    welt.antwort("ok", datum=T0 + timedelta(days=7, minutes=5))
    welt.lauf(T0 + timedelta(days=7, minutes=10))
    assert welt.zahlungen == []
    assert not (welt.d / "freigabe.json").exists()


def test_an_ok_arriving_late_but_dated_in_time_counts(welt):
    """The Telegram date decides, not when our cron happens to read it."""
    welt.lauf()
    welt.antwort("ok", datum=T0 + timedelta(days=6, hours=23))
    welt.lauf(T0 + timedelta(days=7, minutes=3))
    assert len(welt.zahlungen) == 1


@pytest.mark.parametrize("fremd", [
    dict(chat="999", von="999"),             # another private chat
    dict(von="777"),                          # someone else in the chat
    dict(typ="group"),                        # a group, not Lars' chat
    dict(bot=True),                           # a bot
])
def test_a_foreign_ok_does_nothing(welt, fremd):
    welt.lauf()
    welt.antwort("ok", **fremd)
    welt.lauf(T0 + timedelta(hours=1))
    assert welt.zahlungen == []
    assert welt.anfrage()["status"] == "offen"
    assert not any("Antwort nicht" in t for _a, t in welt.gemeldet)  # no reply to strangers


def test_an_ok_dated_before_the_request_does_nothing(welt):
    welt.lauf()
    welt.antwort("ok", datum=T0 - timedelta(hours=1))
    welt.lauf(T0 + timedelta(hours=1))
    assert welt.zahlungen == []


def test_an_ok_to_an_outdated_version_does_nothing(welt):
    welt.lauf()
    alt_id = welt.gesendet[-1][0]
    # The week is republished as version 2 before Lars answers.
    woche(welt.ausgang, zeilen((ADR1, 30000)), version=2)
    welt.live = {"kw": KW, "version": 2, "stand": "vorgeschlagen"}
    welt.lauf(T0 + timedelta(minutes=20))
    assert len(welt.gesendet) == 2 and welt.anfrage()["version"] == 2
    welt.antwort("ok", auf=alt_id)
    welt.lauf(T0 + timedelta(hours=1))
    assert welt.zahlungen == []
    welt.antwort("ok")  # to the new request
    welt.lauf(T0 + timedelta(hours=2))
    assert [z["betrag_mikro"] for z in welt.zahlungen[0]["zeilen"]] == ["30000"]


def test_an_ok_to_an_expired_request_does_nothing_even_if_still_open_on_disk(welt):
    welt.lauf()
    welt.lauf(T0 + timedelta(days=8))  # expires
    welt.antwort("ok", datum=T0 + timedelta(days=8, minutes=1))
    welt.lauf(T0 + timedelta(days=8, minutes=10))
    assert welt.zahlungen == []


# --- Checks before the first transfer ------------------------------------------------

def test_rows_changed_after_the_release_stop_the_payment(welt):
    welt.lauf()
    welt.antwort("ok")
    welt.zahler = lambda *a: pytest.fail("payer must not run")
    # The release is recorded in the same run as it is paid; change the rows
    # between asking and the run that reads the ok.
    (welt.d / "zeilen-v1.json").write_text(json.dumps(zeilen((ADR1, 25000), (ADR2, 99999))))
    welt.lauf(T0 + timedelta(hours=1))
    assert welt.anfrage()["status"] == "gestoppt"
    assert any("Zeilen geaendert" in t for a, t in welt.gemeldet if a)


def test_a_published_result_of_another_version_stops_the_payment(welt):
    welt.live = {"kw": KW, "version": 3, "stand": "vorgeschlagen"}
    welt.lauf()
    welt.antwort("ok")
    welt.lauf(T0 + timedelta(hours=1))
    assert welt.zahlungen == []


def test_a_short_budget_stops_the_payment(welt):
    welt.rest = 70_000
    welt.lauf()
    welt.antwort("ok")
    welt.lauf(T0 + timedelta(hours=1))
    assert welt.zahlungen == []
    assert any("WM-1-Budget reicht nicht" in t for a, t in welt.gemeldet if a)


def test_a_short_wallet_sends_nothing_and_says_so(welt):
    welt.zahler_rc = 3
    welt.lauf()
    welt.antwort("ok")
    welt.lauf(T0 + timedelta(hours=1))
    assert welt.gebucht == [] and welt.veroeffentlicht == []
    assert any("reicht nicht" in t and "Nachladen" in t for a, t in welt.gemeldet if a)


def test_an_own_address_is_never_paid(tmp_path):
    w = Welt(tmp_path, rows=zeilen((A.ZAHLER, 25000)))
    w.lauf()
    w.antwort("ok")
    w.lauf(T0 + timedelta(hours=1))
    assert w.zahlungen == []


def test_the_stop_file_holds_the_release_until_removed(welt):
    welt.lauf()
    welt.antwort("ok")
    (welt.ausgang / "STOP").write_text("")
    welt.lauf(T0 + timedelta(hours=1))
    assert welt.zahlungen == [] and welt.anfrage()["status"] == "freigegeben"
    (welt.ausgang / "STOP").unlink()
    welt.lauf(T0 + timedelta(hours=2))
    assert len(welt.zahlungen) == 1


# --- The weekly settlement does not overwrite a paid week ----------------------------

def test_dauerrunde_woche_refuses_to_recompute_a_paid_week():
    alt = {"kw": KW, "version": 2, "stand": "ausgezahlt", "x": 1}
    with pytest.raises(RuntimeError, match="ausgezahlt"):
        W.plane(alt, {"kw": KW, "stand": "vorgeschlagen", "x": 2})


# --- The real payer, without a key ------------------------------------------------------

NODE = shutil.which("node")
CJS = pathlib.Path(A.ZAHLER_SKRIPT)


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_payer_script_parses():
    subprocess.run([NODE, "--check", str(CJS)], check=True)


@pytest.mark.skipif(not NODE, reason="node not installed")
@pytest.mark.parametrize("plan,grund", [
    ({"kw": KW, "zahler": A.ZAHLER, "eigene": ["0x" + "a" * 40] * 5,
      "zeilen": [{"adresse": ADR1, "betrag_mikro": "1"}]}, "Plan ohne Freigabe"),
    ({"kw": KW, "zahler": A.ZAHLER, "eigene": [ADR1] * 5,
      "freigabe": {"update_id": 1, "zeilen_sha256": "x"},
      "zeilen": [{"adresse": ADR1, "betrag_mikro": "1"}]}, "eigene Adresse"),
    ({"kw": KW, "zahler": A.ZAHLER, "eigene": ["0x" + "a" * 40] * 5,
      "freigabe": {"update_id": 1, "zeilen_sha256": "x"},
      "zeilen": [{"adresse": ADR1, "betrag_mikro": "1"}]}, "BASE_RPC"),
])
def test_payer_refuses_a_plan_before_it_touches_a_key(tmp_path, plan, grund):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    r = subprocess.run([NODE, str(CJS), str(p), str(tmp_path / "n.jsonl"), "--echt"],
                       capture_output=True, text=True, env={"PATH": "/usr/bin:/bin",
                                                            "HOME": str(tmp_path)})
    assert r.returncode == 2 and grund in r.stderr
    assert not (tmp_path / "n.jsonl").exists()


def test_the_only_transfer_call_is_in_the_payer_and_behind_echt():
    src = CJS.read_text()
    assert src.count("writeContract(") == 1
    i = src.index("writeContract(")
    assert "if (!echt)" in src[:i]
    py = pathlib.Path(A.__file__).read_text()
    assert py.count('"--echt"') == 1  # only node_zahler passes it
