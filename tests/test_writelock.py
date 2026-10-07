"""Das Schloss macht sichtbar, wer haelt — und laesst sich nicht wegsehen.

Am 07.10.2026 arbeiteten zwei Sitzungen gleichzeitig an scripts/selftest.py.
Der Deploy lehnte ab, der Server blieb drei Commits zurueck, und neun Minuten
lang wusste niemand, von wem die Aenderung war. Genau diese Frage beantwortet
das Schloss.

Beide Richtungen je Regel: ein Schloss, das immer nachgibt, ist keines, und
eines, das nie nachgibt, blockiert nach dem ersten Absturz fuer immer.
"""
import datetime as dt

import pytest

from scripts import writelock as wl

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 7, 21, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def lockdir(tmp_path, monkeypatch):
    monkeypatch.setattr(wl, "LOCK_DIR", str(tmp_path / "locks"))
    return tmp_path


def fremd(monkeypatch, name="andere-sitzung"):
    monkeypatch.setattr(wl, "session_id", lambda: name)


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
