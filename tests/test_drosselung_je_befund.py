"""Fingerabdruck je Befund, und Laeufe statt Minuten.

Zwei Defekte, beide am 09.10.2026 im Sendeprotokoll nachweisbar, beide an
derselben Meldung — a-track-record-burst, die an diesem Tag faelschlich
UEBERFAELLIG war und sich sechs Stunden lang nicht veraenderte.

Erstens: der Fingerabdruck lag auf dem Buendel. Eine Meldung trug alle
Sofortbefunde eines Laufs, mit Zeilenumbruechen verbunden. Um 04:00 lagen drei
Befunde darin statt einem, der Text war neu, und der alte Befund fuhr mit — 23
Minuten nach seiner vorigen Meldung.

Zweitens: REPEAT_EVERY war eine Stunde und die Wache lief stuendlich. Damit
entschied `now - zuletzt >= REPEAT_EVERY` auf Sekunden:

    03:37:13 gesendet
    04:37:06 gedrosselt   (59:53)
    05:37:07 gesendet
    06:37:12 gesendet     (60:05)
    07:37:06 gedrosselt   (59:54)

Drei von fuenf gingen raus, und welche drei, entschied der Jitter von cron.
"""
import datetime as dt

import pytest

from app import notify


@pytest.fixture
def speicher(tmp_path, monkeypatch):
    """Ein eigener Fingerabdruck-Speicher je Test."""
    p = tmp_path / "fp.json"
    monkeypatch.setattr(notify, "FINGERPRINTS", str(p))
    return str(p)


# -- B2: Laeufe statt Minuten ------------------------------------------------

def test_die_59_53_luecke_verschluckt_nichts_mehr(speicher):
    """Der Fall vom 09.10., Zeile fuer Zeile nachgestellt.

    Mit der alten Regel waren es drei Meldungen aus fuenf Laeufen. Mit dem
    Laufzaehler ist es eine: die erste. Die Uhr wird nicht mehr befragt,
    deshalb ist der Abstand ohne Bedeutung.
    """
    zeiten = ["2026-10-09T03:37:13", "2026-10-09T04:37:06",
              "2026-10-09T05:37:07", "2026-10-09T06:37:12",
              "2026-10-09T07:37:06"]
    text = "[FAIL] a-track-record-burst — 1 (erwartet eq 0)"
    gesendet = []
    for z in zeiten:
        now = dt.datetime.fromisoformat(z).replace(tzinfo=dt.timezone.utc)
        senden, _fp, _zu = notify.throttle(text, now=now, path=speicher)
        if senden:
            gesendet.append(z)

    assert gesendet == ["2026-10-09T03:37:13"], gesendet


def test_nach_24_laeufen_wieder(speicher):
    """Stumm ist nicht vergessen: nach n Laeufen meldet derselbe Befund wieder,
    mit der Zahl der Laeufe im Text."""
    text = "immer dasselbe"
    start = dt.datetime(2026, 10, 9, 0, 37, tzinfo=dt.timezone.utc)
    gesendet = []
    zusaetze = []
    # 25 Laeufe: der erste meldet, dann 23 stumm, der 25. wieder.
    for i in range(25):
        now = start + dt.timedelta(hours=i)
        senden, _fp, zusatz = notify.throttle(text, now=now, path=speicher)
        if senden:
            gesendet.append(i)
            zusaetze.append(zusatz)

    assert gesendet == [0, 24], gesendet
    assert "24 Laeufe" in zusaetze[1], zusaetze[1]


def test_der_zaehler_haengt_nicht_an_der_uhr(speicher):
    """Dieselben 25 Laeufe in zwei Minuten statt in einem Tag — dasselbe
    Ergebnis. Das ist der Unterschied zur alten Fassung."""
    text = "immer dasselbe"
    start = dt.datetime(2026, 10, 9, 0, 0, tzinfo=dt.timezone.utc)
    gesendet = [i for i in range(25)
                if notify.throttle(text, now=start + dt.timedelta(seconds=5 * i),
                                   path=speicher)[0]]
    assert gesendet == [0, 24], gesendet


