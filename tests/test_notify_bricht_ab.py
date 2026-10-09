"""Eine Meldestelle, die nicht melden kann, bricht ab.

Am 09.10.2026 zwischen 08:22 und 08:27 UTC haette ein gescheiterter Deploy
niemanden erreicht. notify loeste die chat_id aus ~/.moltrust_secrets auf, den
Token nur aus os.environ; deploy.sh laeuft als forced command ueber SSH ohne
Umgebung, fand also eine chat_id und keinen Token, gab False zurueck — und
deploy.sh schrieb "console only" und machte weiter. Jeder Exitcode sagte, es
sei alles in Ordnung.

Der fehlende Rueckfall war der kleinere Teil. Der groessere war, dass das
Fehlen folgenlos blieb.

Der Abbruch selbst laeuft ueber os._exit in einem atexit-Haken und ist deshalb
nur in einem eigenen Prozess pruefbar — ein Test, der ihn im eigenen Prozess
ausloest, beendet den Testlauf. Die Tests hier starten also Kinder.
"""
import os
import pathlib
import re
import subprocess
import sys

import pytest

from app import notify

WURZEL = pathlib.Path(__file__).resolve().parent.parent
DEPLOY_SH = WURZEL / "ops" / "deploy" / "deploy.sh"

KIND = """
import sys
sys.path.insert(0, {wurzel!r})
from app import notify
ok = notify.send_telegram("probe", channel=notify.ALERTS)
print("rueckgabe", ok)
print("stumm", notify.stumm_geblieben())
"""


def _lauf(env_zusatz, *, wurzel=None):
    """Ein Kindprozess mit geleerter Umgebung plus env_zusatz."""
    env = {"HOME": os.path.expanduser("~"), "PATH": "/usr/bin:/bin"}
    env.update(env_zusatz)
    return subprocess.run(
        [sys.executable, "-c", KIND.format(wurzel=str(wurzel or WURZEL))],
        env=env, capture_output=True, text=True, timeout=60)


def test_ohne_token_endet_der_prozess_mit_70(tmp_path):
    """Rueckgabe False UND ein Exitcode ungleich 0. Beides, nicht eines."""
    p = _lauf({"MOLTRUST_NOTIFY": "on",
               "MOLTRUST_SECRETS_FILE": "/nonexistent",
               "MOLTRUST_NOTIFY_STATE_DIR": str(tmp_path)})
    assert "rueckgabe False" in p.stdout, p.stdout
    assert "['alerts']" in p.stdout, p.stdout
    assert p.returncode == notify.MUTE_EXIT_CODE, (p.returncode, p.stderr[-400:])


def test_der_grund_steht_im_fehlerprotokoll(tmp_path):
    """Ein Exitcode allein sagt nicht, was fehlt."""
    p = _lauf({"MOLTRUST_NOTIFY": "on",
               "MOLTRUST_SECRETS_FILE": "/nonexistent",
               "MOLTRUST_NOTIFY_STATE_DIR": str(tmp_path)})
    assert "kann nicht melden" in p.stderr, p.stderr[-400:]
    assert "c-notify-kann-melden" in p.stderr, p.stderr[-400:]


def test_geschlossenes_gate_bricht_nicht_ab(tmp_path):
    """Nicht melden KOENNEN und nicht melden SOLLEN sind zwei Dinge.

    Das Gate hat jemand geschlossen. Daraus einen Abbruch zu machen hiesse,
    jede Sitzung mit MOLTRUST_NOTIFY=off scheitern zu lassen.
    """
    p = _lauf({"MOLTRUST_NOTIFY": "off",
               "MOLTRUST_SECRETS_FILE": "/nonexistent",
               "MOLTRUST_NOTIFY_STATE_DIR": str(tmp_path)})
    assert "rueckgabe False" in p.stdout
    assert "stumm []" in p.stdout, p.stdout
    assert p.returncode == 0, (p.returncode, p.stderr[-300:])


