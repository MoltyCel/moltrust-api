#!/usr/bin/env python3
"""daily-report — the daily Telegram report on the published artefacts (ops TB-1).

Runs the two conformance checks in ~/did-moltrust-conformance (artefact-check and
the DID vectors), writes the full output to ~/Downloads/daily-report-<date>.md and
sends one short message. The message is written for Lars, not for the checks: no
check IDs, no register counts, and every deviation in one plain sentence with
"Was heißt das" and "Musst du etwas tun".

Until 2026-10-23 this lived as ~/daily_report.py outside the repo and sent to
Telegram itself. The hourly --urgent run sent again every hour as long as a
deviation stood outside the register: 25 messages on 2026-10-22 for one 404.
Now an urgent message goes out once per deviation and again only after the
deviation was gone and came back. Sending goes through app.notify.

  python scripts/daily_report.py            the daily slot: always one message
  python scripts/daily_report.py --urgent   only new deviations or a failed run
  python scripts/daily_report.py --dry-run  compose and print, send nothing
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app import gh, notify  # noqa: E402

HOME = pathlib.Path(os.path.expanduser("~"))
CONFORMANCE = HOME / "did-moltrust-conformance"
DOWNLOADS = HOME / "Downloads"
STATE = HOME / ".daily_report_state.json"
DEPLOY_LOG = HOME / "logs" / "deploy.log"
REPOS = ["MoltyCel/moltrust-api", "MoltyCel/moltrust-web", "MoltyCel/moltguard"]

# What each check is called in the message.
CHECK_NAMES = {
    "artefact-check": "der Abgleich unserer veröffentlichten Dokumente",
    "did-conformance": "die Prüfung gegen unsere DID-Spezifikation",
}


def checks() -> list:
    py = sys.executable
    return [
        ("artefact-check", [py, str(CONFORMANCE / "tools/artefact_check.py")]),
        ("did-conformance", [py, str(CONFORMANCE / "tools/run_vectors.py"),
                             "--json", str(DOWNLOADS / "did-conformance-run.json")]),
    ]


def run_checks() -> list:
    """(name, exit code, output) per check; a crash or timeout has code None."""
    out = []
    for name, cmd in checks():
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            out.append((name, r.returncode, r.stdout + r.stderr))
        except subprocess.TimeoutExpired:
            out.append((name, None, "Zeitüberschreitung nach 900 s"))
        except Exception as e:
            out.append((name, None, f"{type(e).__name__}: {e}"))
    return out


def findings(results: list) -> tuple[list, list]:
    """The NEU and WEG lines of each check, as (check name, text)."""
    new, gone = [], []
    for name, _code, text in results:
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("NEU "):
                new.append((name, s[4:].strip()))
            elif s.startswith("WEG "):
                gone.append((name, s[4:].strip()))
    return new, gone


_URL_STATUS = re.compile(r"^urls-resolve: URL named publicly answers (\d+): (\S+)")
_URL_SILENT = re.compile(r"^urls-resolve: URL named publicly does not answer: (\S+)")
_KID_NO_JWK = re.compile(r"^keys-symmetry: kid in did\.json without a JWK: (\S+)")
_KID_NO_VM = re.compile(r"^keys-symmetry: kid in jwks\.json without a verification method: (\S+)")
_KID_NO_CODE = re.compile(r"^keys-have-code: no code path signs with kid (\S+)")
_VECTOR = re.compile(r"^\S+ \(section ([^)]+)\) (.+)$")


def plain(check: str, text: str) -> tuple[str, str, str]:
    """One deviation as (sentence, what it means, does Lars have to act)."""
    if m := _URL_STATUS.match(text):
        return (f"Eine Adresse, die wir öffentlich nennen, führt ins Leere: {m.group(2)}",
                "Wer dem Link folgt, bekommt eine Fehlerseite statt der versprochenen Antwort.",
                "nein, die Console korrigiert die Stelle, die die Adresse nennt.")
    if m := _URL_SILENT.match(text):
        return (f"Eine Adresse, die wir öffentlich nennen, antwortet gar nicht: {m.group(1)}",
                "Entweder ist der Dienst dahinter ausgefallen oder die Adresse stimmt nicht.",
                "nein, die Console sieht nach.")
    if m := _KID_NO_JWK.match(text) or _KID_NO_VM.match(text):
        return (f"Ein Signaturschlüssel ({m.group(1)}) steht in nur einer unserer zwei "
                "öffentlichen Schlüssellisten.",
                "Eine Signatur mit diesem Schlüssel lässt sich von außen nicht vollständig prüfen.",
                "nein, die Console gleicht die Listen an. Muss ein Schlüssel neu erzeugt "
                "werden, fragt sie vorher.")
    if m := _KID_NO_CODE.match(text):
        return (f"Wir veröffentlichen einen Signaturschlüssel ({m.group(1)}), mit dem "
                "kein Programm von uns signiert.",
                "Ein Prüfer erwartet Signaturen, die nie kommen.",
                "nein, die Console klärt, ob der Schlüssel aus der Liste kann.")
    if check == "did-conformance" and (m := _VECTOR.match(text)):
        return (f"Unsere API verhält sich an einer Stelle anders, als unsere DID-Spezifikation "
                f"in Abschnitt {m.group(1)} beschreibt: {m.group(2)}",
                "Wer sich an die Spezifikation hält, bekommt von uns eine andere Antwort.",
                "nein, die Console klärt, ob die API oder der Text falsch ist.")
    return (f"{_upper(CHECK_NAMES.get(check, check))} fand eine Abweichung: {text}",
            "Zwei unserer veröffentlichten Angaben passen nicht zusammen.",
            "nein, die Console sieht nach.")


def merges_line(now: datetime.datetime, fetch=None) -> str:
    """How many changes merged and how many deploys finished in 24 hours."""
    fetch = fetch or _gh_get
    since = now - datetime.timedelta(hours=24)
    merged = 0
    for repo in REPOS:
        data = fetch(f"/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=50")
        for p in data if isinstance(data, list) else []:
            at = p.get("merged_at")
            if at and _ts(at) >= since:
                merged += 1
    deploys = count_deploys(since)
    return (f"Letzte 24 Stunden: {merged} Änderung{'en' if merged != 1 else ''} übernommen, "
            f"{deploys} Mal ausgerollt.")


def count_deploys(since: datetime.datetime, path: pathlib.Path | None = None) -> int:
    """deploy.sh ends every successful run with a line 'OK <repo> <sha>'."""
    path = path or DEPLOY_LOG
    if not path.is_file():
        return 0
    n = 0
    for line in path.read_text(errors="replace").splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[1] == "OK" and parts[2].startswith(("moltrust-", "moltguard")):
            try:
                if _ts(parts[0]) >= since:
                    n += 1
            except ValueError:
                continue
    return n


def _upper(s: str) -> str:
    return s[:1].upper() + s[1:]


def _ts(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.UTC)


def _gh_get(path: str):
    tok = gh.token()
    if not tok:
        return None
    req = urllib.request.Request(f"https://api.github.com{path}", headers={
        "Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # nosec B310 - fixed https host
            return json.load(r)
    except Exception:
        return None


def load_state(path: pathlib.Path | None = None) -> dict:
    path = path or STATE
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_state(state: dict, path: pathlib.Path | None = None) -> None:
    path = path or STATE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.chmod(0o600)
    tmp.replace(path)


def compose(now, new, gone, failed, known: dict, *, urgent: bool, merges: str,
            detail: pathlib.Path) -> str:
    day = now.strftime("%d.%m.")
    if failed:
        head = (f"Tagesbericht {day}: {_count(len(failed), 'Prüfung lief', 'Prüfungen liefen')} "
                "nicht durch.")
    elif new:
        head = f"Tagesbericht {day}: {_count(len(new), 'Abweichung', 'Abweichungen')}."
    elif gone:
        head = f"Tagesbericht {day}: {_count(len(gone), 'Abweichung', 'Abweichungen')} behoben."
    else:
        head = f"Tagesbericht {day}: nichts Neues."
    if urgent:
        head = "Außerhalb des Tagesberichts, " + head.split(": ", 1)[1]
    lines = [head]
    for name, _code, text in failed:
        last = text.strip().splitlines()[-1][:160] if text.strip() else "ohne Ausgabe"
        lines += ["", f"{_upper(CHECK_NAMES.get(name, name))} ist nicht durchgelaufen ({last}).",
                  "Was heißt das: Heute ist dort nichts geprüft, eine neue Abweichung bliebe unbemerkt.",
                  "Musst du etwas tun: nein, die Console sieht nach."]
    for check, text in new:
        sentence, means, act = plain(check, text)
        since = known.get(_key(check, text))
        lines += ["", sentence + (f" Steht seit {_day(since)}." if since and not urgent else ""),
                  f"Was heißt das: {means}", f"Musst du etwas tun: {act}"]
    for check, text in gone[:4]:
        sentence, _means, _act = plain(check, _gone_text(check, text))
        lines += ["", f"Behoben: {sentence}", "Musst du etwas tun: nein."]
    if not urgent:
        lines += ["", merges]
    lines.append(f"Details: {detail}")
    return "\n".join(lines)


def _gone_text(check: str, text: str) -> str:
    """WEG lines name the register key ('urls-resolve::URL ...'); the vectors
    put their own id in front ('<id> <key> haelt jetzt — ...')."""
    t = text.split(" haelt jetzt")[0]
    if check == "did-conformance" and " " in t:
        t = t.split(" ", 1)[1]
    return t.replace("::", ": ", 1)


def _count(n: int, one: str, many: str) -> str:
    words = {1: "eine", 2: "zwei", 3: "drei", 4: "vier", 5: "fünf"}
    if n == 1:
        return f"eine {one}"
    return f"{words.get(n, n)} {many}"


def _key(check: str, text: str) -> str:
    return f"{check}|{text}"


def _day(iso: str) -> str:
    try:
        return datetime.datetime.fromisoformat(iso).strftime("%d.%m.")
    except ValueError:
        return iso


def write_detail(now, results, path: pathlib.Path) -> None:
    body = [f"# daily-report {now.isoformat(timespec='seconds')}", ""]
    for name, code, out in results:
        body += [f"## {name} (exit {code})", "", "```", out.strip() or "(keine Ausgabe)", "```", ""]
    path.write_text("\n".join(body) + "\n")
    path.chmod(0o644)


def main(argv: list[str], now: datetime.datetime | None = None, run=run_checks,
         send=None, state_path: pathlib.Path | None = None,
         detail_dir: pathlib.Path | None = None, merges=None) -> int:
    urgent = "--urgent" in argv
    dry = "--dry-run" in argv
    now = now or datetime.datetime.now(datetime.UTC).replace(microsecond=0)
    send = send or notify.send_telegram
    results = run()
    failed = [r for r in results if r[1] is None]
    new, gone = findings(results)

    state = load_state(state_path)
    known = state.get("gemeldet", {})
    # A deviation that is no longer reported drops out, so its return is new.
    current = {_key(c, t) for c, t in new} | {f"lauf|{n}" for n, _, _ in failed}
    known = {k: v for k, v in known.items() if k in current}

    if urgent:
        new = [(c, t) for c, t in new if _key(c, t) not in known]
        failed = [r for r in failed if f"lauf|{r[0]}" not in known]
        if not new and not failed:
            print("nichts Neues außerhalb des Tagesberichts, keine Nachricht")
            state["gemeldet"] = known
            if not dry:
                save_state(state, state_path)
            return 0
        gone = []

    detail = (detail_dir or DOWNLOADS) / f"daily-report-{now.strftime('%Y-%m-%d')}.md"
    if not dry:
        write_detail(now, results, detail)
    text = compose(now, new, gone, failed, known, urgent=urgent,
                   merges="" if urgent else (merges or merges_line)(now), detail=detail)
    if dry:
        print(text)
        return 0

    channel = notify.ALERTS if urgent else notify.STATS
    sent = send(text, channel=channel)
    delivered = sent or notify.letzter_grund() == "gedrosselt"
    if delivered:
        stamp = now.isoformat(timespec="seconds")
        for c, t in new:
            known.setdefault(_key(c, t), stamp)
        for n, _, _ in failed:
            known.setdefault(f"lauf|{n}", stamp)
    state.update({"gemeldet": known, "last_run": now.isoformat(timespec="seconds"),
                  "sent": bool(sent), "urgent": urgent})
    save_state(state, state_path)
    print(f"gesendet: {sent}")
    print(text)
    return 0 if delivered else notify.MUTE_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
