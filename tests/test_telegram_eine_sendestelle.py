"""Eine Sendestelle fuer Telegram, und eine Liste der noch offenen Ausnahmen.

Der Auftrag lautete: pruefen, dass api.telegram.org nur noch in notify steht.
Das ist heute nicht erreichbar — 29 Dateien bauen die Adresse selbst und
senden damit, notify ist eine davon. Ein Test, der das sofort verlangt, waere
von Anfang an rot und wuerde deshalb abgeschaltet oder uebersehen.

Darum eine Sperrklinke: die Liste unten nennt jede Datei, die am 09.10.2026
noch selbst sendet. Der Test faellt, wenn eine dazukommt, und ebenso, wenn
eine verschwindet, ohne aus der Liste gestrichen zu werden. Sie kann nur
kuerzer werden, und jede Streichung ist eine bewusste.

Unterschieden wird nach Verwendung, nicht nach Vorkommen: von den 39 Dateien
mit der Adresse nennen zehn sie nur — in einem Kommentar, in der
Redaktionsregel fuer Tokens, oder in einem Test, der genau das hier verbietet.
Mit grep sind die nicht zu trennen. Der Syntaxbaum trennt sie: eine
Sendestelle gibt das Literal an eine Transportfunktion weiter.
"""
import ast
import pathlib
import re

import pytest

MUSTER = re.compile(r"api\.telegram\.org")
TRANSPORT = {"post", "get", "request", "urlopen", "Request", "send"}

WURZEL = pathlib.Path(__file__).resolve().parent.parent

# Stand 09.10.2026. deploy.sh ist an diesem Tag aus der Liste gefallen — es
# ging von curl auf `python -m app.notify`. Die uebrigen 27 sind der Rest der
# Ausnahme; app/notify.py selbst ist die eine erlaubte Stelle.
#
# Nicht in der Liste, obwohl sie selbst sendet:
# workers/content_scout/.webdocs/scripts/notify_telegram.py. Die Datei steht in
# workers/content_scout/.gitignore und gehoert nicht zum Repo — sie zu listen
# hiesse, den Test von Dateien abhaengig zu machen, die auf einem anderen
# Rechner nicht existieren. Genau daran ist er beim ersten Lauf gefallen.
ERLAUBT = {"app/notify.py"}

NOCH_OFFEN = {
    "agents/ai_review.py",
    "agents/ai_review_v2.py",
    "agents/herald.py",
    "agents/herald_v3.py",
    "agents/reply_radar.py",
    "agents/supervision.py",
    "agents/syndicate.py",
    "agents/traffic_monitor.py",
    "agents/watchdog.py",
    "app/budget.py",
    "scripts/anchor_audit.py",
    "scripts/bazaar_index_check.sh",
    "scripts/bounty_r2_payout_list.py",
    "scripts/check_credits.sh",
    "scripts/concept_review.py",
    "scripts/daily_stats.sh",
    "scripts/discovery_snapshot.py",
    "scripts/endpoint_probe.py",
    "scripts/funnel_diff.py",
    "scripts/security_check.sh",
    "scripts/taskmarket_legal_check.sh",
    "scripts/threadwatch.py",
    "scripts/watch_a175_funding.py",
    "scripts/weekly_traffic.sh",
    "workers/content_scout/telegram.py",
}