def test_mit_token_und_chat_kein_abbruch(tmp_path):
    """Die Gegenrichtung: wo gemeldet werden kann, aendert sich nichts."""
    secrets = tmp_path / "secrets"
    secrets.write_text("TELEGRAM_BOT_TOKEN=123456:" + "a" * 32 + "\n"
                       "TELEGRAM_CHAT_ID=-100123456\n", encoding="utf-8")
    kind = (tmp_path / "kind.py")
    kind.write_text(f"""
import sys
sys.path.insert(0, {str(WURZEL)!r})
from app import notify


class _R:
    status_code = 200
    content = b""

    @staticmethod
    def json():
        return {{"result": {{"message_id": 1}}}}


notify.requests.post = lambda url, **kw: _R()
print("rueckgabe", notify.send_telegram("probe", channel=notify.ALERTS))
print("stumm", notify.stumm_geblieben())
""", encoding="utf-8")
    p = subprocess.run(
        [sys.executable, str(kind)],
        env={"HOME": os.path.expanduser("~"), "PATH": "/usr/bin:/bin",
             "MOLTRUST_NOTIFY": "on",
             "MOLTRUST_SECRETS_FILE": str(secrets),
             "MOLTRUST_NOTIFY_STATE_DIR": str(tmp_path / "zustand")},
        capture_output=True, text=True, timeout=60)
    assert "rueckgabe True" in p.stdout, (p.stdout, p.stderr[-300:])
    assert "stumm []" in p.stdout
    assert p.returncode == 0, (p.returncode, p.stderr[-300:])


def test_interaktiv_wird_gewarnt_aber_nicht_abgebrochen():
    """An einem Terminal liest jemand die Warnung.

    Mit einem Pseudoterminal geprueft, nicht mit einer gefaelschten
    isatty-Antwort: die Unterscheidung IST der Terminalzustand, und sie
    nachzustellen hiesse, die Bedingung durch ihre Behauptung zu ersetzen.
    """
    pty = pytest.importorskip("pty")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        skript = os.path.join(tmp, "k.py")
        with open(skript, "w", encoding="utf-8") as fh:
            fh.write(KIND.format(wurzel=str(WURZEL)))
        env = {"HOME": os.path.expanduser("~"), "PATH": "/usr/bin:/bin",
               "MOLTRUST_NOTIFY": "on",
               "MOLTRUST_SECRETS_FILE": "/nonexistent",
               "MOLTRUST_NOTIFY_STATE_DIR": tmp}
        gesammelt = []
        # Nicht pty.spawn: das erbt die Umgebung des Testlaufs und laesst sie
        # nicht setzen. Also selbst forken und mit execve die geleerte
        # Umgebung mitgeben.
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, skript], env)
        try:
            while True:
                try:
                    d = os.read(fd, 1024)
                except OSError:
                    break
                if not d:
                    break
                gesammelt.append(d)
        finally:
            _, status = os.waitpid(pid, 0)

    text = b"".join(gesammelt).decode("utf-8", "replace")
    assert "rueckgabe False" in text, text[-400:]
    assert "kein Abbruch" in text, text[-400:]
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, text[-400:]


# -- die Wache ---------------------------------------------------------------

