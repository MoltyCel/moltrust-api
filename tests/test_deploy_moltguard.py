"""moltguard geht über ops/deploy wie die anderen zwei Dienste (ops D3).

Vorher hatte moltguard keinen Deploy-Weg: die 402-Einladung ging am 16.10.2026
von Hand live, `npm run build` über SSH und ein Neustart über die
NOPASSWD-Regel. Ein Handgriff am Server ist genau das, was der Deploy
abschaffen soll — und solange er nötig ist, steht kein Beleg fest, welcher
Commit läuft.

Jede Regel wird in beide Richtungen geprüft. Ein Dateilisten-Filter, der nur
schweigt, ist von einem kaputten nicht zu unterscheiden; einer, der immer
auslöst, baut und startet den Dienst bei jeder Textänderung neu.

Die Funktionen laufen echt: deploy.sh wird mit DEPLOY_SH_FUNCTIONS_ONLY=1
geladen, so wie tests/test_deploy_x402_render.py es tut, und gegen
Wegwerf-Git-Repos gefahren.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import stat
import subprocess

import pytest

from scripts import checkout_dirty, selftest_throttle

WURZEL = pathlib.Path(__file__).resolve().parent.parent
DEPLOY_SH = WURZEL / "ops" / "deploy" / "deploy.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git fehlt")

# Die Dateien, die moltguard im Repo hat, in den Klassen, auf die es ankommt.
BASIS = ("src/index.ts", "src/routes/health.ts", "public/index.html",
         "package.json", "package-lock.json", "tsconfig.json",
         "data/trust-proofs/latest.json", "jwks.json",
         "migrations/001_init.sql",
         "README.md", "DECISIONS.md", "docs/SPEC.md",
         ".github/workflows/deploy.yml")


def sh(script: str, env: dict | None = None, cwd: pathlib.Path | None = None):
    """Eine Zeile Bash mit den geladenen deploy.sh-Funktionen."""
    full = {"DEPLOY_SH_FUNCTIONS_ONLY": "1", "PATH": os.environ["PATH"], **(env or {})}
    return subprocess.run(
        ["bash", "-c", f'source "{DEPLOY_SH}"; LOG=/dev/null; log() {{ echo "$1" >&2; }}; {script}'],
        capture_output=True, text=True, env=full, cwd=str(cwd) if cwd else None)


def git(repo: pathlib.Path, *a: str):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t",
                           "-c", "user.email=t@t", *a],
                          capture_output=True, text=True, check=True)


@pytest.fixture()
def repo(tmp_path):
    """Ein Wegwerf-Checkout mit einem Commit, der alle Klassen enthält."""
    r = tmp_path / "moltguard"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    for p in BASIS:
        f = r / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


def aenderung(r: pathlib.Path, pfad: str) -> tuple[str, str]:
    """Ein Commit, der genau einen Pfad anfasst. Gibt (vorher, nachher)."""
    vorher = git(r, "rev-parse", "HEAD").stdout.strip()
    f = r / pfad
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("y\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", f"touch {pfad}")
    return vorher, git(r, "rev-parse", "HEAD").stdout.strip()


# --------------------------------------------------------------- Dateiliste

@pytest.mark.parametrize("pfad", ["src/index.ts", "src/routes/health.ts",
                                  "public/index.html", "package.json",
                                  "package-lock.json", "tsconfig.json"])
def test_was_den_laufenden_dienst_erreicht_loest_build_und_neustart_aus(repo, pfad):
    a, b = aenderung(repo, pfad)
    r = sh(f'moltguard_files {a} {b}', cwd=repo)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == pfad


@pytest.mark.parametrize("pfad", ["README.md", "DECISIONS.md", "docs/SPEC.md",
                                  ".github/workflows/deploy.yml",
                                  "data/trust-proofs/latest.json",
                                  "migrations/001_init.sql", "drafts/neu.md"])
def test_was_den_dienst_nicht_erreicht_baut_und_startet_nichts(repo, pfad):
    a, b = aenderung(repo, pfad)
    r = sh(f'moltguard_files {a} {b}', cwd=repo)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


def test_eine_neue_quelldatei_zaehlt_mit(repo):
    a, b = aenderung(repo, "src/routes/board.ts")
    assert sh(f'moltguard_files {a} {b}', cwd=repo).stdout.strip() == "src/routes/board.ts"


# --------------------------------------------------------- npm ci, und wann

@pytest.mark.parametrize("pfad", ["package.json", "package-lock.json"])
def test_npm_ci_laeuft_wenn_die_abhaengigkeiten_sich_bewegt_haben(repo, pfad):
    a, b = aenderung(repo, pfad)
    assert sh(f'moltguard_deps_changed {a} {b}', cwd=repo).returncode == 0


@pytest.mark.parametrize("pfad", ["src/index.ts", "tsconfig.json", "README.md"])
def test_npm_ci_laeuft_nicht_fuer_eine_quelldatei(repo, pfad):
    a, b = aenderung(repo, pfad)
    assert sh(f'moltguard_deps_changed {a} {b}', cwd=repo).returncode == 1


# ------------------------------------------------------------------- Port

def test_der_port_kommt_aus_der_env_des_dienstes(tmp_path):
    f = tmp_path / ".env"
    f.write_text("DATABASE_URL=postgres://x\nPORT=8402\nOTHER=1\n")
    r = sh(f'moltguard_port "{f}"')
    assert r.returncode == 0 and r.stdout == "8402"


@pytest.mark.parametrize("zeile", ['export PORT="8402"', "PORT='8402'", "PORT= 8402 "])
def test_der_port_wird_auch_in_den_anderen_schreibweisen_gelesen(tmp_path, zeile):
    f = tmp_path / ".env"
    f.write_text(zeile + "\n")
    assert sh(f'moltguard_port "{f}"').stdout == "8402"


@pytest.mark.parametrize("inhalt", ["", "PORTX=8402\n", "# PORT=8402\n", "DATABASE_URL=x\n"])
def test_ohne_port_zeile_gilt_der_vorgabewert_aus_der_config(tmp_path, inhalt):
    f = tmp_path / ".env"
    f.write_text(inhalt)
    r = sh(f'moltguard_port "{f}"')
    assert r.returncode == 0 and r.stdout == "3000"


def test_ohne_env_datei_gilt_der_vorgabewert(tmp_path):
    r = sh(f'moltguard_port "{tmp_path / "fehlt"}"')
    assert r.returncode == 0 and r.stdout == "3000"


@pytest.mark.parametrize("zeile", ["PORT=achtzig", "PORT=84x2", "PORT=844444"])
def test_ein_port_der_keine_zahl_ist_bricht_ab_statt_zu_raten(tmp_path, zeile):
    f = tmp_path / ".env"
    f.write_text(zeile + "\n")
    r = sh(f'moltguard_port "{f}"')
    assert r.returncode == 1 and r.stdout == ""


# ----------------------------------------------- Deploy-Schloss am Checkout

def test_eine_fremde_aenderung_im_checkout_haelt_den_deploy_an(repo):
    (repo / "src" / "index.ts").write_text("von Hand\n")
    r = sh(f'dirty_tracked "{repo}"')
    assert r.returncode == 0 and "src/index.ts" in r.stdout


def test_was_der_dienst_selbst_schreibt_haelt_den_deploy_nicht_an(repo):
    (repo / "data" / "trust-proofs" / "latest.json").write_text("neuer Beweis\n")
    eigen = sh('printf %s "$MOLTGUARD_OWN_WRITES"').stdout
    r = sh(f'dirty_tracked "{repo}" "{eigen}"')
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_ohne_die_liste_zaehlt_dieselbe_datei_weiter_mit(repo):
    """moltrust-api und moltrust-web uebergeben nichts — fuer sie bleibt alles,
    wie es war."""
    (repo / "data" / "trust-proofs" / "latest.json").write_text("neuer Beweis\n")
    assert "data/trust-proofs/latest.json" in sh(f'dirty_tracked "{repo}"').stdout


def test_eine_fremde_aenderung_neben_einer_eigenen_wird_trotzdem_gemeldet(repo):
    (repo / "data" / "trust-proofs" / "latest.json").write_text("neuer Beweis\n")
    (repo / "package.json").write_text("von Hand\n")
    eigen = sh('printf %s "$MOLTGUARD_OWN_WRITES"').stdout
    out = sh(f'dirty_tracked "{repo}" "{eigen}"').stdout
    assert "package.json" in out and "trust-proofs" not in out


# ------------------------------------------------- Schreibschutz im Checkout

def _schreibbar(p: pathlib.Path) -> bool:
    return bool(os.lstat(p).st_mode & stat.S_IWUSR)


def test_der_dienst_behaelt_schreibrecht_auf_seinen_eigenen_dateien(repo):
    """Gemessen an jwks.json, nicht an den Beweisen: `^data/` steht schon in
    PROTECT_EXCLUDE_RE und waere auch ohne die Liste schreibbar. Das Argument
    ist, was die Entscheidung am Dienst festmacht statt an einer Ausnahme, die
    fuer moltrust-api gedacht ist."""
    eigen = sh('printf %s "$MOLTGUARD_OWN_WRITES"').stdout
    r = sh(f'protect_checkout "{repo}" "{eigen}"')
    assert r.returncode == 0, r.stderr
    assert _schreibbar(repo / "jwks.json")
    assert _schreibbar(repo / "data" / "trust-proofs" / "latest.json")
    assert not _schreibbar(repo / "src" / "index.ts")
    assert not _schreibbar(repo / "package.json")


def test_ohne_die_liste_wird_auch_die_eigene_datei_schreibgeschuetzt(repo):
    assert sh(f'protect_checkout "{repo}"').returncode == 0
    assert not _schreibbar(repo / "jwks.json")


# ------------------------------------------------------- Aufruf und Argumente

def deploy(*args: str):
    return subprocess.run(["bash", str(DEPLOY_SH), *args],
                          capture_output=True, text=True,
                          env={"PATH": os.environ["PATH"]})


def test_moltguard_ist_ein_bekannter_dienst():
    r = deploy("moltguard", "keine-sha")
    assert r.returncode == 2
    assert "sha must be 40 lowercase hex" in r.stderr   # am Dienstnamen vorbei
    assert "unknown repo" not in r.stderr


def test_ein_unbekannter_dienst_wird_weiter_abgewiesen():
    r = deploy("moltbogus", "keine-sha")
    assert r.returncode == 2 and "unknown repo: moltbogus" in r.stderr


def test_die_gebrauchsanweisung_nennt_moltguard():
    r = deploy("moltguard")
    assert r.returncode == 2 and "moltguard" in r.stderr and "usage" in r.stderr


@pytest.mark.parametrize("wort", ["diagnose", "supervise", "superheal"])
def test_die_lesewege_bleiben_bei_moltrust_api(wort):
    r = deploy("moltguard", wort)
    assert r.returncode == 2 and "moltrust-api only" in r.stderr


# --------------------------------------------- Protokoll und Sammelmeldung

def test_die_sammelmeldung_zaehlt_einen_moltguard_deploy(tmp_path):
    """record_deploy schreibt den Dienstnamen, und die Tageszeile nennt ihn.
    Gezaehlt wird nach dem Feld, nicht nach einer Liste von Diensten — ein
    neuer Dienst steht ohne weitere Aenderung in der Meldung."""
    log = tmp_path / "deploy-log.jsonl"
    r = sh(f'REPO=moltguard SHA=abc1234 DEPLOY_LOG="{log}" '
           f'record_deploy ok abc1234567890123456789012345678901234567')
    assert r.returncode == 0, r.stderr
    zeile = log.read_text()
    assert '"dienst":"moltguard"' in zeile and '"status":"ok"' in zeile

    now = selftest_throttle.dt.datetime(2026, 10, 17, 9, 0,
                                        tzinfo=selftest_throttle.dt.timezone.utc)
    log.write_text('{"ts":"2026-10-17T08:10:00Z","dienst":"moltguard",'
                   '"sha":"abc1234567890","status":"ok"}\n')
    zeilen = selftest_throttle.deploy_lines(now, str(log))
    assert "moltguard 1" in zeilen[0] and "1 ok" in zeilen[0]


def test_ein_gescheiterter_moltguard_deploy_steht_namentlich_in_der_meldung(tmp_path):
    log = tmp_path / "deploy-log.jsonl"
    log.write_text('{"ts":"2026-10-17T08:10:00Z","dienst":"moltguard",'
                   '"sha":"abc1234567890","status":"failed"}\n')
    now = selftest_throttle.dt.datetime(2026, 10, 17, 9, 0,
                                        tzinfo=selftest_throttle.dt.timezone.utc)
    zeilen = selftest_throttle.deploy_lines(now, str(log))
    assert "1 gescheitert" in zeilen[0]
    assert any("failed: moltguard" in z for z in zeilen)


# --------------------------------------------------------- c-checkout-clean

def test_die_stuendliche_pruefung_deckt_den_moltguard_checkout():
    assert checkout_dirty.CHECKOUTS["moltguard"] == "~/moltguard"


def test_der_checkout_wird_als_schmutzig_gemeldet(tmp_path, repo):
    (repo / "src" / "index.ts").write_text("von Hand\n")
    zeilen = checkout_dirty.report(checkouts={"moltguard": str(repo)},
                                   seen_path=str(tmp_path / "seen.json"))
    assert any("moltguard" in z and "src/index.ts" in z for z in zeilen)


@pytest.mark.parametrize("pfad", ["data/trust-proofs/latest.json",
                                  "data/trust-proofs/2026-10-17T08-00-00Z.json",
                                  "jwks.json", "jwks.json.new"])
def test_was_der_dienst_selbst_schreibt_ist_kein_handgriff(tmp_path, repo, pfad):
    f = repo / pfad
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("vom Dienst\n")
    zeilen = checkout_dirty.report(checkouts={"moltguard": str(repo)},
                                   seen_path=str(tmp_path / "seen.json"))
    assert zeilen == ["CHECKOUT SAUBER"], zeilen


def test_in_den_anderen_checkouts_gilt_die_ausnahme_nicht(tmp_path, repo):
    """Die Liste haengt am Dienst, nicht am Pfad."""
    (repo / "data" / "trust-proofs" / "latest.json").write_text("vom Dienst\n")
    zeilen = checkout_dirty.report(checkouts={"moltrust-api": str(repo)},
                                   seen_path=str(tmp_path / "seen.json"))
    assert any("trust-proofs" in z for z in zeilen)
