"""Die Wache ueber den Rueckstand des USDC-Pollers.

Am 09.10.2026 stand der Poller sechzehn Stunden. Gemeldet hat nicht der
Rueckstand, sondern der gescheiterte Aufruf — vierzig Minuten nach dem
Stillstand, und nur weil er scheiterte. Die Schwelle lag bei 43200 Bloecken,
etwa einem Tag; sechzehn Stunden haben sie nie erreicht.

Schlimmer als die Schwelle war die Beobachtung danach: als der Zweig beim
ersten Probelauf um 07:28:45Z endlich ansprang, wurde die Meldung unterdrueckt
— der Lauf von Hand hatte die Secrets nicht geladen, und das Gate hat richtig
zugemacht. Die Rueckstandswarnung ist also nie zugestellt worden. Ein Zweig,
der einmal lief und dabei nichts hinterliess, ist nicht belegt. Darum diese
Tests.
"""
import json
import sys
import types

import pytest


@pytest.fixture
def pp(monkeypatch):
    """Der Poller mit einer Kette, die nur das sagt, was der Test braucht."""
    monkeypatch.setenv("BASE_RPC", "https://rpc.ankr.com/base/testschluessel")
    import monitor.poll_payments as modul
    return modul


def _kette(block_number, *, bricht=False):
    """Ein w3-Ersatz. `bricht` laest schon die Blocknummer scheitern."""
    class _Eth:
        @property
        def block_number(self):
            if bricht:
                raise RuntimeError("429 Too Many Requests")
            return block_number
    w3 = types.SimpleNamespace(eth=_Eth())
    return w3


def _lauf(pp, monkeypatch, abstand, *, zustand=None, deckel=None):
    """Einen Lauf mit `abstand` Bloecken Rueckstand fahren. Gibt die Meldungen.

    `zustand` wird ueber Laeufe hinweg mitgefuehrt — die Meldung entscheidet
    sich seit dem 07.11.2026 am Vergleich mit dem Lauf davor, und ein Test, der
    jeden Lauf mit frischem Zustand faehrt, kann das nicht pruefen.
    `deckel` setzt `MAX_CHUNKS_PER_RUN`, um einen Lauf zu bauen, der seinen
    Rueckstand NICHT aufholt.
    """
    gesendet = []
    cursor = 1_000_000
    zustand = zustand if zustand is not None else {"last_block": cursor,
                                                   "processed": []}
    zustand.setdefault("last_block", cursor)
    spitze = zustand["last_block"] + abstand
    if deckel is not None:
        monkeypatch.setattr(pp, "MAX_CHUNKS_PER_RUN", deckel)
    monkeypatch.setattr(pp, "w3_client", lambda: _kette(spitze))
    monkeypatch.setattr(pp, "load_state", lambda: zustand)
    monkeypatch.setattr(pp, "save_state", lambda s: None)
    monkeypatch.setattr(pp, "send_telegram",
                        lambda text, **kw: gesendet.append(text) or True)
    monkeypatch.setattr(pp, "get_usdc_transfers", lambda a, b: [])
    pp.main()
    return gesendet


def test_der_normale_stundenabstand_schweigt(pp, monkeypatch):
    """Der Test, der gefehlt hat — und ohne den die Schwelle 600 durchging.

    Base erzeugt 1800 Bloecke je Stunde, der Poller laeuft stuendlich. Zu
    Beginn jedes Laufs ist der Cursor also rund 1800 Bloecke hinter der
    Spitze. Das ist der Takt, kein Rueckstand. Mit der Schwelle 600 meldete
    die Wache deshalb JEDEN Lauf: am 09.10.2026 elf Mal, waehrend jede
    Laufzeile mit `rueckstand=0 ergebnis=ok` endete.
    """
    assert _lauf(pp, monkeypatch, pp.BLOECKE_JE_LAUF) == []
    # Auch zwei Takte bleiben still: ein einzelner ausgefallener Lauf ist
    # noch kein Stillstand.
    assert _lauf(pp, monkeypatch, 2 * pp.BLOECKE_JE_LAUF) == []


