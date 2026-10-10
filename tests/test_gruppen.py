#!/usr/bin/env python3
"""Die Gruppenpruefung aus Runde 5.

Zwei Sorten Tests, und die zweite ist die, die in Runde 4 gefehlt haette: der
Befund loest aus, UND der Normalfall laeuft durch. Ein Erfolgskriterium, das
nur an seinen Verletzungen geprueft ist, ist geraten.
"""
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import gruppen as G  # noqa: E402

A, B, C, D = ("did:moltrust:aaaaaaaaaaaaaaaa", "did:moltrust:bbbbbbbbbbbbbbbb",
              "did:moltrust:cccccccccccccccc", "did:moltrust:dddddddddddddddd")
T0 = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)


def end(a, b, minuten=0):
    return {"endorser": a, "endorsed": b,
            "issued_at": (T0 + timedelta(minutes=minuten)).isoformat()}


def dreieck(mitglieder=(A, B, C), spanne=60):
    """Sechs Endorsements, gleichmaessig ueber `spanne` Minuten."""
    ps = sorted(G.paare(mitglieder))
    schritt = spanne / max(len(ps) - 1, 1)
    return [end(a, b, int(i * schritt)) for i, (a, b) in enumerate(ps)]


def merkmale(mitglieder=(A, B, C), **ueberschreiben):
    m = {}
    for i, d in enumerate(sorted(mitglieder)):
        m[d] = {"adresse": f"0x{i:040x}", "schluessel": f"key{i}",
                "betreiber": f"did:moltrust:op{i}", "netz": f"10.{i}.0.0/24"}
    for d, felder in ueberschreiben.items():
        m.setdefault(d, {}).update(felder)
    return m


# --- Der Normalfall. Ohne diesen Test ist jeder Befund unten wertlos. -------

def test_ein_vollstaendiges_dreieck_geht_durch():
    b = G.bewerte((A, B, C), {A, B, C}, dreieck(), merkmale())
    assert b["grundklasse"] is None, b
    assert b["vollstaendig"] is True
    assert b["beitraege"] == 6
    assert b["abgeschlossen_am"] is not None
    assert b["kollisionen"] == []


def test_jedes_mitglied_gibt_zwei_und_bleibt_unter_dem_deckel():
    b = G.bewerte((A, B, C), {A, B, C}, dreieck(), merkmale())
    assert sorted(b["betreiber_anteile"].values()) == [2, 2, 2]
    assert max(b["betreiber_anteile"].values()) <= G.DECKEL_JE_BETREIBER


def test_nachendorsieren_wirft_niemanden_heraus():
    """Mehrere Zeilen je Paar sind erlaubt (UNIQUE laeuft ueber den Hash)."""
    e = dreieck() + [end(A, B, 5000), end(B, A, 5001)]
    b = G.bewerte((A, B, C), {A, B, C}, e, merkmale())
    assert b["vollstaendig"] is True, b


# --- Die Befunde ------------------------------------------------------------

def test_zwei_mitglieder_sind_keine_gruppe():
    b = G.bewerte((A, B), {A, B}, dreieck((A, B)), merkmale((A, B)))
    assert b["grundklasse"] == "gruppe_nicht_drei"


def test_wer_nicht_eingereicht_hat_macht_die_gruppe_unvollstaendig():
    b = G.bewerte((A, B, C), {A, B}, dreieck(), merkmale())
    assert b["grundklasse"] == "gruppe_unvollstaendig"


def test_fuenf_paare_reichen_nicht():
    e = [x for x in dreieck() if (x["endorser"], x["endorsed"]) != (C, A)]
    b = G.bewerte((A, B, C), {A, B, C}, e, merkmale())
    assert b["grundklasse"] == "zu_wenige_beitraege"
    assert b["beitraege"] == 5


