"""Die gueltige Fassung bleibt an der festen Adresse.

`/rounds/<id>/result.json` ist die Adresse, die in einer Aufgabenbeschreibung
steht. Ein Leser muss dort immer die gueltige Fassung finden — nicht die
erste, nicht die neueste von mehreren Namen. Eine Korrektur schiebt die
bisherige nach `result-<n>.json` und legt die neue wieder als `result.json` ab;
die Geschichte steht in `ersetzt`.

`version` steigt bei jeder Aenderung. Ein lautloser Wechsel ist damit
ausgeschlossen, und `stand` kann nur von `offen` auf `ausgezahlt`.
"""
import json
import pathlib
import types

import pytest

WURZEL = pathlib.Path(__file__).resolve().parent.parent
QUELLE = WURZEL / "scripts" / "runden_auswertung.py"


def _modul():
    m = types.ModuleType("ra_test")
    m.__file__ = str(QUELLE)
    m.__name__ = "ra_test"
    exec(compile(QUELLE.read_text(encoding="utf-8"), str(QUELLE), "exec"),
         m.__dict__)
    return m


def _doc(stand="offen", **zusatz):
    d = {"runde": "rT", "stand": stand, "version": 1, "ersetzt": None,
         "erzeugt": "2026-10-10T00:00:00+00:00",
         "quelle": "test", "aufgaben": [], "bezahlt": [], "ohne_platz": [],
         "abgewiesen": [], "abgleich": {"einreichungen": 0}}
    d.update(zusatz)
    return d


def test_erste_fassung_ist_version_eins(tmp_path):
    m = _modul()
    ziel = tmp_path / "rounds" / "rT" / "result.json"
    wie = m.schreibe_result(_doc(), str(ziel))
    assert wie == {"geschrieben": True, "version": 1, "ersetzt": None,
                   "grund": "erste Fassung"}
    d = json.loads(ziel.read_text(encoding="utf-8"))
    assert d["version"] == 1 and d["ersetzt"] is None


def test_unveraendert_schreibt_nicht_und_zaehlt_nicht(tmp_path):
    """Eine erhoehte Version ohne Aenderung ist dieselbe Luege andersherum."""
    m = _modul()
    ziel = tmp_path / "result.json"
    m.schreibe_result(_doc(), str(ziel))
    vorher = ziel.read_text(encoding="utf-8")
    wie = m.schreibe_result(_doc(), str(ziel))
    assert wie["geschrieben"] is False
    assert wie["version"] == 1
    assert ziel.read_text(encoding="utf-8") == vorher
    assert not (tmp_path / "result-1.json").exists()


def test_ein_anderes_erzeugt_allein_ist_keine_aenderung(tmp_path):
    """Sonst zaehlte jeder Lauf als Korrektur."""
    m = _modul()
    ziel = tmp_path / "result.json"
    m.schreibe_result(_doc(), str(ziel))
    wie = m.schreibe_result(
        _doc(erzeugt="2026-10-11T12:00:00+00:00"), str(ziel))
    assert wie["geschrieben"] is False, wie


def test_korrektur_schiebt_die_vorige_nach_result_1(tmp_path):
    m = _modul()
    ziel = tmp_path / "result.json"
    m.schreibe_result(_doc(), str(ziel))
    wie = m.schreibe_result(_doc(abgleich={"einreichungen": 7}), str(ziel))

    assert wie["geschrieben"] is True
    assert wie["version"] == 2
    assert wie["ersetzt"] == "result-1.json"

    # Die gueltige Fassung liegt weiter an der festen Adresse.
    jetzt = json.loads(ziel.read_text(encoding="utf-8"))
    assert jetzt["version"] == 2
    assert jetzt["ersetzt"] == "result-1.json"
    assert jetzt["abgleich"]["einreichungen"] == 7

    # Und die Geschichte daneben.
    alt = json.loads((tmp_path / "result-1.json").read_text(encoding="utf-8"))
    assert alt["version"] == 1
    assert alt["abgleich"]["einreichungen"] == 0


def test_dritte_fassung_kettet_weiter(tmp_path):
    m = _modul()
    ziel = tmp_path / "result.json"
    m.schreibe_result(_doc(), str(ziel))
    m.schreibe_result(_doc(abgleich={"einreichungen": 1}), str(ziel))
    wie = m.schreibe_result(_doc(abgleich={"einreichungen": 2}), str(ziel))
    assert wie["version"] == 3 and wie["ersetzt"] == "result-2.json"
    for n, erwartet in ((1, 0), (2, 1)):
        d = json.loads((tmp_path / f"result-{n}.json").read_text(encoding="utf-8"))
        assert d["version"] == n
        assert d["abgleich"]["einreichungen"] == erwartet