def test_ein_abstand_den_der_lauf_aufholt_schweigt(pp, monkeypatch):
    """Drei Takte Abstand sind kein Befund, wenn der Lauf sie liest.

    Bis zum 07.11.2026 meldete die Wache den Abstand VOR dem Scan. Am 07.11.
    stand dort dreimal ein Abstand von 18000 Bloecken, und jeder dieser Laeufe
    hat ihn im selben Lauf gelesen: 0,8 Sekunden nach der Meldung stand
    `rueckstand=0 ergebnis=ok` im Protokoll. Gemeldet wurde also der Takt.

    Seitdem entscheidet sich die Meldung nach dem Scan (NOTIFY-4): was der
    Poller selbst aufholt, ist Ablage. Dieser Test ist die Schweigeprobe dazu
    — er loest aus, wenn jemand die Meldung wieder vor den Scan zieht.
    """
    assert _lauf(pp, monkeypatch, 3 * pp.BLOECKE_JE_LAUF + 1) == []
    # Auch ein Abstand von zehn Takten: 18001 Bloecke liest ein Lauf mit
    # 2000 Bloecken je Aufruf und 200 Aufrufen muehelos.
    assert _lauf(pp, monkeypatch, 10 * pp.BLOECKE_JE_LAUF + 1) == []


def test_zwei_laeufe_ohne_fortschritt_melden(pp, monkeypatch):
    """Der Befund, der bleibt: der Poller kommt nicht nach.

    Ein Lauf, der an seinem Deckel endet, laesst Rueckstand stehen. Schrumpft
    der nicht, ist das der Stillstand vom 09.10.2026 — und der gehoert
    gemeldet, mit Handlungsempfehlung.
    """
    zustand = {"last_block": 1_000_000, "processed": []}
    abstand = 50 * pp.CHUNK_BLOCKS

    # Erster Lauf: Rueckstand bleibt, aber es gibt keinen Vergleich — still.
    assert _lauf(pp, monkeypatch, abstand, zustand=zustand, deckel=1) == []
    assert zustand["rueckstand_vorher"] > pp.LAG_ALERT_BLOCKS

    # Zweiter Lauf: die Spitze zieht genauso weit mit, wie der Lauf liest.
    gesendet = _lauf(pp, monkeypatch, abstand, zustand=zustand, deckel=1)
    assert len(gesendet) == 1, gesendet
    text = gesendet[0]
    assert "holt nicht auf" in text
    assert str(pp.LAG_ALERT_BLOCKS) in text, "die Schwelle fehlt in der Meldung"
    assert str(pp.BLOECKE_JE_LAUF) in text, "der Takt fehlt in der Meldung"
    assert "Zu tun" in text, "die Handlungsempfehlung fehlt (NOTIFY-4)"


def test_schrumpfender_rueckstand_schweigt(pp, monkeypatch):
    """Der Poller arbeitet sich heran — das ist Ablage, keine Meldung."""
    zustand = {"last_block": 1_000_000, "processed": []}
    assert _lauf(pp, monkeypatch, 50 * pp.CHUNK_BLOCKS,
                 zustand=zustand, deckel=1) == []
    # Zweiter Lauf mit kleinerem Abstand: die Spitze bewegt sich nicht mit.
    assert _lauf(pp, monkeypatch, 10 * pp.CHUNK_BLOCKS,
                 zustand=zustand, deckel=1) == []


def test_die_stunden_kommen_aus_der_gemessenen_blockrate(pp, monkeypatch):
    """Die Zahl, die dreimal um den Faktor zehn falsch war.

    `lag * 2 / 3600` rechnete 18000 Bloecke in 10 Stunden um, weil zwei
    Sekunden je Block festgeschrieben waren. Gemessen sind es fuenf Bloecke je
    Sekunde, also eine Stunde.
    """
    zeiten = {}

    class _Eth:
        @property
        def block_number(self):
            return 1_000_000 + 50 * pp.CHUNK_BLOCKS

        def get_block(self, nummer):
            # Fuenf Bloecke je Sekunde, wie Base sie seit dem 05.11.2026 liefert.
            zeiten[nummer] = 1_762_000_000 + nummer // 5
            return {"timestamp": zeiten[nummer]}

    import types
    monkeypatch.setattr(pp, "w3_client", lambda: types.SimpleNamespace(eth=_Eth()))
    assert abs(pp.blockrate(1_000_000) - 5.0) < 0.01

    # Und in der Meldung steht die gemessene Stundenzahl, nicht die gerechnete.
    # Der Lauf davor endete mit genau dem Rueckstand, den dieser auch wieder
    # stehen laesst: 98000 Bloecke. Kein Fortschritt, also ein Befund.
    zustand = {"last_block": 1_000_000, "processed": [],
               "rueckstand_vorher": 49 * pp.CHUNK_BLOCKS}
    gesendet = []
    monkeypatch.setattr(pp, "MAX_CHUNKS_PER_RUN", 1)
    monkeypatch.setattr(pp, "load_state", lambda: zustand)
    monkeypatch.setattr(pp, "save_state", lambda s: None)
    monkeypatch.setattr(pp, "send_telegram",
                        lambda text, **kw: gesendet.append(text) or True)
    monkeypatch.setattr(pp, "get_usdc_transfers", lambda a, b: [])
    pp.main()
    assert len(gesendet) == 1, gesendet
    assert "(gemessen)" in gesendet[0]
    # 98000 Bloecke bei 5 je Sekunde sind 5,4 Stunden, nicht 54,4.
    assert "5.4 Stunden" in gesendet[0], gesendet[0]