def _py_sendet(p: pathlib.Path) -> bool:
    """Wahr, wenn ein Literal mit der Adresse in einem Transportaufruf steht.

    Kommentare tauchen im AST nicht auf, und das ist genau richtig: ein
    Kommentar sendet nichts.
    """
    try:
        baum = ast.parse(p.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return False

    literale = set()
    for k in ast.walk(baum):
        if isinstance(k, ast.Constant) and isinstance(k.value, str) \
                and MUSTER.search(k.value):
            literale.add(k)
        elif isinstance(k, ast.JoinedStr):
            text = "".join(v.value for v in k.values
                           if isinstance(v, ast.Constant)
                           and isinstance(v.value, str))
            if MUSTER.search(text):
                literale.add(k)
    if not literale:
        return False

    for k in ast.walk(baum):
        if not isinstance(k, ast.Call):
            continue
        f = k.func
        name = (f.attr if isinstance(f, ast.Attribute)
                else f.id if isinstance(f, ast.Name) else "")
        if name not in TRANSPORT:
            continue
        for arg in list(k.args) + [kw.value for kw in k.keywords]:
            for u in ast.walk(arg):
                if u in literale:
                    return True
    return False


def _sh_sendet(p: pathlib.Path) -> bool:
    """Fuer Shell: die Adresse in einer nicht kommentierten Zeile, mit curl in
    derselben oder einer der fuenf Zeilen davor — curl bricht seine Argumente
    gern ueber mehrere Zeilen um."""
    try:
        zeilen = p.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return False
    for i, ln in enumerate(zeilen, 1):
        if not MUSTER.search(ln) or ln.strip().startswith("#"):
            continue
        if "curl" in "\n".join(zeilen[max(0, i - 6):i]):
            return True
    return False


def _alle_sendestellen() -> set:
    treffer = set()
    for p in WURZEL.rglob("*"):
        if not p.is_file() or p.suffix not in (".py", ".sh"):
            continue
        # Verzeichnisse mit Punkt bleiben draussen, nicht nur .git.
        if any(t.startswith(".") for t in p.parts):
            continue
        if any(t in p.parts for t in ("venv", "node_modules", "site-packages",
                                      "build")):
            continue
        try:
            if not MUSTER.search(p.read_text(encoding="utf-8")):
                continue
        except (UnicodeDecodeError, OSError):
            continue
        sendet = _py_sendet(p) if p.suffix == ".py" else _sh_sendet(p)
        if sendet:
            treffer.add(str(p.relative_to(WURZEL)))
    return treffer


def test_keine_neue_sendestelle():
    """Die Liste darf nur kuerzer werden."""
    ist = _alle_sendestellen()
    neu = ist - ERLAUBT - NOCH_OFFEN
    assert neu == set(), (
        "neue Telegram-Sendestelle, die nicht durch notify geht: "
        + ", ".join(sorted(neu))
        + " — entweder auf `python -m app.notify` umstellen oder, mit Grund, "
          "in NOCH_OFFEN aufnehmen")


def test_geschlossene_ausnahmen_sind_gestrichen():
    """Wer die Liste verlaesst, wird daraus gestrichen.

    Ohne diese Richtung verwahrlost die Sperrklinke: eine Datei wird
    umgestellt, der Eintrag bleibt stehen, und die Liste behauptet laenger
    eine Ausnahme, als es eine gab.
    """
    ist = _alle_sendestellen()
    erledigt = NOCH_OFFEN - ist
    assert erledigt == set(), (
        "sendet nicht mehr selbst, steht aber noch in NOCH_OFFEN: "
        + ", ".join(sorted(erledigt)))


def test_deploy_sh_geht_durch_notify():
    """Der Fall, um den es am 09.10.2026 ging.

    deploy.sh hat am 07./08.10. 35 Meldungen in 24 Stunden geschickt, 32 davon
    "ok", und keine davon stand im Sendeprotokoll oder wurde gedrosselt.
    """
    s = (WURZEL / "ops/deploy/deploy.sh").read_text(encoding="utf-8")
    assert "app.notify" in s, "deploy.sh ruft notify nicht"
    assert not _sh_sendet(WURZEL / "ops/deploy/deploy.sh"), \
        "deploy.sh sendet wieder selbst per curl"
    # Und kein Rueckfall daneben: ein curl auf die Telegram-Adresse, der nur
    # dann greift, wenn notify schweigt, ist dieselbe Ausnahme in seltener.
    assert "api.telegram.org" not in s, \
        "die Telegram-Adresse steht wieder in deploy.sh"


@pytest.mark.parametrize("datei", sorted(NOCH_OFFEN))
def test_jede_offene_ausnahme_existiert_noch(datei):
    """Ein Eintrag fuer eine Datei, die es nicht mehr gibt, taeuscht Arbeit vor.

    Ohne das haelt die Liste geloeschte Dateien als Ausnahme fest und wird nie
    wieder leer.
    """
    assert (WURZEL / datei).exists(), f"{datei} aus NOCH_OFFEN existiert nicht"
