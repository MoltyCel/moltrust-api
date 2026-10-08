"""Das Schloss macht sichtbar, wer haelt — und laesst sich nicht wegsehen.

Am 07.10.2026 arbeiteten zwei Sitzungen gleichzeitig an scripts/selftest.py.
Der Deploy lehnte ab, der Server blieb drei Commits zurueck, und neun Minuten
lang wusste niemand, von wem die Aenderung war. Genau diese Frage beantwortet
das Schloss.

Beide Richtungen je Regel: ein Schloss, das immer nachgibt, ist keines, und
eines, das nie nachgibt, blockiert nach dem ersten Absturz fuer immer.
"""
import datetime as dt
import os
import pathlib

import pytest

from scripts import writelock as wl

ROOT = pathlib.Path(__file__).resolve().parent.parent

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 7, 21, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def lockdir(tmp_path, monkeypatch):
    monkeypatch.setattr(wl, "LOCK_DIR", str(tmp_path / "locks"))
    return tmp_path


def fremd(monkeypatch, name="andere-sitzung"):
    # session_id() liefert seit dem 08.10. (kennung, woher) — die Herkunft
    # steht im Schloss, damit sichtbar ist, worauf die Kennung beruht.
    monkeypatch.setattr(wl, "session_id", lambda: (name, "test"))


# -- nehmen und freigeben ---------------------------------------------------

def test_freie_datei_laesst_sich_nehmen():
    with wl.acquire("scripts/selftest.py", "Drossel", NOW) as e:
        assert e["pfad"] == "scripts/selftest.py"
        assert e["auftrag"] == "Drossel"
        assert wl.inspect("scripts/selftest.py", NOW) is not None
    assert wl.inspect("scripts/selftest.py", NOW) is None


def test_nach_freigabe_kann_eine_andere_sitzung_nehmen(monkeypatch):
    with wl.acquire("scripts/a.py", "erste", NOW):
        pass
    fremd(monkeypatch)
    with wl.acquire("scripts/a.py", "zweite", NOW) as e:
        assert e["sitzung"] == "andere-sitzung"
        assert "uebernommen_von" not in e


def test_eigenes_schloss_blockiert_nicht():
    """Dieselbe Sitzung zweimal ist kein Konflikt, sonst steht sie sich nach
    einem Teilabbruch selbst im Weg."""
    wl.acquire("scripts/a.py", "erster Griff", NOW)
    with wl.acquire("scripts/a.py", "zweiter Griff", NOW) as e:
        assert e["auftrag"] == "zweiter Griff"


# -- fremd und frisch -------------------------------------------------------

def test_fremdes_frisches_schloss_wirft(monkeypatch):
    fremd(monkeypatch, "sitzung-A")
    wl.acquire("scripts/selftest.py", "Register einbauen", NOW)
    fremd(monkeypatch, "sitzung-B")
    with pytest.raises(wl.LockHeld) as exc:
        wl.acquire("scripts/selftest.py", "Drossel", NOW + dt.timedelta(hours=3))
    msg = str(exc.value)
    assert "sitzung-A" in msg
    assert "Register einbauen" in msg
    assert "Nicht anfangen, melden" in msg


def test_drei_stunden_neunundfuenfzig_ist_noch_frisch(monkeypatch):
    fremd(monkeypatch, "A")
    wl.acquire("scripts/a.py", "x", NOW)
    fremd(monkeypatch, "B")
    with pytest.raises(wl.LockHeld):
        wl.acquire("scripts/a.py", "y", NOW + dt.timedelta(hours=3, minutes=59))


# -- verwaist ---------------------------------------------------------------

def test_aelter_als_vier_stunden_darf_uebernommen_werden(monkeypatch):
    fremd(monkeypatch, "sitzung-A")
    wl.acquire("scripts/a.py", "abgestuerzt", NOW)
    fremd(monkeypatch, "sitzung-B")
    with wl.acquire("scripts/a.py", "weiter", NOW + dt.timedelta(hours=4, minutes=1)) as e:
        assert e["sitzung"] == "sitzung-B"
        u = e["uebernommen_von"]
        assert u["sitzung"] == "sitzung-A"
        assert u["auftrag"] == "abgestuerzt"
        assert u["alter_bei_uebernahme"].startswith("4:01")


