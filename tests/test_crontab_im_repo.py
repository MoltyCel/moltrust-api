"""Was aus der Crontab laeuft, liegt im Repo — und findet seine Pakete selbst.

Am 09.10.2026 lief `~/ops-watch/watch_attestation_window.py` einen Tag nach
Ablauf seines Fensters weiter (WINDOW_END 2026-10-08 16:41Z) und sendete per
eigenem urllib-Aufruf an api.telegram.org. Weder die URL-Durchsicht vom 07.10.
noch die Telegram-Sperre haben es gesehen: beide lesen das Repo, und die Datei
lag daneben.

Die Bestandsaufnahme ergab 16 von 88 aktiven Crontab-Zeilen ausserhalb des
Repos, davon vier sendende — plus Zeile 81, die mit einem eigenen curl sendet
und in keiner Datei steht.

Diese Tests sind Sperrklinken auf dem Schnappschuss `ops/crontab.txt`, nicht
auf der lebenden Crontab: ein Test, der von der Crontab des ausfuehrenden
Rechners abhaengt, ist auf jedem anderen rot.
"""
import ast
import pathlib
import re

import pytest

WURZEL = pathlib.Path(__file__).resolve().parent.parent
SNAPSHOT = WURZEL / "ops" / "crontab.txt"

# Stand 09.10.2026. Jede Zeile hier ist ein Programm, das aus der Crontab
# laeuft und nicht im Repo liegt. Die Liste darf nur kuerzer werden.
#
# Nicht darunter, weil stillgelegt: ~/ops-watch/watch_attestation_window.py.
# Nicht darunter, weil die Zeile entfernt ist: ~/probe_report_20261002.py —
# Einmalprobe vom 02.10., Takt `0 7 2 10 *`, haette 2027 wieder gefeuert. Der
# Kommentarblock in der Crontab sagte selbst, dass die Zeile danach zu loeschen ist.
AUSSERHALB = {
    "~/backup_db.sh",
    "~/monitor.sh",
    "~/scripts/backup_moltrust.sh",
    "~/scripts/daily_journal.sh",
    "~/moltrust-knowledge/weekly_summary.py",
    "~/moltycelbot/scripts/discovery.py",
    "~/moltguard/dist/scripts/x402_manifest.js",
    "~/moltguard/dist/scripts/x402_facilitator_check.js",
    "/usr/sbin/logrotate",
    "~/daily_report.py",
}


def _aktive_zeilen():
    if not SNAPSHOT.exists():
        pytest.skip("kein Crontab-Schnappschuss im Baum")
    raus = []
    for i, z in enumerate(SNAPSHOT.read_text(encoding="utf-8").splitlines(), 1):
        if not z.strip() or z.lstrip().startswith("#"):
            continue
        if re.match(r"^[A-Z_]+=", z.strip()):
            continue
        raus.append((i, z))
    return raus


def test_die_stillgelegte_wache_steht_nicht_mehr_drin():
    """Der Fund vom 09.10., als Negativ."""
    text = SNAPSHOT.read_text(encoding="utf-8")
    aktiv = [z for z in text.splitlines()
             if "watch_attestation_window" in z and not z.lstrip().startswith("#")]
    assert aktiv == [], aktiv


# Keine mehr. Am 09.10.2026 waren es vier: Zeilen, die selbst per curl an
# Telegram schickten, mit Programmtext, der in keiner Datei stand und von
# keinem Sucher erreichbar war — auch nicht von einem, der das ganze
# Dateisystem liest. Gefunden erst, als dieser Test die ZEILEN durchsuchte
# statt der aufgerufenen Programme; die Bestandsaufnahme davor hatte nur eine
# davon. Sie liegen jetzt in ops/cron/ und senden ueber notify.
#
# Null, nicht vier: eine Sperrklinke, die den geschlossenen Zustand nicht
# festhaelt, laesst ihn wieder aufgehen.
TELEGRAM_IN_DER_ZEILE = 0


def test_keine_neue_telegram_sendestelle_in_der_crontab():
    """Sperrklinke auf die vier bekannten. Es darf nur weniger werden."""
    treffer = [nr for nr, z in _aktive_zeilen() if "api.telegram.org" in z]
    assert len(treffer) <= TELEGRAM_IN_DER_ZEILE, (
        f"neue Crontab-Zeile, die selbst an Telegram sendet: {treffer}")


def test_kein_programm_aus_tmp():
    """/tmp ueberlebt keinen Neustart.

    Am 09.10.2026 rief eine Zeile /tmp/refresh_tweets_0800.py, Takt
    `0 8 6 3 *`. Die Datei war weg, der Job waere am 6. Maerz still
    gescheitert — und mit zurueckgeholter Datei haette er sieben Tweets
    geloescht und einen Thread auf @MolTrust gepostet, was nach WORKFLOW 0.1
    nicht gedeckt ist. Zeile entfernt, Logauszug in
    ~/Downloads/tweet_refresh-2026-03-06.log.
    """
    treffer = [nr for nr, z in _aktive_zeilen()
               if re.search(r"/tmp/\S+\.(py|sh)", z)]
    assert treffer == [], f"Programm aus /tmp in der Crontab: {treffer}"


