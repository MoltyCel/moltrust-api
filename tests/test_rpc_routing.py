"""Our own runs go on our own quota; the reference implementations do not.

Measured on 2026-10-05: the public endpoint capped eth_getLogs at 50 blocks and
one open provider answered 403 mid verification. The load test for the
track-record path put about two thousand calls on it and 73 of 100 came back
unanswered.

The exemptions are the point of this file. Three scripts are third-party
reference implementations: a stranger holding a credential must be able to run
them with no key, and registry_proof.py says so in its own words — "If this
script only passed against our own node it would be worth nothing."
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Published verifiers. They default to the public endpoint on purpose.
REFERENCE = {
    "scripts/registry_proof.py",
    "scripts/verify_anchor.py",
    "scripts/verify-solvency.py",
}
HARDCODED = re.compile(r'=\s*"https://mainnet\.base\.org"'
                       r'|HTTPProvider\("https://mainnet')


def ours():
    """Every .py under app/, agents/ and scripts/ that is not a reference tool."""
    out = []
    for folder in ("app", "agents", "scripts"):
        for p in sorted((ROOT / folder).rglob("*.py")):
            rel = str(p.relative_to(ROOT))
            if rel in REFERENCE or rel == "app/base_rpc.py":
                continue
            out.append((rel, p))
    return out


def test_no_module_of_ours_hardwires_the_public_endpoint():
    bad = [rel for rel, p in ours()
           if HARDCODED.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert not bad, f"haengt am oeffentlichen Endpunkt statt an BASE_RPC: {bad}"


def test_the_reference_tools_still_stand_alone():
    # They may not import from this repository, or a stranger cannot run them.
    for rel in sorted(REFERENCE):
        src = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        assert "from app" not in src and "import app" not in src, (
            f"{rel} importiert aus dem Repo und ist damit nicht mehr "
            f"eigenstaendig lauffaehig")
        assert "mainnet.base.org" in src, (
            f"{rel} braucht einen oeffentlichen Vorgabewert — ohne Schluessel "
            f"laeuft es sonst nicht")


def test_base_rpc_falls_back_so_nothing_breaks_unset(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("br", ROOT / "app" / "base_rpc.py")
    br = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(br)
    monkeypatch.delenv("BASE_RPC", raising=False)
    assert br.base_rpc_url() == br.PUBLIC_BASE_RPC
    assert br.is_public() is True
    monkeypatch.setenv("BASE_RPC", "https://rpc.example/base/k")
    assert br.base_rpc_url() == "https://rpc.example/base/k"
    assert br.is_public() is False
