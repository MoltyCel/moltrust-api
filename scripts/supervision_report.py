"""The week in self-tests. Sunday 06:50 UTC, alongside the existing report.

Counts rather than impressions: how often the supervisor ran, how often it
found something, what was corrected, and which pipeline keeps coming back.

The last of those is the point. **A pipeline that needs the same correction
three times in a week is reported as a construction fault and not repaired
again.** A repair that runs weekly is not maintenance, it is a workaround with
a cron entry, and the thing it works around never gets fixed because nothing
ever looks broken.

Reads data/supervision_history.jsonl (one row per supervisor run) and
data/selfheal_state.json (one timestamp per correction). Writes nothing except
the Telegram message.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify, paths

BASE = os.path.expanduser("~/moltstack")
HISTORY = os.path.join(BASE, "data", "supervision_history.jsonl")
def heal_state() -> str:
    """The same file selfheal writes, resolved the same way.

    It used to be bound at import here while selfheal resolves it at call time;
    under MOLTRUST_ROOT the two then pointed at different files, so the weekly
    report would have counted corrections out of the production state while a
    test wrote the redirected one.
    """
    return paths.data("selfheal_state.json")
# The invariant runner's GREEN fixes, one line per execution. A different store
# from selfheal's, because a different thing decided to run them — and both
# belong in the same weekly list, or the week looks quieter than it was.
AUTOFIX_LOG = os.path.expanduser("~/Downloads/selftest/autofix.jsonl")
# One file per day, each holding the runs of that day.
SELFTEST_DIR = os.path.expanduser("~/Downloads/selftest")
CATALOGUE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "docs", "invariants")

# Findings that are not a query result and would otherwise go unreported: a
# check that ran and measured the wrong thing leaves no trace in its own
# output, because its output was a number. Each carries the week it belongs to
# and drops out of the report by itself afterwards, so this list cannot become
# a permanent banner.
NAMED_FINDINGS = [
    {"week": "2026-W40", "id": "b-registry-equals-pypi",
     "what": "las den ersten statt den neuesten Registry-Eintrag und meldete "
             "einen Drift, den es nicht gab (PyPI 1.2.4 gegen 0.3.2 vom "
             "Februar; isLatest sitzt auf 1.2.4)"},
    {"week": "2026-W40", "id": "c-cron-sudo-permitted",
     "what": "verglich nur den Binaerpfad und meldete gruen auf genau dem "
             "Defekt, fuer den sie geschrieben war — /usr/bin/install steht "
             "in der NOPASSWD-Liste, mit einer anderen Argumentliste"},
    {"week": "2026-W40", "id": "e-one-writer-per-artefact",
     "what": "zaehlte Prosa als Schreiber: ein Kommentar in app/main.py und "
             "ein Docstring, der den install-Befehl zitiert"},
    {"week": "2026-W40", "id": "ops/crontab.txt",
     "what": "wich um 38 Zeilen vom Server ab und trug im Kopf "
             "\"Apply with: crontab ops/crontab.txt\" — das haette 38 "
             "laufende Jobs geloescht"},
]
EXPECTED_PER_DAY = 24          # the workflow runs at :17, every hour
REPEAT_IS_DESIGN_FAULT = 3     # same correction, same week


def rows(days: int) -> list[dict]:
    cut = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=days)).isoformat()
    out = []
    try:
        with open(HISTORY) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (r.get("at") or "") >= cut:
                    out.append(r)
    except FileNotFoundError:
        return []
    return out


def corrections(days: int) -> dict[str, int]:
    cut = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=days)).isoformat()
    try:
        st = json.load(open(heal_state()))
    except Exception:
        return {}
    out: dict[str, int] = {}
    for key, stamps in (st.get("runs") or {}).items():
        n = sum(1 for s in stamps if s >= cut)
        if n:
            out[key] = n
    return out


def autofixes(days: int) -> list[dict]:
    """Executed GREEN autofixes inside the window, newest first."""
    cut = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=days)).isoformat()
    out = []
    try:
        with open(AUTOFIX_LOG) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (r.get("at") or "") >= cut:
                    out.append(r)
    except FileNotFoundError:
        return []
    return sorted(out, key=lambda r: r.get("at") or "", reverse=True)


def activation() -> dict:
    """External DIDs holding an anchored track record. No credential count.

    The count of credentials is not an activation figure and would be read as
    one. Three agents polled the issuing endpoint between 1 and 3 October 2026
    and were issued one per call; four DIDs account for most of what exists.
    The number of DIDs is unaffected by that, which is why it is the one
    reported.
    """
    sql = """
        SELECT count(*) FROM (
          SELECT a.did FROM agents a
           WHERE a.revoked_at IS NULL
             AND a.agent_type <> 'system'
             AND coalesce(a.platform, '') NOT IN ('test', 'own_test', 'ownify')
             AND EXISTS (SELECT 1 FROM credentials c
                           JOIN credential_anchors k ON k.credential_id = c.id
                          WHERE c.subject_did = a.did AND NOT c.revoked
                            AND c.credential_type = 'TrackRecordCredential')
           GROUP BY 1) x"""
    loops = """
        SELECT count(*) FROM (
          SELECT subject_did FROM credentials
           WHERE NOT revoked AND credential_type = 'TrackRecordCredential'
           GROUP BY 1 HAVING count(*) > 2) y"""
    out = {}
    for key, q in (("dids", sql), ("loops", loops)):
        r = subprocess.run(
            ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
             "-X", "-A", "-t", "-c", q],
            capture_output=True, text=True, timeout=120)
        if r.returncode or not r.stdout.strip().isdigit():
            # No number rather than a wrong one.
            return {"error": (r.stderr or "keine Zahl").strip()[:120]}
        out[key] = int(r.stdout.strip())
    # Die Zahl bewegt sich taeglich, also reist ihr Stichtag mit. Ohne ihn
    # faellt der Abschnitt durch undated_sections() — und das zu Recht.
    out["asof"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return out


def gate_usage(days: int) -> dict:
    """Wird das Gate benutzt — und kommt jemand durch?

    Die Aktivierungszahl sagt, dass ein Agent uns einmal aufgerufen hat. Sie
    sagt nicht, ob er das Gate bedient, und genau dort liegt der Unterschied
    zwischen Neugier und Benutzung. Am 06.10.2026 kostete das eine Antwort:
    did:moltrust:cad78d76790d4a40 legte seit dem 02.10. alle dreißig Minuten
    ein Attestat vor, wurde jedes Mal zurueckgewiesen und kam in keiner Zahl
    vor. Vier Tage lang.

    Drei Stufen, absteigend: Aufrufe, davon mit vorgelegtem Attestat, davon
    angenommen. `attestation_missing` heißt, dass gar kein Attestat kam — der
    Normalfall jedes unauthentifizierten Aufrufs an einen bepreisten Pfad und
    deshalb die Trennlinie zur zweiten Stufe.

    Agenten werden nur gezaehlt, wo die DID feststeht, und das setzt voraus,
    dass das Attestat verifiziert hat. Ein Aufrufer, dessen Attestat an Form
    oder Signatur scheitert, hinterlässt keine DID; diese Fälle stehen als
    eigene Zahl daneben, statt die Agentenzahl stillschweigend zu drücken.
    """
    # psql setzt :tage selbst ein, statt die Abfrage in Python zusammenzusetzen.
    # bandit B608 meldet die Form; der Wert ist hier eine Zahl, aber die Vorlage
    # wird kopiert.
    sql = """
        WITH g AS (SELECT * FROM gate_decisions
                    WHERE ts > now() - make_interval(days => :tage))
        SELECT (SELECT count(*) FROM g),
               (SELECT count(*) FROM g WHERE reason <> 'attestation_missing'),
               (SELECT count(*) FROM g WHERE reason = 'ok'),
               (SELECT count(DISTINCT did) FROM g WHERE did IS NOT NULL),
               (SELECT count(DISTINCT did) FROM g
                 WHERE did IS NOT NULL AND reason = 'ok'),
               (SELECT count(*) FROM g
                 WHERE did IS NULL AND reason <> 'attestation_missing')"""
    # Über stdin, nicht über -c: psql ersetzt Variablen in Dateien und auf
    # stdin, bei -c bleibt der Doppelpunkt stehen.
    r = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-t", "-F", "\x1f", "-v", f"tage={int(days)}"],
        input=sql, capture_output=True, text=True, timeout=120)
    parts = (r.stdout or "").strip().split("\x1f")
    if r.returncode or len(parts) != 6 or not all(p.strip().isdigit() for p in parts):
        # Keine Zahl ist besser als eine falsche.
        return {"error": (r.stderr or "keine Zahl").strip()[:120]}
    keys = ("aufrufe", "mit_attestat", "angenommen",
            "agenten", "agenten_angenommen", "attestat_ohne_did")
    return dict(zip(keys, (int(p) for p in parts)))


def psql(sql: str, **variables: object) -> list:
    """Abfrage ueber stdin, Werte als psql-Variablen.

    Ueber stdin und nicht ueber `-c`: psql ersetzt Variablen in Dateien und auf
    stdin, bei `-c` bleibt der Doppelpunkt stehen. `:'name'` quotet psql selbst,
    sodass hier keine Abfrage aus Python zusammengesetzt wird.
    """
    args = ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
            "-X", "-A", "-t", "-F", "\x1f"]
    for name, value in variables.items():
        args += ["-v", f"{name}={value}"]
    out = subprocess.run(args, input=sql, capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:200])
    return [ln.split("\x1f") for ln in out.stdout.splitlines() if ln.strip()]


def gate_abandoners() -> dict:
    """Kommt einer der Agenten zurueck, die am Gate aufgegeben haben?

    Acht Agenten haben zwischen dem 29.09. und 06.10.2026 ein Attestat
    vorgelegt und kamen nie durch; alle acht an `proof_invalid`, demselben
    Defekt, den moltguard#57 behoben hat. Sechs weitere haben es durch
    Probieren selbst geloest. Die Liste in docs/gate-abandoners.json ist
    deshalb keine Buchfuehrung, sondern die Messung: wenn die veroeffentlichte
    Vorschrift und die 402-Antwort wirken, kommt jemand zurueck.

    Gezaehlt wird nach der letzten festgehaltenen Spur je Agent, nicht nach
    einem festen Datum — sonst meldet die Pruefung den Verkehr, der schon in
    der Liste steht, als Rueckkehr.
    """
    path = os.path.join(os.path.dirname(__file__), "..", "docs", "gate-abandoners.json")
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - keine Liste ist keine Null
        return {"error": f"{type(exc).__name__}: {exc}"[:120]}

    agents = doc.get("agenten") or []
    if not agents:
        return {"error": "Liste ohne Agenten"}

    back, quiet = [], 0
    for a in agents:
        did, since = a.get("did"), a.get("letzte_gate_entscheidung")
        if not did or not since:
            continue
        try:
            rows = psql(
                "SELECT (SELECT count(*) FROM gate_decisions g "
                "         WHERE g.did = :'did' AND g.ts > :'seit'), "
                "       (SELECT count(*) FROM gate_decisions g "
                "         WHERE g.did = :'did' AND g.ts > :'seit' AND g.reason = 'ok')",
                did=did, seit=since)
        except Exception as exc:  # noqa: BLE001 - keine Zahl ist besser als eine falsche
            return {"error": f"{type(exc).__name__}: {exc}"[:120]}
        if not rows or len(rows[0]) != 2 or not all(x.strip().isdigit() for x in rows[0]):
            return {"error": f"keine Zahl fuer {did[:24]}"}
        n, ok = (int(x) for x in rows[0])
        if n:
            back.append({"did": did, "name": a.get("name"), "entscheidungen": n,
                         "angenommen": ok})
        else:
            quiet += 1
    return {"gesamt": len(agents), "zurueck": back, "still": quiet,
            "erfasst": doc.get("erfasst", "ohne Stichtag")}


def selftest_week(days: int) -> dict:
    """Runs, findings and the catalogue size, out of the runner's own reports."""
    cut = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=days)).isoformat()
    try:
        catalogue = len([f for f in os.listdir(CATALOGUE) if f.endswith(".yaml")])
    except OSError:
        catalogue = None
    runs, findings, meta_bad = 0, collections.Counter(), 0
    try:
        files = sorted(os.listdir(SELFTEST_DIR))
    except OSError:
        files = []
    for name in files:
        if not name.endswith(".json") or name == "autofix.jsonl":
            continue
        try:
            doc = json.load(open(os.path.join(SELFTEST_DIR, name)))
        except (OSError, ValueError):
            continue
        for run in doc if isinstance(doc, list) else [doc]:
            if (run.get("started") or "") < cut:
                continue
            runs += 1
            if (run.get("meta_invariante") or {}).get("status") not in (None, "OK"):
                meta_bad += 1
            for r in run.get("ergebnisse") or []:
                if r.get("status") in ("FAIL", "ERROR"):
                    findings[r.get("id") or "?"] += 1
    return {"catalogue": catalogue, "runs": runs, "meta_bad": meta_bad,
            "findings": findings.most_common(8)}


