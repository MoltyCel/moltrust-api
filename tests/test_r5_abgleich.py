#!/usr/bin/env python3
"""Der Abgleich der Gruppenfassung: jede Einreichung traegt einen Grund.

Diese Probe hat in Runde 4 den Fehler gefunden, der sonst veroeffentlicht
worden waere — 20 statt 49 Abweisungen und 29 Einreichungen "ohne
aufgeschluesselten Grund". Die Gruppenfassung hat mehr Stellen, an denen eine
Einreichung durchfallen kann, also gilt sie hier erst recht:

    einreichungen = bezahlt + ohne_platz + abgewiesen + rest,  rest = 0
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

from scripts import gruppen as G  # noqa: E402
from scripts import runden_auswertung as R  # noqa: E402

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
SPEC = {"ref": "GRUPPE-R5-1", "profile": "gruppe", "slots": 9, "cap": 1,
        "round": "r5", "gross": 0.541, "label": "Runde 5, Gruppe 1 von 2",
        "id": "0x" + "a" * 64}


def did(n):
    return f"did:moltrust:{n:016x}"


def eintrag(n, gruppe, minute=0, betreiber=None):
    return {"did": did(n), "addr": f"0x{n:040x}", "addr_lc": f"0x{n:040x}",
            "at": (T0 + timedelta(minutes=minute)).isoformat(),
            "operator_did": betreiber or f"did:moltrust:op{n}",
            "gruppe": {did(x) for x in gruppe}}


def dreieck(ns, start=0):
    m = [did(n) for n in ns]
    return [{"endorser": a, "endorsed": b,
             "issued_at": (T0 + timedelta(minutes=start + i)).isoformat()}
            for i, (a, b) in enumerate(sorted(G.paare(m)))]


@pytest.fixture
def ohne_datenbank(monkeypatch):
    """Die vier Merkmale und die Endorsements kommen hier aus der Probe.

    `gruppen_auswertung` liest sie sonst aus `agents` und `endorsements`; der
    Abgleich ist eine Eigenschaft der Rechnung und nicht der Datenbank.
    """
    def merkmale(dids):
        return {d: {"adresse": f"0xwallet{d[-4:]}", "schluessel": f"k{d[-4:]}",
                    "betreiber": f"op-{d[-4:]}", "netz": f"10.{d[-2:]}.0.0/24"}
                for d in dids}
    monkeypatch.setattr(R, "merkmale_fuer", merkmale)
    monkeypatch.setattr(G, "ausgeschlossene", lambda pfad=None: set())
    return monkeypatch


def lauf(monkeypatch, kept, endorsements, subs_n=None):
    monkeypatch.setattr(R, "endorsements_fuer", lambda dids: endorsements)
    subs = [{"id": f"s{i}"} for i in range(subs_n if subs_n is not None
                                           else len(kept))]
    gathered = [(SPEC, {"expiryTime": None}, subs, kept, 0, {})]
    capped = {SPEC["ref"]: (kept, 0)}
    return R.ergebnis("r5", gathered, capped, {}, stand="offen")


def test_eine_vollstaendige_gruppe_geht_auf(ohne_datenbank):
    kept = [eintrag(n, (1, 2, 3)) for n in (1, 2, 3)]
    doc = lauf(ohne_datenbank, kept, dreieck((1, 2, 3)))
    ab = doc["abgleich"]
    assert ab["ohne_aufgeschluesselten_grund"] == 0, doc["abgewiesen"]
    assert ab["bezahlt"] == 3
    assert ab["einreichungen"] == 3
    assert ab["bezahlt"] + ab["ohne_platz"] + ab["abgewiesen"] == 3


def test_vier_gruppen_die_vierte_ohne_platz(ohne_datenbank):
    """Drei Gruppen zahlen, die vierte steht als `platz_vergeben`."""
    kept, ends = [], []
    for g, ns in enumerate([(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]):
        kept += [eintrag(n, ns, minute=g) for n in ns]
        ends += dreieck(ns, start=g * 10)
    doc = lauf(ohne_datenbank, kept, ends)
    ab = doc["abgleich"]
    assert ab["bezahlt"] == 9, doc["bezahlt"]
    assert ab["ohne_platz"] == 3
    assert ab["abgewiesen"] == 0
    assert ab["ohne_aufgeschluesselten_grund"] == 0
    assert ab["einreichungen"] == 12


def test_eine_gescheiterte_gruppe_steht_mit_grund_in_der_datei(ohne_datenbank):
    """Fuenf Paare statt sechs — und dann greift der Rueckfall.

    Meine erste Fassung dieses Tests erwartete drei Absagen. Falsch: der
    Aufgabentext sagt zu, dass ohne eine einzige vollstaendige Gruppe die
    ersten zehn Einzeleinreichungen zahlen. Sie werden also bezahlt und
    tragen zurecht keinen Absagegrund.

    Genau daran ist aufgefallen, dass der Grund dann nirgends stand: der
    Gruppenbefund wurde gerechnet und nicht geschrieben. Er steht jetzt im
    `gruppen`-Abschnitt — sonst koennte die Datei die Frage nicht
    beantworten, fuer die die Adresse existiert.
    """
    kept = [eintrag(n, (1, 2, 3)) for n in (1, 2, 3)]
    fuenf = [e for e in dreieck((1, 2, 3))
             if (e["endorser"], e["endorsed"]) != (did(3), did(1))]
    doc = lauf(ohne_datenbank, kept, fuenf)
    ab = doc["abgleich"]
    assert ab["bezahlt"] == 3, "Rueckfall: die Einzelnen werden bezahlt"
    assert ab["ohne_aufgeschluesselten_grund"] == 0
    g = doc["gruppen"]
    assert len(g) == 1
    assert g[0]["grundklasse"] == "zu_wenige_beitraege"
    assert g[0]["beitraege"] == 5
    assert g[0]["vollstaendig"] is False
    assert g[0]["stand"] == "bezahlt", "ueber den Rueckfall, nicht als Gruppe"
    assert g[0]["grund_text"]


def test_wer_keine_gruppe_bildet_traegt_auch_einen_grund(ohne_datenbank):
    """Uneinigkeit: zwei nennen ein Trio, der dritte ein anderes."""
    kept = [eintrag(1, (1, 2, 3)), eintrag(2, (1, 2, 3)), eintrag(3, (3, 4, 5))]
    doc = lauf(ohne_datenbank, kept, dreieck((1, 2, 3)))
    ab = doc["abgleich"]
    assert ab["ohne_aufgeschluesselten_grund"] == 0, doc["abgewiesen"]
    assert ab["bezahlt"] + ab["ohne_platz"] + ab["abgewiesen"] == 3
    # Keine Gruppe wird vollstaendig, also greift der Rueckfall und niemand
    # wird abgewiesen.
    assert ab["bezahlt"] == 3
    # Meine erste Erwartung war eine leere `gruppen`-Liste. Falsch: did(3)
    # nennt {3,4,5}, und weil 4 und 5 nicht eingereicht haben, widerspricht
    # ihm niemand — die Gruppe entsteht also und faellt erst in `bewerte`
    # als `gruppe_unvollstaendig` durch. Genau so soll es sein: wuerde sie
    # schon bei der Bildung verschwinden, bekaeme niemand einen Grund zu
    # lesen.
    g = doc["gruppen"]
    assert len(g) == 1
    assert g[0]["grundklasse"] == "gruppe_unvollstaendig"
    assert g[0]["mitglieder"] == sorted([did(3), did(4), did(5)])


def test_eine_unlesbare_einreichung_bleibt_gezaehlt(ohne_datenbank):
    """Mehr Einreichungen als qualifizierte: der Rest muss benannt sein.

    `qualify` haette den Grund geliefert; hier wird er leer gelassen, damit
    sichtbar wird, dass der Abgleich eine Luecke dann auch zeigt.
    """
    kept = [eintrag(n, (1, 2, 3)) for n in (1, 2, 3)]
    doc = lauf(ohne_datenbank, kept, dreieck((1, 2, 3)), subs_n=5)
    assert doc["abgleich"]["ohne_aufgeschluesselten_grund"] == 2, \
        "zwei Einreichungen ohne Grund — genau das soll der Abgleich zeigen"


def test_der_rueckfall_erscheint_als_bezahlt(ohne_datenbank):
    """Keine Gruppe vollstaendig: die ersten zehn Einzelnen werden bezahlt."""
    kept = [eintrag(n, (1, 2, 3)) for n in (1, 2, 3)]
    doc = lauf(ohne_datenbank, kept, [])        # keine Endorsements
    ab = doc["abgleich"]
    assert ab["bezahlt"] == 3, doc["bezahlt"]
    assert ab["ohne_aufgeschluesselten_grund"] == 0
    assert ab["abgewiesen"] == 0


def test_bedingungen_und_deckel_stehen_in_der_datei(ohne_datenbank):
    kept = [eintrag(n, (1, 2, 3)) for n in (1, 2, 3)]
    doc = lauf(ohne_datenbank, kept, dreieck((1, 2, 3)))
    assert doc["bedingungen"]["fassung"] == "gruppen"
    assert doc["rundendeckel"]["plaetze_je_aufgabe"] == 9
    assert doc["bedingungen"]["gruppe"]["ausschlussliste"]["pfad"] == \
        "config/ausgeschlossene-betreiber.json"


def test_die_vierte_gruppe_steht_als_ohne_platz_in_der_datei(ohne_datenbank):
    kept, ends = [], []
    for g, ns in enumerate([(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]):
        kept += [eintrag(n, ns, minute=g) for n in ns]
        ends += dreieck(ns, start=g * 10)
    doc = lauf(ohne_datenbank, kept, ends)
    stand = {tuple(e["mitglieder"]): e["stand"] for e in doc["gruppen"]}
    assert sorted(stand.values()) == ["bezahlt"] * 3 + ["ohne_platz"]
    vierte = [e for e in doc["gruppen"] if e["stand"] == "ohne_platz"][0]
    assert vierte["vollstaendig"] is True
    assert vierte["grundklasse"] == "platz_vergeben"


def test_die_datei_nennt_kein_merkmal_mit_wert(ohne_datenbank):
    """Der Befund gehoert hinein, der Wert nicht.

    Dass zwei Mitglieder dasselbe Netz haben, ist die Aussage; welches Netz,
    gehoert nicht in eine Datei, die ohne Anlass veroeffentlicht wird.
    """
    kept = [eintrag(n, (1, 2, 3)) for n in (1, 2, 3)]
    doc = lauf(ohne_datenbank, kept, dreieck((1, 2, 3)))
    roh = json.dumps(doc)
    assert "0/24" not in roh and "0xwallet" not in roh
    for e in doc["gruppen"]:
        assert all(isinstance(k, str) for k in e["kollisionen"])


def test_die_einzelfassung_bekommt_keinen_gruppen_abschnitt(ohne_datenbank):
    """Eine leere Liste waere eine Zusage, die es dort nicht gibt."""
    spec = {**SPEC, "profile": "stage1", "slots": 10, "ref": "STUFE1-R4-1"}
    kept = [{"did": did(n), "addr": f"0x{n:040x}", "addr_lc": f"0x{n:040x}",
             "at": T0.isoformat()} for n in (1, 2)]
    gathered = [(spec, {"expiryTime": None}, [{"id": "s0"}, {"id": "s1"}],
                 kept, 0, {})]
    doc = R.ergebnis("r4", gathered, {spec["ref"]: (kept, 0)}, {})
    assert "gruppen" not in doc