def test_uebernahme_steht_im_neuen_schloss_nicht_nur_im_log(monkeypatch):
    """Ohne den Vermerk sieht die Uebernahme aus wie ein Erstzugriff."""
    fremd(monkeypatch, "A")
    wl.acquire("scripts/a.py", "alt", NOW)
    fremd(monkeypatch, "B")
    # Ohne `with`: B stuerzt ab, statt sauber freizugeben. Genau dieser Fall
    # ist gemeint - ein sauberes Ende hinterlaesst kein Schloss zum Uebernehmen.
    wl.acquire("scripts/a.py", "neu", NOW + dt.timedelta(hours=5))
    fremd(monkeypatch, "C")
    e = wl.acquire("scripts/a.py", "noch neuer", NOW + dt.timedelta(hours=10))
    with e as held:
        assert held["uebernommen_von"]["sitzung"] == "B"


# -- freigeben --------------------------------------------------------------

def test_fremdes_schloss_wird_nicht_freigegeben(monkeypatch):
    fremd(monkeypatch, "A")
    wl.acquire("scripts/a.py", "x", NOW)
    fremd(monkeypatch, "B")
    assert wl.release("scripts/a.py") is False
    assert wl.inspect("scripts/a.py", NOW) is not None


# -- Nebensachen, die vorher schon einmal wehgetan haben --------------------

def test_zwei_pfade_teilen_sich_kein_schloss():
    assert wl.slug("app/main.py") != wl.slug("scripts/main.py")


def test_kaputtes_schloss_blockiert_nicht_fuer_immer(tmp_path, monkeypatch):
    """Eine halb geschriebene Datei ist kein Halter. Sonst sperrt ein
    abgebrochener Schreibvorgang den Pfad dauerhaft."""
    import os
    os.makedirs(wl.LOCK_DIR, exist_ok=True)
    with open(os.path.join(wl.LOCK_DIR, wl.slug("scripts/a.py")), "w") as fh:
        fh.write("{kein json")
    assert wl.inspect("scripts/a.py", NOW) is None
    with wl.acquire("scripts/a.py", "trotzdem", NOW) as e:
        assert e["auftrag"] == "trotzdem"


def test_auftrag_bleibt_eine_zeile():
    with wl.acquire("scripts/a.py", "erste Zeile\nzweite Zeile", NOW) as e:
        assert e["auftrag"] == "erste Zeile"


def test_listing_nennt_frisch_und_verwaist(monkeypatch):
    fremd(monkeypatch, "A")
    wl.acquire("scripts/alt.py", "alt", NOW - dt.timedelta(hours=9))
    wl.acquire("scripts/neu.py", "neu", NOW)
    rows = {r["pfad"]: r for r in wl.listing(NOW)}
    assert rows["scripts/alt.py"]["_verwaist"] is True
    assert rows["scripts/neu.py"]["_verwaist"] is False


# ---------------------------------------------------------------------------
# Wo das Schloss liegt
#
# Die erste Fassung legte es nach ~/moltstack/.locks. Auf diesem Host ist
# ~/moltstack der Checkout, also lag das Schloss gegen das Schreiben im
# Checkout im Checkout und erschien als `?? .locks/` — in genau der Liste, die
# `deploy.sh` liest, bevor es ablehnt.
#
# Diese Tests laufen ohne die lockdir-Fixture: sie pruefen die Konstante
# selbst, nicht das Verhalten unter einem umgebogenen Pfad.
# ---------------------------------------------------------------------------

def test_lockdir_liegt_nicht_unter_dem_checkout():
    """Das Negativ, das die erste Fassung gerissen haette."""
    import os
    lock = os.path.abspath(wl.LOCK_DIR)
    checkout = os.path.abspath(wl.CHECKOUT)
    assert os.path.commonpath([lock, checkout]) != checkout, (
        f"LOCK_DIR {lock} liegt unter dem Checkout {checkout} — "
        f"die Schlossdateien tauchen dann im git status auf")