def tick_ratio(days: int = 7) -> dict | None:
    """How reliably the external schedule fired, as a running measurement.

    It is here rather than in an alarm because 15 % is GitHub's queue and not
    our state. Reported weekly so the decision to bring the timing expectation
    back is made on a trend and not on a mood.
    """
    import subprocess
    try:
        r = subprocess.run(
            [os.path.join(BASE, "venv", "bin", "python"),
             os.path.join(BASE, "scripts", "check_external_runs.py"),
             "--ratio", "--days", str(days), "--json"],
            capture_output=True, text=True, timeout=120, cwd=BASE)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if r.returncode != 0 or not r.stdout.strip():
        return {"error": (r.stderr or "").strip().splitlines()[-1][:140]
                if r.stderr else f"exit {r.returncode}"}
    try:
        return {"rows": json.loads(r.stdout)}
    except json.JSONDecodeError:
        return {"error": "Antwort war kein JSON"}


def collect(days: int = 7) -> dict:
    hist = rows(days)
    lights = collections.Counter(r.get("light") for r in hist)
    offenders = collections.Counter()
    for r in hist:
        for check in (r.get("offenders") or {}):
            offenders[check] += 1
    fixes = corrections(days)
    repeats = {k: v for k, v in fixes.items() if v >= REPEAT_IS_DESIGN_FAULT}
    expected = EXPECTED_PER_DAY * days
    auto = autofixes(days)
    iso_week = datetime.datetime.now(datetime.timezone.utc).strftime("%G-W%V")
    return {"days": days, "runs": len(hist), "expected_runs": expected,
            "ticks": tick_ratio(days),
            "autofixes": auto,
            "selftest": selftest_week(days),
            "named": [n for n in NAMED_FINDINGS if n["week"] == iso_week],
            "activation": activation(),
            "gate": gate_usage(days),
            "abandoners": gate_abandoners(),
            "green": lights.get("green", 0), "yellow": lights.get("yellow", 0),
            "red": lights.get("red", 0), "broken": lights.get("broken", 0),
            "offenders": offenders.most_common(8), "fixes": fixes,
            "repeats": repeats}



