"""Ein Endpunkt, eine Quelle, kein Rueckfallwert.

Bis zum 09.10.2026 stand in base_rpc_url() ein `or PUBLIC_BASE_RPC`. Der
USDC-Poller lief sechzehn Stunden gegen mainnet.base.org und bekam 429 nach
429, waehrend BASE_RPC in den Secrets auf den eigenen Anbieter zeigte — nicht
wegen dieses Rueckfallwerts, sondern weil die Crontab POLL_RPC_URL festschrieb
und eine Variable namens BASE_RPC im Poller etwas anderes las, als ihr Name
sagte. Beide Wege sind hier zu.
"""
import os

import pytest

from app import base_rpc as br


@pytest.fixture(autouse=True)
def sauber(monkeypatch):
    monkeypatch.delenv("BASE_RPC", raising=False)


def test_fehlender_wert_ist_ein_abbruch(monkeypatch):
    """Kein Default. Ein stiller Wechsel auf den oeffentlichen Knoten verdeckt,
    dass der eigene nicht antwortet, und sieht in jedem anderen Signal gesund
    aus."""
    with pytest.raises(br.BaseRpcNotConfigured) as exc:
        br.base_rpc_url()
    assert "nicht gesetzt" in str(exc.value)


def test_leerer_wert_ist_ein_abbruch(monkeypatch):
    monkeypatch.setenv("BASE_RPC", "   ")
    with pytest.raises(br.BaseRpcNotConfigured):
        br.base_rpc_url()


@pytest.mark.parametrize("v", ["http://rpc.example", "ws://rpc.example",
                               "file:///etc/passwd", "rpc.example"])
def test_nur_https(monkeypatch, v):
    monkeypatch.setenv("BASE_RPC", v)
    with pytest.raises(br.BaseRpcNotConfigured):
        br.base_rpc_url()


def test_https_geht_durch(monkeypatch):
    monkeypatch.setenv("BASE_RPC", "https://rpc.ankr.com/base/abc")
    assert br.base_rpc_url() == "https://rpc.ankr.com/base/abc"


def test_der_oeffentliche_knoten_ist_erlaubt_wenn_er_eingetragen_ist(monkeypatch):
    """Wer ihn will, traegt ihn ein. Dann steht es in der Konfiguration und
    nicht in einem Default."""
    monkeypatch.setenv("BASE_RPC", br.PUBLIC_BASE_RPC)
    assert br.base_rpc_url() == br.PUBLIC_BASE_RPC
    assert br.is_public() is True


def test_eigener_anbieter_ist_nicht_public(monkeypatch):
    monkeypatch.setenv("BASE_RPC", "https://rpc.ankr.com/base/abc")
    assert br.is_public() is False


def test_jeder_aufruf_liest_neu(monkeypatch):
    """Nicht beim Import gemerkt: ein Neustart soll reichen, um den Anbieter zu
    wechseln."""
    monkeypatch.setenv("BASE_RPC", "https://eins.example")
    assert br.base_rpc_url() == "https://eins.example"
    monkeypatch.setenv("BASE_RPC", "https://zwei.example")
    assert br.base_rpc_url() == "https://zwei.example"


def test_quelltext_traegt_keinen_rueckfall():
    """Das Negativ gegen die alte Fassung: `or PUBLIC_BASE_RPC` darf nicht
    zurueckkommen."""
    # Ohne den Docstring geprueft: der erklaert den alten Rueckfall und waere
    # sonst selbst der Treffer. Genau diese Verwechslung steht in #641 als
    # dritte Frage — ein Muster, das seinen eigenen Text findet, prueft nichts.
    import ast
    import inspect
    fn = ast.parse(inspect.getsource(br.base_rpc_url)).body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]
    rumpf = ast.unparse(fn)
    assert "PUBLIC_BASE_RPC" not in rumpf, rumpf
    # Nicht auf "https://" pruefen — das Praefix steht zu Recht im Rumpf, die
    # Schemapruefung braucht es. Was nicht darf, ist ein vollstaendiges
    # Endpunkt-Literal, also ein Schema mit einem Host dahinter.
    import re as _re
    urls = [n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and _re.match(r"^https?://[^\s/]+\.[^\s/]+", n.value)]
    assert urls == [], urls


# -- der Poller -------------------------------------------------------------

def test_poller_liest_keine_eigene_variable():
    """Der Name sagt, was gelesen wird. POLL_RPC_URL war der Grund, warum die
    URL-Durchsicht vom 07.10. diese Stelle nicht fand."""
    import monitor.poll_payments as pp
    import inspect
    src = inspect.getsource(pp)
    aktiv = [ln for ln in src.splitlines()
             if "POLL_RPC_URL" in ln and not ln.strip().startswith("#")]
    assert aktiv == [], aktiv


def test_poller_protokolliert_nur_den_host(monkeypatch):
    """Der Endpunkt traegt den Schluessel im Pfad. Beim ersten Probelauf am
    09.10. stand er in der Laufzeile."""
    import monitor.poll_payments as pp
    monkeypatch.setenv("BASE_RPC", "https://rpc.ankr.com/base/geheim123")
    h = pp.rpc_host()
    assert h == "rpc.ankr.com"
    assert "geheim123" not in h


def test_poller_schwelle_ist_zwanzig_minuten():
    """600 Bloecke, nicht 43200. Der alte Wert haette den Stillstand vom
    09.10. nach sechzehn Stunden noch nicht gemeldet."""
    import monitor.poll_payments as pp
    assert pp.LAG_ALERT_BLOCKS == 600