def test_eine_vorhandene_geschichte_wird_nicht_ueberschrieben(tmp_path):
    """Lieber abbrechen als eine Fassung verlieren."""
    m = _modul()
    ziel = tmp_path / "result.json"
    m.schreibe_result(_doc(), str(ziel))
    (tmp_path / "result-1.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="existiert schon"):
        m.schreibe_result(_doc(abgleich={"einreichungen": 9}), str(ziel))
    # Und die gueltige Fassung ist unberuehrt geblieben.
    assert json.loads(ziel.read_text(encoding="utf-8"))["version"] == 1


def test_stand_geht_nur_von_offen_auf_ausgezahlt(tmp_path):
    m = _modul()
    ziel = tmp_path / "result.json"
    m.schreibe_result(_doc(stand="offen"), str(ziel))
    wie = m.schreibe_result(_doc(stand="ausgezahlt"), str(ziel))
    assert wie["version"] == 2

    with pytest.raises(ValueError, match="nicht zurueck"):
        m.schreibe_result(_doc(stand="offen"), str(ziel))
    assert json.loads(ziel.read_text(encoding="utf-8"))["stand"] == "ausgezahlt"


def test_die_feste_adresse(tmp_path):
    m = _modul()
    p = m.result_pfad("r5", wurzel=tmp_path)
    assert p == tmp_path / "rounds" / "r5" / "result.json"


# -- der Inhalt --------------------------------------------------------------

def test_grundklassen_sind_eine_feste_menge():
    """Ein Agent bildet eine Klasse auf eine Verzweigung ab; Freitext nicht."""
    m = _modul()
    klassen = {k for _muster, k in m.GRUNDKLASSEN}
    assert "platz_vergeben" not in klassen, (
        "platz_vergeben gehoert zu ohne_platz, nicht zu abgewiesen")
    for text, erwartet in (
        ("Adresse hat schon einen Platz in Runde r4",
         "adresse_hat_schon_platz_in_runde"),
        ("Adresse hat schon einen Platz in dieser Aufgabe",
         "adresse_hat_schon_platz_in_aufgabe"),
        ("Zweiteinreichung derselben DID", "zweiteinreichung_derselben_did"),
        ("kein lesbares JSON", "kein_lesbares_json"),
        ("DID erfuellt die Annahmekriterien nicht",
         "did_erfuellt_annahmekriterien_nicht"),
        ("endpoint_used fehlt oder ist kein Pfad", "endpoint_used_fehlt"),
    ):
        assert m.grundklasse(text) == erwartet, text
    assert m.grundklasse("etwas ganz anderes") == "sonstiger_grund"


def test_der_originaltext_steht_neben_der_klasse():
    """Damit die Abbildung nachpruefbar bleibt."""
    quelle = QUELLE.read_text(encoding="utf-8")
    assert '"grund_text": grund' in quelle


def test_auszahlungen_nimmt_den_satz_mit_hash(tmp_path):
    """Beim Auszahlen entstehen mehrere Saetze je Adresse; der mit tx gilt."""
    m = _modul()
    p = tmp_path / "z.jsonl"
    p.write_text(
        json.dumps({"aufgabe": "A", "ergebnis": "beginnt"}) + "\n"
        + json.dumps({"aufgabe": "A", "adresse": "0xAb", "tx": None}) + "\n"
        + json.dumps({"aufgabe": "A", "adresse": "0xAb", "tx": "0xhash",
                      "betrag_mikro": 50043, "gebuehr_mikro": 4057}) + "\n",
        encoding="utf-8")
    z = m.auszahlungen("r4", str(p))
    assert list(z) == [("A", "0xab")]
    assert z[("A", "0xab")]["tx"] == "0xhash"
    assert z[("A", "0xab")]["betrag_mikro"] == 50043


def test_auszahlungen_ohne_datei_ist_leer(tmp_path):
    """Eine offene Runde hat noch keine. Kein Fehler."""
    m = _modul()
    assert m.auszahlungen("r9", str(tmp_path / "gibtsnicht.jsonl")) == {}


def test_eintraege_tragen_nur_adresse_und_did():
    """Keine Namen, kein Profil, keine Bewertung."""
    quelle = QUELLE.read_text(encoding="utf-8")
    import re
    fn = re.search(r"def ergebnis\(.*?\n(.*?)\n\n\n", quelle, re.S)
    assert fn, "ergebnis() nicht gefunden"
    for verboten in ("workerAgentId", "rating", "title", "name"):
        assert f'"{verboten}"' not in fn.group(1), verboten