# --- Stichtagsregel ---------------------------------------------------------
#
# `docs/zaehlregel.md` hält fest: jede Nennung einer beweglichen Zahl trägt
# ihren Stichtag, oder sie unterbleibt. Als Satz in einer Datei hat die Regel
# am 06.10.2026 nicht gehalten — 47 aktivierte DIDs wurden ohne Datum zitiert
# und waren am Tag danach 66, am Abend 74. Deshalb steht sie hier als Prüfung.
#
# Mechanisch heißt „trägt ihren Stichtag": im Abschnitt steht entweder ein
# Fenster („in 7 Tagen", „letzte 24 h") oder ein Zeitpunkt (ISO-Datum oder
# HH:MM UTC). Geprüft wird je Abschnitt, nicht je Zahl: ein Fenster gilt für
# die Zahlen darunter, und eine Prüfung je Zahl würde an Versionsnummern und
# PR-Nummern hängenbleiben.
#
# Abschnitte ohne bewegliche Zahl stehen nicht in der Liste. Wer einen neuen
# Abschnitt mit einer Zahl aus einer Live-Abfrage ergänzt, trägt ihn hier ein;
# die Prüfung kann nicht erraten, woher eine Zahl kommt.
DATED_SECTIONS = ("Aktivierung", "Gate-Nutzung", "Gate-Aufgeber",
                  "Externer Zeitplan")

