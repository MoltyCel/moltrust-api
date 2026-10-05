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
    return out


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


# Repos whose Actions mail into the same inbox.
ACTIONS_REPOS = ("MoltyCel/moltrust-api", "MoltyCel/moltguard", "MoltyCel/moltrust-web")


def actions_noise(days: int = 7) -> dict:
    """How many Actions runs would have mailed, and how many deserved to.

    GitHub mails on a failed run, and it marks a run whose job was *cancelled*
    as failed. On 2026-10-05 eleven of thirteen non-green runs across the three
    repos were cancelled jobs reported as failures, and reading that as a
    backlog is what sent one merge past a check on a wrong premise.

    Counting it weekly turns a full inbox into a number. The split is the point:
    `failed` is work to do, `cancelled_as_failed` is noise, and a week where the
    second is larger says the tooling is lying rather than the code.
    """
    import urllib.error
    import urllib.request

    token = os.environ.get("MOLTYCEL_GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return {"error": "kein GitHub-Token in der Umgebung"}
    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def api(path: str):
        req = urllib.request.Request(
            f"https://api.github.com{path}",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "moltrust-supervision/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310  # nosec B310 - api.github.com, literal
            return json.load(r)

    out = {"failed": 0, "cancelled_as_failed": 0, "per_repo": {}}
    for repo in ACTIONS_REPOS:
        try:
            runs = api(f"/repos/{repo}/actions/runs?per_page=100&created=>{since}")
        except Exception as e:  # noqa: BLE001 - a missing figure must not fail the report
            out["per_repo"][repo] = {"error": f"{type(e).__name__}"}
            continue
        failed = cancelled = 0
        for run in runs.get("workflow_runs", []):
            if run.get("conclusion") != "failure":
                continue
            # A run reads "failure" when its only job was cancelled. Ask the job.
            try:
                jobs = api(f"/repos/{repo}/actions/runs/{run['id']}/jobs")
                concl = {j.get("conclusion") for j in jobs.get("jobs", [])}
            except Exception:  # noqa: BLE001
                concl = set()
            if concl and concl <= {"cancelled", "skipped", None}:
                cancelled += 1
            else:
                failed += 1
        out["per_repo"][repo] = {"failed": failed, "cancelled_as_failed": cancelled}
        out["failed"] += failed
        out["cancelled_as_failed"] += cancelled
    return out


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
            "actions": actions_noise(days),
            "autofixes": auto,
            "selftest": selftest_week(days),
            "named": [n for n in NAMED_FINDINGS if n["week"] == iso_week],
            "activation": activation(),
            "green": lights.get("green", 0), "yellow": lights.get("yellow", 0),
            "red": lights.get("red", 0), "broken": lights.get("broken", 0),
            "offenders": offenders.most_common(8), "fixes": fixes,
            "repeats": repeats}


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

    a = k.get("actions") or {}
    if a.get("error"):
        L += ["", f"<b>Actions-Post</b>: nicht messbar — {a['error']}"]
    elif a:
        total = a["failed"] + a["cancelled_as_failed"]
        L += ["", f"<b>Actions-Post</b>: {total} Mails zu erwarten — "
                  f"{a['failed']} echte Fehlschläge, "
                  f"<b>{a['cancelled_as_failed']} abgebrochene Läufe</b>, die GitHub als "
                  f"Fehlschlag meldet und mailt."]
        if a["cancelled_as_failed"] > a["failed"]:
            L += ["Mehr Rauschen als Befund. Ein abgebrochener Lauf entsteht beim "
                  "Schließen eines PRs, beim Mergen und beim Löschen des Zweigs — "
                  "nicht an der Sache."]
        loud = [f"{r.split('/')[-1]} {v['failed']}/{v['cancelled_as_failed']}"
                for r, v in (a.get("per_repo") or {}).items() if not v.get("error")
                and (v["failed"] or v["cancelled_as_failed"])]
        if loud:
            L += ["je Repo (echt/abgebrochen): " + " · ".join(loud)]

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
        L += [f"<b>{act.get('dids')}</b> externe DIDs mit Track Record."]
        L += [f"Fußnote: {act.get('loops')} DIDs tragen mehr als zwei Track "
              f"Records, weil sie den ausstellenden Endpunkt gepollt haben "
              f"statt den Trust Score. Die Ursache lag bei uns — der Endpunkt "
              f"prägte bei jedem Aufruf ein neues Credential, der Aufgabentext "
              f"sagte „poll until it appears\" und war nach der Anlage nicht "
              f"mehr änderbar. Seit dem Idempotenz-Fix gibt derselbe Aufruf "
              f"das vorhandene Credential zurück. Eine Credential-Zahl steht "
              f"hier bewusst nicht: sie wäre keine Aktivierungszahl."]

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
    # A week with a red, or with runs missing, exits non-zero so a cron wrapper
    # can tell the difference without parsing the text.
    return 1 if (k["red"] or k["broken"] or
                 k["runs"] < k["expected_runs"] * 0.9) else 0


if __name__ == "__main__":
    raise SystemExit(main())
