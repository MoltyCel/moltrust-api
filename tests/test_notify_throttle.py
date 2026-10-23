"""Dieselbe Nachricht zweimal ist eine Nachricht mit einem Zaehler.

Am 08.10.2026 schickte die R4-Wache fuenfzehn gleichlautende Fehlalarme in vier
Stunden. Sie war von der Drosselung ausgenommen, weil sie als wichtig galt —
und kam deshalb fuenfzehnmal durch. Wichtig und wiederholt sind zwei
verschiedene Eigenschaften.

Die Drosselung sitzt an der Sendestelle. Eine Ausnahme gibt es nicht: ein
Alarm, der sich aendert, hat einen anderen Fingerabdruck und kommt sofort.
"""
import datetime as dt
import json

from app import notify as n

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def store(tmp_path):
    return str(tmp_path / "fp.json")


# -- Fingerabdruck ----------------------------------------------------------

def test_zeitstempel_macht_keine_neue_nachricht():
    a = "at-Job 6 fehlt, Stand 2026-10-08T10:36:21Z"
    b = "at-Job 6 fehlt, Stand 2026-10-08T11:56:03Z"
    assert n.fingerprint(a) == n.fingerprint(b)


def test_commit_hash_macht_keine_neue_nachricht():
    assert n.fingerprint("Deploy ok 3aed8c5") == n.fingerprint("Deploy ok 35151e8")


def test_laufzeit_macht_keine_neue_nachricht():
    assert n.fingerprint("Lock alt 1:47:01.066693") == n.fingerprint("Lock alt 0:09:26")


def test_eigener_zaehler_macht_keine_neue_nachricht():
    """Sonst waere die gedrosselte Meldung bei jedem Mal wieder neu."""
    a = "Befund X\n\n(3x seit 10:36Z, gleichlautend)"
    b = "Befund X\n\n(9x seit 11:36Z, gleichlautend)"
    assert n.fingerprint(a) == n.fingerprint(b)


def test_anderer_grund_ist_eine_andere_nachricht():
    """Der Fall, der nie gedrosselt werden darf."""
    a = "at-Job 7 lief mit Exitcode 2"
    b = "at-Job 7 lief mit Exitcode 7"
    assert n.fingerprint(a) != n.fingerprint(b)


def test_anderer_text_ist_eine_andere_nachricht():
    assert n.fingerprint("Deploy rot") != n.fingerprint("Deploy gruen")


# -- Drosselung -------------------------------------------------------------

def test_erstmals_geht_sofort(tmp_path):
    senden, _fp, zusatz = n.throttle("Befund A", NOW, store(tmp_path))
    assert senden is True
    assert zusatz == ""


def test_zweites_mal_binnen_einer_stunde_geht_nicht(tmp_path):
    p = store(tmp_path)
    n.throttle("Befund A", NOW, p)
    senden, _fp, _z = n.throttle("Befund A", NOW + dt.timedelta(minutes=20), p)
    assert senden is False


def test_unveraendert_nie_wieder(tmp_path):
    """Seit NOTIFY-1 (23.10.2026): ein unveraenderter Befund geht einmal raus.

    Bis dahin kam er nach 24 Laeufen wieder, mit Zaehlerzeile. Bei der
    Web-Root-Wache im 5-Minuten-Takt waren das zwei Stunden: am 21.10. ging
    dieselbe Meldung um 09:00, 11:00, 13:00 und 15:00Z raus.
    """
    p = store(tmp_path)
    gesendet = [i for i in range(3 * n.REPEAT_EVERY_RUNS)
                if n.throttle("Befund A", NOW + dt.timedelta(minutes=5 * i), p)[0]]
    assert gesendet == [0], gesendet


def test_der_abstand_in_sekunden_spielt_keine_rolle(tmp_path):
    """Knapp unter und knapp ueber einer Stunde Abstand: dasselbe Ergebnis."""
    for versatz in (dt.timedelta(minutes=59, seconds=53),
                    dt.timedelta(hours=1, seconds=5)):
        p = store(tmp_path / str(versatz.total_seconds()))
        gesendet = [i for i in range(n.REPEAT_EVERY_RUNS + 1)
                    if n.throttle("A", NOW + versatz * i, p)[0]]
        assert gesendet == [0], (versatz, gesendet)



def test_nach_24_h_gilt_wieder_als_erstmals(tmp_path):
    p = store(tmp_path)
    n.throttle("A", NOW, p)
    senden, _fp, zusatz = n.throttle("A", NOW + dt.timedelta(hours=25), p)
    assert senden is True
    assert zusatz == "", "nach dem Vergessen ist es keine Wiederholung"


def test_vergessene_eintraege_fallen_aus_der_datei(tmp_path):
    p = store(tmp_path)
    n.throttle("A", NOW, p)
    n.throttle("B", NOW + dt.timedelta(hours=25), p)
    d = json.load(open(p, encoding="utf-8"))
    assert len(d) == 1, "A haette nach 24 h verschwinden muessen"


def test_zwei_verschiedene_nachrichten_drosseln_sich_nicht(tmp_path):
    p = store(tmp_path)
    assert n.throttle("A", NOW, p)[0] is True
    assert n.throttle("B", NOW, p)[0] is True


def test_kaputter_speicher_sendet_lieber(tmp_path):
    """Lieber eine Nachricht zu viel als eine Drosselung, die hinter einer
    kaputten Datei alles verschluckt."""
    p = store(tmp_path)
    open(p, "w", encoding="utf-8").write("{kaputt")
    assert n.throttle("A", NOW, p)[0] is True


def test_speicher_ist_nur_fuer_den_eigentuemer_lesbar(tmp_path):
    import os
    p = store(tmp_path)
    n.throttle("A", NOW, p)
    assert oct(os.stat(p).st_mode)[-3:] == "600"


# -- Sendeprotokoll ---------------------------------------------------------

def test_protokoll_haelt_auch_den_fehlschlag(tmp_path):
    p = str(tmp_path / "sent.jsonl")
    n._record_sent("alerts", False, 400, "abc123", "Eine Nachricht" * 20,
                   grund="gedrosselt", path=p)
    z = json.loads(open(p, encoding="utf-8").read().strip())
    assert z["kanal"] == "alerts"
    assert z["erfolg"] is False
    assert z["http_status"] == 400
    assert z["fingerabdruck"] == "abc123"
    assert len(z["text_anfang"]) == 80
    assert z["grund"] == "gedrosselt"


def test_protokoll_haengt_an_statt_zu_ueberschreiben(tmp_path):
    p = str(tmp_path / "sent.jsonl")
    n._record_sent("stats", True, 200, "a", "eins", path=p)
    n._record_sent("stats", True, 200, "b", "zwei", path=p)
    assert len(open(p, encoding="utf-8").read().strip().splitlines()) == 2


def test_protokoll_ist_nur_fuer_den_eigentuemer_lesbar(tmp_path):
    import os
    p = str(tmp_path / "sent.jsonl")
    n._record_sent("stats", True, 200, "a", "eins", path=p)
    assert oct(os.stat(p).st_mode)[-3:] == "600"
