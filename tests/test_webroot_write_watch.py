"""Schreibwache ueber den Web-Root (scripts/webroot_write_watch.py, ops W1).

Je Kriterium eine Ausloese- und eine Schweigeprobe: ein Schreibvorgang ohne
Ledger-Zeile meldet sich mit Pfad und Zeitpunkt, ein Install ueber deploy.sh
bleibt still. Der letzte Test faehrt die echte Funktion aus deploy.sh mit einem
Stub als Wrapper, damit Ledger-Format und Leser nicht auseinanderlaufen.
"""
import datetime as dt
import hashlib
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


def _scan(root, ledger, state, bl, cursor):
    state.write_text(json.dumps({"cursor": cursor}))
    return w.scan(str(root), str(ledger), str(state), str(bl))[0]


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


def test_generated_files_and_older_writes_stay_silent(tmp_path):
    root, ledger, state, bl = _env(tmp_path)
    (root / "old.html").write_text("before the cursor")
    time.sleep(0.05)
    cursor = time.time()  # old.html liegt davor
    time.sleep(0.05)
    assert _scan(root, ledger, state, bl, cursor) == []
    (root / "blog").mkdir()
    (root / "blog" / "index.html").write_text("regenerated")
    (root / "registry-proof.json").write_text("{}")
    assert _scan(root, ledger, state, bl, cursor) == []


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