def test_die_liste_der_fremden_programme_waechst_nicht():
    """Sperrklinke. Sie darf nur kuerzer werden.

    Geprueft wird gegen den Schnappschuss, nicht gegen die lebende Crontab:
    die Zahl soll sich aendern, wenn jemand den Schnappschuss aendert, und
    nicht, wenn jemand auf einem anderen Rechner etwas anderes faehrt.
    """
    zeilen = _aktive_zeilen()
    # Grob gezaehlt: Zeilen, die einen Pfad ausserhalb des Repos nennen.
    fremd = set()
    for _nr, z in zeilen:
        for m in re.finditer(r"(/home/moltstack/[^\s>&;|]+|/tmp/[^\s>&;|]+"
                             r"|/usr/sbin/[^\s>&;|]+)", z):
            pfad = m.group(1)
            if pfad.startswith("/home/moltstack/moltstack/"):
                continue
            if pfad.startswith("/home/moltstack/.") or pfad.endswith(".log"):
                continue
            if "/logs/" in pfad or "/Downloads/" in pfad:
                continue
            kurz = pfad.replace("/home/moltstack/", "~/")
            if kurz.endswith((".py", ".sh", ".js")) or kurz == "/usr/sbin/logrotate":
                fremd.add(kurz)
    neu = fremd - AUSSERHALB
    assert neu == set(), (
        "neues Programm ausserhalb des Repos in der Crontab: "
        + ", ".join(sorted(neu))
        + " — ins Repo legen, oder mit Grund in AUSSERHALB aufnehmen")


# -- der Suchpfad ------------------------------------------------------------

REPO_PAKETE = {"app", "agents", "monitor", "scripts", "services", "workers",
               "operator", "sdk", "packages", "activity"}

def _setzt_pfad(baum) -> bool:
    """Ein Aufruf von sys.path.insert oder .append auf Modulebene.

    Mit dem AST, nicht als Zeichenkette: es gibt mindestens drei
    Schreibweisen im Baum — `sys.path.insert(0, os.path.dirname(...))`,
    `_sys.path.insert(...)` unter einem Alias, und
    `sys.path.insert(0, os.path.abspath(os.path.join(...)))`. Die erste
    Fassung dieses Tests suchte eine davon und meldete acht Dateien als
    defekt, die in Ordnung waren — die Probe hatte sie vorher schon als
    lauffaehig gemessen.
    """
    for k in ast.walk(baum):
        if not isinstance(k, ast.Call) or not isinstance(k.func, ast.Attribute):
            continue
        if k.func.attr not in ("insert", "append"):
            continue
        ziel = k.func.value
        if (isinstance(ziel, ast.Attribute) and ziel.attr == "path"
                and isinstance(ziel.value, ast.Name)
                and ziel.value.id in ("sys", "_sys")):
            return True
    return False


def _cron_programme():
    """Repo-Python-Dateien, die der Schnappschuss als Skript aufruft."""
    raus = set()
    for _nr, z in _aktive_zeilen():
        if " -m " in z:
            continue        # -m legt das Arbeitsverzeichnis auf sys.path
        for m in re.finditer(r"(?:/home/moltstack/moltstack/|\s)"
                             r"((?:app|agents|monitor|scripts|services|workers"
                             r"|operator)/[A-Za-z0-9_/]+\.py)", z):
            p = WURZEL / m.group(1)
            if p.exists():
                raus.add(p)
    return raus


def test_jedes_cron_skript_findet_seine_pakete_selbst():
    """Ohne PYTHONPATH aus der Crontab.

    Python legt beim Skriptaufruf das Verzeichnis des Skripts auf sys.path,
    nicht das Arbeitsverzeichnis. Ein `cd` ins Repo reicht also nicht, und
    eine Crontab, die den Suchpfad setzt, ist dieselbe unsichtbare
    Ueberstimmung wie POLL_RPC_URL.

    Am 09.10. betraf das 14 Crontab-Zeilen in 13 Dateien. Gemessen wurde es,
    nicht geschlossen: eine erste Probe mit `python -c` meldete 5 statt 14,
    weil dort das Arbeitsverzeichnis auf sys.path liegt und nicht das
    Skriptverzeichnis.
    """
    ohne = []
    for p in sorted(_cron_programme()):
        quelle = p.read_text(encoding="utf-8")
        try:
            baum = ast.parse(quelle)
        except SyntaxError:
            continue
        braucht = set()
        for k in baum.body:
            if isinstance(k, ast.Import):
                braucht |= {a.name.split(".")[0] for a in k.names}
            elif isinstance(k, ast.ImportFrom) and k.level == 0 and k.module:
                braucht.add(k.module.split(".")[0])
        if not (braucht & REPO_PAKETE):
            continue
        if p.parent == WURZEL:
            continue        # liegt in der Wurzel, findet alles
        if not _setzt_pfad(baum):
            ohne.append(str(p.relative_to(WURZEL)))
    assert ohne == [], (
        "ruft aus der Crontab, importiert ein Repo-Paket und setzt sys.path "
        "nicht selbst: " + ", ".join(ohne))


def test_der_schnappschuss_setzt_kein_pythonpath():
    """Nachdem die 13 Dateien ihren Pfad selbst setzen, hat die Crontab dort
    nichts mehr zu bestimmen."""
    treffer = [nr for nr, z in _aktive_zeilen() if "PYTHONPATH=" in z]
    kopf = [i for i, z in enumerate(
        SNAPSHOT.read_text(encoding="utf-8").splitlines(), 1)
        if re.match(r"^PYTHONPATH=", z.strip())]
    assert treffer == [], f"PYTHONPATH in Job-Zeilen: {treffer}"
    assert kopf == [], f"PYTHONPATH als globale Zuweisung in Zeile {kopf}"