def test_doppeltes_paar_zaehlt_einmal():
    e = [x for x in dreieck() if (x["endorser"], x["endorsed"]) != (C, A)]
    e.append(end(A, B, 7))          # dasselbe Paar nochmal, anderer Zeitpunkt
    b = G.bewerte((A, B, C), {A, B, C}, e, merkmale())
    assert b["beitraege"] == 5, "ein Paar zweimal ist ein Beitrag"
    assert b["grundklasse"] == "zu_wenige_beitraege"


def test_ueber_25_stunden_verteilt_faellt_aus_dem_fenster():
    b = G.bewerte((A, B, C), {A, B, C}, dreieck(spanne=25 * 60), merkmale())
    assert b["grundklasse"] == "fenster_ueberschritten"


def test_genau_24_stunden_sind_noch_drin():
    b = G.bewerte((A, B, C), {A, B, C}, dreieck(spanne=24 * 60), merkmale())
    assert b["grundklasse"] is None, b


def test_ein_betreiber_mit_vier_der_sechs_bricht_am_deckel():
    """Zwei Betreiber koennen das Dreieck nicht tragen — genau der Punkt."""
    m = merkmale()
    m[sorted((A, B, C))[1]]["betreiber"] = m[sorted((A, B, C))[0]]["betreiber"]
    b = G.bewerte((A, B, C), {A, B, C}, dreieck(), m)
    assert b["grundklasse"] == "betreiber_deckel", b
    assert max(b["betreiber_anteile"].values()) == 4


def test_geteilte_adresse_schliesst_aus():
    m = merkmale()
    m[C]["adresse"] = m[A]["adresse"]
    m[C]["betreiber"] = "did:moltrust:opX"   # Deckel vorher nicht ausloesen
    b = G.bewerte((A, B, C), {A, B, C}, dreieck(), m)
    assert b["grundklasse"] == "betreiber_nicht_unabhaengig"
    assert b["kollisionen"][0]["merkmal"] == "adresse"


def test_geteiltes_netz_wird_vermerkt_und_schliesst_nicht_aus():
    """Drei Betreiber koennen sich ein /24 teilen, ohne davon zu wissen.

    Ein Ausschluss daran waere wieder eine Absage, die niemand vorher
    pruefen konnte — genau die 42 aus Runde 4. Also ausweisen, nicht
    ausschliessen.
    """
    m = merkmale()
    m[C]["netz"] = m[A]["netz"]
    b = G.bewerte((A, B, C), {A, B, C}, dreieck(), m)
    assert b["grundklasse"] is None, b
    assert b["vollstaendig"] is True
    assert [k["merkmal"] for k in b["kollisionen"]] == ["netz"]


def test_geteilter_schluessel_schliesst_aus():
    m = merkmale()
    m[C]["schluessel"] = m[A]["schluessel"]
    b = G.bewerte((A, B, C), {A, B, C}, dreieck(), m)
    assert b["grundklasse"] == "betreiber_nicht_unabhaengig"
    assert [k["merkmal"] for k in b["kollisionen"]] == ["schluessel"]


def test_ausschluss_und_hinweis_sind_disjunkt():
    assert set(G.MERKMALE_AUSSCHLUSS) & set(G.MERKMALE_HINWEIS) == set()
    assert set(G.MERKMALE) == set(G.MERKMALE_AUSSCHLUSS) | set(G.MERKMALE_HINWEIS)


def test_ein_fehlendes_merkmal_ist_keine_kollision():
    """Eine fehlende Messung wird nicht als Befund verkauft."""
    m = merkmale()
    m[C]["netz"] = None
    b = G.bewerte((A, B, C), {A, B, C}, dreieck(), m)
    assert b["grundklasse"] is None, b
    assert b["ungemessen"] == [{"merkmal": "netz", "dids": [C]}]


# --- Gruppenbildung aus den Einreichungen ----------------------------------

def test_drei_die_dasselbe_nennen_bilden_eine_gruppe():
    e = [{"did": d, "gruppe": {A, B, C}} for d in (A, B, C)]
    gs, gruende = G.gruppen(e)
    assert gs == [frozenset({A, B, C})]
    assert gruende == {}


