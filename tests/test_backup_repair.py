"""ops BAK-REPAIR: an old or missing backup is repaired without Lars.

Until 02.11.2026 scripts/offsite_backup_check.sh answered a stale off-site
snapshot with a sentence for Telegram ("Bitte die Console bitten ... neu zu
starten"), and scripts/security_check.sh section 7 filed an old or missing DB
dump as ABLAGE. Both now start scripts/backup_repair.sh. Telegram hears of it
only when the repair fails, once, with the cause and what Lars has to do.

No real restic, pg_dump or Telegram: restic is a stub behind RESTIC_BIN that
keeps the newest snapshot time in a file, backup_db.sh is a stub behind
BACKUP_DB_SCRIPT that writes a dump file, crontab is a stub on PATH, and the
security report's notify_send writes to a file. The real offsite_backup.sh and
offsite_backup_check.sh run against these stubs.
"""
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPAIR = ROOT / "scripts" / "backup_repair.sh"
CHECK = ROOT / "scripts" / "offsite_backup_check.sh"
OFFSITE = ROOT / "scripts" / "offsite_backup.sh"
SECURITY = ROOT / "scripts" / "security_check.sh"

# GNU coreutils first where a Mac has them under another name (stat -c, timeout).
GNUBIN = "/opt/homebrew/opt/coreutils/libexec/gnubin"

# An address of the shape the Storage Box uses. It must never reach a message
# or the log, neither as a whole nor as user or host.
REPO = "sftp:u987654@u987654.your-storagebox.de:/moltrust"
USER = "u987654"
HOST = "u987654.your-storagebox.de"


def _path() -> str:
    parts = [GNUBIN] if os.path.isdir(GNUBIN) else []
    return os.pathsep.join(parts + [os.environ["PATH"]])


def _have(tool: str) -> bool:
    return shutil.which(tool, path=_path()) is not None


pytestmark = pytest.mark.skipif(
    not all(_have(t) for t in ("bash", "flock", "timeout")),
    reason="bash, flock or GNU timeout missing")

RESTIC_STUB = r'''#!/usr/bin/env bash
# restic stub: the newest snapshot time lives in $STUB/snapshot.
set -u
cmd=""
for a in "$@"; do case "$a" in backup|forget|check|snapshots) cmd=$a; break ;; esac; done
echo "$cmd" >> "$STUB/restic.calls"
if [ -n "${RESTIC_STUB_FAIL:-}" ] && [ "$cmd" != snapshots ]; then
  echo "Fatal: unable to open repository at $RESTIC_REPOSITORY: ssh: connect to host ${RESTIC_REPOSITORY#*@} port 23: Connection refused" >&2
  exit 1
fi
case "$cmd" in
  backup) date -u +%Y-%m-%dT%H:%M:%S+00:00 > "$STUB/snapshot"; echo "snapshot 1a2b3c4d saved" ;;
  snapshots)
    if [ -s "$STUB/snapshot" ]; then
      printf '[{"short_id":"1a2b3c4d","time":"%s"}]\n' "$(cat "$STUB/snapshot")"
    else
      echo '[]'
    fi ;;
esac
exit 0
'''

BACKUP_DB_STUB = r'''#!/usr/bin/env bash
# backup_db.sh stub: one dump of now, complete unless told otherwise.
echo run >> "$STUB/backup_db.calls"
if [ -n "${BACKUP_DB_STUB_FAIL:-}" ]; then echo "pg_dump: error: connection failed" >&2; exit 1; fi
f="$OFFSITE_DUMP_DIR/moltstack_$(date -u +%Y%m%d_%H%M%S).sql"
printf -- '-- dump\n--\n-- PostgreSQL database dump complete\n--\n' > "$f"
'''


