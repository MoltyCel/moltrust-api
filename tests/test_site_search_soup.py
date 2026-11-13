"""GET /api/search parses the site's HTML with BeautifulSoup (ops SEC-1101).

The 2026-11-13 floor raises soupsieve, bs4's selector engine, from 2.9.1 to
4.x, and declares beautifulsoup4, which production had and requirements.txt
did not. The handler swallows every parse error per file (`except Exception:
continue`), so a parser that broke under the new versions would not show up
as an error: the search would just come back empty. These tests drive the
handler over a small page tree and check that the hit, its title and its
snippet survive, and that the stripped parts (nav, script, the mobile menu
found through find_all(class_=[...])) stay out of the text.
"""
import pathlib

import bs4
import pytest
import soupsieve
from fastapi.testclient import TestClient

import app.main as main

client = TestClient(main.app)

_REAL_PATH = pathlib.Path

_PAGE = """<!doctype html><html><head><title>Agent Trust Primer</title>
<script>var zebraquartz = 1;</script></head><body>
<nav>navonlyword</nav>
<div class="mobile-menu">menuonlyword</div>
<h1>Ignored when a title exists</h1>
<p>Pre-transaction trust for agents. The marker word is quokkafield and it
appears twice: quokkafield.</p>
<footer>footeronlyword</footer>
</body></html>"""

_NO_TITLE = """<html><body><h1>Heading Fallback</h1>
<p>Only in the second page: wombatline.</p></body></html>"""


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "primer.html").write_text(_PAGE)
    (tmp_path / "blog").mkdir()
    (tmp_path / "blog" / "fallback.html").write_text(_NO_TITLE)
    (tmp_path / "admin-panel.html").write_text("<p>quokkafield</p>")

    class _Redirected(type(_REAL_PATH())):
        # The handler imports Path inside the function and roots it at
        # /var/www/html; send exactly that path to the fixture tree.
        def __new__(cls, *args):
            if args == ("/var/www/html",):
                args = (str(tmp_path),)
            return _REAL_PATH(*args)

    monkeypatch.setattr(pathlib, "Path", _Redirected)
    monkeypatch.setattr(main, "db_pool", None)
    main._site_search_cache.clear()
    yield tmp_path
    main._site_search_cache.clear()


def _search(q):
    r = client.get("/api/search", params={"q": q})
    assert r.status_code == 200, r.text
    return r.json()


def test_versions_parse_together():
    # The import above is what failed before bs4 was declared; the parse is
    # the part soupsieve 4.x could change.
    soup = bs4.BeautifulSoup('<p class="a">x</p>', "html.parser")
    assert soup.find_all(class_=["a"])[0].get_text() == "x"
    assert soupsieve.__version__


def test_hit_carries_title_snippet_and_score(site):
    hits = _search("quokkafield")
    assert [h["url"] for h in hits] == ["/primer.html"]  # admin page skipped
    hit = hits[0]
    assert hit["title"] == "Agent Trust Primer"
    assert "quokkafield" in hit["snippet"]
    assert hit["score"] == 2


def test_h1_is_the_title_when_there_is_none(site):
    hits = _search("wombatline")
    assert hits == [{"title": "Heading Fallback", "url": "/blog/fallback.html",
                     "snippet": "Heading Fallback Only in the second page: wombatline.",
                     "score": 1}]


@pytest.mark.parametrize("word", ["navonlyword", "menuonlyword", "footeronlyword", "zebraquartz"])
def test_stripped_parts_are_not_searched(site, word):
    assert _search(word) == []
