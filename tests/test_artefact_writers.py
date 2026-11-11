"""Schreiber je oeffentlichem Artefakt (scripts/check_artefact_writers.py, ops E1).

Am 15.10. meldete e-one-writer-per-artefact einen Befund: scripts/webroot_write_watch.py
nennt registry-proof.json im Code und schreibt Dateien. Die Wache baut das Dokument
in einem Sandkasten neu und vergleicht, sie schreibt das Artefakt nicht. Jetzt steht
sie als erklaerter Pruefer in INSPECTORS. Je Fall eine Ausloese- und eine
Schweigeprobe; die Tests am echten Baum halten die Liste gegen den Repo-Stand.
"""
import contextlib
import io
import pathlib

from scripts import check_artefact_writers as c

ROOT = pathlib.Path(__file__).resolve().parents[1]
ART = "registry-proof.json"


def _run(**kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = c.main(**kw)
    return rc, err.getvalue()


def _tree(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


WRITER = f'import json\njson.dump({{}}, open("/var/www/{ART}", "w"))\n'
INSPECTOR = (f'import json\nPRUEFER = {{"{ART}": None}}\n'
             'json.dump({}, open("state.json", "w"))\n')


# Echter Baum

def test_real_tree_is_silent():
    rc, err = _run()
    assert (rc, err) == (0, "")


def test_real_tree_without_inspector_list_reports_the_watch():
    rc, err = _run(inspectors={})
    assert rc == 1
    assert f"UNDECLARED WRITER: scripts/webroot_write_watch.py names {ART}" in err
    assert err.count("UNDECLARED WRITER") == 1


def test_declared_inspector_does_not_write_the_artefact():
    # Die Begruendung der Liste als Probe: die Wache nennt das Artefakt nur als
    # Schluessel ihrer Pruefertabelle und schreibt es ueber den erklaerten
    # Schreiber in einen Sandkasten.
    src = (ROOT / "scripts/webroot_write_watch.py").read_text()
    assert f'"{ART}": pruefe_registry_proof' in src
    assert "tempfile.mkdtemp" in src and '"--out", out' in src


# Kleiner Baum

def test_declared_inspector_is_silent(tmp_path):
    root = _tree(tmp_path, {"scripts/w.py": WRITER, "scripts/i.py": INSPECTOR})
    rc, err = _run(root=root, writers={ART: ["scripts/w.py"]},
                   inspectors={ART: ["scripts/i.py"]})
    assert (rc, err) == (0, "")


def test_undeclared_inspector_fires(tmp_path):
    root = _tree(tmp_path, {"scripts/w.py": WRITER, "scripts/i.py": INSPECTOR})
    rc, err = _run(root=root, writers={ART: ["scripts/w.py"]}, inspectors={})
    assert rc == 1 and "UNDECLARED WRITER: scripts/i.py" in err


def test_new_writer_still_fires_next_to_an_inspector(tmp_path):
    root = _tree(tmp_path, {"scripts/w.py": WRITER, "scripts/i.py": INSPECTOR,
                            "app/neu.py": WRITER})
    rc, err = _run(root=root, writers={ART: ["scripts/w.py"]},
                   inspectors={ART: ["scripts/i.py"]})
    assert rc == 1
    assert err.strip() == f"UNDECLARED WRITER: app/neu.py names {ART} in code and writes files"


def test_missing_inspector_fires(tmp_path):
    root = _tree(tmp_path, {"scripts/w.py": WRITER})
    rc, err = _run(root=root, writers={ART: ["scripts/w.py"]},
                   inspectors={ART: ["scripts/weg.py"]})
    assert rc == 1 and f"DECLARED INSPECTOR MISSING: scripts/weg.py for {ART}" in err


def test_prose_only_stays_silent(tmp_path):
    root = _tree(tmp_path, {"scripts/w.py": WRITER,
                            "scripts/p.py": f'"""ueber {ART}"""\nopen("x", "w")\n'})
    rc, err = _run(root=root, writers={ART: ["scripts/w.py"]}, inspectors={})
    assert (rc, err) == (0, "")
