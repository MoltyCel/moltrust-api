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


def digest_line(slot: dt.datetime, runs: int, neu: int, bekannt: list,
                autofix_gruen: list, offen: list | None = None) -> str:
    """Die eine Zeile. Auch bei null neuen Befunden.

    Offene Befunde stehen als **eigene** Gruppe, nicht unter „bekannt": das
    eine ist erwartet und hat ein Grün-Datum, das andere ist unerklärt und hat
    einen Verfall. Sie zusammenzuzählen würde den Unterschied verwischen, auf
    den beide Register gebaut sind.
    """
    parts = [f"Selftest {slot:%H:%M}Z — {runs} Läufe, {neu} neue Befunde, "
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
    if autofix_gruen:
        parts.append(", Autofix: " + ", ".join(
            f"{n}x grün ({name})" if n > 1 else f"1x grün ({name})"
            for name, n in sorted(autofix_gruen)))
    return "".join(parts)


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


def open_line(entry: dict) -> str:
    """Die Meldung eines offenen Befunds, mit Zaehler und Verfall."""
    return (f"{entry['invariante']} {entry['befund']}, "
            f"{entry.get('gesehen', 1)}x seit letzter Meldung, unerklärt, "
            f"verfällt {_parse(str(entry['verfaellt_am'])):%d.%m. %H:%MZ}")

