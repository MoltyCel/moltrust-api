#!/usr/bin/env python3
"""Die acht Vorpruefungen H1 bis H8 — je zwei Gegenproben, je aus einem Fall.

Since ops G4-1 (27.10.2026) all eight are implemented and no test is marked
as expected to fail. The sixteen probes from the 11.10. stay word for word; further
probes below cover the negation rule (CLAUDE.md, 05.10.2026) and the
standing round, whose inputs come from scripts/dauerrunde.py.

Je Pruefung ein Fall, der rot sein muss, und einer, der durchgehen muss. Der
zweite ist der wichtigere: eine Pruefung, die nur an ihren Verletzungen
geprueft ist, ist geraten — dieselbe Regel wie bei einer Schwelle.

Die roten Faelle sind woertlich die des 11.10.2026, nicht erfundene.
"""
import datetime
import decimal
import os
import pathlib
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import dauerrunde as D  # noqa: E402
from scripts import runde_vorpruefung as V  # noqa: E402


# --- Die Textstuecke des 11.10., woertlich ---------------------------------

H1_ROT = ("After settlement nothing can be taken back: the finding is recorded "
          "in the result file, and that operator takes no place in later "
          "rounds.")
H1_GRUEN = (H1_ROT + "\nExcluded operators are listed in "
            "config/ausgeschlossene-betreiber.json, with the reason and a "
            "pointer to the result file that carries the finding.")

H2_ROT = ("This path grants no credits. You do not need any: every call in "
          "this task is free, and a registered DID gets 60 free calls an "
          "hour.")
H2_GRUEN = ("This path grants no credits, and you do not need any: none of "
            "the calls in this task is priced, so none of them touches a "
            "credit balance or the hourly credit allowance.")

H3_ROT = ("In round 4 we rejected on a criterion submitters could not see in "
          "advance and then could not tell them why.")
H3_GRUEN = ("In round 4 every unpaid submission failed a condition the task "
            "text had stated — ten paid places, filled by submission time, "
            "and one place per address across the round.")

H4_ROT = ("If a group turns out after the fact to have been one operator, its "
          "places go.")
H4_GRUEN = ("If we find before settlement that a group was one operator, its "
            "places go to the next group. After settlement nothing can be "
            "taken back: the finding is recorded in the result file.")

H5_ROT = ("The first three complete groups on this task, ranked by the time "
          "of the group's sixth endorsement.")
H5_GRUEN = (H5_ROT + "\n- all three submissions arrive inside the 72 hours; a "
            "group only counts once all three have submitted")

H6_ROT = '    "timestamp": "2026-10-10T12:00:00Z",'
H6_GRUEN = '    "timestamp": "<ISO-8601 UTC, the time of the interaction>",'


def stand(b):
    return b["stand"]


# --- H1 --------------------------------------------------------------------

def test_h1_zusage_ohne_ort_ist_rot():
    """11.10.: die Ausschlussliste existierte nicht, als der Text sie zusagte."""
    assert stand(V.h1_zusage_ohne_ort(H1_ROT)) == V.ROT


def test_h1_zusage_mit_ort_geht_durch():
    assert stand(V.h1_zusage_ohne_ort(H1_GRUEN)) == V.GRUEN


# --- H2 --------------------------------------------------------------------

def test_h2_unbelegte_zahl_ist_rot():
    """Die 60 galten nur fuer acht bepreiste Endpunkte, keiner im Text."""
    assert stand(V.h2_zahl_ohne_messung(H2_ROT)) == V.ROT


def test_h2_text_ohne_unbelegte_zahl_geht_durch():
    assert stand(V.h2_zahl_ohne_messung(H2_GRUEN)) == V.GRUEN


# --- H3 --------------------------------------------------------------------

def test_h3_falsche_selbstanklage_ist_rot():
    """Alle 42 trugen platz_vergeben, und der r4-Text nannte die Bedingung."""
    assert stand(V.h3_selbstaussage_ohne_beleg(
        H3_ROT, beschreibungen={"r4": "Submissions past the tenth are "
                                      "qualified and unpaid."})) == V.ROT


def test_h3_belegte_selbstaussage_geht_durch():
    assert stand(V.h3_selbstaussage_ohne_beleg(
        H3_GRUEN, beschreibungen={"r4": "The first ten submissions that "
                                        "satisfy every condition, by "
                                        "submission time. Submissions past "
                                        "the tenth are qualified and "
                                        "unpaid. One paid place per worker "
                                        "address across all three tasks "
                                        "together."})) == V.GRUEN


# --- H4 --------------------------------------------------------------------

def test_h4_folge_nach_der_zahlung_ist_rot():
    """Eine Zahlung auf Base kommt nicht zurueck."""
    assert stand(V.h4_folge_ohne_deckung(H4_ROT)) == V.ROT


