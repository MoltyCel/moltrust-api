"""Web-Root gegen Repo (scripts/webroot_drift.py, 2026-10-08)."""
import hashlib
import json
import subprocess

from scripts import webroot_drift as w


def _setup(tmp_path, committed: dict, served: dict, baseline: dict):
    repo = tmp_path / "repo"; repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True,
                                    capture_output=True, text=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    for p, b in committed.items():
        (repo / p).parent.mkdir(parents=True, exist_ok=True); (repo / p).write_bytes(b)
    git("add", "-A"); git("commit", "-q", "-m", "c")
    sha = git("rev-parse", "HEAD").stdout.strip()
    root = tmp_path / "www"; root.mkdir()
    for p, b in served.items():
        (root / p).parent.mkdir(parents=True, exist_ok=True); (root / p).write_bytes(b)
    dep = tmp_path / "deployed"; dep.write_text(f"{sha}\t2026-10-08T00:00:00Z\tok\n")
    bl = tmp_path / "baseline.json"; bl.write_text(json.dumps(baseline))
    return w.lines(str(root), str(repo), str(dep), str(bl))


BASE = {"generated": {"blog/index.html": "cron"}, "untracked": ["old.pdf"],
        "drift": {"card.json": hashlib.sha256(b"live").hexdigest()},
        # Wie die echte Baseline seit dem 12.10.2026: der Ausgangsstand ist
        # begruendet. Fehlt der Block, kostet er eine eigene Zeile — das ist
        # dann der Befund und steht in test_sammelmeldung.py.
        "begruendung": {"ohne_commit": [{"gruppe": "Altbestand", "zahl": 1,
                                         "grund": "Fixtur"}],
                        "abweichend": [{"pfad": "card.json",
                                        "grund": "Fixtur"}]}}


def test_quiet_day_is_one_line_with_the_baseline_counted(tmp_path):
    out = _setup(tmp_path, {"index.html": b"a", "card.json": b"repo", "blog/index.html": b"x"},
                 {"index.html": b"a", "card.json": b"live", "old.pdf": b"p",
                  "blog/index.html": b"regenerated"}, BASE)
    # Eine Zeile bleibt eine Zeile. Die Begruendung steht seit dem
    # 12.10.2026 in der Kopfzeile als Verweis und nicht als eigene Zeile —
    # ein statischer Umstand, der zweimal taeglich zwei Zeilen kostet, wird
    # nach einer Woche nicht mehr gelesen. Diese Probe hat genau das
    # abgewiesen, und sie hatte recht.
    assert len(out) == 1, out
    assert "0 neu ohne Commit, 0 neu abweichend" in out[0]
    assert "Ausgangsstand 1 ohne Commit, 1 abweichend" in out[0]
    # Ohne begruendung-Block steht kein Verweis da — BASE traegt keinen.
    assert "begruendet in config/webroot_baseline.json" in out[0]


def test_a_file_only_in_the_web_root_is_named(tmp_path):
    out = _setup(tmp_path, {"index.html": b"a"},
                 {"index.html": b"a", "about.html.bak-1": b"old"}, BASE)
    assert "1 neu ohne Commit" in out[0]
    assert any(o.strip().startswith("ohne Commit: about.html.bak-1") for o in out)


def test_a_hand_edit_of_a_served_file_is_named(tmp_path):
    out = _setup(tmp_path, {"index.html": b"repo"}, {"index.html": b"edited on the server"}, BASE)
    assert "1 neu abweichend" in out[0]
    assert any(o.strip().startswith("weicht ab: index.html") for o in out)


def test_a_baselined_drift_that_changes_again_is_new(tmp_path):
    out = _setup(tmp_path, {"card.json": b"repo"}, {"card.json": b"changed once more"}, BASE)
    assert "1 neu abweichend" in out[0]


