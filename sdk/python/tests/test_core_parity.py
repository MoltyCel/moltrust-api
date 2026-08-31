"""Tests — der vendorierte Kern muss dem Server-Kern entsprechen.

`src/moltrust_enforce/_core.py` ist eine Kopie von `app/enforcement/enforce_check.py`. Genau
eine Zeile weicht ab: der Import der JCS-Kanonisierung zeigt hier auf `jcs` statt auf
`app.signature`, weil das SDK ohne das Server-Paket auskommen muss. `app.signature.canonicalize`
ist ein direkter Durchreicher auf `jcs.canonicalize`, die Semantik ist also dieselbe.

Zwei Pruefungen, weil eine allein nicht traegt: der Zeilenvergleich faengt eine stille
Abweichung im Quelltext, der Fallkorpus faengt eine, die sich trotz gleichem Text anders
verhaelt. Faellt einer der beiden, ist das SDK von der Server-Signatur abgedriftet.
"""
import pathlib

import pytest

from moltrust_enforce import action_digest, core_digest, enforce_check

VENDORED = pathlib.Path(__file__).resolve().parents[1] / "src" / "moltrust_enforce" / "_core.py"

_SERVER_IMPORT = "from app.signature import canonicalize  # RFC 8785 JCS -> bytes"
_SDK_IMPORT = "from jcs import canonicalize  # RFC 8785 JCS -> bytes"


def _server_core() -> pathlib.Path:
    """`app/enforcement/enforce_check.py` im umgebenden Repo, falls vorhanden.

    Nach der Auslagerung in ein eigenes Repo gibt es die Datei nicht mehr — dann greift der
    Verhaltenskorpus unten, der ohne sie auskommt.
    """
    for parent in pathlib.Path(__file__).resolve().parents:
        candidate = parent / "app" / "enforcement" / "enforce_check.py"
        if candidate.is_file():
            return candidate
    return None


def test_vendored_core_differs_in_exactly_one_line():
    server = _server_core()
    if server is None:
        pytest.skip("server core not reachable from here (SDK checked out standalone)")
    a = server.read_text().splitlines()
    b = VENDORED.read_text().splitlines()
    assert len(a) == len(b), "vendored core has a different line count than the server core"
    differing = [(i, x, y) for i, (x, y) in enumerate(zip(a, b), 1) if x != y]
    assert len(differing) == 1, f"expected exactly one differing line, got {differing}"
    _lineno, server_line, sdk_line = differing[0]
    assert server_line == _SERVER_IMPORT
    assert sdk_line == _SDK_IMPORT


def test_vendored_import_is_the_documented_one():
    text = VENDORED.read_text()
    assert _SDK_IMPORT in text
    assert "from app." not in text, "vendored core must not import the server package"


# --- Ratifikations-Kern: dieselbe Regel, eigene Datei --------------------------------

VENDORED_RATIFY = (pathlib.Path(__file__).resolve().parents[1]
                   / "src" / "moltrust_enforce" / "_ratify_core.py")

_SERVER_RATIFY_IMPORT = "from app.enforcement.enforce_check import ("
_SDK_RATIFY_IMPORT = "from ._core import ("


def _server_ratify() -> pathlib.Path:
    for parent in pathlib.Path(__file__).resolve().parents:
        candidate = parent / "app" / "enforcement" / "ratify.py"
        if candidate.is_file():
            return candidate
    return None


def test_vendored_ratify_differs_in_exactly_one_line():
    server = _server_ratify()
    if server is None:
        pytest.skip("server ratify core not reachable from here (SDK checked out standalone)")
    a = server.read_text().splitlines()
    b = VENDORED_RATIFY.read_text().splitlines()
    assert len(a) == len(b), "vendored ratify core has a different line count"
    differing = [(i, x, y) for i, (x, y) in enumerate(zip(a, b), 1) if x != y]
    assert len(differing) == 1, f"expected exactly one differing line, got {differing}"
    _lineno, server_line, sdk_line = differing[0]
    assert server_line == _SERVER_RATIFY_IMPORT
    assert sdk_line == _SDK_RATIFY_IMPORT


def test_vendored_ratify_does_not_import_the_server_package():
    assert "from app." not in VENDORED_RATIFY.read_text()