def test_h4_getrennte_folge_geht_durch():
    assert stand(V.h4_folge_ohne_deckung(H4_GRUEN)) == V.GRUEN


# --- H5 --------------------------------------------------------------------

def test_h5_reihung_ohne_frist_ist_rot():
    assert stand(V.h5_frist_fehlt(H5_ROT)) == V.ROT


def test_h5_frist_und_reihung_gehen_durch():
    assert stand(V.h5_frist_fehlt(
        H5_GRUEN, felder={"duration": 72})) == V.GRUEN


# --- H6 --------------------------------------------------------------------

def test_h6_zeitstempel_in_der_vergangenheit_ist_rot():
    jetzt = datetime.datetime(2026, 10, 11, tzinfo=datetime.timezone.utc)
    assert stand(V.h6_beispiel_in_der_vergangenheit(H6_ROT,
                                                    jetzt=jetzt)) == V.ROT


def test_h6_platzhalter_geht_durch():
    jetzt = datetime.datetime(2026, 10, 11, tzinfo=datetime.timezone.utc)
    assert stand(V.h6_beispiel_in_der_vergangenheit(H6_GRUEN,
                                                    jetzt=jetzt)) == V.GRUEN


# --- H7 --------------------------------------------------------------------

def test_h7_klasse_ohne_bedingung_im_text_ist_rot():
    """Eine Absage, die der Einreicher nicht vorhersehen konnte."""
    assert stand(V.h7_absageklasse_ohne_bedingung(
        "Three agents, six endorsements, inside 24 hours.",
        klassen={"betreiber_ausgeschlossen": "Ein Betreiber der Gruppe steht "
                                             "auf der Ausschlussliste"})) \
        == V.ROT


def test_h7_jede_klasse_mit_entsprechung_geht_durch():
    assert stand(V.h7_absageklasse_ohne_bedingung(
        "fewer than six distinct endorsed pairs; two endorsements for the "
        "same pair count once",
        klassen={"zu_wenige_beitraege": "Weniger als sechs endorsierte "
                                        "Paare"})) == V.GRUEN


# --- H8 --------------------------------------------------------------------

def test_h8_zwei_herleitungen_sind_rot():
    """11.10.: result.json sagte bezahlt 0, die Telegram-Zeile 1 bezahlt."""
    quelle = (
        "def ergebnis(...):\n"
        "    gewinner = gruppen_auswertung(spec, kept_c)['bezahlt']\n"
        "def markdown(...):\n"
        "    winners = kept_c[:spec['slots']]\n")
    assert stand(V.h8_zwei_herleitungen(modul=quelle)) == V.ROT


def test_h8_eine_herleitung_geht_durch():
    quelle = (
        "def gruppen_alle(gathered, capped):\n"
        "    return {r: gruppen_auswertung(s, k) for ...}\n"
        "def ergebnis(..., gruppen_aus=None):\n"
        "    aus = gruppen_aus[ref]\n"
        "def markdown(...):\n"
        "    aus = gruppen_befund.get(ref)\n")
    assert stand(V.h8_zwei_herleitungen(modul=quelle)) == V.GRUEN




# --- Further probes: negations, missing inputs, edges ----------------------

def test_h1_verneinte_zusage_ist_keine():
    """CLAUDE.md 05.10.2026: a negation is not an affirmation."""
    assert stand(V.h1_zusage_ohne_ort("We will not keep a list.")) == V.GRUEN


def test_h1_zusage_ohne_jeden_ort_ist_rot():
    assert stand(V.h1_zusage_ohne_ort("We will record it.")) == V.ROT


def test_h1_genannter_ort_der_fehlt_ist_rot():
    t = "Excluded operators are listed in config/gibt-es-nicht.json."
    assert stand(V.h1_zusage_ohne_ort(t)) == V.ROT


def test_h1_ort_im_anderen_absatz_traegt_nicht():
    t = ("That operator takes no place in later rounds.\n\n"
         "See config/ausgeschlossene-betreiber.json.")
    assert stand(V.h1_zusage_ohne_ort(t)) == V.ROT


def test_h2_freie_aufrufe_mit_endpunkt_gehen_durch():
    t = "A registered DID gets 60 free calls an hour on /identity/verify/{did}."
    assert stand(V.h2_zahl_ohne_messung(t)) == V.GRUEN


def test_h2_falsche_zahl_freier_aufrufe_ist_rot():
    t = "A registered DID gets 100 free calls an hour on /identity/verify/{did}."
    assert stand(V.h2_zahl_ohne_messung(t)) == V.ROT


def test_h2_zahl_ohne_messung_ist_nicht_gruen():
    assert stand(V.h2_zahl_ohne_messung("Bring 42 agents.")) == V.OFFEN


def test_h2_abweichender_betrag_ist_rot():
    t = "At most 0.10 USDC per activation."
    assert stand(V.h2_zahl_ohne_messung(t, messen=D.messwerte)) == V.ROT


