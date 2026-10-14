"""Eine Quelle fuer den Verifier (scripts/check_verifier_one_source.py, ops W4).

Der Pruefer liegt zweimal vor — api scripts/registry_proof.py und web
registry-proof.py — und nach D1 lagen beide siebzehn Tage auseinander, ohne
dass eine Pruefung es gesehen haette. Je Fall eine Ausloese- und eine
Schweigeprobe:

- gleiche Bytes schweigen, verschiedene melden mit beiden Hashes;
- verglichen wird gegen den deployten Commit, nicht gegen den Arbeitsbaum: eine
  Aenderung im Checkout, die kein Deploy gesehen hat, faerbt nichts gruen;
- was nicht gemessen werden konnte, heisst UNREADABLE und nicht gruen, und
  scripts/selftest.py liest das auch so.
"""
import json
import pathlib
import subprocess

import pytest
import yaml

from scripts import check_verifier_one_source as c
from scripts import selftest

INVARIANTE = (pathlib.Path(__file__).resolve().parents[1]
              / "docs" / "invariants" / "c-verifier-one-source.yaml")
ALT = b"#!/usr/bin/env python3\nprint('verifier, alte Fassung')\n"
NEU = ALT.replace(b"alte", b"neue")


def _repo(tmp_path: pathlib.Path, commit: bytes, arbeitsbaum: bytes | None = None):
    """Ein Repo mit <commit> im Commit und optional anderen Bytes im Baum."""
    repo = tmp_path / "moltstack"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "registry_proof.py").write_bytes(commit)
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True,  # noqa: E731
                                    capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "Test")
    run("add", "-A")
    run("commit", "-q", "-m", "verifier")
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    if arbeitsbaum is not None:
        (repo / "scripts" / "registry_proof.py").write_bytes(arbeitsbaum)
    deployed = tmp_path / "deployed"
    deployed.write_text(f"{sha}\t2026-10-14T17:00:00Z\tok\n")
    return repo, deployed, sha


def _lauf(tmp_path, repo, deployed, live: bytes, monkeypatch, capsys):
    monkeypatch.setattr(c, "live_fassung", lambda url, timeout=c.TIMEOUT: live)
    rc = c.main(["--repo", str(repo), "--deployed", str(deployed),
                 "--url", "https://moltrust.ch/registry-proof.py"])
    return rc, capsys.readouterr().out.strip()


def test_the_same_bytes_stay_silent(tmp_path, monkeypatch, capsys):
    repo, deployed, sha = _repo(tmp_path, ALT)
    rc, out = _lauf(tmp_path, repo, deployed, ALT, monkeypatch, capsys)
    assert rc == 0, out
    assert out.startswith("gleich ") and sha[:7] in out


def test_a_served_verifier_that_differs_is_reported_with_both_hashes(
        tmp_path, monkeypatch, capsys):
    repo, deployed, sha = _repo(tmp_path, ALT)
    rc, out = _lauf(tmp_path, repo, deployed, NEU, monkeypatch, capsys)
    assert rc == 1, out
    assert out.startswith("ABWEICHUNG")
    assert c._sha(NEU)[:12] in out and c._sha(ALT)[:12] in out
    assert f"{len(NEU)} B" in out and f"{len(ALT)} B" in out


def test_the_comparison_is_against_the_deployed_commit_not_the_worktree(
        tmp_path, monkeypatch, capsys):
    """Ausloeseprobe: der Checkout traegt die ausgelieferten Bytes, der
    deployte Commit nicht. Gegen den Arbeitsbaum waere das gruen."""
    repo, deployed, sha = _repo(tmp_path, ALT, arbeitsbaum=NEU)
    assert (repo / "scripts" / "registry_proof.py").read_bytes() == NEU
    rc, out = _lauf(tmp_path, repo, deployed, NEU, monkeypatch, capsys)
    assert rc == 1 and out.startswith("ABWEICHUNG"), out
    # Schweigeprobe derselben Lage: ausgeliefert ist der deployte Commit.
    rc, out = _lauf(tmp_path, repo, deployed, ALT, monkeypatch, capsys)
    assert rc == 0 and out.startswith("gleich "), out


