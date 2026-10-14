"""Je Zweck ein Schlüssel: die Liste stimmt, und die Ableitung ist weg.

Ops-Job S5. Zwei Dinge werden geprüft, und jedes mit beiden Proben — der
Befund löst aus, der Normalbetrieb schweigt.

1. `scripts/key_purposes_check.py` gegen diesen Baum: still, solange die
   Lesestellen die aus `config/key_purposes.json` sind, und laut bei einem
   neuen Leser, einem toten Eintrag und einem umbenannten Zugang.
2. Die beiden Challenge-HMACs hängen nicht mehr am Agent-Card-Schlüssel.
   Gegen `main` ist dieser Teil rot: dort ändert ein Tausch von
   MOLTRUST_REGISTRY_PRIVATE_KEY jede ausstehende Challenge mit.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Öffentlich bekannter Wegwerf-Schlüssel (Hardhat-Konto 0). Steht hier, damit
# der Test eine Adresse gegen eine Ableitung halten kann, ohne einen echten
# Schlüssel zu brauchen.
TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def _load_checker():
    path = os.path.join(ROOT, "scripts", "key_purposes_check.py")
    spec = importlib.util.spec_from_file_location("key_purposes_check", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["key_purposes_check"] = mod
    spec.loader.exec_module(mod)
    return mod


checker = _load_checker()


def _config() -> dict:
    with open(os.path.join(ROOT, "config", "key_purposes.json"), encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# 1 — die Liste gegen den Baum
# ---------------------------------------------------------------------------
def test_der_baum_haelt_die_liste_ein():
    """Schweigeprobe: der echte Baum erzeugt keinen Befund."""
    assert checker.findings(ROOT, _config()) == []


def _tiny_repo(tmp_path, files: dict[str, str]):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return str(tmp_path)


MINI_CONFIG = {
    "zwecke": {
        "ipr-anker": {
            "beschreibung": "x",
            "variablen": ["BASE_WRITE_KEY"],
            "adresse": None,
            "zugang": "app/chain_keys.py:ipr_anchor_signer",
            "rotationsweg": "x",
        }
    },
    "ausgelaufen": {"BASE_WALLET_KEY": "Zweitname, keine Leser mehr"},
    "leser": {"BASE_WRITE_KEY": {"app/chain_keys.py": "die Lesestelle"}},
}

# Die Proben setzen den Variablennamen erst zur Laufzeit ein. Stünde die
# Leseform hier wörtlich, wäre diese Testdatei selbst ein Leser und müsste in
# die Liste — eine Liste, die ihren eigenen Prüfer enthält, prüft weniger.
LESEND = 'import os\n\n\ndef ipr_anchor_signer():\n    return os.getenv("{var}", "")\n'
ACCESSOR_FILE = LESEND.format(var="BASE_WRITE_KEY")


def test_ein_neuer_leser_faellt_auf(tmp_path):
    root = _tiny_repo(tmp_path, {
        "app/chain_keys.py": ACCESSOR_FILE,
        "app/neu.py": LESEND.format(var="BASE_WRITE_KEY").replace("getenv", "environ.get"),
    })
    out = checker.findings(root, MINI_CONFIG)
    assert any("app/neu.py" in f and "neuer Leser" in f for f in out), out


def test_ein_wiederbelebter_zweitname_faellt_auf(tmp_path):
    """BASE_WALLET_KEY ist ausgelaufen: null Leser ist die Prüfung."""
    root = _tiny_repo(tmp_path, {
        "app/chain_keys.py": ACCESSOR_FILE,
        "scripts/alt.py": LESEND.format(var="BASE_WALLET_KEY"),
    })
    out = checker.findings(root, MINI_CONFIG)
    assert any("BASE_WALLET_KEY" in f and "scripts/alt.py" in f for f in out), out


def test_ein_toter_eintrag_faellt_auf(tmp_path):
    root = _tiny_repo(tmp_path, {
        "app/chain_keys.py": ACCESSOR_FILE,
        "scripts/liest_nicht.py": "# nur Prosa über BASE_WRITE_KEY\n",
    })
    config = json.loads(json.dumps(MINI_CONFIG))
    config["leser"]["BASE_WRITE_KEY"]["scripts/liest_nicht.py"] = "steht hier zu Unrecht"
    out = checker.findings(root, config)
    assert any("liest nicht mehr" in f for f in out), out


def test_ein_umbenannter_zugang_faellt_auf(tmp_path):
    root = _tiny_repo(tmp_path, {"app/chain_keys.py": ACCESSOR_FILE})
    config = json.loads(json.dumps(MINI_CONFIG))
    config["zwecke"]["ipr-anker"]["zugang"] = "app/chain_keys.py:anchor_key"
    out = checker.findings(root, config)
    assert any("keine Funktion anchor_key" in f for f in out), out


def test_eine_zuweisung_ist_kein_leser(tmp_path):
    """`BASE_WRITE_KEY=` in einer Vorlage liest nichts."""
    root = _tiny_repo(tmp_path, {
        "app/chain_keys.py": ACCESSOR_FILE,
        "deploy.sh": "#!/bin/sh\nBASE_WRITE_KEY=\nexport BASE_WRITE_KEY\n",
    })
    out = checker.findings(root, MINI_CONFIG)
    assert out == [], out


# ---------------------------------------------------------------------------
# 2 — die Challenge-HMACs stehen für sich
# ---------------------------------------------------------------------------
CHALLENGE_MODULES = [
    ("app.keyless_register", "POP_CHALLENGE_SECRET"),
    ("app.enforcement.subject_binding", "AAE_CHALLENGE_SECRET"),
]


@pytest.mark.parametrize("module_name,var", CHALLENGE_MODULES)
def test_der_eigene_wert_wird_benutzt(module_name, var, monkeypatch):
    mod = importlib.import_module(module_name)
    monkeypatch.setenv(var, "s5-eigener-wert")
    eins = mod._secret()
    assert isinstance(eins, bytes) and len(eins) == 32
    assert mod._secret() == eins
    monkeypatch.setenv(var, "s5-anderer-wert")
    assert mod._secret() != eins, "ein Tausch des Werts muss wirken"


@pytest.mark.parametrize("module_name,var", CHALLENGE_MODULES)
def test_in_produktion_ohne_eigenen_wert_bricht_es_ab(module_name, var, monkeypatch):
    """Auslöseprobe. Auf main leitet dieser Fall still vom Kartenschlüssel ab."""
    mod = importlib.import_module(module_name)
    monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MOLTRUST_ENV", "production")
    monkeypatch.setenv("MOLTRUST_REGISTRY_PRIVATE_KEY", "a" * 64)
    with pytest.raises(RuntimeError, match=var):
        mod._secret()


@pytest.mark.parametrize("module_name,var", CHALLENGE_MODULES)
def test_der_kartenschluessel_bewegt_die_challenge_nicht_mehr(module_name, var, monkeypatch):
    """Der Kern von S5: ein Tausch des Signaturschlüssels lässt sie kalt."""
    mod = importlib.import_module(module_name)
    monkeypatch.setenv(var, "s5-eigener-wert")
    monkeypatch.setenv("MOLTRUST_REGISTRY_PRIVATE_KEY", "a" * 64)
    vorher = mod._secret()
    monkeypatch.setenv("MOLTRUST_REGISTRY_PRIVATE_KEY", "b" * 64)
    assert mod._secret() == vorher


@pytest.mark.parametrize("module_name,var", CHALLENGE_MODULES)
def test_ausserhalb_der_produktion_laeuft_es_ohne_wert(module_name, var, monkeypatch):
    """Schweigeprobe: Tests und ein Dev-Lauf brauchen keinen gesetzten Wert."""
    mod = importlib.import_module(module_name)
    monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("MOLTRUST_ENV", raising=False)
    eins = mod._secret()
    assert isinstance(eins, bytes) and len(eins) == 32
    assert mod._secret() == eins, "innerhalb eines Prozesses muss er stabil sein"


def test_die_beiden_nonce_raeume_bleiben_getrennt(monkeypatch):
    """Beide Variablen auf denselben Wert, und es bleiben zwei Nonce-Räume."""
    pop = importlib.import_module("app.keyless_register")
    aae = importlib.import_module("app.enforcement.subject_binding")
    monkeypatch.setenv("POP_CHALLENGE_SECRET", "derselbe-wert")
    monkeypatch.setenv("AAE_CHALLENGE_SECRET", "derselbe-wert")
    assert pop._secret() != aae._secret()


# ---------------------------------------------------------------------------
# 3 — die Zugänge selbst
# ---------------------------------------------------------------------------
def test_jeder_zugang_leitet_die_adresse_aus_dem_schluessel_ab(monkeypatch):
    from app import chain_keys
    for name in ("vc_anchor_signer", "ipr_anchor_signer", "registry_write_signer"):
        monkeypatch.setenv("BASE_ANCHOR_KEY", TEST_KEY)
        monkeypatch.setenv("BASE_WRITE_KEY", TEST_KEY)
        monkeypatch.delenv("BASE_ANCHOR_ADDR", raising=False)
        key, addr = getattr(chain_keys, name)()
        assert addr == TEST_ADDR, name
        assert key == TEST_KEY, name


def test_ein_fehlender_schluessel_nennt_seine_variable(monkeypatch):
    from app import chain_keys
    monkeypatch.delenv("BASE_WRITE_KEY", raising=False)
    with pytest.raises(chain_keys.ChainKeyNotConfigured, match="BASE_WRITE_KEY"):
        chain_keys.ipr_anchor_signer()


def test_eine_deklarierte_adresse_die_nicht_passt_haelt_den_anker_auf(monkeypatch):
    """Auslöseprobe für den Fall 0x9068 aus docs/WALLET_STATE.md."""
    from app import chain_keys
    monkeypatch.setenv("BASE_ANCHOR_KEY", TEST_KEY)
    monkeypatch.setenv("BASE_ANCHOR_ADDR", "0x9068E25d8EA247a24Abf9Ff42BfA084931B3bA91")
    with pytest.raises(chain_keys.ChainKeyNotConfigured, match="BASE_ANCHOR_ADDR"):
        chain_keys.vc_anchor_signer()


def test_kein_zweck_liest_den_schluessel_eines_anderen():
    """Ein Rückfall quer zu den Zwecken ist genau die Kopplung, die S5 löst."""
    text = open(os.path.join(ROOT, "app", "chain_keys.py"), encoding="utf-8").read()
    assert 'os.getenv("%s"' % "BASE_KEY" not in text
    assert "BASE_ADDR" not in text.split('"""', 2)[-1], \
        "die Sendeadresse kommt aus dem Schlüssel, nicht aus der Zahlungsvariable"
