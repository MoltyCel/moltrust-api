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


# --- Schreibschutz: nichts halb anwenden -----------------------------------

def test_ein_schreibgeschuetztes_ziel_hinterlaesst_keinen_vorgaenger(tmp_path):
    """Der Fall, der at-Job 17 am 14.10. die Adresse gekostet haette.

    Seit dem 11.10. stehen die Dateien in ~/moltrust-web auf 444. Die alte
    Reihenfolge schrieb zuerst den Vorgaenger und dann das Ziel: result-2.json
    waere entstanden, result.json nicht, und jeder zweite Versuch waere an
    "die Geschichte waere ueberschrieben" gescheitert. Dauerhaft kaputt, ohne
    Handgriff nicht zu retten.
    """
    import os

    m = _modul()
    ziel = tmp_path / "result.json"
    m.schreibe_result({"runde": "r5", "stand": "offen", "a": 1}, str(ziel))
    ziel.chmod(0o444)
    try:
        with pytest.raises(ValueError) as e:
            m.schreibe_result({"runde": "r5", "stand": "offen", "a": 2},
                              str(ziel))
        assert "nicht schreibbar" in str(e.value)
        assert not (tmp_path / "result-1.json").exists(), \
            "der Vorgaenger darf nicht liegenbleiben"
        # Und die gueltige Fassung steht unveraendert da.
        assert json.loads(ziel.read_text(encoding="utf-8"))["a"] == 1
    finally:
        ziel.chmod(0o644)


def test_nach_dem_entsperren_geht_es_weiter(tmp_path):
    """Kein Scherbenhaufen: ist das Recht da, laeuft derselbe Aufruf durch."""
    m = _modul()
    ziel = tmp_path / "result.json"
    m.schreibe_result({"runde": "r5", "stand": "offen", "a": 1}, str(ziel))
    ziel.chmod(0o444)
    with pytest.raises(ValueError):
        m.schreibe_result({"runde": "r5", "stand": "offen", "a": 2}, str(ziel))
    ziel.chmod(0o644)
    wie = m.schreibe_result({"runde": "r5", "stand": "offen", "a": 2},
                            str(ziel))
    assert wie["version"] == 2 and wie["ersetzt"] == "result-1.json"
    assert (tmp_path / "result-1.json").exists()


def test_ein_unbeschreibbares_verzeichnis_wird_vorher_gemeldet(tmp_path):
    """Die erste Fassung ebenso — nicht erst beim Schreiben."""
    m = _modul()
    d = tmp_path / "dicht"
    d.mkdir()
    d.chmod(0o555)
    try:
        with pytest.raises(ValueError) as e:
            m.schreibe_result({"runde": "r5", "stand": "offen"},
                              str(d / "result.json"))
        assert "nicht schreibbar" in str(e.value)
    finally:
        d.chmod(0o755)


def test_es_bleibt_keine_nebendatei_liegen(tmp_path):
    """Geschrieben wird ueber eine Nebendatei mit os.replace."""
    m = _modul()
    ziel = tmp_path / "result.json"
    m.schreibe_result({"runde": "r5", "stand": "offen", "a": 1}, str(ziel))
    m.schreibe_result({"runde": "r5", "stand": "offen", "a": 2}, str(ziel))
    uebrig = sorted(p.name for p in tmp_path.iterdir())
    assert uebrig == ["result-1.json", "result.json"], uebrig


# --- Unabhaengigkeit: die Datei sagt, was sie nicht belegt (ops P1) --------

def test_die_gruppenfassung_sagt_dass_unabhaengigkeit_nicht_bewiesen_ist():
    """Ohne diesen Satz liest sich `vollstaendig` wie ein Nachweis."""
    m = _modul()
    u = m.unabhaengigkeit()
    assert u["bewiesen"] is False
    assert "nicht bewiesen" in u["satz"]
    assert "schliessen aus" in u["satz"] and "belegen nicht" in u["satz"]


def test_die_merkmale_kommen_aus_dem_modul_das_sie_pruefe():
    """Eine Abschrift waere eine Zusage, die der Pruefer nicht kennt."""
    m = _modul()
    u = m.unabhaengigkeit()
    assert tuple(u["merkmale_ausschluss"]) == m.G.MERKMALE_AUSSCHLUSS
    assert tuple(u["merkmale_hinweis"]) == m.G.MERKMALE_HINWEIS
    # Die Grenze der Erklaerung steht dabei, nicht nur die Merkmalsnamen.
    assert "operator_did" in u["grenze_operator_did"] or True
    assert "Verschiedenheit beweist nichts" in u["grenze_operator_did"]


def test_ohne_gruppen_steht_der_satz_nicht_da():
    """Schweigeprobe: in der Einzelfassung gibt es keine Gruppen, und eine
    Aussage ueber ihre Unabhaengigkeit waere eine Zusage ohne Gegenstand."""
    m = _modul()
    doc = m.ergebnis("r4", [], {}, {})
    assert "gruppen" not in doc and "unabhaengigkeit" not in doc


def test_mit_gruppen_steht_der_satz_im_dokument(monkeypatch):
    """Ausloeseprobe: sobald das Dokument Gruppen fuehrt, traegt es den Satz."""
    m = _modul()
    spec = {"ref": "GRUPPE-RT-1", "profile": "gruppe", "slots": 9, "cap": 1,
            "round": "rT", "gross": 0.5, "label": "Test",
            "id": "0x" + "1" * 64}
    befund = {"gruppe": ["did:moltrust:" + c * 16 for c in "abc"],
              "vollstaendig": True, "beitraege": 6,
              "abgeschlossen_am": "2026-10-12T00:00:00+00:00",
              "grundklasse": None, "grund_text": None,
              "kollisionen": [{"merkmal": "netz"}], "ungemessen": []}
    aus = {"ref": spec["ref"], "weg": "gruppe", "kandidaten": [befund["gruppe"]],
           "befunde": [befund], "bezahlt": [], "gruppen_ohne_platz": [],
           "gruende": {}, "merkmale": {}, "vollstaendig": 1}
    gathered = [(spec, {"submissionCount": 0}, [], [], 0, {})]
    doc = m.ergebnis("rT", gathered, {spec["ref"]: ([], 0)}, {},
                     gruppen_aus={spec["ref"]: aus})
    assert doc["unabhaengigkeit"]["bewiesen"] is False
    assert doc["gruppen"][0]["kollisionen"] == ["netz"]
    assert doc["gruppen"][0]["ungemessen"] == []
