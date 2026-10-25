"""The daily report speaks plainly and says a deviation once (ops TB-1).

On 2026-10-22 the hourly --urgent run sent the same 404 25 times, and every
message carried check IDs and register counts Lars had no use for. Each test
pins one of the two halves: the wording, and the once-only rule.
"""
import datetime
import json

import pytest

from scripts import daily_report as dr

NOW = datetime.datetime(2026, 10, 23, 6, 30, tzinfo=datetime.UTC)
URL_404 = ("artefact-check", "urls-resolve: URL named publicly answers 404: "
           "https://api.moltrust.ch/x402/verify?did=did:moltrust:abc123")

ARTEFACT_404 = """Bericht: /home/x/Downloads/artefact-check-2026-10-23.md
Abweichungen: 16 gesamt, 1 neu, 0 verschwunden
  NEU  urls-resolve: URL named publicly answers 404: https://api.moltrust.ch/x402/verify?did=did:moltrust:abc123
"""
ARTEFACT_CLEAN = "Abweichungen: 15 gesamt, 0 neu, 0 verschwunden\n"
VECTORS_CLEAN = "Vektoren: 40   gehalten 30   registriert abweichend 10   neu abweichend 0\n"


def _run(artefact=ARTEFACT_404, vectors=VECTORS_CLEAN, crash=False):
    def run():
        if crash:
            return [("artefact-check", None, "Zeitüberschreitung nach 900 s"),
                    ("did-conformance", 0, vectors)]
        return [("artefact-check", 1 if "NEU" in artefact else 0, artefact),
                ("did-conformance", 0, vectors)]
    return run


@pytest.fixture
def env(tmp_path, monkeypatch):
    sent = []

    def send(text, *, channel, **k):
        sent.append((channel, text))
        return box["deliver"]

    box = {"deliver": True, "sent": sent}
    monkeypatch.setattr(dr.notify, "letzter_grund", lambda: "")
    box["kw"] = dict(send=send, state_path=tmp_path / "state.json", detail_dir=tmp_path,
                     pending=lambda: [],
                     merges=lambda now: "Letzte 24 Stunden: 3 Änderungen übernommen, 2 Mal ausgerollt.")
    box["state"] = tmp_path / "state.json"
    return box


# --- wording ---------------------------------------------------------------

FORBIDDEN = ("urls-resolve", "keys-symmetry", "keys-have-code", "artefact-check",
             "did-conformance", "Register", "offen,", "geschlossen", "NEU ", "WEG ")


@pytest.mark.parametrize("check,text", [
    URL_404,
    ("artefact-check", "urls-resolve: URL named publicly does not answer: https://moltrust.ch/x"),
    ("artefact-check", "keys-symmetry: kid in did.json without a JWK: did:web:api.moltrust.ch#k"),
    ("artefact-check", "keys-symmetry: kid in jwks.json without a verification method: gw-v1"),
    ("artefact-check", "keys-have-code: no code path signs with kid gw-v1"),
    ("did-conformance", "dm-4.4-03 (section 4.4) deactivated DID document carries deactivated"),
    ("artefact-check", "something-new: a shape nobody has seen yet"),
])
def test_every_kind_is_one_sentence_with_meaning_and_action(check, text):
    sentence, means, act = dr.plain(check, text)
    for part in (sentence, means, act):
        assert part and not any(f in part for f in FORBIDDEN), part
    assert act.startswith(("ja", "nein"))
    # A step for Lars carries its link, or the first line would not be one.
    assert not act.startswith("ja") or "https://" in act


