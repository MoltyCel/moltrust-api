"""Was eine Escrow-Zeile fortschreiben darf, und was nicht.

Am 16.10.2026 meldete f-no-stale-escrow zwei liegende Escrows, und daneben
standen drei abgerechnete weiter auf `escrowed`: die Runde-4-Aufgaben waren
am 9.10. bezahlt, nur ihr Alter hielt sie aus der Meldung heraus. Drei Tage
spaeter waeren sie als liegend gezaehlt worden. Hier steht, woran der
Abgleich eine abgerechnete Zeile erkennt — und
vor allem, wann er die Finger von einer Zeile laesst. Eine Buchung
fortzuschreiben, die noch offen ist, waere der schlimmere Fehler: danach
sucht niemand mehr.

Kein Netz, keine Datenbank. Die drei Quellen — Markt, Kette, pool_spend —
kommen als Werte herein, damit auch der Fall pruefbar ist, in dem sie
einander widersprechen.
"""
import ast
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "escrow_reconcile", ROOT / "scripts" / "escrow_reconcile.py")
er = importlib.util.module_from_spec(spec)
spec.loader.exec_module(er)

TX = "0x0629f425ee74c8e81173c4bef4a9b787b3fd3b3360fa1a30a30035dc7a067819"
SETTLE = "0x9e35d49678e4b38382d583350d174df9beecbdeb7419629ed3ad1c46f28b028c"
ZEILE = {"id": 95, "usdc": "0.541000", "mikro": 541000, "tx": TX,
         "alter_tage": 11.84, "zweck": "bounty-r4-stufe1-1"}


def task(status="completed", phase="resolved", awards=10, brutto=54100,
         settle=SETTLE):
    return {"referenceCode": "TSK-09GC1Y9D", "status": status, "phase": phase,
            "escrowTxHash": TX, "reward": "541000",
            "awards": [{"grossAmount": str(brutto), "settlementTxHash": settle}
                       for _ in range(awards)]}


def receipt(mikro=541000, status="0x1", n=11):
    """Ein Receipt, dessen USDC-Transfers in Summe genau `mikro` bewegen.

    Der Rest der Division liegt auf dem ersten Transfer — eine Aufteilung in
    n gleiche Teile waere um den Rest zu klein, und dann prueft die
    Gegenprobe den Rundungsfehler der Vorrichtung statt den Befund.
    """
    teil, rest = divmod(mikro, n)
    werte = [teil + rest] + [teil] * (n - 1)
    return {"status": status,
            "logs": [{"address": er.USDC.upper(), "topics": [er.XFER],
                      "data": hex(w)} for w in werte]}


# --- Betrag ------------------------------------------------------------------

def test_mikro_rechnet_ohne_fliesskomma():
    # float("0.541") * 1e6 ist 540999.9999999999 und damit nach int() eine
    # Zeile, die um ein Mikro nicht abgerechnet ist.
    assert er.mikro("0.541000") == 541000
    assert er.mikro("5.410000") == 5410000
    assert er.mikro("3") == 3000000
    assert er.mikro("0.000001") == 1


# --- Zuordnung ---------------------------------------------------------------

def test_kandidaten_lassen_adressen_und_die_nullstelle_liegen():
    r = {"logs": [
        {"topics": [er.XFER, "0x" + "0" * 24 + "ab" * 8]},        # Adresse
        {"topics": ["0xdead", er.ZERO32]},                        # Nullstelle
        {"topics": ["0xbeef", "0x" + "7" * 64]},                  # Kandidat
        {"topics": ["0xbeef", "0x" + "7" * 64]},                  # derselbe
        {"topics": ["0xbeef"]},                                   # ohne topic1
    ]}
    assert er.task_id_kandidaten(r) == ["0x" + "7" * 64]


def test_eine_aufgabe_gilt_nur_wenn_sie_dieselbe_escrow_tx_nennt():
    kandidat = "0x" + "7" * 64
    gefragt = []

    def hole_task(t):
        gefragt.append(t)
        return {"escrowTxHash": TX} if t == kandidat else {"escrowTxHash": "0xff"}

    d = er.aufgabe_zu(
        TX, hole_receipt=lambda h: {"logs": [{"topics": ["0xa", "0x" + "1" * 64]},
                                             {"topics": ["0xa", kandidat]}]},
        hole_task=hole_task)
    assert d == {"escrowTxHash": TX}
    assert gefragt == ["0x" + "1" * 64, kandidat]


def test_ohne_passende_aufgabe_kein_befund_und_kein_zustand():
    neu, text = er.befund(ZEILE, None, {})
    assert neu is None
    assert "keine Aufgabe" in text


# --- Abgerechnet -------------------------------------------------------------

def test_voll_abgerechnet_und_auf_der_kette_gedeckt_wird_spent():
    neu, text = er.befund(ZEILE, task(), {SETTLE: receipt()})
    assert neu == "spent"
    assert "abgerechnet" in text


def test_awards_die_den_escrow_nicht_treffen_werden_nicht_geschrieben():
    # Neun von zehn Slots: 486900 statt 541000. Wie eine Praemie aufgeteilt
    # wird, ist keine Buchhaltungsfrage.
    neu, text = er.befund(ZEILE, task(awards=9), {SETTLE: receipt()})
    assert neu is None
    assert "541000" in text


