"""The ambassador's DID follows method specification section 2.2 (ops A2, 2026-10-12).

`did:moltrust:ambassador0001` predates the 16-hex convention; the lookup endpoints
follow section 2.2 and no longer accept it (#660). The ambassador was reissued
additively and every place that names it names the same strict identifier.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
STRICT = re.compile(r"^did:moltrust:[0-9a-f]{16}$")
SITES = {"agents/ambassador.py": "AMBASSADOR_DID", "agent/ambassador.py": "AMBASSADOR_DID",
         "agents/moltbook_poster.py": "POSTER_DID",
         # The referrer the Moltbook channel line counts (ops AMB-1).
         "app/kohorten.py": "MOLTBOOK_REFERRER_DID"}


def _value(path, name):
    m = re.search(rf'^{name} = "([^"]+)"', (ROOT / path).read_text(), re.M)
    assert m, f"{name} not found in {path}"
    return m.group(1)


def test_every_site_names_one_strict_did():
    values = {_value(p, n) for p, n in SITES.items()}
    assert len(values) == 1, values
    assert STRICT.match(values.pop())


def test_self_registration_is_a_system_agent():
    src = (ROOT / "agent/ambassador.py").read_text()
    assert "agent_type, created_at" in src and "'system', now()" in src