def test_the_message_has_no_check_ids_and_no_register_counts(env):
    dr.main([], now=NOW, run=_run(), **env["kw"])
    channel, text = env["sent"][0]
    assert channel == dr.notify.STATS
    assert not any(f in text for f in FORBIDDEN), text
    assert text.splitlines()[:2] == ["Lars muss: nichts", "Tagesbericht 23.10.: eine Abweichung."]
    assert "Eine Adresse, die wir öffentlich nennen, führt ins Leere: " \
           "https://api.moltrust.ch/x402/verify?did=did:moltrust:abc123" in text
    assert "Was heißt das: " in text and "Musst du etwas tun: nein" in text
    assert "3 Änderungen übernommen, 2 Mal ausgerollt." in text


def test_a_quiet_day_is_still_one_message(env):
    assert dr.main([], now=NOW, run=_run(ARTEFACT_CLEAN), **env["kw"]) == 0
    assert len(env["sent"]) == 1
    assert env["sent"][0][1].startswith("Lars muss: nichts\nTagesbericht 23.10.: nichts Neues.")


def test_a_failed_check_says_so_plainly(env):
    dr.main([], now=NOW, run=_run(crash=True), **env["kw"])
    text = env["sent"][0][1]
    assert text.startswith("Lars muss: nichts\nTagesbericht 23.10.: eine Prüfung lief nicht durch.")
    assert "Der abgleich" not in text and "Der Abgleich unserer veröffentlichten Dokumente ist nicht" in text


def test_a_closed_deviation_reads_as_fixed(env):
    gone = "Abweichungen: 15 gesamt, 0 neu, 1 verschwunden\n  WEG  urls-resolve::URL named " \
           "publicly answers 404: https://moltrust.ch/whitepaper\n"
    dr.main([], now=NOW, run=_run(gone), **env["kw"])
    text = env["sent"][0][1]
    assert "Behoben: Eine Adresse, die wir öffentlich nennen, führt ins Leere: " \
           "https://moltrust.ch/whitepaper" in text
    assert "urls-resolve" not in text


def test_the_first_line_names_the_step_and_its_link(env, monkeypatch):
    step = "ja, den neuen Schlüssel freigeben: https://github.com/MoltyCel/moltrust-api/pull/1"
    monkeypatch.setattr(dr, "plain", lambda c, t: ("Satz.", "Bedeutung.", step))
    dr.main([], now=NOW, run=_run(), **env["kw"])
    text = env["sent"][0][1]
    assert text.splitlines()[0] == ("Lars muss: den neuen Schlüssel freigeben: "
                                    "https://github.com/MoltyCel/moltrust-api/pull/1")
    assert text.splitlines()[1].startswith("Tagesbericht 23.10.")


def test_the_throttle_names_an_urgent_report_by_its_second_line():
    assert dr.notify._anfang("Lars muss: nichts\nAußerhalb des Tagesberichts, eine Abweichung.\nx") \
        == "Außerhalb des Tagesberichts, eine Abweichung."


# --- once, not every hour ---------------------------------------------------

def test_urgent_says_a_deviation_once(env):
    later = NOW + datetime.timedelta(hours=1)
    assert dr.main(["--urgent"], now=NOW, run=_run(), **env["kw"]) == 0
    assert dr.main(["--urgent"], now=later, run=_run(), **env["kw"]) == 0
    assert len(env["sent"]) == 1
    assert env["sent"][0][0] == dr.notify.ALERTS
    assert env["sent"][0][1].startswith("Lars muss: nichts\nAußerhalb des Tagesberichts, eine Abweichung.")


def test_urgent_is_silent_after_the_daily_slot_named_it(env):
    dr.main([], now=NOW, run=_run(), **env["kw"])
    dr.main(["--urgent"], now=NOW + datetime.timedelta(minutes=47), run=_run(), **env["kw"])
    assert len(env["sent"]) == 1


def test_the_daily_slot_repeats_a_standing_one_with_its_date(env):
    dr.main(["--urgent"], now=NOW, run=_run(), **env["kw"])
    dr.main([], now=NOW + datetime.timedelta(days=1), run=_run(), **env["kw"])
    assert len(env["sent"]) == 2
    assert "Steht seit 23.10." in env["sent"][1][1]