def test_nach_24_stunden_ohne_auftreten_meldet_es_sofort(speicher):
    """FORGET_AFTER bleibt zeitbasiert, und das ist richtig: ein Befund, der
    einen Tag weg war, ist beim Wiederkommen keine 25. Wiederholung."""
    text = "kommt und geht"
    t0 = dt.datetime(2026, 10, 9, 0, 0, tzinfo=dt.timezone.utc)
    assert notify.throttle(text, now=t0, path=speicher)[0] is True
    assert notify.throttle(text, now=t0 + dt.timedelta(hours=1),
                           path=speicher)[0] is False
    spaet = t0 + dt.timedelta(hours=26)
    assert notify.throttle(text, now=spaet, path=speicher)[0] is True


# -- B1: je Befund, nicht je Buendel -----------------------------------------

@pytest.fixture
def sendeversuche(monkeypatch, tmp_path):
    """Faengt die zusammengesetzte Nachricht ab, ohne zu senden."""
    gesendet = []

    class _R:
        status_code = 200
        content = b""

        @staticmethod
        def json():
            return {"result": {"message_id": 1}}

    def _post(url, **kw):
        gesendet.append(kw["data"]["text"])
        return _R()

    monkeypatch.setattr(notify.requests, "post", _post)
    monkeypatch.setenv("MOLTRUST_NOTIFY", "on")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:" + "a" * 32)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
    for k in notify.CHANNELS:
        monkeypatch.delenv(f"TELEGRAM_CHAT_ID_{k.upper()}", raising=False)
    monkeypatch.setattr(notify, "SENT_LOG", str(tmp_path / "sent.jsonl"))
    notify._ENV_CACHE.clear()
    return gesendet


def test_ein_alter_befund_fuehrt_nicht_mit(speicher, sendeversuche):
    """Der Fall von 04:00: drei Befunde im Buendel, einer davon alt.

    Mit dem Buendel-Fingerabdruck war der Text neu und der alte Befund fuhr
    mit. Jetzt traegt die Nachricht nur die zwei neuen.
    """
    alt = ("a-track-record-burst", "[FAIL] a-track-record-burst — 1")
    # Der alte Befund wurde gerade gemeldet.
    notify.throttle(alt[1], path=speicher)
    sendeversuche.clear()

    ergebnis = notify.send_befunde(
        [alt,
         ("e-page-matches-source", "[FAIL] e-page-matches-source — 2"),
         ("f-no-stale-escrow", "[FAIL] f-no-stale-escrow — 1")],
        channel=notify.ALERTS, kopf="MolTrust Selftest —")

    assert ergebnis["gedrosselt"] == ["a-track-record-burst"]
    assert sorted(ergebnis["gesendet"]) == ["e-page-matches-source",
                                            "f-no-stale-escrow"]
    assert len(sendeversuche) == 1
    nachricht = sendeversuche[0]
    assert "a-track-record-burst" not in nachricht, nachricht
    assert "e-page-matches-source" in nachricht
    assert "f-no-stale-escrow" in nachricht


def test_sind_alle_gedrosselt_geht_nichts_raus(speicher, sendeversuche):
    befunde = [("a", "Befund A"), ("b", "Befund B")]
    notify.send_befunde(befunde, channel=notify.ALERTS)
    sendeversuche.clear()

    ergebnis = notify.send_befunde(befunde, channel=notify.ALERTS)
    assert ergebnis["gesendet"] == []
    assert ergebnis["erfolg"] is None, "kein Fehlschlag, sondern ein ruhiger Lauf"
    assert sendeversuche == []


