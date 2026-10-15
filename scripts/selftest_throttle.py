#!/usr/bin/env python3
"""Eine Wache, die einen bekannten Zustand wiederholt, meldet nicht — sie sammelt.

Der stündliche Selftest schickte bei **jedem** Lauf eine Telegram-Nachricht.
Gemessen über 48 Stunden am 07.10.2026: 50 Läufe, 50 Nachrichten, alle nach
ALERTS, weil `a-track-record-burst` in jedem einzelnen Lauf fehlschlug. Wer
fünfzig Mal dasselbe liest, liest beim einundfünfzigsten Mal nicht mehr — und
dann geht der eine neue Befund mit unter.

Die Läufe bleiben stündlich. Nur die Meldung wird gedrosselt.

Drei Begriffe
-------------

**bekannt** — der Befund steht im Register `bekannte-abweichungen.json`, mit
einem Grund und einem Datum, an dem er grün sein soll. Er zählt in die
Sammelmeldung und löst nichts aus.

**neu** — der Befund steht nicht im Register. Er geht sofort raus. Das ist der
Normalfall für alles, was niemand erwartet hat.

**verfallen** — das erwartete Grün-Datum ist verstrichen und der Befund steht
weiter. Ab diesem Lauf gilt er als neu, geht sofort raus und fliegt aus dem
Register. Ohne diesen Verfall stellt das Register Befunde auf Dauerstumm, und
ein Register, das Befunde verschwinden lässt, ist schlimmer als keines.

Ein Eintrag ohne `gruen_erwartet` wird nicht angenommen. Kein Standardwert:
ein Eintrag, der nie abläuft, ist genau die Dauerstummschaltung, die der
Verfall verhindern soll.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request

UTC = dt.timezone.utc

REGISTER = os.path.expanduser("~/selftest/bekannte-abweichungen.json")
DIGEST_STATE = os.path.expanduser("~/selftest/digest-state.json")
DIGEST_HOURS = (8, 20)

# Ein Eintrag, der zwei Wochen steht, ist keine Erwartung mehr, sondern ein
# Zustand. Er bleibt gültig, wird aber in der Sammelmeldung benannt.
STALE_DAYS = 14

REQUIRED = ("invariante", "befund", "grund", "gruen_erwartet", "eingetragen_am")


class RegisterError(ValueError):
    """Das Register ist nicht benutzbar. Kein Standardwert, kein Weiterlaufen."""


def _parse(ts: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def load_register(path: str = REGISTER) -> list[dict]:
    """Liest das Register und prüft jeden Eintrag. Wirft, statt zu raten."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    entries = doc.get("eintraege") if isinstance(doc, dict) else doc
    if not isinstance(entries, list):
        raise RegisterError(f"{path}: kein eintraege-Feld mit einer Liste")
    for i, e in enumerate(entries):
        missing = [k for k in REQUIRED if not e.get(k)]
        if missing:
            raise RegisterError(
                f"{path}, Eintrag {i} ({e.get('invariante', '?')}): "
                f"fehlende Felder {', '.join(missing)}. Ein Eintrag ohne "
                f"gruen_erwartet wird nicht angenommen.")
        if e["befund"] not in ("FAIL", "WARN"):
            raise RegisterError(
                f"{path}, Eintrag {i}: befund ist {e['befund']!r}, "
                f"erwartet FAIL oder WARN")
    return entries


def _pr_number(value: str) -> int | None:
    v = value.strip()
    if v.upper().startswith("PR"):
        v = v[2:].strip()
    v = v.lstrip("#").strip()
    return int(v) if v.isdigit() else None