_WINDOW = re.compile(
    r"\b(?:in|letzte[nr]?|seit|über)\s+\d+\s*(?:Tag|Tage|Tagen|Stunde|Stunden|h|min)\b"
    r"|\b\d+\s*(?:Tage|Tagen|Stunden|h)\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{2}:\d{2}(?::\d{2})?\s*(?:UTC|Z)\b"
    r"|\b\d{2}\.\d{2}\.(?:\d{4}|\d{2})?\b")
_FIGURE = re.compile(r"(?<![#\w.])\d+(?![\w.])")


def undated_sections(report: str) -> list[str]:
    """Abschnitte mit einer Zahl, aber ohne Fenster oder Zeitpunkt.

    Gibt die Überschriften zurück. Leer heißt: jede bewegliche Zahl im Bericht
    ist datiert.
    """
    offending = []
    current = None
    body: list[str] = []

    def close():
        if current is None:
            return
        text = " ".join(body)
        if _FIGURE.search(re.sub(r"<[^>]+>", "", text)) and not _WINDOW.search(text):
            offending.append(current)

    for line in report.splitlines():
        head = re.match(r"\s*<b>(.+?)</b>\s*$", line)
        if head:
            close()
            title = head.group(1)
            current = title if any(d in title for d in DATED_SECTIONS) else None
            body = [title]
        elif current is not None:
            body.append(line)
    close()
    return offending