@pytest.fixture
def box(tmp_path):
    """A server in a directory: dumps, state, stubs, env file, log."""
    stub = tmp_path / "stub"
    stub.mkdir()
    (tmp_path / "backups").mkdir()
    for name, body in (("restic", RESTIC_STUB), ("backup_db.sh", BACKUP_DB_STUB),
                       ("crontab", "#!/usr/bin/env bash\necho '# crontab stub'\n")):
        p = stub / name
        p.write_text(body)
        p.chmod(0o755)
    (tmp_path / "pass").write_text("not-a-real-password\n")
    env_file = tmp_path / "offsite.env"
    env_file.write_text(f"RESTIC_REPOSITORY={REPO}\nRESTIC_PASSWORD_FILE={tmp_path / 'pass'}\n")
    env = {
        "PATH": f"{stub}{os.pathsep}{_path()}",
        "HOME": str(tmp_path),
        "STUB": str(stub),
        "RESTIC_BIN": str(stub / "restic"),
        "BACKUP_DB_SCRIPT": str(stub / "backup_db.sh"),
        "OFFSITE_ENV": str(env_file),
        "OFFSITE_DUMP_DIR": str(tmp_path / "backups"),
        "OFFSITE_STATE_DIR": str(tmp_path / "state"),
        "OFFSITE_REPAIR_LOG": str(tmp_path / "backup_repair.log"),
        "OFFSITE_REPAIR_WAIT_MIN": "1",
        "TMPDIR": str(tmp_path),
    }
    return {"tmp": tmp_path, "stub": stub, "env": env}


def snapshot_hours_ago(box, hours: float):
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    (box["stub"] / "snapshot").write_text(t.strftime("%Y-%m-%dT%H:%M:%S+00:00") + "\n")


def dump_of_today(box):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_030001")
    f = box["tmp"] / "backups" / f"moltstack_{stamp}.sql"
    f.write_text("-- dump\n-- PostgreSQL database dump complete\n")
    return f


def old_dump(box, days: int):
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    f = box["tmp"] / "backups" / f"moltstack_{t.strftime('%Y%m%d')}_030001.sql"
    f.write_text("-- dump\n-- PostgreSQL database dump complete\n")
    os.utime(f, (t.timestamp(), t.timestamp()))
    return f


def calls(box, name: str) -> list:
    p = box["stub"] / name
    return p.read_text().split() if p.exists() else []


def run(cmd, box, **extra):
    env = dict(box["env"], **extra)
    return subprocess.run(["bash", *map(str, cmd)], capture_output=True, text=True,
                          env=env, timeout=120)


def log_text(box) -> str:
    p = box["tmp"] / "backup_repair.log"
    return p.read_text() if p.exists() else ""


def assert_no_access_data(*texts):
    for t in texts:
        assert REPO not in t
        assert USER not in t
        assert HOST not in t
        assert "not-a-real-password" not in t


# --- the repair itself -------------------------------------------------------

def test_a_fresh_backup_starts_nothing(box):
    dump_of_today(box)
    snapshot_hours_ago(box, 2)
    p = run([REPAIR, "probe"], box)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "nichts neu gestartet" in p.stdout
    assert calls(box, "backup_db.calls") == []
    assert "backup" not in calls(box, "restic.calls")


def test_old_snapshot_alone_runs_only_the_offsite_copy(box):
    dump_of_today(box)
    snapshot_hours_ago(box, 30)
    p = run([REPAIR, "probe"], box)
    assert p.returncode == 0, p.stdout + p.stderr
    assert calls(box, "backup_db.calls") == []
    assert calls(box, "restic.calls").count("backup") == 1
    assert "offsite_backup.sh neu gestartet" in p.stdout
    assert "backup_db.sh" not in p.stdout
    assert len(p.stdout.strip().splitlines()) == 1


def test_missing_dump_runs_backup_db_then_the_offsite_copy(box):
    old_dump(box, 8)
    snapshot_hours_ago(box, 30)
    p = run([REPAIR, "probe"], box)
    assert p.returncode == 0, p.stdout + p.stderr
    assert calls(box, "backup_db.calls") == ["run"]
    assert calls(box, "restic.calls").count("backup") == 1
    assert "backup_db.sh und offsite_backup.sh neu gestartet" in p.stdout
    assert "PostgreSQL database dump complete" in next(
        (box["tmp"] / "backups").glob(
            f"moltstack_{dt.datetime.now(dt.timezone.utc):%Y%m%d}_*.sql")).read_text()