def test_lockdir_ist_nicht_aus_der_tilde_abgeleitet():
    """`~/moltstack` ist auf diesem Host der Checkout. Ein Pfad, der aus der
    Tilde gebaut wird, wandert mit HOME und landet irgendwann wieder drin."""
    import inspect
    src = inspect.getsource(wl)
    line = next(ln for ln in src.splitlines() if ln.startswith("LOCK_DIR ="))
    assert "expanduser" not in line, line
    assert line.strip() == 'LOCK_DIR = "/home/moltstack/.moltstack-locks"', line


def test_verzeichnis_wird_mit_700_angelegt(tmp_path, monkeypatch):
    import os
    d = tmp_path / "locks-neu"
    monkeypatch.setattr(wl, "LOCK_DIR", str(d))
    with wl.acquire("scripts/a.py", "x", NOW):
        assert oct(os.stat(d).st_mode)[-3:] == "700"


def test_zu_offenes_verzeichnis_wird_zugezogen(tmp_path, monkeypatch):
    """exist_ok laesst den Modus stehen. Ein Verzeichnis, das einmal mit 755
    entstanden ist, bliebe sonst fuer immer lesbar."""
    import os
    d = tmp_path / "locks-offen"
    os.makedirs(d, mode=0o755)
    monkeypatch.setattr(wl, "LOCK_DIR", str(d))
    with wl.acquire("scripts/a.py", "x", NOW):
        assert oct(os.stat(d).st_mode)[-3:] == "700"


# ---------------------------------------------------------------------------
# Die drei Defekte, die das Schloss wirkungslos machten (08.10.2026)
#
# 1. slug() rechnete gegen einen festen Checkout-Pfad und hing damit am
#    Arbeitsverzeichnis. Dieselbe Datei ergab aus dem Checkout
#    `scripts-task_watch.py.lock` und aus einem Worktree
#    `..-moltstack-wt-wache-scripts-task_watch.py.lock`. Zwei Sitzungen in zwei
#    Worktrees kollidierten nie.
# 2. session_id() war hostname:PID, also je Befehl eine andere. release()
#    verweigerte der eigenen Sitzung dreimal.
# 3. Ein Slug durfte mit ".." beginnen und wurde zur versteckten Datei, die
#    `ls` und `rm *.lock` nicht sahen.
# ---------------------------------------------------------------------------

