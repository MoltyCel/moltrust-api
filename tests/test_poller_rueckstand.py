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


def test_rueckstand_ueber_der_schwelle_meldet(pp, monkeypatch, tmp_path):
    """601 Bloecke Abstand, und die Meldung geht raus."""
    gesendet = []
    monkeypatch.setattr(pp, "w3_client", lambda: _kette(1_000_601))
    monkeypatch.setattr(pp, "load_state",
                        lambda: {"last_block": 1_000_000, "processed": []})
    monkeypatch.setattr(pp, "save_state", lambda s: None)
    monkeypatch.setattr(pp, "send_telegram",
                        lambda text, **kw: gesendet.append((text, kw)) or True)
    monkeypatch.setattr(pp, "get_usdc_transfers", lambda a, b: [])

    pp.main()

    assert len(gesendet) == 1, gesendet
    text = gesendet[0][0]
    assert "haengt zurueck" in text
    # Cursor, Spitze und Abstand gehoeren in die Meldung. Ohne sie ist nicht zu
    # entscheiden, ob der Poller aufarbeitet oder steht.
    assert "1000000" in text and "1000601" in text and "601" in text
    assert "600" in text, "die Schwelle fehlt in der Meldung"


def test_unter_der_schwelle_schweigt(pp, monkeypatch):
    """599 Bloecke sind normaler Betrieb: 600 Bloecke sind rund 20 Minuten,
    und der Poller laeuft stuendlich."""
    gesendet = []
    monkeypatch.setattr(pp, "w3_client", lambda: _kette(1_000_599))
    monkeypatch.setattr(pp, "load_state",
                        lambda: {"last_block": 1_000_000, "processed": []})
    monkeypatch.setattr(pp, "save_state", lambda s: None)
    monkeypatch.setattr(pp, "send_telegram",
                        lambda text, **kw: gesendet.append(text) or True)
    monkeypatch.setattr(pp, "get_usdc_transfers", lambda a, b: [])

    pp.main()
    assert gesendet == []


def test_die_meldung_traegt_den_schluessel_nicht(pp, monkeypatch):
    """Der Endpunkt steht in der Meldung — als Host, nicht als URL.

    Beim ersten Probelauf stand der ganze Endpunkt in der Laufzeile, Pfad und
    Schluessel inklusive. Eine Telegram-Meldung ist der schlechtere Ort dafuer:
    sie verlaesst den Rechner.
    """
    gesendet = []
    monkeypatch.setattr(pp, "w3_client", lambda: _kette(1_002_000))
    monkeypatch.setattr(pp, "load_state",
                        lambda: {"last_block": 1_000_000, "processed": []})
    monkeypatch.setattr(pp, "save_state", lambda s: None)
    monkeypatch.setattr(pp, "send_telegram",
                        lambda text, **kw: gesendet.append(text) or True)
    monkeypatch.setattr(pp, "get_usdc_transfers", lambda a, b: [])

    pp.main()
    assert gesendet, "keine Meldung bei 2000 Bloecken Abstand"
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

    assert standard.get("POLL_LAG_ALERT_BLOCKS") == "600", standard
    assert standard.get("POLL_CHUNK_BLOCKS") == "2000", standard


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