def test_an_incomplete_dump_is_a_failure(box):
    old_dump(box, 8)
    snapshot_hours_ago(box, 30)
    stub = box["stub"] / "backup_db.sh"
    stub.write_text('#!/usr/bin/env bash\necho run >> "$STUB/backup_db.calls"\n'
                    'echo "-- half" > "$OFFSITE_DUMP_DIR/moltstack_$(date -u +%Y%m%d_%H%M%S).sql"\n')
    p = run([REPAIR, "probe"], box)
    assert p.returncode == 1
    assert "keinen vollstaendigen Dump" in p.stdout
    assert "Lars muss:" in p.stdout
    assert "backup" not in calls(box, "restic.calls")


def test_storage_refusing_names_the_cause_and_no_access_data(box):
    dump_of_today(box)
    snapshot_hours_ago(box, 30)
    p = run([REPAIR, "probe"], box, RESTIC_STUB_FAIL="1")
    assert p.returncode == 1
    out = p.stdout.strip()
    assert len(out.splitlines()) == 1
    assert "Storage Box" in out and "Lars muss:" in out
    assert "Hetzner Console" in out
    log = log_text(box)
    assert "Connection refused" in log  # the cause is in the log, scrubbed
    assert_no_access_data(out, p.stderr, log)


def test_a_locked_second_run_starts_nothing(box):
    old_dump(box, 8)
    snapshot_hours_ago(box, 30)
    lock = box["tmp"] / "state" / "repair.lock"
    lock.parent.mkdir(parents=True)
    holder = subprocess.Popen(["flock", str(lock), "sleep", "30"], env=box["env"])
    try:
        # wait until the holder has the lock
        for _ in range(50):
            if subprocess.run(["flock", "-n", str(lock), "true"],
                              env=box["env"]).returncode != 0:
                break
            subprocess.run(["sleep", "0.1"])
        p = run([REPAIR, "probe"], box)
    finally:
        holder.kill()
        holder.wait()
    assert p.returncode == 75
    assert calls(box, "backup_db.calls") == []
    assert calls(box, "restic.calls") == []
    assert "Sperre" in p.stdout


def test_the_cap_stops_the_fourth_repair_and_repeats_the_same_line(box):
    dump_of_today(box)
    snapshot_hours_ago(box, 30)
    outs = [run([REPAIR, "probe"], box, RESTIC_STUB_FAIL="1") for _ in range(4)]
    assert [p.returncode for p in outs] == [1, 1, 1, 3]
    assert calls(box, "restic.calls").count("backup") == 3
    # the same sentence each time: the notify throttle sends it once
    assert len({p.stdout for p in outs}) == 1


# --- offsite_backup_check.sh, the invariant c-offsite-backup-fresh ----------

def test_check_old_snapshot_is_repaired_and_reads_green_with_a_note(box):
    dump_of_today(box)
    snapshot_hours_ago(box, 30)
    p = run([CHECK], box)
    assert p.returncode == 0
    lines = p.stdout.strip().splitlines()
    assert lines[0].startswith("HINWEIS: Sicherung repariert")
    assert lines[-1].startswith("offsite-backup=ok alter=0h")
    assert "Bitte die Console bitten" not in p.stdout
    assert calls(box, "restic.calls").count("backup") == 1


def test_check_probe_with_max_age_zero_repairs_and_turns_green(box):
    """The NACHPRUEFUNG in the job text: OFFSITE_MAX_AGE_H=0."""
    dump_of_today(box)
    snapshot_hours_ago(box, 2)
    p = run([CHECK], box, OFFSITE_MAX_AGE_H="0")
    assert "offsite-backup=ok" in p.stdout.splitlines()[-1], p.stdout
    assert calls(box, "restic.calls").count("backup") == 1


