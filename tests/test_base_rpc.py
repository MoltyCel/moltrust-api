"""Ein Endpunkt, eine Quelle, kein Rueckfallwert.

Bis zum 09.10.2026 stand in base_rpc_url() ein `or PUBLIC_BASE_RPC`. Der
USDC-Poller lief sechzehn Stunden gegen mainnet.base.org und bekam 429 nach
429, waehrend BASE_RPC in den Secrets auf den eigenen Anbieter zeigte — nicht
wegen dieses Rueckfallwerts, sondern weil die Crontab POLL_RPC_URL festschrieb
und eine Variable namens BASE_RPC im Poller etwas anderes las, als ihr Name
sagte. Beide Wege sind hier zu.
"""
import os
import pathlib

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


# -- kein Aufruf beim Import ------------------------------------------------

def test_kein_aufruf_auf_modulebene():
    """base_rpc_url() wird an der Benutzungsstelle gerufen, nicht beim Import.

    Am 09.10.2026 blieben nach dem ersten Durchgang drei Stellen stehen, die
    beim Import lasen — agents/watchdog.py, app/skale_anchor.py und
    scripts/watch_a175_funding.py. Vier Testmodule importieren watchdog.py fuer
    voellig andere Pruefungen; mit dem strengen Leser wurde daraus ein
    Sammelfehler, und drei Gates in CI waren rot. Lokal fiel es nicht auf, weil
    eine .env auf dem Server BASE_RPC liefert.

    Mit dem AST geprueft, nicht mit grep: derselbe Aufruf ist im
    Funktionsrumpf richtig und auf Modulebene ein Defekt. Nur der Syntaxbaum
    unterscheidet die beiden. Dekoratoren und Default-Argumente zaehlen zur
    Modulebene — die laufen beim Import mit.
    """
    import ast

    wurzel = pathlib.Path(__file__).resolve().parent.parent
    namen = {"base_rpc_url", "w3_client"}

    def modulebene(baum):
        for k in baum.body:
            if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in k.decorator_list:
                    yield from ast.walk(d)
                for d in (list(k.args.defaults)
                          + [x for x in k.args.kw_defaults if x]):
                    yield from ast.walk(d)
                continue
            if isinstance(k, ast.ClassDef):
                for d in k.decorator_list:
                    yield from ast.walk(d)
                for u in k.body:
                    if isinstance(u, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        for d in u.decorator_list:
                            yield from ast.walk(d)
                        continue
                    yield from ast.walk(u)
                continue
            yield from ast.walk(k)

    treffer = []
    for p in sorted(wurzel.rglob("*.py")):
        if any(t in p.parts for t in (".git", "venv", "node_modules", "build",
                                      ".venv", "site-packages")):
            continue
        try:
            baum = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for k in modulebene(baum):
            if not isinstance(k, ast.Call):
                continue
            f = k.func
            n = (f.id if isinstance(f, ast.Name)
                 else f.attr if isinstance(f, ast.Attribute) else None)
            if n in namen:
                treffer.append(f"{p.relative_to(wurzel)}:{k.lineno} {n}()")

    assert treffer == [], "beim Import gelesen: " + "; ".join(treffer)


def test_jedes_modul_laedt_ohne_die_variable(monkeypatch):
    """Die Gegenprobe zum AST: importieren, mit leerer Umgebung.

    Der Baum findet den direkten Aufruf. Diese Probe findet auch den
    verschachtelten — eine Modulebene, die eine Funktion ruft, die ihrerseits
    liest.
    """
    import importlib

    monkeypatch.delenv("BASE_RPC", raising=False)
    monkeypatch.delenv("POLL_RPC_URL", raising=False)
    for name in ("agents.watchdog", "app.skale_anchor", "app.usdc",
                 "app.erc8004", "app.track_record", "monitor.poll_payments"):
        for m in [k for k in list(__import__("sys").modules) if k == name]:
            del __import__("sys").modules[m]
        importlib.import_module(name)
