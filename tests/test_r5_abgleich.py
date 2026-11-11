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


def lauf(monkeypatch, kept, endorsements, subs_n=None, frist_vorbei=True):
    """`frist_vorbei` steuert, ob der Rueckfall ueberhaupt in Frage kommt.

    Vorgabe True, weil die meisten Proben das Urteil nach Ablauf pruefen. Die
    Gegenprobe davor steht ausdruecklich daneben — eine Schwelle traegt zwei
    Tests.
    """
    monkeypatch.setattr(R, "endorsements_fuer", lambda dids: endorsements)
    monkeypatch.setattr(R, "frist_vorbei", lambda runde, jetzt=None: frist_vorbei)
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
    # Paid on the fallback, so the group itself is not recognised: since #791
    # only the group route can make a group "bezahlt" (a group with the reason
    # "fewer than six endorsed pairs" stood there as "bezahlt" on 2026-10-14).
    # The route is stated per task.
    assert g[0]["stand"] == "nicht_anerkannt", "ueber den Rueckfall, nicht als Gruppe"
    assert [a["weg"] for a in doc["aufgaben"]] == ["rueckfall"]
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


# --- Die Frist ist eine Schwelle, also zwei Proben --------------------------

def test_vor_ablauf_greift_der_rueckfall_nicht(ohne_datenbank):
    """Sonst traegt eine Datei mit `stand: offen` eine Gewinnerliste.

    Der Aufgabentext sagt den Rueckfall fuer den Fall zu, dass die 72 Stunden
    ablaufen. Vorher angewendet erzeugt er eine Liste, die ein Leser als "das
    sind die Bezahlten" liest und die sich mit jeder Einreichung aendert.
    """
    kept = [eintrag(n, (1, 2, 3)) for n in (1, 2, 3)]
    doc = lauf(ohne_datenbank, kept, [], frist_vorbei=False)
    ab = doc["abgleich"]
    assert ab["bezahlt"] == 0, doc["bezahlt"]
    assert ab["ohne_aufgeschluesselten_grund"] == 0


def test_nach_ablauf_greift_er(ohne_datenbank):
    kept = [eintrag(n, (1, 2, 3)) for n in (1, 2, 3)]
    doc = lauf(ohne_datenbank, kept, [], frist_vorbei=True)
    assert doc["abgleich"]["bezahlt"] == 3


def test_eine_fertige_gruppe_zahlt_auch_vor_ablauf(ohne_datenbank):
    """Ihr Rang ist die Zeit ihres sechsten Endorsements, und eine spaeter
    fertige Gruppe kann eine frueher fertige nicht verdraengen. Nur der
    Rueckfall ist unstetig."""
    kept = [eintrag(n, (1, 2, 3)) for n in (1, 2, 3)]
    doc = lauf(ohne_datenbank, kept, dreieck((1, 2, 3)), frist_vorbei=False)
    assert doc["abgleich"]["bezahlt"] == 3
    assert doc["gruppen"][0]["stand"] == "bezahlt"


def test_eine_fehlende_frist_laesst_den_rueckfall_nicht_greifen():
    """Kein Rueckfallwert in der erlaubenden Richtung."""
    assert R.frist_vorbei("r9") is False
    assert R.frist_vorbei("") is False


def test_die_frist_kommt_aus_derselben_stelle_wie_die_wache():
    from datetime import datetime, timezone

    from scripts.task_watch import LAEUFE

    bis = datetime.fromisoformat(LAEUFE["r5"]["deadline"])
    assert R.frist_vorbei("r5", jetzt=bis.replace(microsecond=0)) is False
    spaeter = bis.timestamp() + 60
    assert R.frist_vorbei(
        "r5", jetzt=datetime.fromtimestamp(spaeter, timezone.utc)) is True


# --- Eine Rechnung, zwei Ausgaben ------------------------------------------