def test_generated_files_are_ignored(tmp_path):
    out = _setup(tmp_path, {"blog/index.html": b"x"}, {"blog/index.html": b"y"}, BASE)
    assert "0 neu ohne Commit, 0 neu abweichend" in out[0]


def test_an_unreadable_setup_is_a_line_not_silence(tmp_path):
    out = w.lines(str(tmp_path), str(tmp_path / "nope"), str(tmp_path / "nodeployed"),
                  str(tmp_path / "nobaseline"))
    assert len(out) == 1 and "nicht moeglich" in out[0]


def test_the_committed_baseline_parses():
    d = json.load(open(w.BASELINE, encoding="utf-8"))
    assert set(d["generated"]) == {"registry-proof.json", "blog/index.html"}
    # 32 seit TA-SPLIT (27.10.): trouvart/index.html bis zum Rueckbau am 04.11.
    assert len(d["untracked"]) == 32 and not any(".bak" in p for p in d["untracked"])
    # 5 am 08.10.; registry-proof.py ging am 14.10. ins Repo (ops W1),
    # docs/caep.html wird seit 14.10. ausgeliefert (ops W2).
    # .well-known/agent-card.json und MolTrust_Protocol_Whitepaper_v0.8.pdf
    # sind Blob-gleich mit dem deployten Commit (ops M1). Ein Ausgangsstand,
    # der Abweichungen auffuehrt, die es nicht mehr gibt, stellt kuenftige still.
    assert set(d["drift"]) == {".gitignore"}
    # Und jede verbliebene ist begruendet.
    assert {a["pfad"] for a in d["begruendung"]["abweichend"]} == set(d["drift"])
    assert d["rendered"] == {".well-known/x402.json": ".well-known/x402.json.tpl"}


# ── gerenderte Dateien (ops W1, 14.10.2026) ──
# .well-known/x402.json ist kein Commit-Blob: deploy.sh rendert es aus der
# Vorlage in den Checkout und installiert es von dort. Bis W1 zaehlte es als
# "ohne Commit" (Befund BR3).

RBASE = {**BASE, "rendered": {".well-known/x402.json": ".well-known/x402.json.tpl"}}


def _setup_rendered(tmp_path, rendered_in_checkout: bytes, served: bytes, with_tpl=True):
    committed = {"index.html": b"a"}
    if with_tpl:
        committed[".well-known/x402.json.tpl"] = b'{"payTo": "{{BASE_ADDR}}"}'
    repo = tmp_path / "repo"
    out_holder = {}
    orig = w.tracked_blobs

    def tracked(r, sha):
        (repo / ".well-known").mkdir(parents=True, exist_ok=True)
        (repo / ".well-known" / "x402.json").write_bytes(rendered_in_checkout)
        return orig(r, sha)
    w.tracked_blobs = tracked
    try:
        out_holder["out"] = _setup(tmp_path, committed,
                                   {"index.html": b"a", ".well-known/x402.json": served}, RBASE)
    finally:
        w.tracked_blobs = orig
    return out_holder["out"]


def test_a_rendered_file_equal_to_the_deploy_output_is_quiet(tmp_path):
    out = _setup_rendered(tmp_path, b'{"payTo": "0xabc"}', b'{"payTo": "0xabc"}')
    assert "0 neu ohne Commit, 0 neu abweichend" in out[0], out


def test_a_rendered_file_that_differs_from_the_deploy_output_is_named(tmp_path):
    out = _setup_rendered(tmp_path, b'{"payTo": "0xabc"}', b'{"payTo": "0xdef"}')
    assert "1 neu abweichend" in out[0], out
    assert any(o.strip().startswith("weicht ab: .well-known/x402.json") for o in out)


def test_a_rendered_file_without_its_template_in_the_commit_is_named(tmp_path):
    out = _setup_rendered(tmp_path, b'{"payTo": "0xabc"}', b'{"payTo": "0xabc"}', with_tpl=False)
    assert "1 neu abweichend" in out[0], out