def test_h2_ausgeschriebene_zahl_wird_gemessen():
    gut = "An operator_did gets at most three paid activations per week."
    schlecht = "An operator_did gets at most four paid activations per week."
    assert stand(V.h2_zahl_ohne_messung(gut, messen=D.messwerte)) == V.GRUEN
    assert stand(V.h2_zahl_ohne_messung(schlecht, messen=D.messwerte)) == V.ROT


def test_h2_bezeichner_und_uhrzeiten_sind_keine_zahlen():
    t = ("Settled every Monday 06:00 UTC for W44, see ERC-8004 and x402, "
         "same /24, did:moltrust:0123456789abcdef, 0xA175...9Eb9.")
    assert stand(V.h2_zahl_ohne_messung(t)) == V.GRUEN


def test_h3_runde_ohne_beschreibung_ist_nicht_gruen():
    t = "In round 4 every unpaid submission failed a stated condition."
    assert stand(V.h3_selbstaussage_ohne_beleg(t)) == V.OFFEN


def test_h3_fruehere_runde_ohne_nummer_ist_nicht_gruen():
    t = "In the previous round we paid late."
    assert stand(V.h3_selbstaussage_ohne_beleg(t)) == V.OFFEN


def test_h3_text_ohne_runde_geht_durch():
    assert stand(V.h3_selbstaussage_ohne_beleg("Register a DID.")) == V.GRUEN


def test_h3_die_eigene_runde_wird_nicht_geprueft():
    t = "Round 5 pays per group."
    assert stand(V.h3_selbstaussage_ohne_beleg(t, runde="r5")) == V.GRUEN


def test_h4_verneinte_ruecknahme_geht_durch():
    t = "After settlement nothing can be taken back."
    assert stand(V.h4_folge_ohne_deckung(t)) == V.GRUEN


def test_h4_rueckforderung_ist_rot():
    t = "Payouts are reclaimed if fraud shows up later."
    assert stand(V.h4_folge_ohne_deckung(t)) == V.ROT


def test_h5_ohne_fristfeld_ist_rot():
    assert stand(V.h5_frist_fehlt(H5_GRUEN, felder={"reward": 1})) == V.ROT


def test_h5_ohne_felder_ist_nicht_gruen():
    assert stand(V.h5_frist_fehlt(H5_GRUEN)) == V.OFFEN


def test_h5_feld_und_text_widersprechen_sich():
    assert stand(V.h5_frist_fehlt(H5_GRUEN, felder={"duration": 48})) == V.ROT


def test_h5_text_ohne_reihung_braucht_keine_frist():
    assert stand(V.h5_frist_fehlt("Register a DID and call /agents/me.")) \
        == V.GRUEN


def test_h6_zukuenftiger_zeitstempel_geht_durch():
    jetzt = datetime.datetime(2026, 10, 11, tzinfo=datetime.timezone.utc)
    t = '"timestamp": "2026-10-12T12:00:00Z", "day": "2026-10-11"'
    assert stand(V.h6_beispiel_in_der_vergangenheit(t, jetzt=jetzt)) == V.GRUEN


def test_h6_ungueltiges_datum_ist_rot():
    jetzt = datetime.datetime(2026, 10, 11, tzinfo=datetime.timezone.utc)
    assert stand(V.h6_beispiel_in_der_vergangenheit(
        '"day": "2026-13-40"', jetzt=jetzt)) == V.ROT


def test_h7_ohne_klassen_ist_nicht_gruen():
    assert stand(V.h7_absageklasse_ohne_bedingung("any text")) == V.OFFEN


def test_h7_klasse_ohne_hinterlegte_bedingung_ist_nicht_gruen():
    assert stand(V.h7_absageklasse_ohne_bedingung(
        "any text", klassen={"neu": "eine neue Klasse"})) == V.OFFEN


def test_h8_ohne_modul_ist_nicht_gruen():
    assert stand(V.h8_zwei_herleitungen()) == V.OFFEN


def test_h8_das_rechenmodul_der_dauerrunde_hat_eine_herleitung():
    assert stand(V.h8_zwei_herleitungen(modul=pathlib.Path(D.__file__))) \
        == V.GRUEN


# --- The standing round: a text after its rule passes all eight -----------

