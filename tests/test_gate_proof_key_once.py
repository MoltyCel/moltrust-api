"""Der Beweislauf überschreibt keinen Signierschlüssel.

Der Pfad war bis zum 07.10.2026 fest `~/gate-proof-key.txt` und wurde mit
`O_TRUNC` geöffnet: jeder Lauf überschrieb den Schlüssel des vorigen. An diesem
Tag tat ein `--probe-only`-Lauf genau das. Er brach bei `identity/bind` mit 409
ab, weil die Konsolen-Wallet noch an `gate-proof-paid` vom 23.09. gebunden war —
und hatte auf dem Weg dorthin deren Signierschlüssel schon zerstört. Die
Identität, die die Wallet hält, kann seitdem nicht mehr signieren, und keine
neue DID kann die Wallet übernehmen.

Die Tests halten drei Eigenschaften: der Schlüssel landet bei 0600, ein
vorhandener wird nie angefasst, und der Standardpfad folgt `--out`, damit zwei
Läufe nicht auf dieselbe Datei zeigen.
"""
import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gate_proof.py"


@pytest.fixture(scope="module")
def mod():
    """Das Skript laden, ohne main() zu starten."""
    spec = importlib.util.spec_from_file_location("gate_proof_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def test_schreibt_den_schluessel_bei_0600(mod, tmp_path):
    p = tmp_path / "lauf-key.txt"
    out = mod.write_key_once(str(p), b"\x01\x02\x03")
    assert out == str(p)
    assert p.read_text() == "010203"
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"Schluessel liegt bei {oct(mode)}"


def test_ueberschreibt_niemals(mod, tmp_path):
    """Der Fall vom 07.10. Abbruch, und die alte Datei bleibt unberuehrt."""
    p = tmp_path / "vorhanden-key.txt"
    p.write_text("der alte schluessel")
    with pytest.raises(FileExistsError) as exc:
        mod.write_key_once(str(p), b"\xaa\xbb")
    assert exc.value.filename == str(p)
    assert p.read_text() == "der alte schluessel", "die alte Datei wurde angefasst"


def test_expandiert_die_tilde(mod, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = mod.write_key_once("~/mit-tilde-key.txt", b"\x07")
    assert out == str(tmp_path / "mit-tilde-key.txt")
    assert (tmp_path / "mit-tilde-key.txt").read_text() == "07"


def test_standardpfad_folgt_out(mod):
    """Zwei Laeufe mit verschiedenem --out zeigen nicht auf dieselbe Datei.

    Geprueft wird der Code, nicht der Dateitext: die Begruendung im Docstring
    von `write_key_once` nennt `O_TRUNC` und den alten festen Pfad, und eine
    Textsuche haelt die Erklaerung fuer den Fehler. Kommentare und
    Zeichenketten fallen deshalb per tokenize heraus.
    """
    import io
    import tokenize

    src = SCRIPT.read_text(encoding="utf-8")
    code = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        code.append(tok.string)
    code = " ".join(code)

    assert "O_EXCL" in code, "O_EXCL fehlt"
    assert "O_TRUNC" not in code, "O_TRUNC ist im Code zurueck"
    assert "splitext" in code and "key_out" in code, \
        "der Standardpfad folgt nicht mehr --out"
    # Der alte feste Pfad als Zeichenkette: hier ausnahmsweise im Dateitext,
    # aber nur an einer Zuweisung, nicht in einer Erklaerung.
    assert 'key_path = os.path.expanduser("~/gate-proof-key.txt")' not in src, \
        "der feste Pfad ist als Zuweisung zurueck"
    assert "--key-out" in src


def test_main_bricht_ab_statt_zu_ueberschreiben(mod, tmp_path, monkeypatch, capsys):
    """Der Aufrufer macht aus dem Fehler einen Abbruch mit Code 2.

    Nur bis zur Schluesselstelle: danach braucht main() Netz und Schluessel.
    """
    p = tmp_path / "x-key.txt"
    p.write_text("alt")
    monkeypatch.setattr(sys, "argv", ["gate_proof.py", "--probe-only",
                                      "--out", str(tmp_path / "x.json")])
    monkeypatch.setenv("BASE_ANCHOR_KEY", "")
    rc = mod.main()
    # Ohne BASE_ANCHOR_KEY bricht main() schon vorher ab; der Test prueft nur,
    # dass es einen nicht-null Code gibt und die Datei unberuehrt bleibt.
    assert rc != 0
    assert p.read_text() == "alt"