def test_die_gruppengruende_haben_genau_eine_herleitung():
    """Zwei Stellen weichen ab, und zwar still.

    Am 11.10. sagte die Markdown-Auswertung 1 bezahlt und 0,500425 USDC, wo
    die result.json desselben Laufs 0 bezahlt sagte — ich hatte nur den
    Dokumentenbau auf die Gruppenfassung gebracht. Nach dem Fix sagte sie
    14 von 15 Abweisungen, wo die Datei 15 sagte, weil nur eine der beiden
    die Gruppenebene kannte. Beides waere nicht passiert, wenn es die
    Herleitung nur einmal gaebe.
    """
    import ast
    import pathlib

    from scripts import runden_auswertung as R

    quelle = pathlib.Path(R.__file__).read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    namen = [k.name for k in ast.walk(baum)
             if isinstance(k, ast.FunctionDef) and k.name == "gruppen_gruende"]
    assert namen == ["gruppen_gruende"], "genau eine Herleitung"

    aufrufe = [k for k in ast.walk(baum)
               if isinstance(k, ast.Call)
               and isinstance(k.func, ast.Name)
               and k.func.id == "gruppen_gruende"]
    assert len(aufrufe) == 2, f"erwartet zwei Aufrufer, gefunden {len(aufrufe)}"

    # Und `gruppen_auswertung` hat genau einen Aufrufer: `gruppen_alle`.
    # Jede weitere Stelle ist eine zweite Rechnung, und die weicht ab.
    rufer = []
    for fn in ast.walk(baum):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for k in ast.walk(fn):
            if (isinstance(k, ast.Call) and isinstance(k.func, ast.Name)
                    and k.func.id == "gruppen_auswertung"):
                rufer.append(fn.name)
    assert rufer == ["gruppen_alle"], rufer


def test_markdown_und_dokument_zaehlen_dieselben_abweisungen(ohne_datenbank):
    """Die Zahl, die in beiden Ausgaben steht, muss dieselbe sein."""
    kept = [eintrag(n, (1, 2, 3)) for n in (1, 2)]   # drittes fehlt
    doc = lauf(ohne_datenbank, kept, dreieck((1, 2, 3)), subs_n=5,
               frist_vorbei=False)
    aus = R.gruppen_alle(
        [(SPEC, {"expiryTime": None}, [{"id": f"s{i}"} for i in range(5)],
          kept, 0, {})],
        {SPEC["ref"]: (kept, 0)})
    gruende = R.gruppen_gruende(aus[SPEC["ref"]], kept)
    # Was die Markdown-Schleife aufaddieren wuerde, ist was im Dokument steht.
    assert sum(gruende.values()) == doc["abgleich"]["abgewiesen"]
    assert doc["abgleich"]["ohne_aufgeschluesselten_grund"] == 3, (
        "fuenf Einreichungen, zwei qualifiziert — drei ohne Grund, "
        "und der Abgleich zeigt sie")


def test_die_auswertung_wird_einmal_gerechnet(ohne_datenbank, monkeypatch):
    """Zwei Abfragen koennten verschiedene Staende sehen."""
    zaehler = {"n": 0}
    echt = R.gruppen_auswertung

    def gezaehlt(spec, kept):
        zaehler["n"] += 1
        return echt(spec, kept)

    monkeypatch.setattr(R, "gruppen_auswertung", gezaehlt)
    # The endorsements and the deadline come from the probe, as in lauf():
    # unstubbed, endorsements_fuer() runs psql against the live database name.
    monkeypatch.setattr(R, "endorsements_fuer", lambda dids: dreieck((1, 2, 3)))
    monkeypatch.setattr(R, "frist_vorbei", lambda runde, jetzt=None: True)
    kept = [eintrag(n, (1, 2, 3)) for n in (1, 2, 3)]
    R.gruppen_alle(
        [(SPEC, {"expiryTime": None}, [{"id": "s0"}], kept, 0, {})],
        {SPEC["ref"]: (kept, 0)})
    assert zaehler["n"] == 1, zaehler
