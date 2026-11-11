"""Ein Vorbehalt meldet beim ersten Mal, Wiederholungen zaehlen.

Am 07./08.10.2026 liefen 30 Deploys in 24 Stunden. Ein stehender Vorbehalt
haette dreissigmal gesendet — und beim dritten Mal liest ihn niemand mehr.

Fingerabdruck: Dienst + Pfad des Vorbehalts. Zwei Dienste mit demselben Pfad
sind zwei Befunde, und ein Vorbehalt, der verschwindet, meldet beim naechsten
Auftreten wieder als neu.
"""
import json

import pytest

from scripts import deploy_verify as dv

NOW = "2026-10-08T08:00:00+00:00"


def res(*paare):
    return [{"path": p, "ok": ok, "detail": "x"} for p, ok in paare]


@pytest.fixture
def store(tmp_path):
    return str(tmp_path / "gesehen.json")


def test_erster_vorbehalt_ist_neu(store):
    erst, wied = dv.classify_new(res(("prompt/version", False)), "moltrust-api",
                                 NOW, store)
    assert [r["path"] for r in erst] == ["prompt/version"]
    assert wied == []


def test_zweites_mal_zaehlt_nur(store):
    dv.classify_new(res(("prompt/version", False)), "moltrust-api", NOW, store)
    erst, wied = dv.classify_new(res(("prompt/version", False)), "moltrust-api",
                                 NOW, store)
    assert erst == []
    assert [r["path"] for r in wied] == ["prompt/version"]


def test_zaehler_laeuft_mit(store):
    for _ in range(4):
        dv.classify_new(res(("prompt/version", False)), "moltrust-api", NOW, store)
    d = json.load(open(store, encoding="utf-8"))
    assert d["moltrust-api|prompt/version"]["gesehen"] == 4


def test_gruener_pfad_steht_nicht_im_speicher(store):
    dv.classify_new(res(("prompt/version", True), ("docs/mirror", False)),
                    "moltrust-api", NOW, store)
    d = json.load(open(store, encoding="utf-8"))
    assert list(d) == ["moltrust-api|docs/mirror"]


def test_verschwundener_vorbehalt_meldet_wieder_als_neu(store):
    """Weg heisst weg. Sonst bleibt ein behobener Befund fuer immer `bekannt`
    und sein Rueckfall faellt niemandem auf."""
    dv.classify_new(res(("docs/mirror", False)), "moltrust-api", NOW, store)
    dv.classify_new(res(("docs/mirror", True)), "moltrust-api", NOW, store)
    erst, wied = dv.classify_new(res(("docs/mirror", False)), "moltrust-api",
                                 NOW, store)
    assert [r["path"] for r in erst] == ["docs/mirror"]
    assert wied == []


def test_zwei_dienste_teilen_keinen_fingerabdruck(store):
    dv.classify_new(res(("docs/mirror", False)), "moltrust-api", NOW, store)
    erst, wied = dv.classify_new(res(("docs/mirror", False)), "moltrust-web",
                                 NOW, store)
    assert [r["path"] for r in erst] == ["docs/mirror"]
    assert wied == []


def test_fremder_dienst_bleibt_beim_aufraeumen_stehen(store):
    """Ein Lauf fuer api darf die Fingerabdruecke von web nicht loeschen."""
    dv.classify_new(res(("docs/mirror", False)), "moltrust-web", NOW, store)
    dv.classify_new(res(("prompt/version", False)), "moltrust-api", NOW, store)
    d = json.load(open(store, encoding="utf-8"))
    assert set(d) == {"moltrust-web|docs/mirror", "moltrust-api|prompt/version"}


def test_kaputter_speicher_meldet_lieber_zu_viel(store, tmp_path):
    """Lieber eine Nachricht zu viel als ein Vorbehalt, der hinter einer
    kaputten Datei verschwindet."""
    open(store, "w", encoding="utf-8").write("{kaputt")
    erst, wied = dv.classify_new(res(("prompt/version", False)), "moltrust-api",
                                 NOW, store)
    assert [r["path"] for r in erst] == ["prompt/version"]


def test_speicher_ist_nur_fuer_den_eigentuemer_lesbar(store):
    import os
    dv.classify_new(res(("prompt/version", False)), "moltrust-api", NOW, store)
    assert oct(os.stat(store).st_mode)[-3:] == "600"
