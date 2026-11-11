"""Was der Lock-Vergleich durchlaesst und was er anhaelt.

Am 17.10.2026 stand auf dem Server a2a-sdk 1.1.0, waehrend requirements.lock
1.1.2 pinnte — durch jeden Deploy hindurch, weil requirements.txt einen
Bereich deklariert und pip stehen laesst, was ihn erfuellt. Von 106 Pins lagen
67 auseinander (ops DEP-1). Seitdem vergleicht der Deploy, und er bricht ab,
wenn eine Abweichung nicht in ops/lockdrift-bekannt.txt steht.

Beide Richtungen stehen hier: der Befund loest aus, und der Normalbetrieb
schweigt. Die Schweigeprobe ist die wichtigere von beiden — ein Vergleich, der
die 66 aufgeschriebenen Abweichungen anmeldet, haelt jeden Deploy an, und ein
Tor, das immer zu ist, wird aufgekeilt.

Kein Netz, kein pip: die installierten Versionen kommen als Abbildung herein.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "lock_drift", ROOT / "scripts" / "lock_drift.py")
ld = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ld)


LOCK = """\
a2a-sdk[http-server]==1.1.2 \\
    --hash=sha256:eb9a
    # via -r requirements.txt
web3==8.0.0 \\
    --hash=sha256:0000
    # via -r requirements.txt
truststore==0.10.4 \\
    --hash=sha256:1111
    # via mcp
