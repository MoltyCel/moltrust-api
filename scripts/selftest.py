#!/usr/bin/env python3
"""Run the invariant catalogue and say what is not true any more.

We checked for signs of life and called it monitoring. The track-record defect
ran for five days while every watchdog stayed green, because the watchdog asks
whether the service answers and nobody asked whether the distribution made
sense. Three agents took 75 credentials, each one anchored and paid for.

Invariants live in docs/invariants/, one file each, with the incident that
produced them written in. The runner reads them, runs them, and reports.

The meta-invariant is not one of the files and cannot be switched off: the run
records which invariants actually executed, and raises if one was skipped twice
running or if the run died before the end. A selftest that quietly checks
nothing is the failure we have already had three times in the harness tests, and
it is the one failure this file exists to make impossible.

Two families run in one pass. The catalogue answers "does this query still
return this value". agents/supervision.py answers the two questions a query
cannot: is each pipeline meeting its declaration in config/expectations.yaml —
including whether its silence has a *declared reason* — and does each external
dependency answer a real call whose resource resolves. Its green/yellow/red
map onto OK/WARN/FAIL here, and its findings count for the meta-invariant like
any other.

    python3 scripts/selftest.py --tempo hourly
    python3 scripts/selftest.py --tempo daily
    python3 scripts/selftest.py --all --dry-run     # run everything, arm nothing
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import selftest_throttle as throttle  # noqa: E402

import yaml  # noqa: E402

from app import notify  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOGUE = os.path.join(HERE, "..", "docs", "invariants")
OUTDIR = os.path.expanduser("~/Downloads/selftest")
STATE = os.path.expanduser("~/.selftest_state.json")
# Every GREEN fix that ran, one line each, append-only. The cap in the state
# file is a counter and answers "may another one run"; this answers "what did
# it do", which is the question the weekly report asks.
FIXLOG = os.path.join(OUTDIR, "autofix.jsonl")

# GREEN fixes: idempotent restoration, nothing else. A fix that is not in this
# map is reported and not run, whatever the invariant file claims.
GREEN_FIXES = {
    "rerun_registry_export": [
        "bash", "-lc",
        "cd ~/moltstack && scripts/registry_proof_publish.sh",
    ],
}


def load_catalogue() -> list[dict]:
    out = []
    for name in sorted(os.listdir(CATALOGUE)):
        if not name.endswith(".yaml"):
            continue
        path = os.path.join(CATALOGUE, name)
        try:
            d = yaml.safe_load(open(path))
        except yaml.YAMLError as exc:
            # A catalogue entry that does not parse is itself a finding. It must
            # not silently reduce the set of things we check.
            out.append({"id": name[:-5], "broken": f"YAML: {exc}", "schweregrad": "fail"})
            continue
        d["_file"] = name
        out.append(d)
    return out


def run_query(inv: dict) -> tuple[str | None, str | None]:
    """Return (value, error). The value is a string; comparison casts it."""
    art = inv["abfrage"]["art"]
    q = inv["abfrage"]["wert"]
    try:
        if art == "sql":
            p = subprocess.run(
                ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
                 "-X", "-A", "-t", "-c", q],
                capture_output=True, text=True, timeout=180)
        elif art == "shell":
            p = subprocess.run(["bash", "-lc", q], capture_output=True, text=True,
                               timeout=300)
        else:
            return None, f"unbekannte Abfrageart {art}"
    except subprocess.TimeoutExpired:
        return None, "Zeitüberschreitung"
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if p.returncode and not p.stdout.strip():
        return None, (p.stderr or "").strip()[:200]
    return p.stdout.strip(), None


def unreadable(value: str) -> str | None:
    """The reason the measurement is not a measurement, or None.

    A query that could not read its source must not be compared against an
    expectation — whichever way the comparison falls, the answer is made up. On
    2026-10-03 b-registry-equals-pypi reported a drift that did not exist
    because its parser took the oldest of five registry entries, and it
    reported it as a WARN, which is a verdict about the world rather than about
    the parser. So a query says UNREADABLE and the runner believes it, at FAIL,
    whatever severity the file carries: not knowing is not a mild condition.
    """
    v = value.strip()
    if not v:
        return "leere Antwort"
    for line in v.splitlines():
        if line.strip().startswith("UNREADABLE"):
            return line.strip()[len("UNREADABLE"):].strip(" :") or "unlesbar"
    if v.splitlines()[-1].strip() in ("?", "None", "null", "nan"):
        return f"Platzhalter statt Messwert: {v.splitlines()[-1].strip()!r}"
    return None


def judge(inv: dict, value: str) -> tuple[bool, str]:
    op = inv["erwartung"]["operator"]
    want = inv["erwartung"]["wert"]
    last = value.strip().splitlines()[-1].strip() if value.strip() else ""
    if op == "contains":
        return (str(want) in value), f"{'enthält' if str(want) in value else 'ohne'} {want!r}"
    if op == "empty":
        return (value.strip() == ""), f"{len(value.strip().splitlines())} Zeilen"
    try:
        got = float(last)
    except ValueError:
        return False, f"kein Zahlenwert: {last!r}"
    want_n = float(want)
    ok = {"eq": got == want_n, "lte": got <= want_n, "gte": got >= want_n}[op]
    shown = int(got) if got == int(got) else got
    return ok, f"{shown} (erwartet {op} {want})"


def fixes_today(state: dict, inv_id: str) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return (state.get("fixes", {}).get(inv_id, {}) or {}).get(today, 0)


def record_fix(state: dict, inv_id: str) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    state.setdefault("fixes", {}).setdefault(inv_id, {})
    state["fixes"][inv_id][today] = state["fixes"][inv_id].get(today, 0) + 1


def log_fix(inv_id: str, fix: str, proc, detail: str, nth: int, cap) -> None:
    """One line per executed GREEN fix. Append-only, read by the weekly report."""
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "invariante": inv_id, "fix": fix,
             "befund": detail, "rc": proc.returncode,
             "ok": proc.returncode == 0, "lauf": nth, "deckel": cap,
             "ausgabe": (proc.stdout or proc.stderr or "").strip()[-400:]}
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        with open(FIXLOG, "a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        # A fix that ran and left no trace is worse than one that did not run.
        print(f"WARNUNG: Autofix-Protokoll nicht schreibbar: {exc}", file=sys.stderr)


# Libraries the checks reach for. Missing here means the run is under the wrong
# interpreter, which produces findings about us rather than about the world.
NEEDED = ("yaml", "httpx", "requests", "requests_oauthlib")


def preflight() -> list[str]:
    """Modules this interpreter lacks, in the order they are declared."""
    import importlib.util
    return [m for m in NEEDED if importlib.util.find_spec(m) is None]


def send_digest() -> int:
    """Die Sammelmeldung. Immer eine Zeile, auch bei null neuen Befunden.

    Eigener Modus und eigener cron-Eintrag, damit sie um 08:00 und 20:00 UTC
    faellt und nicht um :37, wenn der stuendliche Lauf zufaellig danebenliegt.
    Und damit sie auch dann kommt, wenn kein Lauf stattgefunden hat — dann ist
    "0 Laeufe" die Aussage.

    Gepingt wird healthchecks.io erst nach dem Absenden: bleibt die Zeile aus,
    schlaegt der Check an. Die Stille ist nicht das Signal, das Ausbleiben der
    Zeile ist es.
    """
    now = datetime.now(timezone.utc)
    st = throttle.state()
    slot = throttle.due_digest_slot(now, st.get("gesendet") or {})
    if not slot:
        print("keine faellige Sammelmeldung")
        return 0
    key, slot_at = slot
    seit = slot_at - timedelta(hours=24 // len(throttle.DIGEST_HOURS))

    # Laeufe und Autofixe im Fenster, aus den Laufberichten.
    runs, autofix, neu_im_fenster = 0, {}, 0
    for day in {seit.date(), slot_at.date()}:
        f = os.path.join(OUTDIR, f"{day.isoformat()}.json")
        if not os.path.exists(f):
            continue
        try:
            docs = json.load(open(f))
        except Exception:  # noqa: BLE001 - ein kaputter Bericht ist kein Lauf
            continue
        for d in docs if isinstance(docs, list) else [docs]:
            try:
                fin = datetime.fromisoformat(d["finished"])
            except Exception:  # noqa: BLE001
                continue
            if fin.tzinfo is None:
                fin = fin.replace(tzinfo=timezone.utc)
            if not (seit <= fin < slot_at):
                continue
            runs += 1
            for r in d.get("ergebnisse", []):
                fix = str(r.get("autofix") or "")
                if fix and "fehlgeschlagen" not in fix and "waere gelaufen" not in fix:
                    name = fix.split(":")[0]
                    autofix[name] = autofix.get(name, 0) + 1
            neu_im_fenster += int(d.get("sofort_gemeldet") or 0)

    try:
        entries = throttle.load_register()
    except Exception as exc:  # noqa: BLE001
        entries = []
        print(f"Register nicht lesbar: {exc}")
    bekannt = [{"id": e["invariante"], "status": e["befund"], "eintrag": e}
               for e in entries]

    try:
        offen = throttle.load_open()
    except Exception as exc:  # noqa: BLE001
        offen = []
        print(f"offene-befunde nicht lesbar: {exc}")
    line = throttle.digest_line(slot_at, runs, neu_im_fenster, bekannt,
                                sorted(autofix.items()), offen)
    notify.send_telegram("MolTrust — " + line, channel=notify.STATS)
    print(line)

    st.setdefault("gesendet", {})[key] = now.isoformat(timespec="seconds")
    for k in sorted(st["gesendet"])[:-28]:
        st["gesendet"].pop(k, None)
    throttle.save_state(st)

    # Erst nach dem Absenden pingen.
    url = os.environ.get("HEALTHCHECK_SELFTEST_DIGEST_URL", "").strip()
    # Die Umgebung ist Konfiguration, kein Vertrauen. Ein file:- oder gopher:-
    # Wert aus einer verunglueckten Zeile in ~/.moltrust_secrets wuerde hier
    # sonst geoeffnet; geprueft wird das Schema, nicht der Host.
    if url and not url.startswith("https://"):
        print(f"HEALTHCHECK_SELFTEST_DIGEST_URL ist kein https-URL "
              f"({url.split(':', 1)[0]}:) — kein Ping")
        url = ""
    if url:
        try:
            urllib.request.urlopen(url, timeout=15).read()  # noqa: S310  # nosec B310 - Schema oben geprueft
        except Exception as exc:  # noqa: BLE001 - ein Ping, der scheitert, ist kein Befund
            print(f"healthchecks-Ping fehlgeschlagen: {type(exc).__name__}")
    else:
        print("HEALTHCHECK_SELFTEST_DIGEST_URL nicht gesetzt — kein Ping")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tempo", choices=["hourly", "daily"], default="hourly")
    ap.add_argument("--all", action="store_true", help="jede Invariante, unabhängig vom Tempo")
    ap.add_argument("--dry-run", action="store_true",
                    help="prüfen und berichten, keinen Autofix ausführen")
    ap.add_argument("--no-supervision", dest="supervision", action="store_false",
                    help="nur den Invarianten-Katalog, ohne Erwartungsregister")
    ap.set_defaults(supervision=True)
    ap.add_argument("--digest", action="store_true",
                    help="Nur die Sammelmeldung senden, keinen Lauf starten.")
    args = ap.parse_args()
    if args.digest:
        return send_digest()

    missing = preflight()
    if missing:
        # Refusing is the honest answer. On 2026-10-03 a hand-run under the
        # system python3 reported dep/x and dep/bluesky red because
        # requests_oauthlib and atproto live in the venv; both were green
        # throughout. A runner that produces findings about its own interpreter
        # costs more than one that declines to start.
        print(f"ABBRUCH: dieser Interpreter ({sys.executable}) kann "
              f"{', '.join(missing)} nicht importieren. Der Lauf gehört in das "
              f"venv: ./venv/bin/python scripts/selftest.py …", file=sys.stderr)
        return 2

    state = {}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE))
        except (OSError, ValueError):
            state = {}

    catalogue = load_catalogue()
    due = [i for i in catalogue
           if args.all or i.get("broken") or i.get("tempo") == args.tempo
           or (args.tempo == "daily" and i.get("tempo") == "hourly")]

    started = datetime.now(timezone.utc)
    results, executed = [], []
    for inv in due:
        inv_id = inv.get("id") or inv.get("_file", "?")
        if inv.get("broken"):
            results.append({"id": inv_id, "status": "FAIL", "detail": inv["broken"],
                            "schweregrad": "fail"})
            executed.append(inv_id)
            continue
        value, err = run_query(inv)
        if err is not None:
            # Not knowing is not the same as being fine. An invariant that could
            # not run is reported as such and counts as skipped for the meta
            # check, never as a pass.
            results.append({"id": inv_id, "status": "ERROR", "detail": err,
                            "titel": inv["titel"], "schweregrad": inv["schweregrad"]})
            continue
        why = unreadable(value)
        if why is not None:
            results.append({"id": inv_id, "titel": inv["titel"],
                            "kategorie": inv["kategorie"], "status": "FAIL",
                            "detail": f"unlesbarer Messwert: {why}",
                            "schweregrad": "fail"})
            executed.append(inv_id)
            continue
        ok, detail = judge(inv, value)
        executed.append(inv_id)
        res = {"id": inv_id, "titel": inv["titel"], "kategorie": inv["kategorie"],
               "status": "OK" if ok else inv["schweregrad"].upper(),
               "detail": detail, "schweregrad": inv["schweregrad"]}
        # A known state is still measured and still reported — it just does not
        # read as a new alarm. The date is in the file so the note expires by
        # itself instead of becoming a permanent excuse.
        if not ok and inv.get("bekannter_zustand"):
            kz = inv["bekannter_zustand"]
            until = str(kz.get("gruen_erwartet", ""))
            overdue = until and datetime.now(timezone.utc).isoformat() > until
            res["bekannt_bis"] = until or "?"
            res["ueberfaellig"] = bool(overdue)
            if overdue:
                res["detail"] += (f" · ÜBERFÄLLIG: grün erwartet war {until}, "
                                  f"der Zustand hält an — das ist jetzt ein neuer Befund")
            else:
                res["detail"] += (f" · bekannter Zustand, grün erwartet {until}: "
                                  f"{kz.get('grund','')}")
        if not ok and inv.get("autofix", "none") != "none":
            fix = inv["autofix"]
            if fix not in GREEN_FIXES:
                res["autofix"] = f"nicht ausgeführt: {fix} steht nicht in GREEN_FIXES"
            elif fixes_today(state, inv_id) >= int(inv.get("deckel", 3)):
                # A repair that runs three times a day repairs nothing.
                res["status"] = "FAIL"
                res["autofix"] = (f"Deckel erreicht ({inv.get('deckel')} je 24 h), "
                                  f"Autofix ausgesetzt")
            elif args.dry_run:
                res["autofix"] = f"waere gelaufen: {fix} (dry-run)"
            else:
                p = subprocess.run(GREEN_FIXES[fix], capture_output=True, text=True,
                                   timeout=900)
                record_fix(state, inv_id)
                res["autofix"] = (f"{fix}: {'ok' if p.returncode == 0 else 'fehlgeschlagen'}"
                                  f" (Lauf {fixes_today(state, inv_id)} von {inv.get('deckel')})")
                log_fix(inv_id, fix, p, detail, fixes_today(state, inv_id),
                        inv.get("deckel"))
        results.append(res)

    # --- the second family: declarations and live dependencies -------------
    # Folded in rather than run separately, so one pass produces one verdict
    # and the meta-invariant counts these too. A family that cannot run is an
    # ERROR, never an absence.
    if args.supervision:
        try:
            from agents import supervision
            for f in supervision.families():
                status = {supervision.GREEN: "OK", supervision.YELLOW: "WARN",
                          supervision.RED: "FAIL"}[f["light"]]
                inv_id = f"sup-{f['check'].replace('/', '-')}"
                results.append({"id": inv_id, "titel": f["check"],
                                "kategorie": "S", "status": status,
                                "detail": f["detail"],
                                "schweregrad": "fail" if status == "FAIL" else "warn",
                                "autofix": f.get("fix") or "none"})
                executed.append(inv_id)
                due.append({"id": inv_id})
        except Exception as exc:
            results.append({"id": "sup-families", "status": "ERROR",
                            "titel": "Erwartungsregister und Abhängigkeiten",
                            "detail": f"{type(exc).__name__}: {exc}",
                            "schweregrad": "fail"})

    # --- the meta-invariant ------------------------------------------------
    expected = {i.get("id") for i in due if i.get("id")}
    skipped = sorted(expected - set(executed))
    prev_skipped = set(state.get("skipped", []))
    twice = sorted(set(skipped) & prev_skipped)
    complete = len(results) == len(due)

    meta = {"erwartet": len(due), "ausgefuehrt": len(executed),
            "uebersprungen": skipped, "zweimal_in_folge": twice,
            "lauf_vollstaendig": complete}
    if twice or not complete:
        meta["status"] = "FAIL"
    elif skipped:
        meta["status"] = "WARN"
    else:
        meta["status"] = "OK"

    doc = {"started": started.isoformat(timespec="seconds"),
           "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "tempo": "all" if args.all else args.tempo,
           "dry_run": args.dry_run,
           "meta_invariante": meta,
           "ergebnisse": results}

    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, f"{started.date().isoformat()}.json")
    runs = json.load(open(path)) if os.path.exists(path) else []
    runs.append(doc)
    json.dump(runs, open(path, "w"), indent=1)

    fails = [r for r in results if r["status"] in ("FAIL", "ERROR")]
    warns = [r for r in results if r["status"] == "WARN"]
    oks = [r for r in results if r["status"] == "OK"]

    line = (f"Selftest {doc['tempo']}: {len(oks)} ok, {len(warns)} warn, "
            f"{len(fails)} fail/error · Meta {meta['status']} "
            f"({meta['ausgefuehrt']}/{meta['erwartet']} ausgeführt)")
    body = line + "\n"
    for r in fails + warns:
        body += f"\n  [{r['status']}] {r['id']}\n    {r.get('titel','')}\n    {r['detail']}"
        if r.get("autofix"):
            body += f"\n    Autofix: {r['autofix']}"
    if meta["status"] != "OK":
        body += (f"\n\n  [META] uebersprungen: {meta['uebersprungen'] or 'keine'}"
                 f" · zweimal in Folge: {meta['zweimal_in_folge'] or 'keine'}"
                 f" · Lauf vollstaendig: {meta['lauf_vollstaendig']}")
    body += f"\n\nBericht: {path}"

    print(body)

    # --- Meldungsdrosselung -------------------------------------------------
    #
    # Die Laeufe bleiben stuendlich; nur die Meldung wird gedrosselt. Gemessen
    # ueber 48 h am 07.10.2026: 50 Laeufe, 50 Telegram-Nachrichten, alle nach
    # ALERTS, weil a-track-record-burst in jedem Lauf fehlschlug. Wer fuenfzig
    # Mal dasselbe liest, liest beim einundfuenfzigsten Mal nicht mehr.
    #
    # Sofort gehen nur drei Faelle raus: ein Befund, der nicht im Register
    # steht; ein Autofix, der rot zurueckkommt; und ein Befund, dessen
    # erwartetes Gruen-Datum verstrichen ist. Alles andere sammelt die
    # Sammelmeldung um 08:00 und 20:00 UTC (--digest).
    now = datetime.now(timezone.utc)
    try:
        entries = throttle.load_register()
        reg_error = None
    except Exception as exc:  # noqa: BLE001 - ein kaputtes Register meldet, es schweigt nicht
        entries, reg_error = [], f"{type(exc).__name__}: {exc}"

    cls = throttle.classify(fails + warns, entries, now,
                            token=os.environ.get("MOLTYCEL_GH_TOKEN", ""))
    # Ein Autofix, der rot zurueckkommt, ist eine gescheiterte Selbstreparatur
    # und damit immer sofort.
    autofix_rot = [r for r in results
                   if r.get("autofix") and "fehlgeschlagen" in str(r["autofix"])]

    # Offene Befunde: unerklaert, 72 h ruhig, hoechstens eine Meldung je 24 h.
    # Getrennt von den bekannten Abweichungen und nie mit ihnen vermischt.
    try:
        offen = {e["invariante"]: e for e in throttle.load_open()}
    except Exception as exc:  # noqa: BLE001 - ein kaputtes Register meldet
        offen, open_error = {}, f"{type(exc).__name__}: {exc}"
    else:
        open_error = None

    sofort = []
    still = []
    for r in list(cls["neu"]):
        e = offen.get(r["id"])
        if e is None or e.get("befund") != r.get("status"):
            continue
        cls["neu"].remove(r)
        e["gesehen"] = int(e.get("gesehen") or 0) + 1
        what = throttle.open_due(e, now)
        if what == "verfallen":
            sofort.append(f"[{r['status']}] {r['id']} — 72 h ohne Erklärung, "
                          f"Verfall {e['verfaellt_am']}; ab jetzt bei jedem Lauf")
        elif what == "melden":
            sofort.append("[OFFEN] " + throttle.open_line(e))
            e["gesehen"] = 0
            e["zuletzt_gemeldet"] = now.isoformat(timespec="seconds")
        else:
            still.append(r["id"])
    if offen:
        throttle.save_open(list(offen.values()))
    if still:
        print(f"offen und ruhig: {', '.join(still)}")

    for r in cls["neu"]:
        sofort.append(f"[{r['status']}] {r['id']} — {r.get('detail', '')}")
    for r in cls["verfallen"]:
        sofort.append(f"[{r['status']}] {r['id']} — {r['grund']}; "
                      f"{r.get('detail', '')}")
    for r in autofix_rot:
        sofort.append(f"[AUTOFIX ROT] {r['id']} — {r['autofix']}")
    if reg_error:
        sofort.append(f"[REGISTER] bekannte-abweichungen nicht lesbar: {reg_error}")
    if open_error:
        sofort.append(f"[REGISTER] offene-befunde nicht lesbar: {open_error}")

    if sofort:
        notify.send_telegram(
            "MolTrust Selftest — " + "\n".join(sofort)
            + f"\n\nBericht: {path}", channel=notify.ALERTS)

    # Verfallene Eintraege fliegen aus dem Register: ein Register, das Befunde
    # auf Dauerstumm stellt, ist schlimmer als keines.
    dropped = throttle.drop_from_register([r["id"] for r in cls["verfallen"]])
    if dropped:
        print(f"{dropped} verfallene Registereintraege entfernt")

    # Die Sammelmeldung zaehlt die Sofortmeldungen des Fensters aus den
    # Laufberichten. Der Bericht wurde oben schon geschrieben, also hier
    # nachtragen — sonst zaehlt sie null und behauptet Ruhe.
    doc["sofort_gemeldet"] = len(sofort)
    doc["bekannt"] = len(cls["bekannt"])
    runs_on_disk = json.load(open(path)) if os.path.exists(path) else []
    if runs_on_disk:
        runs_on_disk[-1] = doc
        json.dump(runs_on_disk, open(path, "w"), indent=1)

    state["skipped"] = skipped
    state["last_run"] = doc["finished"]
    json.dump(state, open(STATE, "w"), indent=1)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