def _pr_closed(number: int, repo: str, token: str) -> bool | None:
    """Ist der PR zu? None heisst: nicht beantwortbar, also nicht verfallen."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/pulls/{number}",
        headers={"authorization": f"Bearer {token}",
                 "accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310  # nosec B310 - die URL steht als https-Literal oben, repo ist eine Modulkonstante und number ein int aus _pr_number
            return json.loads(r.read().decode()).get("state") != "open"
    except Exception:  # noqa: BLE001 - keine Antwort ist kein Verfall
        return None


def expired(entry: dict, now: dt.datetime, *, repo: str = "MoltyCel/moltrust-api",
            token: str = "") -> str | None:
    """Grund des Verfalls, oder None.

    Ein Zeitstempel verfaellt, wenn er verstrichen ist. Eine PR-Nummer
    verfaellt, wenn der PR nicht mehr offen ist — dann haette der Befund gruen
    sein sollen. Ist der PR-Zustand nicht abfragbar, verfaellt nichts: eine
    unbeantwortete Frage ist kein Befund.
    """
    value = str(entry["gruen_erwartet"])
    num = _pr_number(value)
    if num is not None:
        if not token:
            return None
        closed = _pr_closed(num, repo, token)
        if closed:
            return f"PR #{num} ist nicht mehr offen"
        return None
    try:
        when = _parse(value)
    except ValueError:
        return f"gruen_erwartet {value!r} ist weder Zeitstempel noch PR-Nummer"
    return (f"erwartetes Grün-Datum verstrichen ({when:%d.%m. %H:%MZ})"
            if now > when else None)


def stale(entry: dict, now: dt.datetime) -> bool:
    try:
        return (now - _parse(str(entry["eingetragen_am"]))).days > STALE_DAYS
    except ValueError:
        return True


def classify(findings: list[dict], entries: list[dict], now: dt.datetime,
             *, token: str = "") -> dict:
    """Teilt die Befunde eines Laufs in neu, bekannt und verfallen.

    `findings` sind die Ergebnisse mit Status FAIL/ERROR/WARN, je ein dict mit
    mindestens `id` und `status`.
    """
    by_id = {e["invariante"]: e for e in entries}
    neu, bekannt, verfallen = [], [], []
    for f in findings:
        e = by_id.get(f.get("id"))
        if e is None or e["befund"] != f.get("status"):
            # Auch ein bekannter Befund, der sich verschaerft (WARN -> FAIL),
            # ist neu: das Register kennt den anderen Zustand.
            neu.append(f)
            continue
        why = expired(e, now, token=token)
        if why:
            verfallen.append({**f, "grund": why, "eintrag": e})
        else:
            bekannt.append({**f, "eintrag": e, "veraltet": stale(e, now)})
    return {"neu": neu, "bekannt": bekannt, "verfallen": verfallen}


def due_digest_slot(now: dt.datetime, sent: dict) -> tuple[str, dt.datetime] | None:
    """Der jüngste vergangene Sammelmeldungs-Takt, der noch nicht raus ist."""
    cands = []
    for day in (now.date(), now.date() - dt.timedelta(days=1)):
        for h in DIGEST_HOURS:
            t = dt.datetime.combine(day, dt.time(h), tzinfo=UTC)
            if t <= now:
                cands.append(t)
    if not cands:
        return None
    slot = max(cands)
    key = f"{slot:%Y-%m-%dT%H}"
    return None if key in sent else (key, slot)


# How far back the digest looks for the latest verdict of each invariant.
# Wider than a day, so a daily invariant (04:00 UTC) is still in view at both
# digest slots: the 20:00 window alone (08:00-20:00) holds no daily run.
STANDING_HOURS = 26
STANDING_STATUS = ("FAIL", "ERROR")


def standing_findings(docs: list, since: dt.datetime, until: dt.datetime,
                      skip=()) -> list[tuple[str, str, dt.datetime]]:
    """Findings that still stand: the latest verdict per invariant is red.

    Until 15.10.2026 the digest named only what went out immediately in its
    own window, plus the two registers. A finding that was neither in a
    register nor reported in the window did not exist for it.
    f-no-stale-escrow failed in every daily run from 08.10. on; the 20:00Z
    digest of 15.10. named it nowhere, because its window held no daily run,
    and on 11.10. its immediate message had been throttled.

    `docs` are run reports. Only runs that finished in [since, until) count,
    dry runs do not. A later green verdict clears an earlier red one. A red
    verdict carried by a declared known state (`bekannt_bis`, not overdue)
    is expected and not listed. `skip` holds ids the digest already names
    elsewhere (new, known, open).
    """
    latest: dict = {}
    for d in docs:
        if d.get("dry_run"):
            continue
        try:
            fin = dt.datetime.fromisoformat(str(d["finished"]))
        except (KeyError, ValueError):
            continue
        if fin.tzinfo is None:
            fin = fin.replace(tzinfo=UTC)
        if not (since <= fin < until):
            continue
        for r in d.get("ergebnisse") or []:
            rid = r.get("id")
            if not rid:
                continue
            if rid not in latest or fin >= latest[rid][1]:
                latest[rid] = (r, fin)
    skip = set(skip)
    out = []
    for rid, (r, fin) in latest.items():
        if rid in skip or r.get("status") not in STANDING_STATUS:
            continue
        if r.get("bekannt_bis") and not r.get("ueberfaellig"):
            continue
        out.append((rid, r["status"], fin))
    return sorted(out)


def digest_line(slot: dt.datetime, runs: int, neu, bekannt: list,
                autofix_gruen: list, offen: list | None = None,
                autofix_ohne: int = 0, stehend: list | None = None) -> str:
    """Die eine Zeile. Auch bei null neuen Befunden.

    Offene Befunde stehen als **eigene** Gruppe, nicht unter „bekannt": das
    eine ist erwartet und hat ein Grün-Datum, das andere ist unerklärt und hat
    einen Verfall. Sie zusammenzuzählen würde den Unterschied verwischen, auf
    den beide Register gebaut sind.
    """
    # `neu` darf eine Zahl ODER eine Liste von (Kennung, Befund) sein. Bis
    # zum 12.10.2026 war es nur eine Zahl, und "1 neue Befunde" sagte nicht,
    # welcher — wer die Meldung liest, musste den Laufbericht aufschlagen, um
    # zu erfahren, was gefunden wurde. Eine Sammelmeldung, die eine Zahl
    # nennt und keinen Namen, verschiebt die Arbeit nur.
    if isinstance(neu, int):
        neu_text = f"{neu} neue Befunde"
    else:
        namen = list(neu)
        if namen:
            neu_text = (f"{len(namen)} neue Befunde ("
                        + ", ".join(f"{kennung} {status}"
                                    for kennung, status in namen) + ")")
        else:
            neu_text = "0 neue Befunde"
    lauf_wort = "Lauf" if runs == 1 else "Läufe"
    parts = [f"Selftest {slot:%H:%M}Z — {runs} {lauf_wort}, {neu_text}, "
             f"{len(bekannt)} bekannt"]
    if bekannt:
        named = []
        # Nach Ziel gruppiert, und der Status kommt aus der Gruppe — nicht aus
        # dem ersten Eintrag der Liste. Die erste Fassung schrieb "2x FAIL an
        # PR #351" fuer zwei WARN, weil sie den Status des erstbesten Eintrags
        # nahm.
        by_target: dict[str, list] = {}
        for b in bekannt:
            e = b["eintrag"]
            val = str(e["gruen_erwartet"])
            if _pr_number(val) is not None:
                by_target.setdefault(val, []).append(b)
            else:
                try:
                    named.append(f"{e['invariante']} grün {_parse(val):%d.%m. %H:%MZ}")
                except ValueError:
                    named.append(f"{e['invariante']} grün {val}")
        for target, group in sorted(by_target.items()):
            stati = sorted({g["status"] for g in group})
            label = "/".join(stati)
            named.append(f"{len(group)}x {label} an {target}" if len(group) > 1
                         else f"{group[0]['eintrag']['invariante']} an {target}")
        parts.append(" (" + ", ".join(named) + ")")
    if offen:
        named = ", ".join(
            f"{e['invariante']}, verfällt {_parse(str(e['verfaellt_am'])):%d.%m. %H:%MZ}"
            for e in offen)
        parts.append(f", {len(offen)} offen ({named})")
    # Red in the latest run, in no register, not new in this window. Named
    # with the time of that run, so a daily finding reads as such.
    if stehend:
        named = ", ".join(f"{rid} {status} zuletzt {fin:%d.%m. %H:%MZ}"
                          for rid, status, fin in stehend)
        parts.append(f", {len(stehend)} stehend ({named})")
    if autofix_gruen:
        parts.append(", Autofix: " + ", ".join(
            f"{n}x korrigiert ({name})" if n > 1 else f"1x korrigiert ({name})"
            for name, n in sorted(autofix_gruen)))
    # Eigene Angabe und ausdruecklich nicht gruen. Bis zum 12.10.2026 zaehlte
    # die Autofix-Zahl jeden Lauf mit, dessen `autofix`-Feld irgendetwas
    # enthielt — auch den Wert "none", den die Erwartungsfamilien fuer
    # "kein Autofix vorhanden" setzen. In den Laufberichten des Oktobers steht
    # er 267 Mal; die Meldung haette "267x grün (none)" geschrieben und damit
    # Reparaturen behauptet, die es nicht gab.
    if autofix_ohne:
        parts.append(f", {autofix_ohne} "
                     f"{'Lauf' if autofix_ohne == 1 else 'Läufe'} "
                     f"ohne Eingriff")
    if not autofix_gruen and not autofix_ohne:
        parts.append(", keine Autofixe")
    return "".join(parts)


# Welche Form ein ausgefuehrter Autofix im Laufbericht hat. Der Runner
# schreibt "<name>: ok (Lauf n von m)" oder "<name>: fehlgeschlagen (...)";
# alles andere ist kein Eingriff: "none" heisst "kein Autofix vorhanden",
# ein nackter Name kommt aus den Erwartungsfamilien als Deklaration, und
# "waere gelaufen", "Deckel erreicht" und "nicht ausgefuehrt" sagen es selbst.
AUTOFIX_KORRIGIERT = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+): ok \(")


def autofix_zaehlen(werte) -> tuple[dict, int]:
    """({name: Zahl der Korrekturen}, Zahl der Laeufe ohne Eingriff).

    `werte` sind die rohen `autofix`-Felder der Laufberichte.
    """
    korrigiert: dict = {}
    ohne = 0
    for roh in werte:
        v = str(roh or "").strip()
        if not v or v == "none":
            # Kein Autofix vorhanden — kein Lauf, der zu zaehlen waere.
            continue
        m = AUTOFIX_KORRIGIERT.match(v)
        if m:
            name = m.group("name")
            korrigiert[name] = korrigiert.get(name, 0) + 1
        else:
            ohne += 1
    return korrigiert, ohne


def state(path: str = DIGEST_STATE) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 - erster Lauf
        return {}


def save_state(d: dict, path: str = DIGEST_STATE) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)
    os.chmod(path, 0o600)


def drop_from_register(ids: list[str], path: str = REGISTER) -> int:
    """Entfernt verfallene Einträge. Gibt die Zahl der entfernten zurück."""
    if not ids or not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    entries = doc.get("eintraege", [])
    keep = [e for e in entries if e.get("invariante") not in ids]
    removed = len(entries) - len(keep)
    if removed:
        doc["eintraege"] = keep
        doc["zuletzt_geaendert"] = dt.datetime.now(UTC).isoformat(timespec="seconds")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, ensure_ascii=False)
        os.chmod(path, 0o600)
    return removed

# --- Zweites Register: offene Befunde --------------------------------------
#
# Getrennt von `bekannte-abweichungen.json` und nie mit ihr vermischt. Dort
# stehen Abweichungen mit Grund und Grün-Datum; hier stehen Befunde, für die
# **noch keine Erklärung vorliegt**. Der Unterschied ist der Zweck: das eine
# Register sammelt Erwartetes, das andere hält Unerklärtes 72 Stunden ruhig,
# damit es untersucht werden kann, ohne dass stündlich eine Nachricht geht.
#
# Drei Grenzen, alle hart:
#   * 72 Stunden ab Eintragung, nicht verlängerbar. Ein Befund, der drei Tage
#     unerklärt bleibt, ist kein offener Befund mehr, sondern ein Zustand.
#   * höchstens drei Einträge gleichzeitig. Der vierte wird abgelehnt — ein
#     Register ohne Obergrenze ist eine Warteschlange, in der nichts untersucht
#     wird.
#   * höchstens eine Meldung je 24 Stunden, mit Zähler.
#
# Nach Verfall geht der Befund bei **jedem** Lauf sofort raus, dazu einmal die
# Meldung „72 h ohne Erklärung". Ein Eintrag wandert nur nach
# `bekannte-abweichungen.json`, wenn Grund und Grün-Datum vorliegen — von Lars
# freigegeben, nicht hier gesetzt.
OPEN_REGISTER = os.path.expanduser("~/selftest/offene-befunde.json")
OPEN_TTL_HOURS = 72
OPEN_MAX = 3
OPEN_NOTIFY_EVERY_HOURS = 24
OPEN_REQUIRED = ("invariante", "befund", "eingetragen_am", "verfaellt_am")


class OpenRegisterFull(ValueError):
    """Drei Einträge stehen. Der vierte wird abgelehnt, nicht verdrängt."""


def load_open(path: str = OPEN_REGISTER) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    entries = doc.get("eintraege") if isinstance(doc, dict) else doc
    if not isinstance(entries, list):
        raise RegisterError(f"{path}: kein eintraege-Feld mit einer Liste")
    for i, e in enumerate(entries):
        missing = [k for k in OPEN_REQUIRED if not e.get(k)]
        if missing:
            raise RegisterError(
                f"{path}, Eintrag {i} ({e.get('invariante', '?')}): "
                f"fehlende Felder {', '.join(missing)}")
    return entries


def save_open(entries: list[dict], path: str = OPEN_REGISTER) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {"zweck": ("Befunde ohne Erklaerung. 72 h ruhig, hoechstens drei, "
                     "hoechstens eine Meldung je 24 h. Danach zurueck auf "
                     "Sofortmeldung. Nie mit bekannte-abweichungen.json "
                     "vermischen."),
           "zuletzt_geaendert": dt.datetime.now(UTC).isoformat(timespec="seconds"),
           "eintraege": entries}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
    os.chmod(path, 0o600)


def add_open(invariante: str, befund: str, now: dt.datetime,
             path: str = OPEN_REGISTER) -> dict:
    """Traegt einen unerklaerten Befund ein. Wirft, wenn drei stehen."""
    entries = load_open(path)
    if any(e["invariante"] == invariante for e in entries):
        return next(e for e in entries if e["invariante"] == invariante)
    if len(entries) >= OPEN_MAX:
        raise OpenRegisterFull(
            f"{len(entries)} von {OPEN_MAX} Eintraegen stehen; "
            f"{invariante} wird abgelehnt. Erst einen erklaeren oder verfallen "
            f"lassen.")
    entry = {"invariante": invariante, "befund": befund,
             "eingetragen_am": now.isoformat(timespec="seconds"),
             "verfaellt_am": (now + dt.timedelta(hours=OPEN_TTL_HOURS)
                              ).isoformat(timespec="seconds"),
             "gesehen": 1, "zuletzt_gemeldet": None}
    entries.append(entry)
    save_open(entries, path)
    return entry


def open_due(entry: dict, now: dt.datetime) -> str | None:
    """Was mit diesem Eintrag zu tun ist: 'verfallen', 'melden' oder None."""
    if now >= _parse(str(entry["verfaellt_am"])):
        return "verfallen"
    last = entry.get("zuletzt_gemeldet")
    if not last:
        return "melden"
    return ("melden"
            if now - _parse(str(last)) >= dt.timedelta(hours=OPEN_NOTIFY_EVERY_HOURS)
            else None)


def expired_open(entries: list[dict], now: dt.datetime) -> list[dict]:
    """Offene Befunde, deren Verfall verstrichen ist.

    Nach der Uhr, nicht nach dem Wiederauftreten. Bis zum 12.10.2026 hing die
    Verfallspruefung in der Schleife ueber die Befunde DIESES Laufs: ein
    Eintrag verfiel nur, wenn seine Invariante erneut FAIL lieferte. Zwei
    Eintraege vom 07.10. waren Prosa-Befunde ohne Invariante, traten also nie
    wieder auf — und standen deshalb 48 Stunden ueber ihrem Verfall im
    Register, wurden in jeder Sammelmeldung als "2 offen" gezaehlt und
    stellten nichts mehr ruhig, was es noch gab. Ein Befund, dessen Ursache
    behoben ist, konnte unter der alten Regel nicht austreten; das ist das
    Gegenteil des Zwecks.

    Ein unlesbares `verfaellt_am` gilt als verstrichen: ein Eintrag, dessen
    Frist nicht lesbar ist, stellt nichts ruhig, was jemand nachpruefen kann.
    """
    raus = []
    for e in entries:
        try:
            bis = _parse(str(e["verfaellt_am"]))
        except (ValueError, KeyError, TypeError):
            raus.append({**e, "_grund": "verfaellt_am ist nicht lesbar"})
            continue
        if now >= bis:
            raus.append({**e, "_grund": f"Verfall {bis:%d.%m. %H:%MZ} "
                                        f"verstrichen"})
    return raus


def drop_from_open(ids: list[str], path: str = OPEN_REGISTER) -> int:
    """Entfernt Eintraege aus dem offenen Register. Zahl der entfernten.

    Das Gegenstueck zu `drop_from_register`. Es fehlte: das offene Register
    kannte das Eintragen und das Melden, nicht den Austritt.
    """
    if not ids:
        return 0
    entries = load_open(path)
    keep = [e for e in entries if e.get("invariante") not in ids]
    removed = len(entries) - len(keep)
    if removed:
        save_open(keep, path)
    return removed


def ueberfaellig_line(entry: dict) -> str:
    """Die Sofortmeldung eines ueberfaelligen offenen Befunds.

    Das Wort UEBERFAELLIG steht darin, weil `app/notify.py` es im
    Fingerabdruck normalisiert — ohne es faellt die Meldung in die
    Drosselung des unveraenderten Textes.
    """
    return (f"UEBERFAELLIG: {entry['invariante']} — {OPEN_TTL_HOURS} h ohne "
            f"Erklaerung, {entry.get('_grund', 'verfallen')}; Austritt aus "
            f"dem Register")


def open_line(entry: dict) -> str:
    """Die Meldung eines offenen Befunds, mit Zaehler und Verfall."""
    return (f"{entry['invariante']} {entry['befund']}, "
            f"{entry.get('gesehen', 1)}x seit letzter Meldung, unerklärt, "
            f"verfällt {_parse(str(entry['verfaellt_am'])):%d.%m. %H:%MZ}")



# ---------------------------------------------------------------------------
# Deploys: eine Zeile am Tag statt einer je Lauf
#
# Am 07./08.10.2026 gingen 35 Telegram-Nachrichten aus dem Deploy-Pfad raus,
# 32 davon "ok". Dreizehn Deploys in fuenfundvierzig Minuten sind sichtbar,
# sobald sie als Zahl dastehen, und unsichtbar, solange sie als dreizehn
# Nachrichten kommen.
#
# Gescheitert und zurueckgerollt melden weiter sofort. Hier steht nur, was
# sonst niemand mehr sieht.
# ---------------------------------------------------------------------------

DEPLOY_LOG = os.path.expanduser("~/selftest/deploy-log.jsonl")
DEPLOY_WINDOW_HOURS = 24
# Ab hier ist die Zahl selbst der Befund.
DEPLOY_BUSY = 10


def load_deploys(since: dt.datetime, until: dt.datetime,
                 path: str = DEPLOY_LOG) -> list[dict]:
    """Die Deploys im Fenster. Eine unlesbare Zeile wird uebersprungen und
    nicht als Null gelesen — eine kaputte Datei darf nicht wie ein ruhiger Tag
    aussehen; wie viele uebersprungen wurden, sagt `deploy_lines`."""
    rows, kaputt = [], 0
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                e = json.loads(raw)
                t = _parse(str(e["ts"]))
            except (ValueError, KeyError, TypeError):
                kaputt += 1
                continue
            if since <= t < until:
                e["_ts"] = t
                rows.append(e)
    rows.sort(key=lambda e: e["_ts"])
    if kaputt:
        rows.append({"_kaputt": kaputt})
    return rows


def deploy_lines(now: dt.datetime, path: str = DEPLOY_LOG) -> list[str]:
    """Die Deploy-Zeilen der Sammelmeldung. Immer mindestens eine.

    Null Deploys ist eine Aussage und keine Leerstelle: eine Kette, die
    stillsteht, faellt nur auf, wenn jemand sie jeden Tag erwaehnt.
    """
    until = now.replace(minute=0, second=0, microsecond=0)
    since = until - dt.timedelta(hours=DEPLOY_WINDOW_HOURS)
    rows = load_deploys(since, until, path)
    kaputt = sum(r.get("_kaputt", 0) for r in rows)
    rows = [r for r in rows if "_kaputt" not in r]

    ok = [r for r in rows if r.get("status") == "ok"]
    schief = [r for r in rows if r.get("status") != "ok"]
    if not rows:
        out = [f"Deploys {DEPLOY_WINDOW_HOURS} h — keine"]
        if kaputt:
            out.append(f"Deploy-Protokoll: {kaputt} Zeilen nicht lesbar")
        return out

    per: dict[str, int] = {}
    for r in ok:
        per[str(r.get("dienst", "?"))] = per.get(str(r.get("dienst", "?")), 0) + 1
    je = ", ".join(f"{d} {n}" for d, n in sorted(per.items(), key=lambda x: (-x[1], x[0])))
    letzter = rows[-1]
    line = (f"Deploys {DEPLOY_WINDOW_HOURS} h — {len(ok)} ok"
            + (f" ({je})" if je else "")
            + f", {len(schief)} gescheitert"
            + f", letzter {str(letzter.get('sha', '?'))[:7]} "
              f"{letzter['_ts']:%H:%M}Z")
    out = [line]

    # Each one that did not go through, by name. A count alone says something
    # failed; the line says which commit, where and how (2026-10-08: a refused
    # deploy left no trace anywhere else).
    for r in schief[-5:]:
        out.append(f"  {r.get('status', '?')}: {r.get('dienst', '?')} "
                   f"{str(r.get('sha', '?'))[:7]} {r['_ts']:%d.%m. %H:%M}Z")
    if len(schief) > 5:
        out.append(f"  … und {len(schief) - 5} weitere")

    viel = sorted((d, n) for d, n in per.items() if n > DEPLOY_BUSY)
    if viel:
        out.append("auffaellig viele Deploys: "
                   + ", ".join(f"{d} {n} in {DEPLOY_WINDOW_HOURS} h" for d, n in viel))
    if kaputt:
        out.append(f"Deploy-Protokoll: {kaputt} Zeilen nicht lesbar")
    return out