def test_check_fresh_snapshot_repairs_nothing(box):
    """Schweigeprobe: the normal hour starts nothing and prints one line."""
    dump_of_today(box)
    snapshot_hours_ago(box, 3)
    p = run([CHECK], box)
    assert p.stdout.strip().splitlines() == ["offsite-backup=ok alter=3h"]
    assert "backup" not in calls(box, "restic.calls")
    assert calls(box, "backup_db.calls") == []


def test_check_failed_repair_prints_one_sentence_for_lars(box):
    dump_of_today(box)
    snapshot_hours_ago(box, 30)
    p = run([CHECK], box, RESTIC_STUB_FAIL="1")
    out = p.stdout.strip()
    assert "offsite-backup=ok" not in out
    assert len(out.splitlines()) == 1
    assert "Lars muss:" in out
    assert_no_access_data(out, log_text(box))


def test_check_dry_run_only_measures(box):
    dump_of_today(box)
    snapshot_hours_ago(box, 30)
    p = run([CHECK], box, SELFTEST_DRY_RUN="1")
    assert "offsite-backup=ok" not in p.stdout
    assert "30 Stunden alt" in p.stdout
    assert calls(box, "restic.calls") == ["snapshots"]


# --- the selftest: a repair is green and sends nothing ----------------------