# --- Verhaltenskorpus: dieselben Eingaben, dieselben Digests -------------------------

ADDR = "0xABCDEF0123456789ABCDEF0123456789ABCDEF01"
PAY = {"verb": "transfer", "asset": "USDC", "chain": "base"}
PAY_FIELDS = ["verb", "asset", "chain"]


def _g(disposition="allow", constraints=(), type_fields=PAY_FIELDS, action=PAY):
    return {"action_binding": action_digest(action), "disposition": disposition,
            "type_fields": type_fields, "constraints": list(constraints)}


CORPUS = [
    (None, {}),
    ({}, {"action": PAY}),
    ({"grants": []}, {"action": PAY}),
    ({"grants": [_g("allow")]}, {"action": PAY}),
    ({"grants": [_g("hold", [{"type": "exact", "field": "to", "value": ADDR}])]},
     {"action": PAY, "to": ADDR}),
    ({"grants": [_g("forbid")]}, {"action": PAY}),
    ({"grants": [_g("allow", [{"type": "range", "field": "amount", "lo": 0, "hi": 10}])]},
     {"action": PAY, "amount": 11}),
    ({"grants": [_g("allow", [{"type": "enum", "field": "region",
                               "values": ["CH", "DE"]}])]},
     {"action": PAY, "region": "CH"}),
    # --- Typform: dieselben Faelle muessen auf beiden Seiten gleich ausgehen ---------
    ({"grants": [{"action_binding": action_digest(PAY), "disposition": "allow",
                  "constraints": []}]}, {"action": PAY}),          # type_fields fehlt
    ({"grants": [_g("allow", type_fields=["asset", "chain"])]}, {"action": PAY}),
    ({"grants": [_g("allow")]}, {"action": {**PAY, "memo": "x"}}),  # Feld ausserhalb
    ({"grants": [_g("allow")]}, {"action": {"verb": "transfer"}}),  # Feld fehlt
    ({"grants": [_g("allow")]}, {"action": "pay"}),                 # String-Luecke
    ({"grants": [_g("allow")]}, {"action": ["pay"]}),
    ({"grants": [_g("allow")]}, {"to": ADDR}),                      # gar keine action
]


@pytest.mark.parametrize("mandate,transaction", CORPUS)
def test_behaviour_matches_the_server_core(mandate, transaction):
    server = _server_core()
    if server is None:
        pytest.skip("server core not reachable from here (SDK checked out standalone)")
    import importlib.util
    import sys

    # Den Server-Kern unter eigenem Namen laden. `app.signature.canonicalize` reicht auf
    # `jcs.canonicalize` durch, deshalb genuegt ein Stub-Modul fuer den Import.
    if "app.signature" not in sys.modules:
        import types
        import jcs
        app_mod = sys.modules.setdefault("app", types.ModuleType("app"))
        sig = types.ModuleType("app.signature")
        sig.canonicalize = jcs.canonicalize
        sys.modules["app.signature"] = sig
        app_mod.signature = sig

    spec = importlib.util.spec_from_file_location("_server_enforce_core", server)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    theirs = mod.enforce_check(mandate, transaction)
    ours = enforce_check(mandate, transaction)
    assert ours["verdict"] == theirs["verdict"]
    assert ours["reason"] == theirs["reason"]
    assert ours["grant_index"] == theirs["grant_index"]
    assert ours["trace"] == theirs["trace"]
    assert ours["core"] == theirs["core"]
    assert ours["core_digest"] == theirs["core_digest"]
    assert mod.action_digest(PAY) == action_digest(PAY)
    assert mod.core_digest(ours["core"]) == core_digest(ours["core"])


def test_app_signature_is_a_plain_passthrough_to_jcs():
    """Die eine abweichende Zeile ist nur dann harmlos, wenn `app.signature.canonicalize`
    nichts weiter tut als `jcs.canonicalize`. Sonst waere die Portierung eine echte
    Abweichung und muesste gemeldet werden."""
    for parent in pathlib.Path(__file__).resolve().parents:
        candidate = parent / "app" / "signature.py"
        if candidate.is_file():
            src = candidate.read_text()
            assert "return jcs.canonicalize(payload)" in src
            return
    pytest.skip("app/signature.py not reachable from here (SDK checked out standalone)")
