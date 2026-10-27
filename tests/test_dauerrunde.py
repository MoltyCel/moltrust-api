"""The standing round (ops G4-2): rule and split of one week, without money.

Every rule twice: the case that triggers it and the normal case that must
stay silent. No database, no network, no key: pure functions over values the
test brings.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import dauerrunde as D  # noqa: E402
from scripts import gruppen as G  # noqa: E402

KW = "2026-W44"                     # Mon 2026-10-26 .. Mon 2026-11-02
VON, BIS = D.wochenfenster(KW)
MUSTER = D.vorgeschrieben()
LEER = set()                        # exclusion list, empty unless a test says so
EIGENE = set()                      # own addresses, empty unless a test says so

_n = [0]


def _hex(i):
    return f"{i:016x}"


def seite(i, **anders):
    s = {"did": f"did:moltrust:{_hex(i)}",
         "adresse": f"0x{i:040x}",
         "schluessel": f"{i:064x}",
         "betreiber": f"did:moltrust:{_hex(0xa000 + i)}",
         "netz": f"10.{i % 250}.{i // 250}.0/24"}
    s.update(anders)
    return s


def fall(akt=VON + timedelta(days=1), reg=None, endpoint="/agents/me",
         empfohlen=None, empfehlend=None, fakt=None, aufrufe=None):
    _n[0] += 2
    reg = reg or (akt - timedelta(days=2))
    e = empfohlen or seite(_n[0])
    e.setdefault("registriert_am", reg.isoformat())
    return {
        "empfohlen": e,
        "empfehlend": empfehlend or seite(_n[0] + 1),
        "fakt": fakt or {},
        "aufrufe": aufrufe if aufrufe is not None
        else [{"ts": akt.isoformat(), "endpoint": endpoint}],
    }


def rechne(faelle, **kw):
    kw.setdefault("ausgeschlossen", LEER)
    kw.setdefault("eigene_adressen", EIGENE)
    kw.setdefault("muster", MUSTER)
    return D.abrechnung(faelle, kw=KW, **kw)


def klassen(erg):
    return [p["klasse"] for p in erg["abgewiesen"]]


# --- The constants ----------------------------------------------------------

def test_die_konstanten_sind_die_des_auftrags():
    """WM-1: 5 USDC a week, 0.05 per activation; G4: 14 days, three."""
    assert D.WOCHENBUDGET_MIKRO == 5_000_000
    assert D.DECKEL_JE_AKTIVIERUNG_MIKRO == 50_000
    assert D.FENSTER_TAGE == 14
    assert D.BEZAHLT_JE_BETREIBER == 3


def test_vorgeschrieben_kommt_aus_der_sql_datei():
    """One list of prescribed endpoints, shared with the cohort figure."""
    assert "/identity/verify/%" in MUSTER
    assert "/skill/trust-score/%" in MUSTER
    assert len(MUSTER) == 6


def test_eine_datei_ohne_liste_bricht_ab(tmp_path):
    p = tmp_path / "x.sql"
    p.write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError):
        D.vorgeschrieben(p)


def test_like_muster():
    assert D.ist_vorgeschrieben("/identity/verify/did:moltrust:1", MUSTER)
    assert D.ist_vorgeschrieben("/identity/erc8004/register", MUSTER)
    assert not D.ist_vorgeschrieben("/identity/verifyx", MUSTER)
    assert not D.ist_vorgeschrieben("/agents/me", MUSTER)


# --- Activation -------------------------------------------------------------

def test_aufruf_an_vorgeschriebenen_endpunkt_ist_keine_aktivierung():
    f = fall(endpoint="/identity/verify/did:moltrust:abc")
    # No activation: the case belongs to the week its window closes.
    f["empfohlen"]["registriert_am"] = (BIS - timedelta(days=15)).isoformat()
    erg = rechne([f])
    assert klassen(erg) == ["keine_aktivierung"]
    assert erg["zeilen"] == []


def test_aufruf_an_freien_endpunkt_ist_eine_aktivierung():
    erg = rechne([fall(endpoint="/agents/me")])
    assert klassen(erg) == []
    assert len(erg["bezahlt"]) == 1


def test_die_erste_freie_aktivierung_zaehlt_nicht_der_erste_aufruf():
    """A prescribed call first, then a free one: the free one is the
    activation, and it is what the window and the week are measured on."""
    reg = VON - timedelta(days=3)
    f = fall(reg=reg, aufrufe=[
        {"ts": (VON - timedelta(days=2)).isoformat(),
         "endpoint": "/skill/trust-score/did:moltrust:x"},
        {"ts": (VON + timedelta(hours=5)).isoformat(), "endpoint": "/agents/me"},
    ])
    erg = rechne([f])
    assert erg["bezahlt"][0]["aktiviert_am"] == (VON + timedelta(hours=5)).isoformat()


def test_aktivierung_nach_14_tagen_wird_abgewiesen():
    akt = VON + timedelta(days=1)
    erg = rechne([fall(akt=akt, reg=akt - timedelta(days=14, seconds=1))])
    assert klassen(erg) == ["aktivierung_zu_spaet"]


def test_aktivierung_am_14_tag_zaehlt():
    akt = VON + timedelta(days=1)
    erg = rechne([fall(akt=akt, reg=akt - timedelta(days=14))])
    assert klassen(erg) == []
    assert len(erg["bezahlt"]) == 1


def test_aktivierung_einer_anderen_woche_zaehlt_hier_nicht():
    erg = rechne([fall(akt=VON - timedelta(seconds=1)),
                  fall(akt=BIS)])
    assert erg["bezahlt"] == [] and erg["abgewiesen"] == []
    assert "2 gehoeren in eine andere Woche" in erg["abgleich"]


def test_ohne_aktivierung_wird_nur_in_der_woche_des_fensterendes_abgewiesen():
    """Silent probe: a case without activation is not refused every week."""
    f = fall(aufrufe=[])
    f["empfohlen"]["registriert_am"] = (VON - timedelta(days=30)).isoformat()
    assert rechne([f])["abgewiesen"] == []


# --- The referral -----------------------------------------------------------

@pytest.mark.parametrize("fakt", [{"self": True}, {"mutual": True},
                                  {"unknown": True}, {"revoked": True}])
def test_eine_nicht_zaehlende_empfehlung_wird_abgewiesen(fakt):
    erg = rechne([fall(fakt=fakt)])
    assert klassen(erg) == ["empfehlung_zaehlt_nicht"]


def test_eine_zaehlende_empfehlung_geht_durch():
    erg = rechne([fall(fakt={"self": False, "mutual": False,
                             "unknown": False, "revoked": False})])
    assert klassen(erg) == []


# --- Independence -----------------------------------------------------------

@pytest.mark.parametrize("merkmal", ["adresse", "schluessel", "betreiber"])
def test_gleiches_merkmal_wird_abgewiesen(merkmal):
    a = seite(9001)
    b = seite(9002, **{merkmal: a[merkmal].upper()})   # case does not hide it
    a["registriert_am"] = (VON - timedelta(days=1)).isoformat()
    erg = rechne([fall(empfohlen=a, empfehlend=b)])
    assert klassen(erg) == ["nicht_unabhaengig"]


def test_verschiedene_merkmale_gehen_durch():
    erg = rechne([fall()])
    assert klassen(erg) == []


@pytest.mark.parametrize("wo,merkmal", [("empfohlen", "adresse"),
                                        ("empfehlend", "schluessel"),
                                        ("empfehlend", "betreiber")])
def test_fehlendes_merkmal_ist_keine_verschiedenheit(wo, merkmal):
    f = fall()
    f[wo][merkmal] = None
    assert klassen(rechne([f])) == ["merkmal_fehlt"]


def test_gleiches_netz_wird_vermerkt_und_nicht_abgewiesen():
    a = seite(9101, netz="10.1.2.0/24")
    a["registriert_am"] = (VON - timedelta(days=1)).isoformat()
    b = seite(9102, netz="10.1.2.0/24")
    erg = rechne([fall(empfohlen=a, empfehlend=b)])
    assert klassen(erg) == []
    assert erg["vermerk_netz_gleich"] == [a["did"]]


def test_verschiedenes_netz_wird_nicht_vermerkt():
    erg = rechne([fall()])
    assert erg["vermerk_netz_gleich"] == []


# --- Exclusion list ---------------------------------------------------------

@pytest.mark.parametrize("wo", ["empfohlen", "empfehlend"])
def test_ausgeschlossener_betreiber_wird_abgewiesen(wo):
    f = fall()
    aus = {f[wo]["betreiber"].upper()}
    assert klassen(rechne([f], ausgeschlossen=aus)) == ["betreiber_ausgeschlossen"]


def test_nicht_ausgeschlossener_betreiber_geht_durch():
    erg = rechne([fall()], ausgeschlossen={"did:moltrust:ffffffffffffffff"})
    assert klassen(erg) == []


def test_die_liste_im_repo_wird_gelesen(tmp_path, monkeypatch):
    """Without an explicit set the run reads config/ausgeschlossene-betreiber.json."""
    f = fall()
    p = tmp_path / "aus.json"
    p.write_text('{"version": 1, "eintraege": [{"operator_did": "%s", '
                 '"runde": "r5", "grund": "x", "festgestellt_am": "2026-10-14", '
                 '"verweis": "/rounds/r5/result.json"}]}'
                 % f["empfehlend"]["betreiber"], encoding="utf-8")
    monkeypatch.setattr(G, "AUSSCHLUSSLISTE", p)
    erg = D.abrechnung([f], kw=KW, eigene_adressen=EIGENE, muster=MUSTER)
    assert klassen(erg) == ["betreiber_ausgeschlossen"]


def test_eine_unlesbare_liste_bricht_ab(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "AUSSCHLUSSLISTE", tmp_path / "fehlt.json")
    with pytest.raises(G.AusschlussUnlesbar):
        D.abrechnung([fall()], kw=KW, eigene_adressen=EIGENE, muster=MUSTER)


def test_die_echte_liste_ist_lesbar():
    assert isinstance(G.ausgeschlossene(), set)


# --- Cap per operator_did ---------------------------------------------------

def _gleicher_empfehlender(n, betreiber="did:moltrust:00000000000beef0"):
    raus = []
    for i in range(n):
        geb = seite(7000 + i, betreiber=betreiber)
        raus.append(fall(akt=VON + timedelta(hours=1 + i), empfehlend=geb))
    return raus


def test_vierte_aktivierung_eines_betreibers_wird_nicht_bezahlt():
    erg = rechne(_gleicher_empfehlender(4))
    assert len(erg["bezahlt"]) == 3
    assert klassen(erg) == ["deckel_betreiber"]
    # The first three by activation time are the paid ones.
    zeiten = sorted(p["aktiviert_am"] for p in erg["bezahlt"])
    assert erg["abgewiesen"][0]["aktiviert_am"] > zeiten[-1]


def test_drei_aktivierungen_eines_betreibers_werden_bezahlt():
    erg = rechne(_gleicher_empfehlender(3))
    assert len(erg["bezahlt"]) == 3 and klassen(erg) == []


def test_der_deckel_gilt_auch_fuer_den_betreiber_der_empfohlenen():
    raus = []
    for i in range(4):
        emp = seite(7100 + i, betreiber="did:moltrust:00000000000cafe0")
        emp["registriert_am"] = VON.isoformat()
        raus.append(fall(akt=VON + timedelta(hours=1 + i), empfohlen=emp))
    erg = rechne(raus)
    assert len(erg["bezahlt"]) == 3 and klassen(erg) == ["deckel_betreiber"]


# --- Split ------------------------------------------------------------------

def test_eine_woche_ohne_aktivierung_ergibt_null_zeilen_und_vortrag():
    erg = rechne([], vortrag_mikro=1_234_567)
    assert erg["zeilen"] == [] and erg["bezahlt"] == []
    assert erg["summe_mikro"] == 0
    assert erg["vortrag_aus_mikro"] == 5_000_000 + 1_234_567
    assert erg["abgleich"].startswith("0 von 0")


def test_der_deckel_je_aktivierung_greift_und_der_rest_traegt_vor():
    erg = rechne([fall(), fall()])
    assert erg["je_aktivierung_mikro"] == 50_000
    assert erg["summe_mikro"] == 100_000
    assert erg["vortrag_aus_mikro"] == 5_000_000 - 100_000


def test_haelfte_an_empfohlenen_haelfte_an_empfehlenden():
    f = fall()
    erg = rechne([f])
    je = {z["adresse"]: z for z in erg["zeilen"]}
    assert je[f["empfohlen"]["adresse"]]["betrag"] == "0.025000"
    assert je[f["empfehlend"]["adresse"]]["betrag"] == "0.025000"
    assert je[f["empfohlen"]["adresse"]]["rollen"] == ["empfohlen"]
    assert je[f["empfehlend"]["adresse"]]["rollen"] == ["empfehlend"]


def test_ist_das_budget_knapp_wird_gleich_verteilt():
    """Budget 0.1 USDC over three activations: 33332 each (even, so two
    exact halves), the rest carries over."""
    erg = rechne([fall(), fall(), fall()], budget_mikro=100_000)
    assert erg["je_aktivierung_mikro"] == 33_332
    assert {z["betrag_mikro"] for z in erg["zeilen"]} == {16_666}
    assert erg["summe_mikro"] + erg["vortrag_aus_mikro"] == 100_000
    assert erg["vortrag_aus_mikro"] == 4


def test_der_vortrag_der_vorwoche_wird_verteilt():
    erg = rechne([fall(), fall()], budget_mikro=0, vortrag_mikro=60_000)
    assert erg["je_aktivierung_mikro"] == 30_000
    assert erg["vortrag_aus_mikro"] == 0


def test_ein_empfehlender_mit_mehreren_aktivierungen_bekommt_eine_zeile():
    erg = rechne(_gleicher_empfehlender(1)
                 + [fall(akt=VON + timedelta(hours=9),
                         empfehlend=seite(7000, betreiber="did:moltrust:00000000000beef0"))])
    geb = [z for z in erg["zeilen"] if "empfehlend" in z["rollen"]]
    assert len(geb) == 1 and geb[0]["betrag_mikro"] == 50_000
    assert len(geb[0]["aktivierungen"]) == 2


def test_eigene_adresse_bekommt_nichts_und_der_anteil_traegt_vor():
    f = fall()
    erg = rechne([f], eigene_adressen={f["empfehlend"]["adresse"].upper()})
    assert [z["adresse"] for z in erg["zeilen"]] == [f["empfohlen"]["adresse"]]
    assert erg["eigen_nicht_ausgezahlt_mikro"] == 25_000
    assert erg["vortrag_aus_mikro"] == 5_000_000 - 25_000


def test_fremde_adressen_werden_ausgezahlt():
    erg = rechne([fall()], eigene_adressen={"0x" + "f" * 40})
    assert erg["eigen_nicht_ausgezahlt_mikro"] == 0
    assert len(erg["zeilen"]) == 2


def test_der_abgleich_geht_auf():
    faelle = [fall(), fall(fakt={"self": True}), fall(akt=BIS + timedelta(days=1))]
    erg = rechne(faelle)
    assert erg["abgleich"].startswith("2 von 2")
    assert "1 bezahlt, 1 abgewiesen" in erg["abgleich"]
    assert "1 gehoeren in eine andere Woche" in erg["abgleich"]


def test_negatives_budget_bricht_ab():
    with pytest.raises(ValueError):
        rechne([], vortrag_mikro=-1)


def test_kalenderwoche_muss_die_form_haben():
    with pytest.raises(ValueError):
        D.wochenfenster("44")
    von, bis = D.wochenfenster("2026-W44")
    assert von == datetime(2026, 10, 26, tzinfo=timezone.utc)
    assert bis - von == timedelta(days=7)


# --- What the module is not -------------------------------------------------

def test_jede_klasse_hat_text_und_muster():
    for k, v in D.KLASSEN.items():
        assert v["text"] and v["im_text"], k


def test_das_modul_bewegt_kein_geld():
    """No transfer, no key, no network, no subprocess: read, don't run."""
    import ast
    import pathlib

    quelle = pathlib.Path(D.__file__).read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    namen = {n.id for n in ast.walk(baum) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(baum) if isinstance(n, ast.Attribute)}
    importe = {a.name for n in ast.walk(baum)
               if isinstance(n, (ast.Import, ast.ImportFrom))
               for a in n.names} | {n.module for n in ast.walk(baum)
                                    if isinstance(n, ast.ImportFrom)}
    verboten = {"subprocess", "requests", "httpx", "web3", "urllib", "socket",
                "psql", "psycopg2", "notify"}
    assert not (importe & verboten), importe & verboten
    assert not ({"psql", "send_transaction", "transfer"} & (namen | attrs))
    assert "PRIVATE_KEY" not in quelle and "_KEY\"" not in quelle