def test_gone_and_back_is_new(env):
    dr.main(["--urgent"], now=NOW, run=_run(), **env["kw"])
    dr.main(["--urgent"], now=NOW + datetime.timedelta(hours=1), run=_run(ARTEFACT_CLEAN), **env["kw"])
    dr.main(["--urgent"], now=NOW + datetime.timedelta(hours=2), run=_run(), **env["kw"])
    assert len(env["sent"]) == 2


def test_a_failed_send_is_tried_again(env):
    env["deliver"] = False
    assert dr.main(["--urgent"], now=NOW, run=_run(), **env["kw"]) == dr.notify.MUTE_EXIT_CODE
    env["deliver"] = True
    dr.main(["--urgent"], now=NOW + datetime.timedelta(hours=1), run=_run(), **env["kw"])
    assert len(env["sent"]) == 2
    assert json.loads(env["state"].read_text())["sent"] is True


def test_a_throttled_send_counts_as_delivered(env, monkeypatch):
    env["deliver"] = False
    monkeypatch.setattr(dr.notify, "letzter_grund", lambda: "gedrosselt")
    assert dr.main(["--urgent"], now=NOW, run=_run(), **env["kw"]) == 0
    assert dr._key(*URL_404) in json.loads(env["state"].read_text())["gemeldet"]


def test_a_failed_run_is_urgent_once(env):
    dr.main(["--urgent"], now=NOW, run=_run(crash=True), **env["kw"])
    dr.main(["--urgent"], now=NOW + datetime.timedelta(hours=1), run=_run(crash=True), **env["kw"])
    assert len(env["sent"]) == 1


def test_dry_run_sends_and_writes_nothing(env, capsys):
    assert dr.main(["--dry-run"], now=NOW, run=_run(), **env["kw"]) == 0
    assert env["sent"] == [] and not env["state"].exists()
    assert "Tagesbericht 23.10." in capsys.readouterr().out


# --- the deploy count -------------------------------------------------------

def test_deploys_are_counted_from_the_ok_lines(tmp_path):
    log = tmp_path / "deploy.log"
    log.write_text("\n".join([
        "2026-10-22T05:00:00Z OK moltrust-api aaaa",       # older than 24 h
        "2026-10-22T21:17:35Z OK moltrust-web 7b94",
        "2026-10-23T05:00:00Z START moltrust-api bbbb",
        "2026-10-23T05:00:09Z OK moltrust-api bbbb",
        "2026-10-23T05:01:00Z verify: ✅ OK moltrust-api is not a deploy line",
    ]) + "\n")
    since = NOW - datetime.timedelta(hours=24)
    assert dr.count_deploys(since, log) == 2


# --- standing findings that wait on Lars -----------------------------------

STEHEND_E3 = [{"anfang": "[FAIL] c-context-urls-resolve — ohne 'gleich '"},
              {"anfang": "[FAIL] c-watchdog-threshold-fits-cron — 1 (erwartet eq 0)"}]


def test_a_standing_finding_with_a_lars_step_fills_the_first_line():
    steps = dr.pending_steps(STEHEND_E3)
    assert len(steps) == 1 and "https://github.com/MoltyCel/moltrust-web/pull/308" in steps[0]


def test_the_daily_report_names_the_open_step_although_nothing_is_new(env):
    step = "Label setzen: https://example.org/pr/1"
    env["kw"]["pending"] = lambda: [step]
    dr.main([], now=NOW, run=_run(ARTEFACT_CLEAN), **env["kw"])
    first = env["sent"][0][1].splitlines()[0]
    assert first == f"Lars muss: {step}"


def test_the_urgent_report_does_not_repeat_open_steps(env):
    env["kw"]["pending"] = lambda: ["Label setzen: https://example.org/pr/1"]
    dr.main(["--urgent"], now=NOW, run=_run(), **env["kw"])
    assert env["sent"][0][1].startswith("Lars muss: nichts\n")
