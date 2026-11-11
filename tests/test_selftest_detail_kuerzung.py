"""Eine gekürzte Detailzeile sagt, dass sie gekürzt ist, und wie viel fehlt.

`scripts/selftest.py` schnitt den Befund nach 900 Zeichen mitten im Eintrag
ab, ohne ein Wort darüber. Wer das las, sah weder dass etwas fehlte noch wie
viel — und die Gesamtzahl der Treffer stand nirgends als Zahl. Die Tests
prüfen beide Richtungen: der kurze Befund bleibt unberührt, der lange trägt
den Hinweis und beide Zählungen.
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


st = load("scripts/selftest.py", "selftest_detail_mod")


def eintraege(n, laenge=100):
    return "\n".join(f"{i:03d} " + "x" * (laenge - 4) for i in range(n))


# --- die Zeile selbst -------------------------------------------------------

def test_kurzer_befund_bleibt_unveraendert():
    """Unter der Grenze gibt es nichts zu melden — und keinen Hinweis."""
    text, gesamt, gezeigt = st.detail_aus_wert("a.html:3\nb.html:9")
    assert text == "a.html:3 | b.html:9"
    assert (gesamt, gezeigt) == (2, 2)
    assert "gekürzt" not in text


def test_leerzeilen_zaehlen_nicht_mit():
    text, gesamt, gezeigt = st.detail_aus_wert("  \na.html:3\n\n   \nb.html:9\n")
    assert text == "a.html:3 | b.html:9"
    assert (gesamt, gezeigt) == (2, 2)


def test_langer_befund_endet_mit_dem_hinweis():
    """Der Wortlaut ist der Beleg: N von M Einträgen gezeigt."""
    text, gesamt, gezeigt = st.detail_aus_wert(eintraege(30))
    assert gesamt == 30
    assert 0 < gezeigt < 30
    assert text.endswith(f" … gekürzt, {gezeigt} von 30 Einträgen gezeigt")
    assert len(text) <= st.DETAIL_GRENZE


def test_gekuerzt_wird_an_der_eintragsgrenze():
    """Kein halber Eintrag: was steht, steht vollständig."""
    text, gesamt, gezeigt = st.detail_aus_wert(eintraege(30))
    kopf = text.split(" … gekürzt,")[0]
    assert kopf.split(" | ") == eintraege(30).splitlines()[:gezeigt]


def test_genau_an_der_grenze_bleibt_ungekuerzt():
    wert = eintraege(2, laenge=10)
    ganz = " | ".join(wert.splitlines())
    text, gesamt, gezeigt = st.detail_aus_wert(wert, grenze=len(ganz))
    assert text == ganz
    assert (gesamt, gezeigt) == (2, 2)


def test_ein_zeichen_zu_lang_wird_gekuerzt():
    wert = eintraege(2, laenge=10)
    ganz = " | ".join(wert.splitlines())
    text, gesamt, gezeigt = st.detail_aus_wert(wert, grenze=len(ganz) - 1)
    assert text.endswith(f" … gekürzt, {gezeigt} von 2 Einträgen gezeigt")
    assert gezeigt < 2


def test_einziger_zu_langer_eintrag_zeigt_seinen_anfang():
    """Auch wenn kein ganzer Eintrag passt, ist der Anfang der Befund —
    und gezeigt wird dann 0 von M, nicht 1."""
    text, gesamt, gezeigt = st.detail_aus_wert(eintraege(3, laenge=2000))
    assert (gesamt, gezeigt) == (3, 0)
    assert text.startswith("000 xx")
    assert text.endswith(" … gekürzt, 0 von 3 Einträgen gezeigt")
    assert len(text) <= st.DETAIL_GRENZE


# --- und wie sie im Ergebnis ankommt ---------------------------------------

def lauf(monkeypatch, tmp_path, wert):
    """Ein Lauf mit einer einzigen Invariante, deren Wert der Befund ist."""
    inv = {"id": "x-probe", "titel": "Probe", "kategorie": "C",
           "schweregrad": "fail", "tempo": "hourly", "detail_aus_wert": True,
           "erwartung": {"operator": "empty", "wert": ""},
           "abfrage": {"art": "shell", "befehl": "true"}}
    monkeypatch.setattr(st, "load_catalogue", lambda: [inv])
    monkeypatch.setattr(st, "run_query", lambda i: (wert, None))
    monkeypatch.setattr(st, "preflight", lambda: [])
    monkeypatch.setattr(st.notify, "send_telegram", lambda *a, **k: None)
    monkeypatch.setattr(st, "OUTDIR", str(tmp_path / "out"))
    monkeypatch.setattr(st, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(st.throttle, "save_state", lambda *a, **k: None)
    monkeypatch.setattr("sys.argv", ["selftest.py", "--all", "--dry-run",
                                     "--no-supervision"])
    st.main()
    out = list((tmp_path / "out").glob("*.json"))
    assert len(out) == 1
    doc = json.loads(out[0].read_text(encoding="utf-8"))[-1]
    return [r for r in doc["ergebnisse"] if r["id"] == "x-probe"][0]


def test_ergebnis_traegt_beide_zahlen(monkeypatch, tmp_path):
    """Kriterium 3: die Gesamtzahl steht als Zahl im Ergebnis, nicht nur im Text."""
    res = lauf(monkeypatch, tmp_path, eintraege(30))
    assert res["detail_eintraege_gesamt"] == 30
    assert isinstance(res["detail_eintraege_gesamt"], int)
    assert 0 < res["detail_eintraege_gezeigt"] < 30
    assert res["detail"].endswith(
        f" … gekürzt, {res['detail_eintraege_gezeigt']} von 30 Einträgen gezeigt")


def test_kurzes_ergebnis_zaehlt_auch_und_ohne_hinweis(monkeypatch, tmp_path):
    res = lauf(monkeypatch, tmp_path, "a.html:3\nb.html:9")
    assert res["detail_eintraege_gesamt"] == 2
    assert res["detail_eintraege_gezeigt"] == 2
    assert "gekürzt" not in res["detail"]
