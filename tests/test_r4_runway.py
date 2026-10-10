"""Die Wache ueber die beiden Einmallaeufe von Runde 4.

Die erste Fassung fragte nur, ob beide at-Jobs in der Queue stehen. Am
08.10.2026 um 10:36 lief Job 6, verschwand aus der Queue, und ab da haette die
Wache ihn in jedem Lauf als fehlend gemeldet — zwanzig Minuten nach dem
erfolgreichen Lauf, bis zur Frist.

Drei Faelle je Job: vor der Zeit gehoert er in die Queue, nach der Zeit gehoert
er nicht mehr hinein und muss stattdessen gelaufen sein, mit Exitcode 0.
"""
import datetime
import json

def _atq(jobs):
    """atq-Ausgabe mit den genannten Job-Nummern."""
    out = "\n".join(f"{j}\tFri Oct  9 10:40:00 2026 a moltstack" for j in jobs)
    return type("P", (), {"returncode": 0, "stdout": out})()


def _runs(tmp_path, rows):
    p = tmp_path / "r4-runs.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return str(p)


def _setup(monkeypatch, tmp_path, *, jetzt, jobs, runs=()):
    import datetime as _dt
    import scripts.task_watch as w
    monkeypatch.setattr(w.subprocess, "run", lambda *a, **k: _atq(jobs))
    monkeypatch.setattr(w, "R4_RUNS", _runs(tmp_path, list(runs)))
    monkeypatch.setattr(w, "R4_WORKTREE", str(tmp_path))
    f1 = tmp_path / "r4-run.sh"; f1.write_text("x")
    f2 = tmp_path / "runden_auswertung.py"; f2.write_text("x")
    monkeypatch.setattr(w, "R4_FILES", (str(f1), str(f2)))
    monkeypatch.setattr(w, "R4_DEADLINE", "2099-01-01T00:00:00+00:00")

    class _Now(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return jetzt
    monkeypatch.setattr(w, "datetime", _Now)
    return w


VOR = datetime.datetime(2026, 10, 8, 9, 0, tzinfo=datetime.timezone.utc)
ZWISCHEN = datetime.datetime(2026, 10, 8, 12, 0, tzinfo=datetime.timezone.utc)


def test_vor_der_zeit_und_in_der_queue_ist_still(monkeypatch, tmp_path):
    w = _setup(monkeypatch, tmp_path, jetzt=VOR, jobs=["6", "7"])
    assert w.check_r4_runway() == []


def test_vor_der_zeit_und_nicht_in_der_queue_ist_alarm(monkeypatch, tmp_path):
    w = _setup(monkeypatch, tmp_path, jetzt=VOR, jobs=["7"])
    out = w.check_r4_runway()
    assert len(out) == 1
    assert "at-Job 6" in out[0]
    assert "steht nicht in der Queue" in out[0]


def test_nach_der_zeit_mit_lauf_und_rc0_ist_still(monkeypatch, tmp_path):
    """Der Fall vom 08.10.: Job 6 ist gelaufen und aus der Queue, Job 7 steht
    noch. Nichts daran ist ein Befund."""
    w = _setup(monkeypatch, tmp_path, jetzt=ZWISCHEN, jobs=["7"],
               runs=[{"ts": "2026-10-08T10:36:21Z", "modus": "prewarn", "rc": 0}])
    assert w.check_r4_runway() == []


def test_nach_der_zeit_ohne_lauf_ist_alarm(monkeypatch, tmp_path):
    w = _setup(monkeypatch, tmp_path, jetzt=ZWISCHEN, jobs=["7"], runs=[])
    out = w.check_r4_runway()
    assert len(out) == 1
    assert "at-Job 6" in out[0]
    assert "nicht gelaufen" in out[0]


def test_nach_der_zeit_mit_exitcode_ungleich_null_ist_alarm(monkeypatch, tmp_path):
    """Gelaufen ist nicht gelungen. Der Exitcode steht im Wortlaut, sonst
    muesste jemand erst das Log suchen, um zu wissen, was schiefging."""
    w = _setup(monkeypatch, tmp_path, jetzt=ZWISCHEN, jobs=["7"],
               runs=[{"ts": "2026-10-08T10:36:21Z", "modus": "prewarn", "rc": 2}])
    out = w.check_r4_runway()
    assert len(out) == 1
    assert "Exitcode 2" in out[0]


def test_juengster_lauf_entscheidet(monkeypatch, tmp_path):
    """Ein Fehlschlag, dem ein erfolgreicher Lauf folgte, ist erledigt."""
    w = _setup(monkeypatch, tmp_path, jetzt=ZWISCHEN, jobs=["7"],
               runs=[{"ts": "2026-10-08T10:30:00Z", "modus": "prewarn", "rc": 2},
                     {"ts": "2026-10-08T10:36:21Z", "modus": "prewarn", "rc": 0}])
    assert w.check_r4_runway() == []


def test_kaputte_zeile_im_nachweis_verdeckt_den_lauf_nicht(monkeypatch, tmp_path):
    """Eine unlesbare Zeile wird uebersprungen, die gute daneben zaehlt."""
    # Erst _setup, dann die Datei, und unter eigenem Namen: _setup legt
    # r4-runs.jsonl selbst an und wuerde sie sonst ueberschreiben. Beim ersten
    # Anlauf tat es das, und der Test lief gegen eine Fixture, die er nie zu
    # sehen bekam.
    w = _setup(monkeypatch, tmp_path, jetzt=ZWISCHEN, jobs=["7"])
    kaputt = tmp_path / "mit-kaputter-zeile.jsonl"
    kaputt.write_text(
        "{kaputt\n"
        + json.dumps({"ts": "2026-10-08T10:36:21Z", "modus": "prewarn", "rc": 0})
        + "\n", encoding="utf-8")
    monkeypatch.setattr(w, "R4_RUNS", str(kaputt))
    assert w.check_r4_runway() == []