def _repo(tmp_path, name="beispiel"):
    """Eine echte Repo mit einem zweiten Worktree."""
    import subprocess
    main = tmp_path / name
    main.mkdir()
    sub = main / "scripts"
    sub.mkdir()
    (sub / "a.py").write_text("x", encoding="utf-8")
    run = lambda *a: subprocess.run(["git", *a], cwd=str(main), check=True,
                                    capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    run("add", "-A")
    run("commit", "-qm", "erste")
    wt = tmp_path / (name + "-wt")
    subprocess.run(["git", "worktree", "add", "-q", str(wt)], cwd=str(main),
                   check=True, capture_output=True)
    return str(main), str(wt)


def test_zwei_worktrees_ergeben_einen_slug(tmp_path):
    main, wt = _repo(tmp_path)
    assert wl.slug(main + "/scripts/a.py") == wl.slug(wt + "/scripts/a.py")


def test_checkout_und_worktree_ergeben_einen_slug(tmp_path, monkeypatch):
    """Auch wenn der Aufrufer in einem dritten Verzeichnis steht."""
    main, wt = _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    a = wl.slug(main + "/scripts/a.py")
    monkeypatch.chdir(wt)
    b = wl.slug(wt + "/scripts/a.py")
    assert a == b


def test_zwei_verschiedene_repos_teilen_keinen_slug(tmp_path):
    m1, _ = _repo(tmp_path, "eins")
    m2, _ = _repo(tmp_path, "zwei")
    assert wl.slug(m1 + "/scripts/a.py") != wl.slug(m2 + "/scripts/a.py")


def test_ausserhalb_einer_repo_ist_ein_fehler(tmp_path):
    """Kein Rueckfallwert. Ein Schloss, das sich nicht bestimmen laesst, darf
    nicht an einem geratenen Namen haengen."""
    d = tmp_path / "keine-repo"
    d.mkdir()
    (d / "a.py").write_text("x", encoding="utf-8")
    with pytest.raises(wl.LockError):
        wl.slug(str(d / "a.py"))


def test_slug_form_wird_erzwungen(tmp_path, monkeypatch):
    """Was nicht passt, wird abgewiesen und nicht umgeschrieben."""
    main, _ = _repo(tmp_path)
    monkeypatch.setattr(wl, "SLUG_FORM", wl.re.compile(r"^nur-dieser-name$"))
    with pytest.raises(wl.LockError) as exc:
        wl.slug(main + "/scripts/a.py")
    assert "entspricht nicht" in str(exc.value)


def test_kein_slug_beginnt_mit_einem_punkt(tmp_path):
    main, _ = _repo(tmp_path)
    assert not wl.slug(main + "/scripts/a.py").startswith(".")


# -- Sitzung ----------------------------------------------------------------

def test_sitzung_ist_ueber_prozesse_hinweg_dieselbe():
    """Der Kern von Defekt 2: zwei Aufrufe, eine Sitzung."""
    import subprocess
    import sys as _sys
    code = ("import sys; sys.path.insert(0, %r);"
            "from scripts import writelock as w; print(w.session_id()[0])"
            % str(ROOT))
    a = subprocess.run([_sys.executable, "-c", code], capture_output=True,
                       text=True, check=True).stdout.strip()
    b = subprocess.run([_sys.executable, "-c", code], capture_output=True,
                       text=True, check=True).stdout.strip()
    assert a == b and a.startswith("sid:")


def test_nehmen_und_freigeben_in_getrennten_prozessen(tmp_path, monkeypatch):
    """Vorher scheiterte genau das: der zweite Prozess hielt sich fuer eine
    andere Sitzung und gab nicht frei."""
    import subprocess
    import sys as _sys
    main, _ = _repo(tmp_path)
    locks = tmp_path / "locks"
    datei = main + "/scripts/a.py"
    pre = ("import sys; sys.path.insert(0, %r);"
           "from scripts import writelock as w; w.LOCK_DIR = %r;" % (str(ROOT), str(locks)))
    subprocess.run([_sys.executable, "-c", pre + "w.acquire(%r, 'x')" % datei],
                   capture_output=True, text=True, check=True)
    out = subprocess.run(
        [_sys.executable, "-c", pre + "print(w.release(%r))" % datei],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "True"
    assert os.listdir(str(locks)) == []


def test_eine_andere_sitzung_gibt_nicht_frei(tmp_path, monkeypatch):
    main, _ = _repo(tmp_path)
    monkeypatch.setattr(wl, "LOCK_DIR", str(tmp_path / "locks"))
    datei = main + "/scripts/a.py"
    monkeypatch.setenv("MOLTRUST_SESSION", "A")
    wl.acquire(datei, "x", NOW)
    monkeypatch.setenv("MOLTRUST_SESSION", "B")
    assert wl.release(datei) is False
    assert len(os.listdir(wl.LOCK_DIR)) == 1


def test_schloss_nennt_woher_die_kennung_kam(tmp_path, monkeypatch):
    main, _ = _repo(tmp_path)
    monkeypatch.setattr(wl, "LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.delenv("MOLTRUST_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    with wl.acquire(main + "/scripts/a.py", "x", NOW) as e:
        assert e["sitzung_aus"] == "os.getsid"


# -- keine versteckten Dateien ---------------------------------------------

def test_verzeichnis_enthaelt_keine_versteckte_datei(tmp_path, monkeypatch):
    """Mit os.listdir geprueft, nicht mit ls: ein Name, der mit einem Punkt
    beginnt, faellt durch jeden Shell-Glob und blieb am 08.10. unbemerkt
    liegen."""
    main, wt = _repo(tmp_path)
    monkeypatch.setattr(wl, "LOCK_DIR", str(tmp_path / "locks"))
    for d in (main, wt):
        wl.acquire(d + "/scripts/a.py", "x", NOW)
    namen = os.listdir(wl.LOCK_DIR)
    assert namen, "es sollte ein Schloss liegen"
    assert not [n for n in namen if n.startswith(".")]
    assert len(namen) == 1, "beide Worktrees teilen sich ein Schloss"