"""


def schreibe(tmp_path, lock=LOCK, bekannt=None):
    lock_datei = tmp_path / "requirements.lock"
    lock_datei.write_text(lock)
    bekannt_datei = tmp_path / "bekannt.txt"
    if bekannt is not None:
        bekannt_datei.write_text(bekannt)
    return lock_datei, bekannt_datei


def lauf(tmp_path, installiert, lock=LOCK, bekannt=None, monkeypatch=None):
    lock_datei, bekannt_datei = schreibe(tmp_path, lock, bekannt)
    monkeypatch.setattr(ld, "installed_versions", lambda: installiert)
    return ld.main(["--lock", str(lock_datei), "--known", str(bekannt_datei)])


def test_alles_auf_der_lock_version_schweigt(tmp_path, monkeypatch, capsys):
    rc = lauf(tmp_path,
              {"a2a-sdk": "1.1.2", "web3": "8.0.0", "truststore": "0.10.4"},
              monkeypatch=monkeypatch)
    assert rc == 0
    assert "not recorded" not in capsys.readouterr().out


def test_eine_nicht_aufgeschriebene_abweichung_haelt_an(tmp_path, monkeypatch, capsys):
    rc = lauf(tmp_path,
              {"a2a-sdk": "1.1.0", "web3": "8.0.0", "truststore": "0.10.4"},
              monkeypatch=monkeypatch)
    assert rc == 1
    aus = capsys.readouterr().out
    assert "a2a-sdk lock 1.1.2, installed 1.1.0 -- not recorded" in aus
    assert "1 unrecorded" in aus


def test_die_aufgeschriebenen_abweichungen_schweigen(tmp_path, monkeypatch, capsys):
    """Die Schweigeprobe: 2 von 3 weichen ab, beide stehen in der Liste."""
    rc = lauf(tmp_path,
              {"a2a-sdk": "1.1.2", "web3": "7.14.1"},
              bekannt="web3 8.0.0 7.14.1\ntruststore 0.10.4 fehlt\n",
              monkeypatch=monkeypatch)
    assert rc == 0
    aus = capsys.readouterr().out
    assert "0 unrecorded" in aus
    assert "2 recorded" in aus


def test_ein_fehlendes_paket_ist_eine_abweichung(tmp_path, monkeypatch, capsys):
    rc = lauf(tmp_path, {"a2a-sdk": "1.1.2", "web3": "8.0.0"},
              monkeypatch=monkeypatch)
    assert rc == 1
    assert "truststore lock 0.10.4, installed fehlt" in capsys.readouterr().out


def test_eine_andere_abweichung_als_aufgeschrieben_haelt_an(tmp_path, monkeypatch, capsys):
    """Die Liste deckt ein Versionspaar, nicht das Paket.

    Sonst waere eine Zeile ein Dauerfreibrief: web3 duerfte von 7.14.1 auf
    irgendetwas rutschen, und der Vergleich wuerde schweigen.
    """
    rc = lauf(tmp_path,
              {"a2a-sdk": "1.1.2", "web3": "6.0.0", "truststore": "0.10.4"},
              bekannt="web3 8.0.0 7.14.1\n",
              monkeypatch=monkeypatch)
    assert rc == 1
    assert "web3 lock 8.0.0, installed 6.0.0 -- not recorded" in capsys.readouterr().out


def test_eine_geschlossene_abweichung_bricht_nichts_ab(tmp_path, monkeypatch, capsys):
    """Ein Deploy, der Drift schliesst, darf nicht daran scheitern."""
    rc = lauf(tmp_path,
              {"a2a-sdk": "1.1.2", "web3": "8.0.0", "truststore": "0.10.4"},
              bekannt="web3 8.0.0 7.14.1\n",
              monkeypatch=monkeypatch)
    assert rc == 0
    assert "the line can go" in capsys.readouterr().out


def test_namen_in_jeder_schreibweise(tmp_path, monkeypatch):
    """pip kennt a2a_sdk, A2A-SDK und a2a.sdk als dasselbe Paket."""
    rc = lauf(tmp_path,
              {"A2A_SDK": "1.1.2", "Web3": "8.0.0", "TrustStore": "0.10.4"},
              monkeypatch=monkeypatch)
    assert rc == 0


def test_fehlende_liste_ist_keine_leere_liste(tmp_path, monkeypatch, capsys):
    """Ohne Datei gilt keine Abweichung als aufgeschrieben."""
    lock_datei, _ = schreibe(tmp_path)
    monkeypatch.setattr(ld, "installed_versions",
                        lambda: {"a2a-sdk": "1.1.0", "web3": "8.0.0",
                                 "truststore": "0.10.4"})
    rc = ld.main(["--lock", str(lock_datei),
                  "--known", str(tmp_path / "gibt-es-nicht.txt")])
    assert rc == 1
    assert "1 unrecorded" in capsys.readouterr().out


def test_ein_leeres_lock_ist_kein_gruenes_ergebnis(tmp_path, monkeypatch):
    """Sonst meldet ein kaputter Pfad null Abweichungen und niemand merkt es."""
    rc = lauf(tmp_path, {"a2a-sdk": "1.1.0"}, lock="# nichts\n",
              monkeypatch=monkeypatch)
    assert rc == 2


def test_eine_unleserliche_liste_meldet_sich(tmp_path, monkeypatch):
    rc = lauf(tmp_path, {"a2a-sdk": "1.1.2", "web3": "8.0.0",
                         "truststore": "0.10.4"},
              bekannt="web3 8.0.0\n", monkeypatch=monkeypatch)
    assert rc == 2


@pytest.mark.parametrize("name", ["a2a-sdk", "web3", "truststore"])
def test_das_echte_lock_pinnt_was_die_liste_nennt(name):
    """Die Dateien im Repo passen zueinander: jede Zeile der Liste ist ein Pin."""
    pins = ld.read_lock(ROOT / "requirements.lock")
    bekannt = ld.read_known(ROOT / "ops" / "lockdrift-bekannt.txt")
    assert name in pins
    for paket, (lock_version, _) in bekannt.items():
        assert paket in pins, f"{paket} steht in der Liste und nicht im Lock"
        assert pins[paket] == lock_version, (
            f"{paket}: Liste sagt lock {lock_version}, das Lock sagt {pins[paket]}")
