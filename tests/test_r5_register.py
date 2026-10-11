#!/usr/bin/env python3
"""Das Register und die Einmallaeufe der Runde 5.

Die Zahlen muessen zusammenpassen, und zwar mit dem, was wirklich angelegt
wurde: zwei Aufgaben, je 0,541 brutto, zusammen die Praemie aus den
Bedingungen. Eine Abweichung hier heisst, dass die veroeffentlichte Datei
etwas anderes zusagt als der Markt traegt.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import gruppen as G  # noqa: E402
from scripts.runden_auswertung import bedingungen  # noqa: E402
from scripts.task_watch import ELIGIBLE, LAEUFE, TASKS  # noqa: E402

R5 = [t for t in TASKS if t.get("round") == "r5"]


def test_zwei_aufgaben_im_register():
    assert len(R5) == 2
    assert {t["ref"] for t in R5} == {"GRUPPE-R5-1", "GRUPPE-R5-2"}


def test_die_praemie_im_register_ist_die_der_bedingungen():
    summe = sum(int(round(t["gross"] * 1_000_000)) for t in R5)
    assert summe == bedingungen("r5")["praemie_brutto_mikro"] == 1_082_000


def test_neun_plaetze_je_aufgabe_und_das_profil():
    for t in R5:
        assert t["profile"] == "gruppe"
        assert t["slots"] == G.GRUPPEN_JE_AUFGABE * G.MITGLIEDER == 9
        assert t["cap"] == 1
        assert t["id"].startswith("0x") and len(t["id"]) == 66


def test_das_profil_hat_eine_annahmeabfrage():
    """Ohne Eintrag in ELIGIBLE bricht `gather` mit KeyError ab — und zwar
    erst im Auswertungslauf, vier Minuten nach dem Ablauf."""
    assert "gruppe" in ELIGIBLE
    assert callable(ELIGIBLE["gruppe"])


def test_die_zwei_einmallaeufe_stehen_mit_zeiten():
    spez = LAEUFE["r5"]
    modi = [m for _n, _z, m in spez["jobs"]]
    assert modi == ["vorwarnung", "auswertung"]
    assert len(spez["jobs"]) == 2


def test_die_vorwarnung_liegt_24_stunden_vor_dem_ablauf():
    spez = LAEUFE["r5"]
    ablauf = datetime.fromisoformat(spez["deadline"])
    vor = datetime.fromisoformat(
        next(z for _n, z, m in spez["jobs"] if m == "vorwarnung"))
    stunden = (ablauf - vor).total_seconds() / 3600
    assert 23.9 <= stunden <= 24.1, stunden


def test_die_auswertung_liegt_nach_dem_ablauf():
    """Vorher waere sie sinnlos: der Vertrag nimmt bis zur Sekunde an."""
    spez = LAEUFE["r5"]
    ablauf = datetime.fromisoformat(spez["deadline"])
    nach = datetime.fromisoformat(
        next(z for _n, z, m in spez["jobs"] if m == "auswertung"))
    minuten = (nach - ablauf).total_seconds() / 60
    assert 3.5 <= minuten <= 5.0, minuten


def test_die_wache_prueft_runde_fuenf_mit():
    """`check_alle_runways` muss r5 kennen, sonst prueft sie nur r4 —
    und r4 schweigt, weil seine Frist vorbei ist."""
    import scripts.task_watch as w

    assert "r5" in LAEUFE
    assert "r5" in sorted(LAEUFE)
    # Die Pruefung darf fuer eine unbekannte Runde nicht still sein.
    assert w.check_runway("r9") == ["Keine Einmallaeufe fuer r9 in LAEUFE"]


def test_die_gepruefen_dateien_liegen_im_repo():
    """Der Runde-4-Kommentar nennt es als Schwachstelle: der Laufmantel lag
    in ~/bin, also ausserhalb jeder Durchsicht und jeder Sperre."""
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    for f in LAEUFE["r5"]["files"]:
        assert f.startswith("/home/moltstack/moltstack/"), f
        rel = f[len("/home/moltstack/moltstack/"):]
        assert os.path.isfile(os.path.join(repo, rel)), rel


def test_der_laufmantel_schreibt_den_nachweis_selbst():
    """Geschrieben, nicht nachgetragen: die Zeile entsteht im Lauf."""
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(repo, "ops/runde5/r5-run.sh"), encoding="utf-8") as fh:
        s = fh.read()
    assert "r5-runs.jsonl" in s
    assert "printf '{\"ts\":\"%s\",\"modus\":\"%s\",\"rc\":%s}\\n'" in s
    assert LAEUFE["r5"]["runs"] in s
    # Und er meldet sein eigenes Scheitern.
    assert "notify.ALERTS" in s and "gescheitert" in s


def test_der_auswertungslauf_setzt_nicht_auf_ausgezahlt():
    """Nichts ist bezahlt, wenn er laeuft, und `stand` kennt keinen Rueckweg.

    Einmal `ausgezahlt` geschrieben, laesst `schreibe_result` nicht zurueck —
    die Datei wuerde eine Zahlung behaupten, die nicht stattgefunden hat, und
    sich dabei festlegen.
    """
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(repo, "ops/runde5/r5-run.sh"), encoding="utf-8") as fh:
        s = fh.read()
    assert "--stand offen" in s
    assert "--stand ausgezahlt" not in s


def test_der_einplaner_handelt_beim_import_nicht():
    """Sonst reiht ein Import at-Jobs ein.

    `scripts/module_effects.py --check` hat genau das gemeldet: zwei neue
    subprocess.run auf Modulebene, weil der ganze Rumpf oben stand. Gelesen
    wird hier (AST) und nicht ausgefuehrt — eine Probe fuehrt keine
    Modulruempfe aus.
    """
    import ast

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    quelle = open(os.path.join(repo, "ops/runde5/plane_r5.py"),
                  encoding="utf-8").read()
    baum = ast.parse(quelle)
    erlaubt = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef,
               ast.Assign, ast.AnnAssign, ast.Expr, ast.If)
    fremd = [type(k).__name__ for k in baum.body if not isinstance(k, erlaubt)]
    assert fremd == [], fremd
    aufrufe = [k for k in baum.body
               if isinstance(k, ast.Expr) and isinstance(k.value, ast.Call)]
    assert aufrufe == [], "Aufruf auf Modulebene"
    # Genau ein Schutz, und er ruft main().
    schutz = [k for k in baum.body if isinstance(k, ast.If)]
    assert len(schutz) == 1
    # ast.unparse normalisiert auf einfache Anfuehrungszeichen —
    # geprueft wird die Aussage, nicht die Schreibweise.
    assert ast.unparse(schutz[0].test).replace('"', "'") == (
        "__name__ == '__main__'")


def test_die_meldung_nennt_die_runde_und_nicht_eine_feste():
    """Ein r5-Lauf meldete nach Telegram, Runde 4 sei ausgewertet."""
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    quelle = open(os.path.join(repo, "scripts/runden_auswertung.py"),
                  encoding="utf-8").read()
    in_meldung = [z for z in quelle.splitlines()
                  if "MolTrust - Runde" in z and "ausgewertet" in z]
    assert len(in_meldung) == 1, in_meldung
    assert "{runde}" in in_meldung[0], in_meldung[0]
    assert "Runde 4" not in in_meldung[0]


def test_der_fristlauf_zielt_nicht_in_den_geschuetzten_checkout():
    """Ein Fristlauf, der auf fremdes Schreibrecht angewiesen ist, haengt an
    dessen Aenderung — und die kam am 11.10. ohne Ankuendigung.

    Gemessen: ~/moltrust-web/rounds/r5/result.json steht auf 444, ein
    Schreibversuch endet mit PermissionError. Der Lauf schreibt deshalb in
    ein Staging-Verzeichnis; veroeffentlicht wird in einem eigenen Schritt.
    """
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(repo, "ops/runde5/r5-run.sh"), encoding="utf-8") as fh:
        s = fh.read()
    assert "--result-nach" in s
    assert "$STAGE/rounds/r5/result.json" in s
    # Geschrieben wird nirgends in den Web-Checkout.
    schreibend = [z for z in s.splitlines()
                  if "moltrust-web" in z and ">" in z]
    assert schreibend == [], schreibend
    # Gelesen schon — die gueltige Fassung ist der Ausgangspunkt fuer die
    # Versionszaehlung.
    assert "cp /home/moltstack/moltrust-web/rounds/r5/result.json" in s


def test_das_veroeffentlichen_ist_ein_eigener_schritt():
    """Der Web-Deploy allein genuegt nicht: er hat fuer moltrust-web eine
    feste Dateiliste, und `rounds/` steht nicht darin. Am 11.10. gemessen —
    Deploy gruen, Web-Root alt."""
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    with open(os.path.join(repo, "ops/runde5/r5-run.sh"), encoding="utf-8") as fh:
        s = fh.read()
    assert "moltstack-webinstall" in s, \
        "der Weg zum Web-Root muss im Mantel stehen, sonst sucht ihn jemand"