def test_die_wache_findet_den_fall_vom_09_10(tmp_path):
    """Die Gegenprobe, an der die erste Fassung der Wache gescheitert ist.

    Sie rief notify._resolve() direkt ab und blieb deshalb gruen, als der
    Token wieder direkt aus os.environ gelesen wurde — sie prueft eine
    Funktion, die nie defekt war. Jetzt geht sie den echten Sendeweg bis vor
    den Transport.
    """
    quelle = (WURZEL / "app" / "notify.py").read_text(encoding="utf-8")
    kaputt = quelle.replace(
        '    token = _resolve("TELEGRAM_BOT_TOKEN")',
        '    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")', 1)
    assert kaputt != quelle, "die Zeile von 08:22 ist nicht mehr da"

    # Ein Baum, der nur in app/ von der Wurzel abweicht.
    baum = tmp_path / "baum"
    (baum / "app").mkdir(parents=True)
    for name in os.listdir(WURZEL / "app"):
        q = WURZEL / "app" / name
        if q.is_file():
            os.symlink(q, baum / "app" / name)
    (baum / "app" / "notify.py").unlink()
    (baum / "app" / "notify.py").write_text(kaputt, encoding="utf-8")
    (baum / "scripts").mkdir()
    os.symlink(WURZEL / "scripts" / "check_notify_kann_melden.py",
               baum / "scripts" / "check_notify_kann_melden.py")

    p = subprocess.run(
        [sys.executable, str(baum / "scripts" / "check_notify_kann_melden.py")],
        env={"HOME": os.path.expanduser("~"), "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(baum)},
        capture_output=True, text=True, timeout=180)
    letzte = p.stdout.strip().splitlines()[-1]
    assert letzte == "4", (p.stdout, p.stderr[-400:])
    assert "kein token/chat" in p.stdout


def test_die_wache_ist_gruen_auf_dem_jetzigen_stand():
    p = subprocess.run(
        [sys.executable, str(WURZEL / "scripts" / "check_notify_kann_melden.py")],
        env={"HOME": os.path.expanduser("~"), "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(WURZEL)},
        capture_output=True, text=True, timeout=180)
    assert p.stdout.strip().splitlines()[-1] == "0", (p.stdout, p.stderr[-400:])


def test_die_wache_nennt_keinen_wert():
    """Kein Token, keine chat_id, kein Praefix davon im Bericht."""
    p = subprocess.run(
        [sys.executable, str(WURZEL / "scripts" / "check_notify_kann_melden.py")],
        env={"HOME": os.path.expanduser("~"), "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(WURZEL)},
        capture_output=True, text=True, timeout=180)
    bericht = p.stdout + p.stderr
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        wert = notify._resolve(name)
        if not wert:
            continue
        assert wert not in bericht, f"{name} steht im Bericht"
        assert wert[:8] not in bericht, f"Praefix von {name} steht im Bericht"
    # Und keine Token-Form irgendwo im Bericht, auch keine fremde.
    assert not re.search(r"\d{6,}:[A-Za-z0-9_-]{30,}", bericht), bericht[:300]


def test_die_wache_sendet_nicht():
    """Der Transport ist abgeklemmt — sonst waere das vier Nachrichten die
    Stunde, um festzustellen, dass Nachrichten ankommen."""
    quelle = (WURZEL / "scripts"
              / "check_notify_kann_melden.py").read_text(encoding="utf-8")
    assert "notify.requests.post = _platzhalter" in quelle
    # Die Wache darf die echten Zustandsdateien nicht anfassen.
    assert "MOLTRUST_NOTIFY_STATE_DIR" in quelle


def test_die_wache_laeuft_mit_geleerter_umgebung():
    """Der Kern des Befunds: auf diesem Server war beides da.

    Eine Pruefung, die die Umgebung des Pruefers benutzt, waere am 09.10.
    gruen gewesen, waehrend deploy.sh ueber SSH nichts gefunden haette.
    """
    quelle = (WURZEL / "scripts"
              / "check_notify_kann_melden.py").read_text(encoding="utf-8")
    m = re.search(r"env=\{(.*?)\}", quelle, re.S)
    assert m, "kein eigenes env im subprocess-Aufruf"
    erlaubt = {"HOME", "PATH", "MOLTRUST_NOTIFY", "MOLTRUST_NOTIFY_STATE_DIR"}
    gesetzt = set(re.findall(r'"([A-Z_]+)":', m.group(1)))
    assert gesetzt <= erlaubt, gesetzt - erlaubt


# -- deploy.sh ---------------------------------------------------------------

def test_deploy_sh_laeuft_nicht_weiter_als_haette_es_gemeldet():
    quelle = DEPLOY_SH.read_text(encoding="utf-8")
    m = re.search(r"^telegram\(\) \{(.*?)^\}", quelle, re.S | re.M)
    assert m, "telegram() nicht gefunden"
    rumpf = m.group(1)
    # 70 muss behandelt sein: der atexit-Haken ueberschreibt die 5 der CLI.
    assert "70" in rumpf, "deploy.sh kennt MUTE_EXIT_CODE nicht"
    assert re.search(r"5\|70\).*return", rumpf), \
        "deploy.sh gibt bei verlorener Meldung keinen Fehler zurueck"
    # Gate und Drosselung bleiben folgenlos.
    assert re.search(r"3\).*return 0", rumpf), "Gate aus darf kein Fehler sein"
    assert re.search(r"4\).*return 0", rumpf), "gedrosselt darf kein Fehler sein"