def _selftest_run(monkeypatch, tmp_path, wert, dry_run=True):
    import sys
    sys.path.insert(0, str(ROOT))
    from scripts import selftest as st
    inv = {"id": "c-offsite-backup-fresh", "titel": "Off-site", "kategorie": "C",
           "schweregrad": "fail", "tempo": "hourly", "detail_aus_wert": True,
           "erwartung": {"operator": "contains", "wert": "offsite-backup=ok"},
           "abfrage": {"art": "shell", "wert": "true"}}
    sent = []
    monkeypatch.setattr(st, "load_catalogue", lambda: [inv])
    monkeypatch.setattr(st, "run_query", lambda i: (wert, None))
    monkeypatch.setattr(st, "preflight", lambda: [])
    monkeypatch.setattr(st.notify, "send_telegram", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(st.notify, "send_befunde",
                        lambda b, **k: sent.append(list(b)) or
                        {"gesendet": [x[0] for x in b], "gedrosselt": [], "erfolg": True})
    monkeypatch.setattr(st.throttle, "load_register", lambda *a, **k: [])
    monkeypatch.setattr(st.throttle, "load_open", lambda *a, **k: [])
    monkeypatch.setattr(st, "OUTDIR", str(tmp_path / "out"))
    monkeypatch.setattr(st, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr("sys.argv", ["selftest.py", "--all", "--no-supervision"]
                        + (["--dry-run"] if dry_run else []))
    st.main()
    doc = json.loads(next((tmp_path / "out").glob("*.json")).read_text())[-1]
    return [r for r in doc["ergebnisse"] if r["id"] == inv["id"]][0], sent


def test_selftest_reads_a_repair_as_ok_with_a_note_and_sends_nothing(monkeypatch, tmp_path, capsys):
    wert = ("HINWEIS: Sicherung repariert (letzte Sicherung ausserhalb des Servers 30 h alt): "
            "offsite_backup.sh neu gestartet\noffsite-backup=ok alter=0h")
    res, sent = _selftest_run(monkeypatch, tmp_path, wert)
    assert res["status"] == "OK"
    assert res["hinweis"].startswith("Sicherung repariert")
    assert sent == []
    assert "[OK, HINWEIS] c-offsite-backup-fresh" in capsys.readouterr().out


def test_selftest_sends_a_failed_repair(monkeypatch, tmp_path):
    wert = ("Die Sicherung ist alt und die automatische Reparatur ist gescheitert: die Storage "
            "Box nimmt keine Verbindung an. Lars muss: in der Hetzner Console pruefen.")
    res, sent = _selftest_run(monkeypatch, tmp_path, wert)
    assert res["status"] == "FAIL"
    assert "hinweis" not in res
    assert len(sent) == 1 and "Lars muss:" in sent[0][0][1]


def test_selftest_plain_green_has_no_note(monkeypatch, tmp_path):
    res, sent = _selftest_run(monkeypatch, tmp_path, "offsite-backup=ok alter=3h")
    assert res["status"] == "OK" and "hinweis" not in res and sent == []


def test_a_failed_repair_reaches_telegram_once(box, tmp_path):
    """Two hourly runs with the same failure: one message, the second throttled."""
    import sys
    sys.path.insert(0, str(ROOT))
    from app import notify
    dump_of_today(box)
    snapshot_hours_ago(box, 30)
    outs = [run([CHECK], box, RESTIC_STUB_FAIL="1").stdout.strip() for _ in range(2)]
    store = str(tmp_path / "fp.json")
    now = dt.datetime.now(dt.timezone.utc)
    sendet = [notify.throttle(f"[FAIL] c-offsite-backup-fresh — {o}",
                              now=now + dt.timedelta(hours=i), path=store)[0]
              for i, o in enumerate(outs)]
    assert sendet == [True, False]


def test_the_invariant_gives_the_query_time_for_a_repair():
    import yaml
    inv = yaml.safe_load((ROOT / "docs/invariants/c-offsite-backup-fresh.yaml").read_text())
    # 330 s dump + 930 s restic + 1300 s waiting for another repair
    assert int(inv["abfrage"]["timeout"]) >= 330 + 930 + 1300


# --- security_check.sh section 7 --------------------------------------------

def _security(box, body: str):
    sent = box["tmp"] / "sent.txt"
    script = f'''
set -eo pipefail
source "{SECURITY}"
TIMESTAMP="2026-11-08 03:00:00 UTC"
BACKUP_DIR="{box['tmp'] / 'backups'}"
notify_send() {{ cat >> "{sent}"; printf '\\n---\\n' >> "{sent}"; }}
{body}
send_telegram
printf '%s' "$REPORT" > "{box['tmp'] / 'report.txt'}"
'''
    env = dict(box["env"], SECURITY_CHECK_FUNCTIONS_ONLY="1")
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env,
                       timeout=120)
    messages = [m for m in sent.read_text().split("\n---\n") if m.strip()] if sent.exists() else []
    report = (box["tmp"] / "report.txt").read_text() if (box["tmp"] / "report.txt").exists() else ""
    return p, messages, report


def test_security_old_dump_is_repaired_without_a_message(box):
    old_dump(box, 8)
    snapshot_hours_ago(box, 30)
    p, messages, report = _security(box, "check_backup")
    assert p.returncode == 0, p.stderr
    assert messages == []
    assert "Telegram: nothing to send" in p.stdout
    assert "[OK]    Backup repaired: Sicherung repariert" in report
    assert "is 8 days old, starting scripts/backup_repair.sh" in report
    assert calls(box, "backup_db.calls") == ["run"]


def test_security_missing_directory_is_repaired_too(box):
    shutil.rmtree(box["tmp"] / "backups")
    snapshot_hours_ago(box, 2)
    p, messages, report = _security(box, "check_backup")
    assert p.returncode == 0, p.stderr
    assert messages == []
    assert "does not exist, starting" in report
    assert "Backup repaired" in report


def test_security_failed_repair_sends_exactly_one_message(box):
    old_dump(box, 8)
    snapshot_hours_ago(box, 30)
    box["env"]["BACKUP_DB_STUB_FAIL"] = "1"
    p, messages, report = _security(box, "check_backup")
    assert p.returncode == 0, p.stderr
    assert len(messages) == 1
    assert messages[0].startswith("MolTrust Security Alert — 1 issue(s)")
    assert "[HANDLUNG] backup repair failed:" in messages[0]
    assert "Lars muss:" in messages[0]
    assert_no_access_data(messages[0], report, log_text(box))


def test_security_fresh_dump_starts_nothing(box):
    dump_of_today(box)
    p, messages, report = _security(box, "check_backup")
    assert p.returncode == 0, p.stderr
    assert messages == []
    assert "[OK]    Latest backup: moltstack_" in report
    assert calls(box, "backup_db.calls") == [] and calls(box, "restic.calls") == []