def test_wer_etwas_anderes_nennt_ist_uneinig():
    """C nennt sich selbst, aber ein anderes Trio als A und B.

    Nicht {A,B,D} als Cs Gruppe: darin kommt C nicht vor, und das ist
    `gruppe_nicht_drei` — ein anderer Befund, geprueft im Test darunter.
    """
    e = [{"did": A, "gruppe": {A, B, C}}, {"did": B, "gruppe": {A, B, C}},
         {"did": C, "gruppe": {A, C, D}}]
    gs, gruende = G.gruppen(e)
    assert gruende[C] == "gruppe_uneinig"
    assert gruende[A] == "gruppe_uneinig"
    assert gruende[B] == "gruppe_uneinig"
    assert gs == []


def test_wer_sich_selbst_nicht_nennt_faellt_heraus():
    e = [{"did": A, "gruppe": {B, C, D}}]
    gs, gruende = G.gruppen(e)
    assert gruende[A] == "gruppe_nicht_drei"


def test_eine_gruppe_entsteht_auch_wenn_ein_mitglied_fehlt():
    """Die Bildung trennt nicht ab, was `bewerte` nennen soll.

    Nennen alle Anwesenden dasselbe Trio, entsteht die Gruppe — und
    `bewerte` sagt dann `gruppe_unvollstaendig`. Wuerde sie hier schon
    verschwinden, bekaeme niemand einen Grund zu lesen.
    """
    e = [{"did": A, "gruppe": {A, B, C}}, {"did": B, "gruppe": {A, B, C}}]
    gs, gruende = G.gruppen(e)
    assert gs == [frozenset({A, B, C})]
    b = G.bewerte(gs[0], {A, B}, dreieck(), merkmale())
    assert b["grundklasse"] == "gruppe_unvollstaendig"


# --- Rang -------------------------------------------------------------------

def test_die_ersten_drei_gruppen_zahlen_der_vierte_nicht():
    befunde = [{"vollstaendig": True, "gruppe": [f"g{i}"],
                "abgeschlossen_am": f"2026-10-12T0{i}:00:00+00:00"}
               for i in range(4)]
    bezahlt, leer = G.rang(befunde)
    assert [b["gruppe"] for b in bezahlt] == [["g0"], ["g1"], ["g2"]]
    assert leer[0]["grundklasse"] == "platz_vergeben"
    assert leer[0]["grund_text"]


def test_rang_ist_der_abschluss_nicht_die_einreichung():
    befunde = [
        {"vollstaendig": True, "gruppe": ["spaet"],
         "abgeschlossen_am": "2026-10-12T10:00:00+00:00"},
        {"vollstaendig": True, "gruppe": ["frueh"],
         "abgeschlossen_am": "2026-10-12T09:00:00+00:00"},
    ]
    bezahlt, _ = G.rang(befunde)
    assert [b["gruppe"] for b in bezahlt] == [["frueh"], ["spaet"]]


def test_unvollstaendige_gruppen_bekommen_keinen_platz():
    befunde = [{"vollstaendig": False, "gruppe": ["x"],
                "abgeschlossen_am": None}]
    bezahlt, leer = G.rang(befunde)
    assert bezahlt == [] and leer == []


# --- Die Zahlen haengen zusammen -------------------------------------------

def test_neun_plaetze_passen_unter_die_zehn_des_vertrags():
    assert G.GRUPPEN_JE_AUFGABE * G.MITGLIEDER == 9 <= 10


def test_jeder_fehlgrund_hat_einen_text():
    assert set(G.GRUENDE) >= {
        "gruppe_nicht_drei", "gruppe_uneinig", "gruppe_unvollstaendig",
        "zu_wenige_beitraege", "fenster_ueberschritten", "betreiber_deckel",
        "betreiber_nicht_unabhaengig", "platz_vergeben"}
    assert all(t and t[0].isupper() for t in G.GRUENDE.values())