DAUER = """\
The standing round pays for activations of referred agents. It does not end; each week is settled on its own.

An activation is the first authenticated call of a referred agent to an endpoint that no task text prescribes, at most 14 days after its registration. A self-referral or a mutual pair does not count, and neither does an unknown or revoked DID.

Referrer and referred agent must each have a wallet address, a key and an operator_did, and they must differ in all three: two agents that share an address, a key or an operator_did do not qualify. Agents on the same /24 network are noted in the result, not refused. An operator listed in config/ausgeschlossene-betreiber.json, the exclusion list, takes part on neither side.

The weekly budget is 5 USDC. It is split equally over the paid activations of the week, at most 0.05 USDC per activation: 0.025 USDC to the referred agent and 0.025 USDC to the referrer. An operator_did gets at most three paid activations per week, in order of activation time; later ones are listed as qualified and unpaid. What is not paid carries over to the next week, recorded in https://moltrust.ch/rounds/dauer/<KW>/result.json.

Each week is settled every Monday 06:00 UTC for the calendar week before it. The result is published at https://moltrust.ch/rounds/dauer/<KW>/result.json before anything is paid.
"""


def test_ein_text_nach_der_regel_der_dauerrunde_besteht_alle_acht():
    befunde = V.pruefe(DAUER, runde="dauer")
    assert {b["id"]: b["stand"] for b in befunde} == {
        f"H{i}": V.GRUEN for i in range(1, 9)}, befunde


def test_eine_geaenderte_konstante_macht_den_text_rot(monkeypatch):
    """The text and the payout read the same constants (G4-4 criterion 2)."""
    monkeypatch.setattr(D, "WOCHENBUDGET_USDC", decimal.Decimal("6"))
    h2 = {b["id"]: b for b in V.pruefe(DAUER, runde="dauer")}["H2"]
    assert h2["stand"] == V.ROT


def test_main_dauer_gruen_endet_mit_null(tmp_path):
    p = tmp_path / "dauer.txt"
    p.write_text(DAUER, encoding="utf-8")
    assert V.main([str(p), "--runde", "dauer"]) == 0


def test_main_dauer_rot_endet_mit_eins(tmp_path):
    p = tmp_path / "dauer.txt"
    p.write_text(DAUER.replace("0.05 USDC", "0.10 USDC"), encoding="utf-8")
    assert V.main([str(p), "--runde", "dauer"]) == 1


# --- The module itself ----------------------------------------------------

def test_alle_acht_sind_angelegt():
    assert [k for k, _fn in V.PRUEFUNGEN] == [f"H{i}" for i in range(1, 9)]


def test_jede_traegt_ihre_herkunft_im_quelltext():
    """Eine Pruefung ohne Herkunft ist beim naechsten Mal nicht nachpruefbar."""
    for kennung, fn in V.PRUEFUNGEN:
        doc = fn.__doc__ or ""
        assert "Herkunft" in doc, kennung
        assert "11.10.2026" in doc, kennung


def test_keine_pruefung_ist_mehr_zugesagt_statt_umgesetzt():
    """G4-1 criterion 2: nothing raises NichtUmgesetzt any more."""
    quelle = pathlib.Path(V.__file__).read_text(encoding="utf-8")
    assert "NichtUmgesetzt" not in quelle
    assert "NotImplementedError" not in quelle
    for b in V.pruefe("irgendein Text"):
        assert b["stand"] in (V.ROT, V.GRUEN, V.OFFEN)


def test_eine_fehlende_eingabe_ist_nicht_gruen():
    """Der Rueckfallwert zeigt nicht in die erlaubende Richtung."""
    befunde = {b["id"]: b["stand"] for b in V.pruefe("Form a trust triangle")}
    assert befunde["H7"] == V.OFFEN
    assert befunde["H8"] == V.OFFEN


def test_main_legt_nichts_an_und_endet_ungleich_null(tmp_path):
    """Ohne die Eingaben einer Runde ist Exitcode 0 die Zustimmung, die es
    nicht gibt."""
    p = tmp_path / "text.txt"
    p.write_text("Form a trust triangle", encoding="utf-8")
    assert V.main([str(p)]) == 1


def test_eine_fehlende_datei_ist_kein_gruen(tmp_path):
    assert V.main([str(tmp_path / "gibtsnicht.txt")]) == 2


def test_das_modul_handelt_beim_import_nicht():
    """Eine Probe fuehrt keine Modulruempfe aus — sie liest sie."""
    import ast

    baum = ast.parse(pathlib.Path(V.__file__).read_text(encoding="utf-8"))
    erlaubt = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef,
               ast.Assign, ast.AnnAssign, ast.Expr, ast.If)
    assert [type(k).__name__ for k in baum.body
            if not isinstance(k, erlaubt)] == []
    assert [k for k in baum.body
            if isinstance(k, ast.Expr) and isinstance(k.value, ast.Call)] == []


def test_es_haengt_nicht_im_stuendlichen_lauf():
    """The check runs before a text is created, not as an invariant."""
    wurzel = pathlib.Path(V.__file__).resolve().parent.parent
    inv = wurzel / "docs" / "invariants"
    h = sorted(p.name for p in inv.glob("h-*.yaml")) if inv.is_dir() else []
    assert h == [], f"H-Invarianten eingehaengt: {h}"