def test_kopf_und_fuss_erneuern_keinen_fingerabdruck(speicher, sendeversuche):
    """Der Berichtspfad wechselt taeglich. Im Befundtext haette er jeden
    Fingerabdruck jeden Tag erneuert; im Fuss faellt er nicht ins Gewicht."""
    befunde = [("a", "Befund A")]
    notify.send_befunde(befunde, channel=notify.ALERTS,
                        fuss="Bericht: /pfad/2026-10-09.json")
    sendeversuche.clear()
    ergebnis = notify.send_befunde(befunde, channel=notify.ALERTS,
                                   fuss="Bericht: /pfad/2026-10-10.json")
    assert ergebnis["gedrosselt"] == ["a"]
    assert sendeversuche == []


def test_die_drosselung_bleibt_an_der_sendestelle(speicher, sendeversuche):
    """Kein Weg daran vorbei: wer send_telegram direkt ruft, wird weiter
    gedrosselt. send_befunde verschiebt die Einheit, nicht den Ort."""
    assert notify.send_telegram("einzeln", channel=notify.ALERTS) is True
    assert notify.send_telegram("einzeln", channel=notify.ALERTS) is False
    assert notify.letzter_grund() == "gedrosselt"


def test_schon_gedrosselt_ist_kein_oeffentlicher_schalter():
    """Der Parameter ist intern und heisst nicht `throttle=False`.

    Eine Ausnahme von der Drosselung war am 08.10. der Grund fuer fuenfzehn
    gleichlautende Fehlalarme. `schon_gedrosselt` sagt nicht "nicht drosseln",
    sondern "ist schon gefallen" — und steht nur an _deliver_telegram, nicht an
    send_telegram.
    """
    import inspect
    sig = inspect.signature(notify.send_telegram)
    assert "schon_gedrosselt" not in sig.parameters
    assert "throttle" not in sig.parameters
    assert "schon_gedrosselt" in inspect.signature(
        notify._deliver_telegram).parameters


def test_selftest_uebergibt_paare():
    """Jede Fundstelle in selftest.py legt (kennung, text) ab.

    Zwei Stellen hatten das beim ersten Durchgang nicht, und ein String der
    Laenge 2 waere beim Entpacken stillschweigend durchgegangen.
    """
    import ast
    import pathlib

    quelle = (pathlib.Path(__file__).resolve().parent.parent
              / "scripts" / "selftest.py").read_text(encoding="utf-8")
    schlecht = []
    for k in ast.walk(ast.parse(quelle)):
        if (isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute)
                and k.func.attr == "append"
                and isinstance(k.func.value, ast.Name)
                and k.func.value.id == "sofort"):
            arg = k.args[0] if k.args else None
            if not (isinstance(arg, ast.Tuple) and len(arg.elts) == 2):
                schlecht.append(k.lineno)
    assert schlecht == [], schlecht


def test_kein_pfad_als_standardargument():
    """Ein Pfad gehoert nicht in die Signatur.

    `def f(path=FINGERPRINTS)` nimmt den Wert, den die Konstante beim Import
    hatte; wer sie danach umsetzt, aendert nichts. Am 08.10.2026 hat genau das
    drei Tests der R4-Wache gruen gehalten, die nichts geprueft haben —
    `_r4_runs(path=R4_RUNS)`, der Test setzte die Konstante um, die Funktion
    las weiter die echte Datei. Am 09.10. fiel es in send_befunde wieder auf,
    diesmal als rotes Ergebnis.

    Mit dem AST geprueft, weil es um die Signatur geht und nicht um einen Text.
    """
    import ast
    import inspect
    import pathlib as _p

    quelle = _p.Path(inspect.getsourcefile(notify)).read_text(encoding="utf-8")
    verdaechtig = {"FINGERPRINTS", "SENT_LOG", "_STATE_DIR"}
    treffer = []
    for k in ast.walk(ast.parse(quelle)):
        if not isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        standards = list(k.args.defaults) + [d for d in k.args.kw_defaults if d]
        for d in standards:
            for u in ast.walk(d):
                if isinstance(u, ast.Name) and u.id in verdaechtig:
                    treffer.append(f"{k.name}:{k.lineno} ({u.id})")
    assert treffer == [], ("Pfad als Standardargument, friert den Wert beim "
                          "Definieren ein: " + ", ".join(treffer))
