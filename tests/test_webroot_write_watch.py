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

import pytest

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
    gen.FRAME_FILE = None
    with contextlib.redirect_stdout(io.StringIO()):
        gen.main()
    return (blog / "index.html").read_text()


def _pruefer(rahmen=None):
    rahmen = _rahmen() if rahmen is None else rahmen
    return {"blog/index.html": lambda p: w.pruefe_blog_index(p, rahmen=rahmen)}


def test_the_frame_has_no_fixed_value_left():
    """WRW-RAHMEN Kriterium 2: die Erwartung kommt aus dem deployten Commit,
    ein fester Wert im Programm waere der alte Weg."""
    assert not hasattr(w, "BLOG_INDEX_RAHMEN")
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
    assert out[0] == "Lars muss: nichts", out
    assert out[1].startswith("Blog-Index: "), out
    assert "Wer behebt: Job BLOG-GEN-NAV." in out, out


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


# ── Lebenszeichen getrennt vom Meldestand (ops WRW-2, 21.10.2026) ───────────
#
# Vorher schrieb die Wache ihren Zustand nur bei zugestellter Meldung. Eine
# gedrosselte Meldung liess die Datei damit stehen, und c-webroot-write-watch-
# runs las die alte Schreibzeit — nach 30 Minuten meldete die Pruefung eine
# laufende Wache als nicht gelaufen. Je Fall eine Ausloese- und eine
# Schweigeprobe: der Meldestand haelt nur bei einer echten Fehlzustellung, das
# Lebenszeichen wird in jedem Lauf geschrieben.
INVARIANTE = pathlib.Path(__file__).resolve().parents[1] / "docs" / "invariants" / \
    "c-webroot-write-watch-runs.yaml"
ZUSTANDSPFAD = "/home/moltstack/selftest/webroot-write-watch.json"


