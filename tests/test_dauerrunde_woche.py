"""The weekly settlement of the standing round (ops G4-3).

No database, no network: the facts come in as the SQL rows would, the
GitHub reads are injected. Every rule has a trigger and a silent probe; the
week without an activation says "nichts zu zahlen, Vortrag x".
"""
from __future__ import annotations

import ast
import json
import pathlib
import stat
from datetime import datetime, timezone

import pytest

from scripts import dauerrunde as D
from scripts import dauerrunde_woche as W

KW = "2026-W44"
KOHORTEN = {"stichtag": "2026-11-02T06:00:00+00:00", "wochen": [],
            "budget_wm1": {"budget": "20", "ausgegeben": "1.401198"}}


def seite(did, adresse, schluessel, betreiber, ip="10.0.0.1", kette="base"):
    return {"did": did, "adresse": adresse, "schluessel": schluessel,
            "betreiber": betreiber, "netz": ip, "kette": kette}


def fall(n, *, kette="base", aktiv="2026-10-28T10:00:00+00:00",
         betreiber_emp=None, adresse_emp=None):
    emp = seite(f"did:moltrust:e{n:015d}",
                adresse_emp or f"0x{n:040x}", f"k{n}", betreiber_emp or f"did:op:e{n}",
                ip=f"10.1.{n}.7", kette=kette)
    emp["registriert_am"] = "2026-10-27T09:00:00+00:00"
    geb = seite("did:moltrust:r000000000000001",
                "0x26d6bd4cd5c7a6e34f0c4b186a3dae6690f567aa", "kr", "did:op:r",
                ip="10.9.9.9")
    aufrufe = [{"ts": "2026-10-27T09:05:00+00:00", "endpoint": "/identity/verify/x"}]
    if aktiv:
        aufrufe.append({"ts": aktiv, "endpoint": "/credentials/list"})
    return {"empfohlen": emp, "empfehlend": geb,
            "fakt": {"did": emp["did"], "self": False, "mutual": False,
                     "unknown": False, "revoked": False},
            "aufrufe": aufrufe}


def rechne(liste, vortrag=0):
    return W.rechne(KW, faelle_liste=liste, vortrag_mikro=vortrag,
                    vortrag_quelle="test", kohorten=KOHORTEN,
                    erzeugt="2026-11-02T06:00:01+00:00")


MUSTER = ("/identity/verify/%",)


@pytest.fixture(autouse=True)
def _ohne_dateien(monkeypatch):
    """No exclusion list and no prescribed list from disk."""
    real = D.abrechnung

    def abrechnung(faelle, **kw):
        kw.setdefault("ausgeschlossen", set())
        kw.setdefault("muster", MUSTER)
        kw.setdefault("eigene_adressen", set())
        return real(faelle, **kw)
    monkeypatch.setattr(D, "abrechnung", abrechnung)


# --- Weeks -------------------------------------------------------------------

def test_der_montagslauf_rechnet_die_woche_davor():
    j = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)
    assert W.abrechnungswoche(j) == "2026-W44"


def test_ein_lauf_mitten_in_der_woche_rechnet_nicht_die_laufende():
    j = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
    assert W.abrechnungswoche(j) == "2026-W43"


def test_vorwoche_ueber_den_jahreswechsel():
    assert W.vorwoche("2027-W01") == "2026-W53"
    assert W.vorwoche(KW) == "2026-W43"


def test_stichtag_ist_montag_sechs_uhr_oder_jetzt():
    spaet = datetime(2026, 11, 9, tzinfo=timezone.utc)
    frueh = datetime(2026, 10, 30, tzinfo=timezone.utc)
    assert W.stichtag(KW, spaet).isoformat() == "2026-11-02T06:00:00+00:00"
    assert W.stichtag(KW, frueh) == frueh


# --- Carry-over --------------------------------------------------------------

def test_die_erste_woche_hat_keinen_vortrag():
    def nie(*_a):
        raise AssertionError("first week must not read")
    assert W.vortrag(KW, "", lesen=nie)[0] == 0


def test_spaetere_woche_liest_vortrag_der_vorwoche():
    m, q = W.vortrag("2026-W45", "", lesen=lambda kw, _t: {
        "vortrag_aus": "4.950000", "version": 2} if kw == KW else None)
    assert m == 4_950_000
    assert "2026-W44" in q and "Version 2" in q