def test_eine_gescheiterte_abrechnung_zahlt_nicht():
    neu, text = er.befund(ZEILE, task(), {SETTLE: receipt(status="0x0")})
    assert neu is None
    assert "nicht erfolgreich" in text


def test_ohne_receipt_wird_nichts_geschrieben():
    neu, text = er.befund(ZEILE, task(), {})
    assert neu is None
    assert "kein Receipt" in text


def test_eine_transaktion_die_zu_wenig_bewegt_zahlt_nicht():
    neu, text = er.befund(ZEILE, task(), {SETTLE: receipt(mikro=11)})
    assert neu is None
    assert "bewegt" in text


def test_eine_sammelabrechnung_mehrerer_aufgaben_zaehlt():
    # Eine Transaktion darf mehrere Aufgaben abrechnen; dann liegt die
    # bewegte Summe ueber dem einzelnen Escrow. Nach unten gibt es keinen
    # Spielraum, nach oben schon.
    neu, _ = er.befund(ZEILE, task(), {SETTLE: receipt(mikro=1623000, n=33)})
    assert neu == "spent"


def test_fremde_token_zaehlen_nicht_als_bewegtes_usdc():
    r = receipt()
    r["logs"] = [{"address": "0x" + "9" * 40, "topics": [er.XFER],
                  "data": hex(541000)}]
    neu, text = er.befund(ZEILE, task(), {SETTLE: r})
    assert neu is None
    assert "bewegt 0" in text


# --- Liegt wirklich ----------------------------------------------------------

def test_ohne_award_bleibt_die_zeile_stehen_und_wird_benannt():
    neu, text = er.befund(ZEILE, task(status="open", phase="awaiting_settlement",
                                      awards=0), {})
    assert neu is None
    assert "liegt wirklich" in text
    assert "11.8" in text


def test_abgebrochen_ohne_award_ist_erstattet():
    neu, text = er.befund(ZEILE, task(status="cancelled", awards=0), {})
    assert neu == "refunded"
    assert "zurueck" in text


def test_abgebrochen_mit_award_wird_nicht_als_erstattet_verbucht():
    # Ein Abbruch nach einer Teilauszahlung ist beides und keines von beidem.
    neu, _ = er.befund(ZEILE, task(status="cancelled", awards=3), {SETTLE: receipt()})
    assert neu != "refunded"


# --- Was geschrieben werden darf --------------------------------------------

def test_nur_spent_und_refunded_sind_schreibbar():
    assert er.SCHREIBBAR == ("spent", "refunded")
    assert "escrowed" not in er.SCHREIBBAR


def test_das_update_fasst_nur_den_zustand_einer_escrow_zeile_an():
    sql = er.UPDATE_SQL
    assert sql.count("SET") == 1
    assert "state = :'neu'" in sql
    assert "id = :zid" in sql
    assert "state = 'escrowed'" in sql
    for verboten in ("usdc", "tx_hash", "purpose", "spent_at", "DELETE", "INSERT"):
        assert verboten not in sql


def test_die_anweisung_geht_ueber_stdin_und_nicht_ueber_c():
    """psql setzt seine Variablen in einem `-c`-Text nicht ein.

    Mit `-c` schickt psql die Zeile wortwoertlich an den Server und bekommt
    `syntax error at or near ":"` — die Buchung stand nie, und ein Test, der
    nur den Text der Anweisung liest, haette das nie bemerkt. Geprueft wird
    deshalb der Aufruf: die Anweisung kommt ueber stdin herein, und keines
    der Argumente traegt sie.
    """
    gesehen = {}

    class Fertig:
        returncode, stdout, stderr = 0, "UPDATE 1", ""

    def falsches_run(cmd, **kw):
        gesehen["cmd"], gesehen["kw"] = cmd, kw
        return Fertig()

    echtes_run = er.subprocess.run
    er.subprocess.run = falsches_run
    try:
        er.fortschreiben(95, "spent")
    finally:
        er.subprocess.run = echtes_run

    cmd = gesehen["cmd"]
    assert "-c" not in cmd
    assert cmd[-2:] == ["-f", "-"]
    assert gesehen["kw"]["input"] == er.UPDATE_SQL
    assert not any("UPDATE" in teil for teil in cmd)
    assert "-v" in cmd and "neu=spent" in cmd and "zid=95" in cmd
    # Ein stiller Fehlschlag waere hier das Gegenteil dessen, was die Buchung
    # braucht: ON_ERROR_STOP macht aus der Fehlermeldung einen Exitcode.
    assert "ON_ERROR_STOP=1" in cmd


def test_ein_fremder_zustand_wird_nicht_geschrieben():
    try:
        er.fortschreiben(95, "escrowed")
    except SystemExit as exc:
        assert "escrowed" in str(exc)
    else:
        raise AssertionError("fortschreiben hat 'escrowed' angenommen")


def test_nichts_in_dieser_datei_bewegt_geld():
    """An den Aufrufen, nicht an den Woertern.

    `taskmarket` kennt accept, accept-submissions, cancel, refund-expired,
    reject-submission und update — jeder davon kostet USDC oder verschiebt
    es. Hier darf nur `task get` vorkommen.
    """
    tree = ast.parse((ROOT / "scripts" / "escrow_reconcile.py").read_text())
    cli_aufrufe = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "cli":
            cli_aufrufe.append([a.value for a in node.args
                                if isinstance(a, ast.Constant)])
    assert cli_aufrufe == [["task", "get"]]
