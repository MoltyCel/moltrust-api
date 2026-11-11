"""The blog index generator takes its frame from the web checkout (ops BLOG-GEN-NAV).

blog/index.html is not part of the web deploy, and the generator used to take
nav and sprite from the served file, so a nav change never reached the blog
index. Now the frame comes from blog/index.html in the moltrust-web checkout;
if that file cannot be used, the served frame stays and the post list is still
written.
"""
import contextlib
import io
import os

from scripts import webroot_write_watch as w

POST = ('<html><head><title>{t} — MolTrust Blog</title>'
        '<meta name="description" content="{t} desc">'
        '<meta itemprop="datePublished" content="2026-10-0{n}">'
        '</head><body>{t}</body></html>')


def _index(nav: str, cards: str = "") -> str:
    return (f'<html><body>{nav}<section class="posts-container">\n{cards}</section>\n'
            '<footer>x</footer></body></html>')


NAV_ALT = '<nav><a href="/banking.html">Banking</a></nav>'
NAV_NEU = '<nav><a href="/banking.html">Banking</a><a href="/finance.html">Finance</a></nav>'


def _setup(tmp_path, served_nav=NAV_ALT, checkout_text=None):
    blog = tmp_path / "www" / "blog"
    blog.mkdir(parents=True)
    for i, t in enumerate("AB", 1):
        (blog / f"{t.lower()}.html").write_text(POST.format(t=t, n=i))
    (blog / "index.html").write_text(_index(served_nav))
    repo = tmp_path / "moltrust-web"
    (repo / "blog").mkdir(parents=True)
    frame = repo / "blog" / "index.html"
    if checkout_text is not None:
        frame.write_text(checkout_text)
    return blog, frame


def _run(blog, frame):
    gen = w._lade_generator(w.GEN_BLOG_INDEX, "gen_frame_test")
    gen.BLOG_DIR = str(blog)
    gen.INDEX_FILE = str(blog / "index.html")
    gen.FRAME_FILE = str(frame)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        gen.main()
    return (blog / "index.html").read_text(), out.getvalue()


def test_the_frame_comes_from_the_checkout_and_the_post_list_stays(tmp_path):
    blog, frame = _setup(tmp_path, checkout_text=_index(NAV_NEU, "<a>stale card</a>\n"))
    vorher, _ = _run(blog, tmp_path / "fehlt" / "blog" / "index.html")
    nachher, log = _run(blog, frame)
    assert "frame: from" in log and "regenerated with 2 posts" in log, log
    assert "/finance.html" in nachher and "stale card" not in nachher
    assert nachher.replace(NAV_NEU, NAV_ALT) == vorher, "only the frame changed"


def test_without_the_checkout_file_the_served_frame_stays(tmp_path):
    blog, frame = _setup(tmp_path)
    text, log = _run(blog, frame)
    assert "frame: kept the served frame (frame source not readable" in log, log
    assert NAV_ALT in text and "regenerated with 2 posts" in log


def test_a_checkout_file_without_markers_keeps_the_served_frame(tmp_path):
    blog, frame = _setup(tmp_path, checkout_text="<html>" + NAV_NEU + "</html>")
    text, log = _run(blog, frame)
    assert "post list markers missing" in log, log
    assert NAV_ALT in text and "/finance.html" not in text


def test_a_symlinked_frame_source_is_refused(tmp_path):
    blog, frame = _setup(tmp_path)
    secret = tmp_path / "secret.html"
    secret.write_text(_index("<nav>SECRET</nav>"))
    os.symlink(secret, frame)
    text, log = _run(blog, frame)
    assert "frame: kept the served frame" in log, log
    assert "SECRET" not in text


def test_a_hard_linked_frame_source_is_refused(tmp_path):
    blog, frame = _setup(tmp_path)
    other = tmp_path / "other.html"
    other.write_text(_index("<nav>LINKED</nav>"))
    os.link(other, frame)
    text, log = _run(blog, frame)
    assert "refused" in log, log
    assert "LINKED" not in text
