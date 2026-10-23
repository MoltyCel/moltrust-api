"""Schreibwache ueber den Web-Root (scripts/webroot_write_watch.py, ops W1).

Je Kriterium eine Ausloese- und eine Schweigeprobe: ein Schreibvorgang ohne
Ledger-Zeile meldet sich mit Pfad und Zeitpunkt, ein Install ueber deploy.sh
bleibt still. Der letzte Test faehrt die echte Funktion aus deploy.sh mit einem
Stub als Wrapper, damit Ledger-Format und Leser nicht auseinanderlaufen.
"""
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import pathlib
import subprocess
import time

from scripts import webroot_write_watch as w

DEPLOY_SH = pathlib.Path(__file__).resolve().parents[1] / "ops" / "deploy" / "deploy.sh"
BASELINE = {"generated": {"blog/index.html": "root-Cron", "registry-proof.json": "Tageslauf"}}


def _env(tmp_path):
    root = tmp_path / "www"; root.mkdir()
    ledger = tmp_path / "webroot-writes.jsonl"
    state = tmp_path / "state.json"
    bl = tmp_path / "baseline.json"; bl.write_text(json.dumps(BASELINE))
    return root, ledger, state, bl


def _ts(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ledger_line(ledger, rel, data: bytes, epoch: float):
    with open(ledger, "a") as f:
        f.write(json.dumps({"ts": _ts(epoch), "path": rel,
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "sha": "a" * 40, "writer": "deploy.sh"}) + "\n")


def _scan(root, ledger, state, bl, cursor, pruefer=None):
    state.write_text(json.dumps({"cursor": cursor}))
    eigen = {} if pruefer is None else {"pruefer": pruefer}
    return w.scan(str(root), str(ledger), str(state), str(bl), **eigen)[0]


def test_a_write_without_a_ledger_line_is_reported_with_path_and_time(tmp_path):
    root, ledger, state, bl = _env(tmp_path)
    cursor = time.time() - 1
    (root / ".well-known").mkdir()
    (root / ".well-known" / "x402.json").write_text("{}")
    out = _scan(root, ledger, state, bl, cursor)
    assert out and "1 Schreibvorgang" in out[0], out
    line = next(o for o in out if ".well-known/x402.json" in o)
    ct = os.stat(root / ".well-known" / "x402.json").st_ctime
    assert dt.datetime.fromtimestamp(ct, dt.timezone.utc).strftime("%d.%m.%Y %H:%M:%S") in line


def test_a_write_by_deploy_sh_stays_silent(tmp_path):
    root, ledger, state, bl = _env(tmp_path)
    cursor = time.time() - 1
    (root / "index.html").write_bytes(b"page")
    _ledger_line(ledger, "index.html", b"page", time.time())
    assert _scan(root, ledger, state, bl, cursor) == []


def test_same_path_in_the_ledger_but_other_bytes_is_reported(tmp_path):
    root, ledger, state, bl = _env(tmp_path)
    cursor = time.time() - 1
    _ledger_line(ledger, "index.html", b"what the deploy installed", time.time())
    (root / "index.html").write_bytes(b"edited afterwards")
    out = _scan(root, ledger, state, bl, cursor)
    assert any("index.html" in o for o in out[1:]), out


def test_a_ledger_line_far_from_the_write_does_not_cover_it(tmp_path):
    root, ledger, state, bl = _env(tmp_path)
    cursor = time.time() - 1
    (root / "registry-proof.py").write_bytes(b"v")
    _ledger_line(ledger, "registry-proof.py", b"v", time.time() - 3 * w.FENSTER)
    out = _scan(root, ledger, state, bl, cursor)
    assert any("registry-proof.py" in o for o in out[1:]), out


def test_a_write_before_the_cursor_stays_silent(tmp_path):
    root, ledger, state, bl = _env(tmp_path)
    (root / "old.html").write_text("before the cursor")
    time.sleep(0.05)
    cursor = time.time()  # old.html liegt davor
    time.sleep(0.05)
    assert _scan(root, ledger, state, bl, cursor) == []


# --- Kriterium 5, erzeugte Dateien: nicht nach Pfad, sondern nach Inhalt ---

def test_every_generated_file_has_a_check():
    """Die Klasse "generated" im Ausgangsstand gegen die Tabelle der Wache."""
    baseline = json.loads((pathlib.Path(__file__).resolve().parents[1]
                           / "config" / "webroot_baseline.json").read_text())
    assert set(baseline["generated"]) == set(w.PRUEFER)


def test_a_generated_file_without_a_check_is_reported(tmp_path):
    root, ledger, state, bl = _env(tmp_path)
    (root / "registry-proof.json").write_text("{}")
    out = _scan(root, ledger, state, bl, time.time() - 1, pruefer={})
    assert any("registry-proof.json" in o and "ohne Pruefung" in o for o in out[1:]), out


def _blog(root, index_text: str, posts: dict[str, str]):
    blog = root / "blog"
    blog.mkdir(exist_ok=True)
    for name, body in posts.items():
        (blog / name).write_text(body)
    (blog / "index.html").write_text(index_text)
    return blog


POST = ('<html><head><title>{t} — MolTrust Blog</title>'
        '<meta name="description" content="{t} desc">'
        '<meta itemprop="datePublished" content="2026-10-0{n}">'
        '</head><body><span class="read-time">4 min</span>'
        '<span class="article-tag">Protocol</span>{t}</body></html>')
LEER = ('<html><body><section class="posts-container">\n</section>\n'
        '<footer>x</footer></body></html>')


def _rahmen() -> str:
    """Nicht auf Modulebene: die alte Wache soll je Probe rot werden,
    nicht beim Einsammeln der Tests."""
    return w.rahmen_sha256(LEER)


def _generiert(blog) -> str:
    """Der echte Generator auf dem Testverzeichnis, Ausgabe in die Datei."""
    gen = w._lade_generator(w.GEN_BLOG_INDEX, "gen_fuer_test")
    gen.BLOG_DIR = str(blog)
    gen.INDEX_FILE = str(blog / "index.html")
    with contextlib.redirect_stdout(io.StringIO()):
        gen.main()
    return (blog / "index.html").read_text()


def _pruefer(rahmen=None):
    rahmen = _rahmen() if rahmen is None else rahmen
    return {"blog/index.html": lambda p: w.pruefe_blog_index(p, rahmen=rahmen)}


def test_the_frame_of_the_delivered_file_is_the_one_in_the_watch():
    """Die Erwartung im Programm gegen den Rahmen, der ausgeliefert wird.

    Gemessen am 14.10.2026 auf dem Server (a); hier bleibt die Form geprueft:
    64 Hexzeichen, und der Rahmen eines Index ohne Beitraege ist ein anderer.
    """
    assert len(w.BLOG_INDEX_RAHMEN) == 64 and w.BLOG_INDEX_RAHMEN != _rahmen()
    assert w.rahmen_sha256("<html><body>kein Marker</body></html>") is None


def test_the_generators_own_output_stays_silent(tmp_path):
    root, ledger, state, bl = _env(tmp_path)
    blog = _blog(root, LEER, {"a.html": POST.format(t="A", n=1),
                              "b.html": POST.format(t="B", n=2)})
    _generiert(blog)                      # die gelieferte Datei ist die Ausgabe
    assert w.pruefe_blog_index(str(blog / "index.html"), rahmen=_rahmen()) is None
    cursor = time.time() - 1
    out = _scan(root, ledger, state, bl, cursor, _pruefer())
    # Die Beitragsdateien selbst sind Schreibvorgaenge und werden gemeldet; der
    # Index ist der Festpunkt seines Generators und faellt heraus.
    assert not any("index.html" in o for o in out), out
    assert "2 Schreibvorgang" in out[0], out


def test_a_foreign_write_outside_the_post_list_is_reported(tmp_path):
    """Der Generator ist hier blind: die Datei ist nach dem Eingriff ein
    Festpunkt. Gefunden wird der Eingriff am Rahmen."""
    root, ledger, state, bl = _env(tmp_path)
    blog = _blog(root, LEER, {"a.html": POST.format(t="A", n=1)})
    erzeugt = _generiert(blog)
    (blog / "index.html").write_text(erzeugt.replace("</footer>",
                                                     '</footer><script>x</script>'))
    assert _generiert(blog) == (blog / "index.html").read_text(), "kein Festpunkt"
    befund = w.pruefe_blog_index(str(blog / "index.html"), rahmen=_rahmen())
    assert befund and "Rahmen weicht von der Erwartung ab" in befund, befund
    cursor = time.time() - 1
    out = _scan(root, ledger, state, bl, cursor, _pruefer())
    assert any("blog/index.html" in o and "Rahmen weicht" in o for o in out[1:]), out


def test_a_blog_index_the_generator_cannot_read_is_not_silent(tmp_path):
    root, _, _, _ = _env(tmp_path)
    blog = _blog(root, "<html><body>kein Marker</body></html>", {})
    befund = w.pruefe_blog_index(str(blog / "index.html"), rahmen=_rahmen())
    assert befund and "Marker der Beitragsliste fehlt" in befund, befund


def test_a_missing_post_in_the_index_is_reported(tmp_path):
    """Der haertere Fall: Rahmen und Datei bleiben gueltig, ein Beitrag fehlt."""
    root, ledger, state, bl = _env(tmp_path)
    blog = _blog(root, LEER, {"a.html": POST.format(t="A", n=1),
                              "b.html": POST.format(t="B", n=2)})
    _generiert(blog)
    text = (blog / "index.html").read_text()
    anfang = text.find('<a href="/blog/a.html">')
    ende = text.find("</a>", anfang) + len("</a>")
    gekuerzt = text[:anfang] + text[ende:]
    (blog / "index.html").write_text(gekuerzt)
    assert w.rahmen_sha256(gekuerzt) == _rahmen(), "der Rahmen ist unberuehrt"
    befund = w.pruefe_blog_index(str(blog / "index.html"), rahmen=_rahmen())
    assert befund and "weicht von der Generatorausgabe ab" in befund, befund


PROOF = {"as_of": "2026-10-14T08:52:00+00:00", "generated_at": "2026-10-14T08:52:49+00:00",
         "totals": {"rows": 2}, "rows": [{"kind": "agent", "did": "did:moltrust:1"},
                                         {"kind": "aggregate", "bucket": "partner"}]}


def _export(doc):
    def schreiben(out, as_of):
        d = dict(doc)
        d["as_of"] = as_of
        json.dump(d, open(out, "w"))
    return schreiben


def test_a_regenerated_registry_proof_differs_only_in_the_timestamp(tmp_path):
    f = tmp_path / "registry-proof.json"
    f.write_text(json.dumps(PROOF))
    neu = dict(PROOF, generated_at="2026-10-14T16:45:42+00:00")
    assert w.pruefe_registry_proof(str(f), export=_export(neu)) is None


def test_a_changed_row_in_the_registry_proof_is_reported(tmp_path):
    f = tmp_path / "registry-proof.json"
    f.write_text(json.dumps(PROOF))
    neu = json.loads(json.dumps(PROOF))
    neu["generated_at"] = "2026-10-14T16:45:42+00:00"
    neu["rows"][0]["registered"] = "2026-10-01"
    neu["totals"]["rows"] = 3
    befund = w.pruefe_registry_proof(str(f), export=_export(neu))
    assert befund and "1 Zeilen abweichend" in befund and "Felder totals" in befund, befund


def test_a_row_only_in_the_delivered_registry_proof_is_reported(tmp_path):
    f = tmp_path / "registry-proof.json"
    f.write_text(json.dumps(PROOF))
    neu = json.loads(json.dumps(PROOF))
    neu["generated_at"] = "x"
    neu["rows"] = [neu["rows"][1]]
    befund = w.pruefe_registry_proof(str(f), export=_export(neu))
    assert befund and "1 Zeilen nur geliefert" in befund, befund


def test_a_registry_proof_that_cannot_be_regenerated_is_not_silent(tmp_path):
    f = tmp_path / "registry-proof.json"
    f.write_text(json.dumps(PROOF))

    def fehler(out, as_of):
        raise subprocess.CalledProcessError(1, "export", stderr="no counted agents")

    befund = w.pruefe_registry_proof(str(f), export=fehler)
    assert befund and "nicht pruefbar" in befund and "no counted agents" in befund, befund
    f.write_text(json.dumps({"rows": []}))
    assert w.pruefe_registry_proof(str(f), export=_export(PROOF)) == \
        "nicht pruefbar: kein as_of in der gelieferten Datei"


def test_the_first_run_sets_the_cursor_and_reports_nothing(tmp_path):
    root, ledger, state, bl = _env(tmp_path)
    (root / "x.html").write_text("x")
    lines, new = w.scan(str(root), str(ledger), str(state), str(bl))
    assert lines == [] and new.get("init") is True


def test_main_sends_through_notify_and_advances_only_on_delivery(tmp_path, monkeypatch):
    root, ledger, state, bl = _env(tmp_path)
    from app import notify
    sent = []
    monkeypatch.setattr(notify, "send_telegram",
                        lambda text, channel: sent.append((channel, text)) or ok[0])
    monkeypatch.setattr(notify, "letzter_grund", lambda: "")
    args = ["--root", str(root), "--ledger", str(ledger), "--state", str(state),
            "--baseline", str(bl)]
    ok = [False]
    state.write_text(json.dumps({"cursor": time.time() - 1}))
    (root / "a.html").write_text("a")
    before = json.loads(state.read_text())["cursor"]
    assert w.main(args) == 1
    assert json.loads(state.read_text())["cursor"] == before, "Stand vorgerueckt trotz Fehlschlag"
    ok[0] = True
    assert w.main(args) == 0
    assert sent and sent[-1][0] == "alerts" and "a.html" in sent[-1][1]
    assert json.loads(state.read_text())["cursor"] > before
    sent.clear()
    assert w.main(args) == 0 and sent == [], "derselbe Vorgang zweimal gemeldet"


def test_deploy_sh_webinstall_writes_the_line_the_watch_accepts(tmp_path):
    root, ledger, state, bl = _env(tmp_path)
    src = tmp_path / "web"; (src / "rounds" / "r5").mkdir(parents=True)
    (src / "rounds" / "r5" / "result.json").write_text('{"v": 2}')
    stub = tmp_path / "webinstall"
    stub.write_text(f'#!/bin/bash\nmkdir -p "{root}/$(dirname "$1")"\n'
                    f'cp "{src}/$1" "{root}/$1"\n')
    stub.chmod(0o755)
    cursor = time.time() - 1
    env = {"DEPLOY_SH_FUNCTIONS_ONLY": "1", "PATH": os.environ["PATH"],
           "WEBROOT_LEDGER": str(ledger), "WEB_ROOT": str(root)}
    script = (f'source "{DEPLOY_SH}"; LOG=/dev/null; log() {{ echo "$1" >&2; }}; '
              f'sudo() {{ shift; "$@"; }}; WRAPPER="{stub}"; SHA={"b" * 40}; '
              'webinstall rounds/r5/result.json; echo rc=$?')
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert "rc=0" in p.stdout, p.stdout + p.stderr
    row = json.loads(ledger.read_text().strip())
    assert row["path"] == "rounds/r5/result.json" and row["writer"] == "deploy.sh"
    assert _scan(root, ledger, state, bl, cursor) == []
    # Schweigeprobe gegen Ausloeseprobe: dieselbe Datei danach von Hand.
    time.sleep(0.01)
    (root / "rounds" / "r5" / "result.json").write_text('{"v": 3}')
    out = _scan(root, ledger, state, bl, cursor)
    assert any("rounds/r5/result.json" in o for o in out[1:]), out