@pytest.mark.parametrize("was", ["fehlt", "kein_sha", "pfad_nicht_im_commit"])
def test_what_could_not_be_measured_is_unreadable(was, tmp_path, monkeypatch, capsys):
    repo, deployed, sha = _repo(tmp_path, ALT)
    argv = ["--repo", str(repo), "--deployed", str(deployed)]
    if was == "fehlt":
        deployed.unlink()
    elif was == "kein_sha":
        deployed.write_text("kein-commit\t2026-10-14T17:00:00Z\tok\n")
    else:
        argv += ["--pfad", "scripts/gibtsnicht.py"]
    monkeypatch.setattr(c, "live_fassung", lambda url, timeout=c.TIMEOUT: ALT)
    rc = c.main(argv)
    out = capsys.readouterr().out.strip()
    assert rc == 2 and out.startswith("UNREADABLE"), out
    # Und der Runner glaubt es: kein Urteil auf einer Nichtmessung.
    assert selftest.unreadable(out + "\n2") is not None


def test_a_live_answer_that_is_not_200_is_unreadable(tmp_path, monkeypatch, capsys):
    repo, deployed, sha = _repo(tmp_path, ALT)

    class Antwort:
        status_code = 503
        content = b"<html>maintenance</html>"

    monkeypatch.setattr(c.httpx, "get", lambda url, **kw: Antwort())
    rc = c.main(["--repo", str(repo), "--deployed", str(deployed)])
    out = capsys.readouterr().out.strip()
    assert rc == 2 and "HTTP 503" in out, out


def test_the_live_fetch_does_not_follow_a_redirect_in_silence(tmp_path, monkeypatch):
    """Eine Umleitung auf eine andere Datei waere ein Vergleich mit dem
    falschen Gegenstand. Sie faellt als HTTP 30x in die Nichtmessung."""
    gesehen = {}

    class Antwort:
        status_code = 301
        content = b""

    def fake_get(url, **kw):
        gesehen.update(kw)
        return Antwort()

    monkeypatch.setattr(c.httpx, "get", fake_get)
    with pytest.raises(c.NichtPruefbar) as exc:
        c.live_fassung("https://moltrust.ch/registry-proof.py")
    assert "HTTP 301" in str(exc.value)
    assert gesehen["follow_redirects"] is False


def test_the_invariant_runs_this_check_daily_and_expects_zero():
    inv = yaml.safe_load(INVARIANTE.read_text())
    assert inv["id"] == "c-verifier-one-source" and inv["tempo"] == "daily"
    assert inv["abfrage"]["art"] == "shell"
    assert "scripts/check_verifier_one_source.py" in inv["abfrage"]["wert"]
    assert inv["erwartung"] == {"operator": "eq", "wert": 0}
    assert inv["schweregrad"] == "fail" and inv["autofix"] == "none"
    assert "D1" in inv["herkunft"]
    # Der Katalog liest die Datei, und der Runner urteilt ueber die letzte
    # Zeile: 0 ist gruen, 1 und 2 sind es nicht.
    assert any(d.get("id") == "c-verifier-one-source" for d in selftest.load_catalogue())
    assert selftest.judge(inv, "gleich 41e649f1…\n0")[0] is True
    assert selftest.judge(inv, "ABWEICHUNG …\n1")[0] is False
    assert selftest.judge(inv, "UNREADABLE …\n2")[0] is False


def test_the_check_names_both_sides_in_its_docstring():
    """Herkunft im Quelltext, damit in sechs Monaten niemand die Pruefung
    entfernt, deren Grund er nicht kennt."""
    text = pathlib.Path(c.__file__).read_text()
    for wort in ("registry_proof.py", "registry-proof.py", "D1", "deployten"):
        assert wort in text.split('"""')[1], wort


def test_the_state_is_read_from_the_one_file_the_deploy_writes(tmp_path):
    repo, deployed, sha = _repo(tmp_path, ALT)
    assert c.deployter_commit(str(deployed)) == sha
    # Dieselbe Form wie ~/.deployed/<repo>: SHA, Tabulator, Zeitstempel, ok.
    felder = deployed.read_text().strip().split("\t")
    assert len(felder) == 3 and felder[0] == sha
    assert json.dumps(felder[2]) == '"ok"'