def format_report(k: dict) -> str:
    L = [f"🔍 <b>Selbstüberwachung — {k['days']} Tage</b>", ""]
    missing = k["expected_runs"] - k["runs"]
    if k["runs"] == 0:
        # No history is not a clean week. It means the supervisor never ran, or
        # never reached the server, and both are worse than a red week.
        L += ["<b>Keine Selbsttests aufgezeichnet.</b> Das ist keine ruhige "
              "Woche — der Supervisor hat den Server nicht erreicht oder der "
              "Workflow lief nicht. Prüfen: Actions-Historie von "
              "<code>supervise</code>."]
        return "\n".join(L)
    L += [f"Läufe: <b>{k['runs']}</b> von {k['expected_runs']} erwartet"
          + (f" — <b>{missing} fehlen</b>" if missing > 0 else "")]
    L += [f"grün {k['green']} · gelb {k['yellow']} · rot {k['red']}"
          + (f" · kaputt {k['broken']}" if k["broken"] else "")]

    t = k.get("ticks") or {}
    if t.get("error"):
        L += ["", f"<b>Externer Zeitplan</b>: nicht messbar — {t['error']}"]
    elif t.get("rows"):
        L += ["", "<b>Externer Zeitplan (Best-Effort, kein Alarm)</b>"]
        for r in t["rows"]:
            if r.get("note"):
                L.append(f"· {r['workflow']}: {r['note']}")
                continue
            late = ""
            if r.get("median_delay_minutes") is not None:
                late = (f", Verzug median {r['median_delay_minutes']} / max "
                        f"{r['worst_delay_minutes']} min")
            L.append(f"· {r['workflow']} <code>{r['cron']}</code>: "
                     f"{r['fired']}/{r['due']} Takte = <b>{r['pct']} %</b>{late}")
            if r.get("pct") is not None and r["pct"] >= 70:
                L.append(f"  <b>Über 70 %</b> — die Zeiterwartung (3 h) kann "
                         f"zurückkommen, siehe config/expectations.yaml.")
    if k["offenders"]:
        L += ["", "<b>Auffällig, nach Häufigkeit</b>"]
        for check, n in k["offenders"]:
            L.append(f"· {check} — {n}×")
    if k["fixes"]:
        L += ["", "<b>Ausgeführte Korrekturen</b>"]
        for key, n in sorted(k["fixes"].items(), key=lambda kv: -kv[1]):
            L.append(f"· {key} — {n}×")
    st = k.get("selftest") or {}
    L += ["", "<b>Selbsttest — Invarianten</b>"]
    if st.get("catalogue") is None:
        L += ["Katalog nicht lesbar — keine Zahl."]
    else:
        L += [f"Katalog: <b>{st['catalogue']}</b> Invarianten · "
              f"Läufe in {k['days']} Tagen: <b>{st['runs']}</b>"
              + (f" · Meta-Invariante {st['meta_bad']}× nicht OK"
                 if st.get("meta_bad") else " · Meta-Invariante durchweg OK")]
        if st.get("findings"):
            L += ["Befunde, nach Häufigkeit:"]
            for inv, n in st["findings"]:
                L.append(f"· {inv} — {n}×")
        else:
            L += ["Keine Befunde."]
    L += [f"Ausgeführte Autofixes: <b>{len(k.get('autofixes') or [])}</b>"
          + ("" if k.get("autofixes") else " — keiner")]

    if k.get("named"):
        L += ["", "<b>Prüfer, die gelaufen sind und nichts gemessen haben</b>"]
        for n in k["named"]:
            L.append(f"· <code>{n['id']}</code> {n['what']}")
        L += ["Alle vier behoben und mit Herkunft in der jeweiligen Datei. "
              "Ein Prüfer, der das falsche Ding vergleicht, hinterlässt in "
              "seiner eigenen Ausgabe keine Spur — seine Ausgabe war eine Zahl."]

    act = k.get("activation") or {}
    L += ["", "<b>Aktivierung</b>"]
    if act.get("error"):
        L += [f"Nicht gemessen: {act['error']}"]
    else:
        L += [f"<b>{act.get('dids')}</b> externe DIDs mit Track Record, "
              f"Stand {act.get('asof', 'ohne Stichtag')}."]
        L += [f"Fußnote: {act.get('loops')} DIDs tragen mehr als zwei Track "
              f"Records, weil sie den ausstellenden Endpunkt gepollt haben "
              f"statt den Trust Score. Die Ursache lag bei uns — der Endpunkt "
              f"prägte bei jedem Aufruf ein neues Credential, der Aufgabentext "
              f"sagte „poll until it appears\" und war nach der Anlage nicht "
              f"mehr änderbar. Seit dem Idempotenz-Fix gibt derselbe Aufruf "
              f"das vorhandene Credential zurück. Eine Credential-Zahl steht "
              f"hier bewusst nicht: sie wäre keine Aktivierungszahl."]

    ab = k.get("abandoners") or {}
    L += ["", "<b>Gate-Aufgeber</b>"]
    if ab.get("error"):
        L += [f"Nicht gemessen: {ab['error']}"]
    else:
        L += [f"{ab.get('gesamt')} Agenten haben am Gate aufgegeben, erfasst "
              f"{ab.get('erfasst')}. Seitdem zurueckgekommen: "
              f"<b>{len(ab.get('zurueck') or [])}</b>, weiter still "
              f"{ab.get('still')}."]
        for r in (ab.get("zurueck") or []):
            L.append(f"· <b>{r.get('name') or r.get('did','?')[:24]}</b> — "
                     f"{r.get('entscheidungen')} Entscheidungen, davon "
                     f"{r.get('angenommen')} angenommen.")
        if not (ab.get("zurueck") or []):
            L += ["Keiner. Das ist die Messung, ob die veroeffentlichte "
                  "Vorschrift und die 402-Antwort wirken — nicht eine "
                  "Erfolgsmeldung."]

    g = k.get("gate") or {}
    L += ["", "<b>Gate-Nutzung</b>"]
    if g.get("error"):
        L += [f"Nicht gemessen: {g['error']}"]
    else:
        L += [f"Aufrufe in {k.get('days')} Tagen: <b>{g.get('aufrufe')}</b> · "
              f"davon mit vorgelegtem Attestat <b>{g.get('mit_attestat')}</b> · "
              f"davon angenommen <b>{g.get('angenommen')}</b>."]
        L += [f"Agenten: <b>{g.get('agenten')}</b> namentlich erkennbar, davon "
              f"<b>{g.get('agenten_angenommen')}</b> mindestens einmal "
              f"angenommen. Für die erste Stufe gibt es keine Agentenzahl: "
              f"ein Name entsteht erst, wenn das Attestat verifiziert hat."]
        stumm = (g.get("agenten") or 0) - (g.get("agenten_angenommen") or 0)
        if stumm > 0:
            L += [f"{stumm} Agenten legen ein Attestat vor und kommen nie "
                  f"durch. Das ist die Zahl, die zeigt, ob jemand am Gate "
                  f"hängt — sie haette Buffy Worker vier Tage früher "
                  f"gezeigt."]
        if g.get("attestat_ohne_did"):
            L += [f"Fußnote: {g.get('attestat_ohne_did')} Aufrufe legten ein "
                  f"Attestat vor, das an Form oder Signatur scheiterte; sie "
                  f"tragen keine DID und stecken in keiner Agentenzahl."]

    if k.get("autofixes"):
        L += ["", "<b>Invarianten-Autofix (GRÜN), je Ausführung</b>"]
        for r in k["autofixes"]:
            mark = "✅" if r.get("ok") else "❌"
            L.append(f"· {mark} {r.get('at','?')[:16]} {r.get('invariante','?')} "
                     f"→ {r.get('fix','?')} (Lauf {r.get('lauf','?')} von "
                     f"{r.get('deckel','?')}) — Befund: {r.get('befund','?')}")
    if k["repeats"]:
        L += ["", "<b>⚠️ Konstruktionsfehler, nicht weiter reparieren</b>"]
        for key, n in sorted(k["repeats"].items(), key=lambda kv: -kv[1]):
            L.append(f"· <b>{key}</b> — {n}× in {k['days']} Tagen. Eine "
                     f"Korrektur, die {n}× pro Woche läuft, behebt nichts; sie "
                     f"hält den Defekt unsichtbar.")
    elif k["fixes"]:
        L += ["", f"Keine Korrektur {REPEAT_IS_DESIGN_FAULT}× oder häufiger — "
              f"nichts, was als Konstruktionsfehler zu melden wäre."]

    # Der Bericht prüft sich selbst, bevor er geht. Ein fehlender Stichtag
    # wird im Bericht genannt und nicht stillschweigend behoben: wer die Zahl
    # liest, soll sehen, dass sie undatiert ist.
    text = "\n".join(L)
    for title in undated_sections(text):
        L += ["", f"<b>⚠️ Undatierte Zahl: {title}</b>",
              "Der Abschnitt nennt eine Zahl ohne Fenster und ohne Zeitpunkt. "
              "Nach der Zählregel unterbleibt eine solche Nennung — hier steht "
              "sie, damit niemand sie für datiert hält. Zu beheben im Abschnitt, "
              "nicht in der Prüfung."]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args(argv)
    k = collect(a.days)
    report = format_report(k)
    print(report)
    if a.send:
        notify.send_telegram(report, channel=notify.STATS, parse_mode="HTML")
    # A week with a red, with runs missing, or with an undated figure exits
    # non-zero so a cron wrapper can tell the difference without parsing the
    # text. An undated figure counts: it is the defect the rule exists for.
    return 1 if (k["red"] or k["broken"] or undated_sections(report) or
                 k["runs"] < k["expected_runs"] * 0.9) else 0


if __name__ == "__main__":
    raise SystemExit(main())