def _pruefung(state_path) -> str:
    """Fuehrt die Abfrage der Invariante gegen diesen Zustand aus."""
    import yaml
    shell = yaml.safe_load(INVARIANTE.read_text())["abfrage"]["wert"]
    assert ZUSTANDSPFAD in shell, "Invariante liest einen anderen Pfad"
    p = subprocess.run(["bash", "-c", shell.replace(ZUSTANDSPFAD, str(state_path))],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout.strip()


def _lauf(tmp_path, monkeypatch, antwort: dict):
    """Ein Lauf mit einem Fremdschreibvorgang und dieser Antwort von notify."""
    root, ledger, state, bl = _env(tmp_path)
    from app import notify
    gesendet = []
    # Seit NOTIFY-1 (#912) geht die Meldung ueber send_telegram, und
    # letzter_grund() trennt die Drosselung von der Fehlzustellung. `antwort`
    # behaelt die drei Lagen: erfolg True, None (gedrosselt), False.
    erfolg = antwort["erfolg"]
    monkeypatch.setattr(notify, "send_telegram",
                        lambda text, channel: gesendet.append((channel, text))
                        or bool(erfolg))
    monkeypatch.setattr(notify, "letzter_grund",
                        lambda: {True: "", None: "gedrosselt"}.get(erfolg, "fehlgeschlagen"))
    state.write_text(json.dumps({"cursor": time.time() - 1,
                                 "zuletzt": "2026-01-01T00:00:00Z",
                                 "lauf": "2026-01-01T00:00:00Z"}))
    (root / "a.html").write_text("a")
    rc = w.main(["--root", str(root), "--ledger", str(ledger), "--state", str(state),
                 "--baseline", str(bl)])
    return rc, state, gesendet


def test_a_throttled_report_is_no_delivery_failure_and_keeps_the_check_green(
        tmp_path, monkeypatch):
    # erfolg None = gedrosselt: derselbe Befund lag vor weniger als einer
    # Stunde zugestellt vor, der Zaehler steht in jener Nachricht.
    rc, state, gesendet = _lauf(tmp_path, monkeypatch,
                                {"gesendet": [], "gedrosselt": [w.KENNUNG],
                                 "erfolg": None})
    assert rc == 0, "gedrosselte Meldung als Fehlschlag gezaehlt"
    neu = json.loads(state.read_text())
    assert neu["lauf"] != "2026-01-01T00:00:00Z", "kein Lebenszeichen geschrieben"
    assert _pruefung(state).startswith("frisch "), "Pruefung nach gedrosseltem Lauf nicht gruen"
    assert gesendet and gesendet[-1][0] == "alerts"
    assert "a.html" in gesendet[-1][1]


def test_a_failed_delivery_holds_the_report_state_and_still_shows_the_run(
        tmp_path, monkeypatch):
    # Ausloeseprobe zur Schweigeprobe darueber: dieselbe Lage, nur dass die
    # Zustellung wirklich scheitert.
    rc, state, _ = _lauf(tmp_path, monkeypatch,
                         {"gesendet": [], "gedrosselt": [], "erfolg": False})
    assert rc == 1, "Fehlzustellung nicht als Fehlschlag gemeldet"
    neu = json.loads(state.read_text())
    assert neu["zuletzt"] == "2026-01-01T00:00:00Z", "Meldestand trotz Fehlschlag vorgerueckt"
    assert neu["lauf"] != "2026-01-01T00:00:00Z", "kein Lebenszeichen geschrieben"
    assert _pruefung(state).startswith("frisch "), "Pruefung nach Fehlzustellung nicht gruen"


def test_a_delivered_report_advances_the_report_state(tmp_path, monkeypatch):
    rc, state, _ = _lauf(tmp_path, monkeypatch,
                         {"gesendet": [w.KENNUNG], "gedrosselt": [], "erfolg": True})
    assert rc == 0
    neu = json.loads(state.read_text())
    assert neu["zuletzt"] != "2026-01-01T00:00:00Z", "Meldestand nach Zustellung nicht vorgerueckt"
    assert neu["gemeldet"] == 1 and neu["lauf"] != "2026-01-01T00:00:00Z"


def test_a_quiet_run_writes_the_heartbeat_too(tmp_path, monkeypatch):
    root, ledger, state, bl = _env(tmp_path)
    from app import notify
    monkeypatch.setattr(notify, "send_telegram",
                        lambda text, channel: pytest.fail("stiller Lauf hat gemeldet"))
    state.write_text(json.dumps({"cursor": time.time(), "zuletzt": "2026-01-01T00:00:00Z",
                                 "lauf": "2026-01-01T00:00:00Z"}))
    assert w.main(["--root", str(root), "--ledger", str(ledger), "--state", str(state),
                   "--baseline", str(bl)]) == 0
    assert json.loads(state.read_text())["lauf"] != "2026-01-01T00:00:00Z"
    assert _pruefung(state).startswith("frisch ")


def test_the_check_is_not_green_without_a_heartbeat(tmp_path):
    # Schweigeprobe gegen die Pruefung selbst: ohne Lebenszeichen darf sie
    # nicht gruen sein, sonst traegt sie die Aussage nicht.
    alt = tmp_path / "alt.json"
    alt.write_text(json.dumps({"cursor": time.time(), "zuletzt": "2026-01-01T00:00:00Z"}))
    assert _pruefung(alt) == "UNREADABLE kein Lebenszeichen im Zustand"
    veraltet = tmp_path / "veraltet.json"
    veraltet.write_text(json.dumps({"cursor": 1, "lauf": "2026-01-01T00:00:00Z"}))
    assert _pruefung(veraltet).startswith("STEHT ")
    assert _pruefung(tmp_path / "weg.json").startswith("UNREADABLE kein Zustand ")


def test_the_first_run_writes_a_heartbeat(tmp_path):
    root, ledger, state, bl = _env(tmp_path)
    (root / "x.html").write_text("x")
    assert w.main(["--root", str(root), "--ledger", str(ledger), "--state", str(state),
                   "--baseline", str(bl)]) == 0
    assert _pruefung(state).startswith("frisch ")


# ── Rahmen aus dem deployten moltrust-web-Commit (ops WRW-RAHMEN, 24.10.2026) ──
#
# Vorher stand der Rahmen als fester Wert im Programm und wurde nach jeder
# Nav-Aenderung per PR nachgezogen (14.10. 907984…, 21.10. 6b1f0263…). Dazwischen
# meldete die Wache die Aenderung als fremden Eingriff.
NAV_ALT = '<nav><a href="/banking.html">Banking</a></nav>'
NAV_NEU = '<nav><a href="/finance.html">Finance</a></nav>'


def _index(nav: str) -> str:
    return LEER.replace("<body>", "<body>" + nav)


def _git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _web_repo(tmp_path, *staende: str):
    """Ein moltrust-web-Checkout mit einem Commit je Stand von blog/index.html."""
    repo = tmp_path / "moltrust-web"
    (repo / "blog").mkdir(parents=True)
    _git(repo, "init", "-q")
    shas = []
    for text in staende:
        (repo / "blog" / "index.html").write_text(text)
        _git(repo, "add", "blog/index.html")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
        shas.append(_git(repo, "rev-parse", "HEAD"))
    return repo, shas


def _deployed(tmp_path, sha: str, status: str = "ok"):
    f = tmp_path / "deployed-moltrust-web"
    f.write_text(f"{sha}\t2026-10-24T12:34:27Z\t{status}\n")
    return f


def _live(root, nav: str):
    blog = _blog(root, _index(nav), {"a.html": POST.format(t="A", n=1)})
    _generiert(blog)
    return blog / "index.html"


def test_the_frame_of_the_deployed_commit_stays_silent(tmp_path):
    root, *_ = _env(tmp_path)
    repo, (sha,) = _web_repo(tmp_path, _index(NAV_ALT))
    live = _live(root, NAV_ALT)
    assert w.pruefe_blog_index(str(live), deployed=str(_deployed(tmp_path, sha)),
                               checkout=str(repo)) is None


def test_a_frame_other_than_the_deployed_commit_names_the_commit(tmp_path):
    root, *_ = _env(tmp_path)
    repo, (sha,) = _web_repo(tmp_path, _index(NAV_NEU))
    live = _live(root, NAV_ALT)
    befund = w.pruefe_blog_index(str(live), deployed=str(_deployed(tmp_path, sha)),
                                 checkout=str(repo))
    assert befund and "Rahmen weicht von der Erwartung ab" in befund, befund
    assert f"aus moltrust-web {sha[:7]}" in befund, befund


def test_a_nav_change_that_reaches_the_index_through_the_deploy_stays_silent(tmp_path):
    """Kriterium 4: Repo und Live-Datei haben sich beide geaendert. Gegen den
    alten Commit gemessen waere es ein Befund, gegen den deployten ist es keiner."""
    root, *_ = _env(tmp_path)
    repo, (alt, neu) = _web_repo(tmp_path, _index(NAV_ALT), _index(NAV_NEU))
    live = _live(root, NAV_NEU)
    assert w.pruefe_blog_index(str(live), deployed=str(_deployed(tmp_path, neu)),
                               checkout=str(repo)) is None
    assert w.pruefe_blog_index(str(live), deployed=str(_deployed(tmp_path, alt)),
                               checkout=str(repo))


def test_a_rolled_back_deploy_is_read_from_the_first_column(tmp_path):
    root, *_ = _env(tmp_path)
    repo, (sha,) = _web_repo(tmp_path, _index(NAV_ALT))
    live = _live(root, NAV_ALT)
    f = _deployed(tmp_path, sha, status="rolled-back-from:" + "b" * 40)
    assert w.pruefe_blog_index(str(live), deployed=str(f), checkout=str(repo)) is None


@pytest.mark.parametrize("fall, erwartet", [
    ("keine Statusdatei", "nicht pruefbar: kein deployter moltrust-web-Commit"),
    ("kein Commit in der Statusdatei", "nicht pruefbar: kein Commit in"),
    ("Commit fehlt im Checkout", "nicht lesbar"),
    ("Marker fehlen am Commit", "nicht pruefbar: Marker der Beitragsliste fehlt in blog/index.html am Commit"),
])
def test_an_expectation_that_cannot_be_formed_is_a_finding(tmp_path, fall, erwartet):
    """Kriterium 3: je Fall ein Befund, kein Rueckfall auf einen festen Wert."""
    root, *_ = _env(tmp_path)
    live = _live(root, NAV_ALT)
    repo, (sha,) = _web_repo(tmp_path, _index(NAV_ALT) if fall != "Marker fehlen am Commit"
                             else "<html><body>kein Marker</body></html>")
    # Je Fall erst beim Zugriff geschrieben: alle drei schreiben dieselbe Datei.
    deployed = {
        "keine Statusdatei": lambda: tmp_path / "fehlt",
        "kein Commit in der Statusdatei": lambda: _deployed(tmp_path, "kaputt"),
        "Commit fehlt im Checkout": lambda: _deployed(tmp_path, "c" * 40),
        "Marker fehlen am Commit": lambda: _deployed(tmp_path, sha),
    }[fall]()
    befund = w.pruefe_blog_index(str(live), deployed=str(deployed), checkout=str(repo))
    assert befund and erwartet in befund, befund


def test_a_frame_finding_belongs_to_blog_gen_nav():
    """Kriterium 5: solange der Generator die Nav nicht aus dem Repo nimmt,
    besitzt BLOG-GEN-NAV die Abweichung, nicht die Meldetext-Arbeit WRW-3."""
    from app import befund_jobs
    text = ("blog/index.html — Rahmen weicht von der Erwartung ab (sha256 6b1f0263de2f…, "
            "erwartet 43a8664555f2… aus moltrust-web f3487ad)")
    assert befund_jobs.owner(text) == "BLOG-GEN-NAV"
    assert befund_jobs.owner("Blog-Index: Im ausgelieferten Blog-Index fehlt der Eintrag "
                             "„Finance“ (/finance.html), den moltrust-web 5323d32 hat.") \
        == "BLOG-GEN-NAV"
    assert befund_jobs.owner("Web-Root: 1 Schreibvorgang/-vorgaenge ausserhalb deploy.sh "
                             "in /var/www/html\n  x.html — …") == "WRW-3"


# ── Meldung in Worten, einmal je Befund (Lars 25.10., WRW2 und WRW3) ──────────
SPRITE_ALT = '<svg><symbol id="mt-molcheck"></symbol></svg>'
SPRITE_NEU = '<svg><symbol id="mt-moltproof"></symbol></svg>'
NAV_BEIDE = ('<nav><a href="/banking.html">Banking</a>'
             '<a href="/finance.html"><span>Finance</span></a></nav>')


def _live_web(tmp_path, live_nav, repo_nav):
    root, ledger, state, bl = _env(tmp_path)
    repo, (sha,) = _web_repo(tmp_path, _index(repo_nav))
    live = _live(root, live_nav)
    dep = _deployed(tmp_path, sha)
    pruefer = {"blog/index.html": lambda p: w.pruefe_blog_index(
        p, deployed=str(dep), checkout=str(repo))}
    return root, ledger, state, bl, live, repo, dep, sha, pruefer


_MELDUNG = w.meldung_erzeugt


def _wache(monkeypatch, root, ledger, state, bl, pruefer, dep, repo):
    monkeypatch.setattr(w, "WEB_DEPLOYED", str(dep))
    monkeypatch.setattr(w, "WEB_CHECKOUT", str(repo))
    monkeypatch.setattr(w, "meldung_erzeugt", lambda r, rel, b:
                        _MELDUNG(r, rel, b, deployed=str(dep), checkout=str(repo)))
    lines, neu = w.scan(str(root), str(ledger), str(state), str(bl), pruefer=pruefer)
    state.write_text(json.dumps(neu))
    # Nur der Befundblock: der Beitrag a.html ist selbst ein Schreibvorgang
    # und steht danach als eigene Meldung "Web-Root: …".
    return lines[:lines.index("")] if "" in lines else \
        [z for z in lines if not z.startswith(("Web-Root:", "  ", "Herkunft pruefen"))]


def test_the_message_names_what_is_missing_who_fixes_it_and_that_lars_does_nothing(
        tmp_path, monkeypatch):
    root, ledger, state, bl, live, repo, dep, sha, pruefer = _live_web(
        tmp_path, NAV_ALT + SPRITE_ALT, NAV_BEIDE + SPRITE_NEU)
    state.write_text(json.dumps({"cursor": time.time() - 60}))
    out = _wache(monkeypatch, root, ledger, state, bl, pruefer, dep, repo)
    assert out[:2] == ["Lars muss: nichts",
                       "Blog-Index: Im ausgelieferten Blog-Index fehlt der Eintrag „Finance“ "
                       f"(/finance.html), den moltrust-web {sha[:7]} hat."], out
    assert ("Auch die Symbole weichen ab: mt-moltproof fehlt, mt-molcheck steht nur in "
            "der ausgelieferten Datei.") in out, out
    assert "Wer behebt: Job BLOG-GEN-NAV." in out, out
    assert not any("sha256" in z or "Web-Root:" in z for z in out), out


def test_a_standing_finding_is_reported_once_and_again_only_when_it_changes(
        tmp_path, monkeypatch):
    root, ledger, state, bl, live, repo, dep, sha, pruefer = _live_web(
        tmp_path, NAV_ALT, NAV_BEIDE)
    state.write_text(json.dumps({"cursor": time.time() - 60}))
    assert _wache(monkeypatch, root, ledger, state, bl, pruefer, dep, repo), "erste Meldung"
    # Der Cron schreibt dieselbe Datei neu: neue ctime, derselbe Befund.
    for _ in range(3):
        state.write_text(json.dumps({**json.loads(state.read_text()),
                                     "cursor": time.time() - 60}))
        live.write_text(live.read_text())
        assert _wache(monkeypatch, root, ledger, state, bl, pruefer, dep, repo) == []
    # Der Befund aendert sich: ein Deploy bringt eine andere Nav ins Repo.
    _git(repo, "checkout", "-q", "-b", "neu")
    (repo / "blog" / "index.html").write_text(_index('<nav><a href="/x.html">X</a></nav>'))
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "y")
    dep.write_text(_git(repo, "rev-parse", "HEAD") + "\t2026-10-25T08:00:00Z\tok\n")
    state.write_text(json.dumps({**json.loads(state.read_text()), "cursor": time.time() - 60}))
    live.write_text(live.read_text())
    out = _wache(monkeypatch, root, ledger, state, bl, pruefer, dep, repo)
    assert out and "„X“ (/x.html)" in out[1], out


def test_a_finding_that_heals_and_returns_is_reported_again(tmp_path, monkeypatch):
    root, ledger, state, bl, live, repo, dep, sha, pruefer = _live_web(
        tmp_path, NAV_ALT, NAV_BEIDE)
    state.write_text(json.dumps({"cursor": time.time() - 60}))
    assert _wache(monkeypatch, root, ledger, state, bl, pruefer, dep, repo)
    falsch = live.read_text()
    live.write_text(falsch.replace(NAV_ALT, NAV_BEIDE))       # geheilt
    state.write_text(json.dumps({**json.loads(state.read_text()), "cursor": time.time() - 60}))
    assert _wache(monkeypatch, root, ledger, state, bl, pruefer, dep, repo) == []
    assert json.loads(state.read_text())["befunde"] == {}
    live.write_text(falsch)                                   # wieder da
    state.write_text(json.dumps({**json.loads(state.read_text()), "cursor": time.time() - 60}))
    assert _wache(monkeypatch, root, ledger, state, bl, pruefer, dep, repo)