def test_fehlende_vorwoche_bricht_ab_statt_zu_raten():
    with pytest.raises(RuntimeError, match="Vortrag nicht bestimmbar"):
        W.vortrag("2026-W45", "", lesen=lambda *_a: None)


# --- Facts -------------------------------------------------------------------

@pytest.mark.parametrize("ip,netz", [
    ("203.0.113.7", "203.0.113.0/24"),
    ("2001:db8:1:2::5", "2001:db8:1::/48"),
    ("", None), (None, None), ("kein-ip", None)])
def test_netz24(ip, netz):
    assert W.netz24(ip) == netz


def test_zwei_agenten_im_selben_24_werden_vermerkt_nicht_abgewiesen():
    f = fall(1)
    f["empfehlend"]["netz"] = "10.1.1.200"
    e, doc = rechne(W.faelle_aus_zeilen([f]))
    assert doc["vermerk_netz_gleich"] == [f["empfohlen"]["did"]]
    assert len(doc["bezahlt"]) == 1


def test_verschiedene_netze_ergeben_keinen_vermerk():
    e, doc = rechne(W.faelle_aus_zeilen([fall(1)]))
    assert doc["vermerk_netz_gleich"] == []


# --- The document ------------------------------------------------------------

def test_woche_ohne_aktivierung_nichts_zu_zahlen_vortrag():
    """Schweigeprobe G4-3 criterion 4."""
    e, doc = rechne([], vortrag=1_000_000)
    assert doc["ergebnis"] == "nichts zu zahlen, Vortrag 6.000000 USDC"
    assert doc["stand"] == "nichts_zu_zahlen"
    assert doc["zeilen"] == [] and doc["summe"] == "0.000000"


def test_ein_fall_ohne_aktivierung_zahlt_auch_nichts():
    e, doc = rechne(W.faelle_aus_zeilen([fall(1, aktiv=None)]))
    assert doc["zeilen"] == []
    assert doc["ergebnis"].startswith("nichts zu zahlen")
    # the case closes its window in W44 only if registered then; here it
    # belongs to the week its 14-day window closes, which is W46
    assert "0 von 0" in doc["abgleich"]


def test_bezahlte_woche_traegt_pflichtfelder():
    e, doc = rechne(W.faelle_aus_zeilen([fall(1), fall(2)]))
    for feld in ("weg", "abgleich", "kohorten", "zahler", "summe",
                 "vortrag_aus", "stand", "auszahlung"):
        assert feld in doc
    assert doc["weg"] == "direkt"
    assert doc["stand"] == "vorgeschlagen"
    assert doc["abgleich"].startswith("2 von 2")
    assert doc["kohorten"] is KOHORTEN
    assert doc["auszahlung"]["stand"] == "nicht gezahlt"
    assert doc["ergebnis"].startswith("3 Zeilen")


def test_das_dokument_traegt_keine_volle_adresse():
    e, doc = rechne(W.faelle_aus_zeilen([fall(1), fall(2)]))
    text = json.dumps(doc, ensure_ascii=False)
    assert not __import__("re").search(r"0x[0-9a-fA-F]{40}", text)
    assert "0x26d6…67aa" in text


def test_privat_traegt_die_volle_adresse():
    liste = W.faelle_aus_zeilen([fall(1)])
    e, _doc = rechne(liste)
    privat = W.zeilen_privat(e, liste)
    assert "0x26d6bd4cd5c7a6e34f0c4b186a3dae6690f567aa" in [z["adresse"] for z in privat]


def test_solana_zeile_ist_als_nicht_zahlbar_markiert():
    adr = "So1anaAddre55So1anaAddre55So1anaAddre55xyz"
    e, doc = rechne(W.faelle_aus_zeilen([fall(1, kette="solana", adresse_emp=adr)]))
    z = [z for z in doc["zeilen"] if z["kette"] == "solana"]
    assert z and z[0]["auf_base_zahlbar"] is False
    assert all(z["auf_base_zahlbar"] for z in doc["zeilen"] if z["kette"] == "base")


def test_kurz():
    assert W.kurz("0x26d6bd4cd5c7a6e34f0c4b186a3dae6690f567aa") == "0x26d6…67aa"
    assert W.kurz(None) is None