def test_blockrate_nicht_messbar_nennt_den_rueckfall(pp, monkeypatch):
    """Scheitert die Messung, sagt die Meldung das — statt zu schweigen."""
    monkeypatch.setattr(pp, "w3_client", lambda: _kette(0, bricht=True))
    assert pp.blockrate(1_000_000) is None


def test_die_schwelle_liegt_ueber_dem_takt(pp):
    """Die Bedingung, aus der sich alles andere ergibt.

    Eine Schwelle unter dem Abstand eines Laufs meldet jeden Lauf; eine, die
    ein Vielfaches eines Tages ist, meldet nie. 43200 war das eine, 600 das
    andere.
    """
    assert pp.LAG_ALERT_BLOCKS > pp.BLOECKE_JE_LAUF, (
        f"Schwelle {pp.LAG_ALERT_BLOCKS} liegt unter dem Takt "
        f"{pp.BLOECKE_JE_LAUF} — die Wache meldet dann jeden Lauf")
    assert pp.LAG_ALERT_BLOCKS <= 6 * pp.BLOECKE_JE_LAUF, (
        f"Schwelle {pp.LAG_ALERT_BLOCKS} ist mehr als sechs Takte — ein "
        f"Stillstand bleibt dann einen halben Tag unbemerkt")


def test_knapp_unter_der_schwelle_schweigt(pp, monkeypatch):
    """Ein Block unter der Schwelle ist noch kein Befund."""
    assert _lauf(pp, monkeypatch, pp.LAG_ALERT_BLOCKS) == []


def test_die_meldung_traegt_den_schluessel_nicht(pp, monkeypatch):
    """Der Endpunkt steht in der Meldung — als Host, nicht als URL.

    Beim ersten Probelauf stand der ganze Endpunkt in der Laufzeile, Pfad und
    Schluessel inklusive. Eine Telegram-Meldung ist der schlechtere Ort dafuer:
    sie verlaesst den Rechner.
    """
    gesendet = []
    abstand = 50 * pp.CHUNK_BLOCKS
    monkeypatch.setattr(pp, "MAX_CHUNKS_PER_RUN", 1)
    monkeypatch.setattr(pp, "w3_client", lambda: _kette(1_000_000 + abstand))
    monkeypatch.setattr(pp, "load_state",
                        lambda: {"last_block": 1_000_000, "processed": [],
                                 "rueckstand_vorher": abstand - pp.CHUNK_BLOCKS})
    monkeypatch.setattr(pp, "save_state", lambda s: None)
    monkeypatch.setattr(pp, "send_telegram",
                        lambda text, **kw: gesendet.append(text) or True)
    monkeypatch.setattr(pp, "get_usdc_transfers", lambda a, b: [])

    pp.main()
    assert gesendet, "keine Meldung bei zwei Laeufen ohne Fortschritt"
    assert "testschluessel" not in gesendet[0]
    assert "rpc.ankr.com" in gesendet[0]


def test_unerreichbare_kette_meldet_und_nennt_nur_den_host(pp, monkeypatch):
    gesendet = []
    monkeypatch.setattr(pp, "w3_client", lambda: _kette(0, bricht=True))
    monkeypatch.setattr(pp, "load_state",
                        lambda: {"last_block": 1_000_000, "processed": []})
    monkeypatch.setattr(pp, "send_telegram",
                        lambda text, **kw: gesendet.append(text) or True)

    assert pp.main() == 1
    assert len(gesendet) == 1
    assert "cannot reach the chain" in gesendet[0]
    assert "testschluessel" not in gesendet[0]


