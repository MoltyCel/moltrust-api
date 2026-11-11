"""A moltrust.ch @context URL is named in code only if it is published (2026-10-09).

https://api.moltrust.ch/contexts/trust/v1 stood in every credential from
2026-03-10 and answered 404, as did /ns/music/v1 and /ns/violation/v1.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
URL = re.compile(r"https?://(?:api\.)?moltrust\.ch/(?:contexts|ns)/[A-Za-z0-9_./-]+?(?=[\"'#\s]|$)")


def _listed():
    lines = (ROOT / "config" / "published_contexts.txt").read_text().splitlines()
    return {l.strip() for l in lines if l.strip() and not l.startswith("#")}


def test_every_context_url_in_code_is_on_the_published_list():
    listed = _listed()
    found = {}
    for d in ("app", "agents", "services", "scripts"):
        for f in (ROOT / d).rglob("*.py"):
            for m in URL.finditer(f.read_text(errors="replace")):
                found.setdefault(m.group(0).rstrip("/."), str(f.relative_to(ROOT)))
    missing = {u: f for u, f in found.items() if u not in listed}
    assert not missing, f"context URLs in code but not published: {missing}"


def test_the_list_is_well_formed():
    for u in _listed():
        assert URL.fullmatch(u), u