# --- Versions ----------------------------------------------------------------

def test_erster_lauf_ist_version_eins():
    _e, doc = rechne([])
    p = W.plane(None, doc)
    assert p["version"] == 1
    assert list(p["dateien"]) == ["result.json"]
    assert p["dateien"]["result.json"]["ersetzt"] is None


def test_gleicher_inhalt_schreibt_nichts():
    _e, doc = rechne([])
    alt = W.plane(None, doc)["dateien"]["result.json"]
    neu = dict(doc, erzeugt="2026-11-02T07:00:00+00:00")
    assert W.plane(alt, neu) is None


def test_geaenderter_inhalt_wird_neue_version_und_haelt_die_alte():
    _e, doc = rechne([])
    alt = W.plane(None, doc)["dateien"]["result.json"]
    _e, neu = rechne(W.faelle_aus_zeilen([fall(1)]))
    p = W.plane(alt, neu)
    assert p["version"] == 2
    assert p["dateien"]["result-1.json"] is alt
    assert p["dateien"]["result.json"]["ersetzt"] == "result-1.json"


# --- Proposal ----------------------------------------------------------------

def test_vorschlag_ohne_volle_adresse_mit_bestand_und_freigabe():
    _e, doc = rechne(W.faelle_aus_zeilen([fall(1)]))
    t = W.vorschlag_text(doc, version=1, budget_wm1=KOHORTEN["budget_wm1"])
    assert not __import__("re").search(r"0x[0-9a-fA-F]{40}", t)
    assert "0x26d6…67aa" in t
    assert "Überwiesen ist nichts" in t
    assert "18.598802 USDC davor" in t
    assert "Abgleich: 1 von 1" in t


def test_vorschlag_sagt_wenn_der_bestand_nicht_lesbar_ist():
    _e, doc = rechne(W.faelle_aus_zeilen([fall(1)]))
    t = W.vorschlag_text(doc, version=1, budget_wm1={"fehler": "psql: weg"})
    assert "nicht lesbar: psql: weg" in t


def test_ablage_ohne_zeilen_schreibt_keinen_vorschlag(tmp_path):
    e, doc = rechne([])
    d = W.lege_ab(KW, 1, doc, [], None, ausgang=tmp_path)
    assert not (d / "vorschlag.md").exists()
    assert (d / "result-v1.json").exists()


def test_ablage_mit_zeilen_privat_nur_fuer_den_besitzer(tmp_path):
    liste = W.faelle_aus_zeilen([fall(1)])
    e, doc = rechne(liste)
    d = W.lege_ab(KW, 1, doc, W.zeilen_privat(e, liste), "text", ausgang=tmp_path)
    modus = stat.S_IMODE((d / "zeilen-v1.json").stat().st_mode)
    assert modus == 0o600
    assert (d / "vorschlag.md").read_text() == "text"


# --- What the module may do --------------------------------------------------

def test_das_modul_bewegt_kein_geld():
    """It talks to psql and to GitHub; it imports nothing that signs or
    sends a transaction."""
    baum = ast.parse(pathlib.Path(W.__file__).read_text(encoding="utf-8"))
    namen = set()
    for k in ast.walk(baum):
        if isinstance(k, ast.Import):
            namen |= {a.name for a in k.names}
        elif isinstance(k, ast.ImportFrom):
            namen.add(k.module or "")
    verboten = {"web3", "eth_account", "requests"}
    assert not {n.split(".")[0] for n in namen} & verboten
    quelle = pathlib.Path(W.__file__).read_text(encoding="utf-8")
    for wort in ("taskmarket", "send_transaction", "BASE_WRITE_KEY",
                 "BASE_ANCHOR_KEY", "private_key"):
        assert wort not in quelle, wort


def test_trocken_mit_vortrag_von_hand_nur_trocken(capsys):
    assert W.main(["--kw", KW, "--vortrag", "1"]) == 2
    assert "nur mit --trocken" in capsys.readouterr().out


def test_herzschlag(tmp_path):
    p = tmp_path / "hb.json"
    W.herzschlag("ok", "MolTrust - Dauerrunde 2026-W44: nichts zu zahlen", pfad=p)
    hb = json.loads(p.read_text())
    assert hb["status"] == "ok" and "timestamp" in hb and "W44" in hb["detail"]