def test_send_telegram_geht_durch_notify(pp, monkeypatch):
    """Der Poller baut seine Nutzlast nicht selbst.

    Bis zum 09.10.2026 tat er es: er benutzte notify nur fuer das Gate und die
    Chat-ID und schickte dann selbst. Damit stand keine Poller-Meldung im
    Sendeprotokoll und keine Wiederholung wurde gedrosselt — derselbe Bypass
    wie in deploy.sh.
    """
    from app import notify
    gerufen = {}

    def _fake(text, *, channel, **kw):
        gerufen["channel"] = channel
        gerufen["text"] = text
        gerufen["kw"] = kw
        return True

    monkeypatch.setattr(notify, "send_telegram", _fake)
    pp.send_telegram("probe")

    assert gerufen["channel"] == notify.MONEY
    assert gerufen["text"] == "probe"


def test_schwelle_und_blockgroesse_im_quelltext():
    """Die beiden Zahlen, die der Stillstand widerlegt hat.

    43200 Bloecke waren rund ein Tag — sechzehn Stunden Stillstand erreichten
    sie nicht. 50 Bloecke je Aufruf waren die Decke eines Endpunkts, den wir
    verlassen haben: 50 mal 200 Aufrufe sind 10000 Bloecke je Stunde gegen 1800
    erzeugte, und ein Rueckstand von 28900 haette drei Stunden gebraucht.

    Geprueft wird der Standardwert im Quelltext, nicht der geladene Wert. Beide
    Zahlen kommen aus `os.environ.get(..., "600")` — wer POLL_LAG_ALERT_BLOCKS
    setzt, verschiebt die Schwelle, und ein Test auf den geladenen Wert wuerde
    genau dann gruen bleiben, wenn die Wache abgeschaltet ist.
    """
    import ast
    import pathlib

    quelle = (pathlib.Path(__file__).resolve().parent.parent
              / "monitor" / "poll_payments.py").read_text(encoding="utf-8")
    standard = {}
    for k in ast.walk(ast.parse(quelle)):
        if not isinstance(k, ast.Call):
            continue
        f = k.func
        if not (isinstance(f, ast.Attribute) and f.attr == "get"):
            continue
        if len(k.args) != 2:
            continue
        name, wert = k.args
        if isinstance(name, ast.Constant) and isinstance(wert, ast.Constant):
            standard[name.value] = wert.value

    assert standard.get("POLL_CHUNK_BLOCKS") == "2000", standard
    # Die Schwelle steht nicht mehr als Literal im Standardwert, sondern wird
    # aus dem Takt gerechnet: `str(3 * BLOECKE_JE_LAUF)`. Geprueft wird
    # deshalb, dass sie NICHT als nackte Zahl dasteht — ein Literal waere ein
    # Wert ohne Begruendung, und genau so kamen 43200 und 600 zustande.
    import monitor.poll_payments as pp
    assert "POLL_LAG_ALERT_BLOCKS" not in standard, (
        "die Schwelle ist wieder ein Literal im Standardwert: "
        f"{standard.get('POLL_LAG_ALERT_BLOCKS')}")
    assert pp.LAG_ALERT_BLOCKS == 3 * pp.BLOECKE_JE_LAUF


def test_keine_crontab_zeile_verstellt_die_wache():
    """Die Schwelle ist von aussen verstellbar, und das darf niemand nutzen.

    Dieselbe Klasse wie POLL_RPC_URL am 09.10.2026: eine Crontab-Zeile setzt
    eine Variable, ueberstimmt damit den Quelltext und ist an der Stelle nicht
    zu sehen, an der man den Wert sucht. Bei einem Endpunkt kostet das
    Durchsatz, bei einer Schwelle kostet es die Wache selbst — sie wuerde
    schweigen und dabei gesund aussehen.
    """
    import pathlib
    import re

    schnappschuss = (pathlib.Path(__file__).resolve().parent.parent
                     / "ops" / "crontab.txt")
    if not schnappschuss.exists():
        pytest.skip("kein Crontab-Schnappschuss im Baum")
    text = schnappschuss.read_text(encoding="utf-8")
    verstellt = [ln for ln in text.splitlines()
                 if not ln.strip().startswith("#")
                 and re.search(r"\bPOLL_(LAG_ALERT_BLOCKS|CHUNK_BLOCKS|"
                               r"MAX_CHUNKS_PER_RUN|RPC_URL)\s*=", ln)]
    assert verstellt == [], verstellt