def test_das_modul_handelt_beim_import_nicht():
    """Eine Probe fuehrt keine Modulruempfe aus — sie liest sie."""
    import ast

    quelle = pathlib.Path(G.__file__).read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    erlaubt = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef,
               ast.Assign, ast.AnnAssign, ast.Expr)
    fremd = [type(k).__name__ for k in baum.body
             if not isinstance(k, erlaubt)]
    assert fremd == [], fremd
    aufrufe = [k for k in baum.body
               if isinstance(k, ast.Expr) and isinstance(k.value, ast.Call)]
    assert aufrufe == [], "Aufruf auf Modulebene"


# --- Der Rueckfall ----------------------------------------------------------

def test_ohne_eine_einzige_gruppe_zahlen_die_einzelnen():
    """Sonst bleibt der Escrow gebunden, und der einzige Ausgang des Marktes
    waere `reject-all-submissions` — "spam or low-quality" ueber Agenten, die
    gearbeitet haben."""
    entries = [{"did": f"did:moltrust:{i:016x}", "at": f"2026-10-12T0{i}:00:00Z"}
               for i in range(4)]
    p = G.plaetze([{"vollstaendig": False, "gruppe": ["x"],
                    "abgeschlossen_am": None}], entries)
    assert p["weg"] == "rueckfall"
    assert [e["did"] for e in p["einzeln"]] == [e["did"] for e in entries]
    assert p["gruppen"] == []


def test_sobald_eine_gruppe_steht_greift_der_rueckfall_nicht():
    entries = [{"did": "did:moltrust:0000000000000009", "at": "2026-10-12T01:00:00Z"}]
    p = G.plaetze([{"vollstaendig": True, "gruppe": [A, B, C],
                    "abgeschlossen_am": "2026-10-12T09:00:00+00:00"}], entries)
    assert p["weg"] == "gruppen"
    assert p["einzeln"] == []
    assert len(p["gruppen"]) == 1


def test_eine_halb_gefuellte_aufgabe_ist_kein_fehlschlag():
    """Eine Gruppe statt drei fuellt drei von neun Plaetzen. Das ist kein
    Grund, auf den Rueckfall zu wechseln und daneben zu zahlen."""
    p = G.plaetze([{"vollstaendig": True, "gruppe": [A, B, C],
                    "abgeschlossen_am": "2026-10-12T09:00:00+00:00"}],
                  [{"did": "did:moltrust:000000000000000a", "at": "2026-10-12T01:00:00Z"}])
    assert p["weg"] == "gruppen" and p["einzeln"] == []


def test_der_rueckfall_haelt_den_zehner_des_vertrags_ein():
    entries = [{"did": f"did:moltrust:{i:016x}", "at": f"2026-10-12T{i:02d}:00:00Z"}
               for i in range(14)]
    p = G.plaetze([], entries)
    assert p["weg"] == "rueckfall"
    assert len(p["einzeln"]) == G.RUECKFALL_PLAETZE == 10


def test_der_rueckfall_ist_nach_zeit_und_nicht_nach_dict_reihenfolge():
    entries = [{"did": "did:moltrust:000000000000000b", "at": "2026-10-12T05:00:00Z"},
               {"did": "did:moltrust:000000000000000c", "at": "2026-10-12T01:00:00Z"}]
    p = G.plaetze([], entries)
    assert [e["at"] for e in p["einzeln"]] == ["2026-10-12T01:00:00Z",
                                               "2026-10-12T05:00:00Z"]


def test_gleiche_zeit_wird_nach_did_entschieden():
    entries = [{"did": "did:moltrust:00000000000000ff", "at": "2026-10-12T01:00:00Z"},
               {"did": "did:moltrust:0000000000000011", "at": "2026-10-12T01:00:00Z"}]
    p = G.plaetze([], entries)
    assert [e["did"] for e in p["einzeln"]][0].endswith("11")
