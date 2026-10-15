#!/usr/bin/env python3
"""Kein Endpunkt bejaht Gültigkeit für eine widerrufene Kennung.

Am 07.10.2026 antworteten `/identity/verify/<did>` und `/identity/badge/<did>`
für alle **16** widerrufenen DIDs mit `verified: true` — der älteste Widerruf
vom 15.04.2026, zwölf weitere vom 19.09. Gemessen: 32 Bejahungen.
`/skill/trust-score/` antwortete für dieselben DIDs korrekt mit
`grade: REVOKED`. Dieselbe Frage, zwei Antworten, und die falsche stand auf
dem Pfad, den eine Gegenparteiprüfung tatsächlich benutzt: am 07.10. um 03:59
rief eine Quelle `/identity/verify` 37 Mal über 36 DIDs in fünfzehn Sekunden
auf.

Die Prüfung holt die widerrufenen DIDs aus der Datenbank — nicht aus einer
Liste im Quelltext, sonst prüft sie den Stand von gestern — und fragt jeden
Endpunkt, der eine Gültigkeitsaussage trifft, einzeln ab. Ein `verified: true`,
ein `valid: true` oder ein Grade, das nicht `REVOKED` ist, ist ein Befund.

Keine Widerrufe in der Datenbank heißt UNREADABLE und nicht null: dann hat die
Prüfung nichts geprüft.

Letzte Zeile ist die Zahl der Endpunkte, die bejahen. Exit 0 ohne, 1 mit,
2 wenn die Frage nicht beantwortbar ist.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "https://api.moltrust.ch"

# Jeder Eintrag: (Name, URL-Vorlage). `{did}` wird eingesetzt.
ENDPOINTS = (
    ("/identity/verify/<did>", "/identity/verify/{did}"),
    ("/identity/badge/<did>", "/identity/badge/{did}"),
    ("/skill/trust-score/<did>", "/skill/trust-score/{did}"),
    ("/a2a/agent-card/<did>", "/a2a/agent-card/{did}"),
    ("/swarm/graph/<did>", "/swarm/graph/{did}"),
)

# Felder, die eine Bejahung ausdrücken, und was als Widerruf gilt.
AFFIRMING = ("verified", "valid", "is_valid", "active", "trusted")


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-t", "-F", "\x1f"],
        input=sql, capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:200])
    return [ln.split("\x1f") for ln in out.stdout.splitlines() if ln.strip()]


def get(url: str) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:  # noqa: S310
            body = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(body or "{}")
            except json.JSONDecodeError:
                return r.status, body[:200]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body or "{}")
        except json.JSONDecodeError:
            return e.code, body[:200]
    except Exception as exc:  # noqa: BLE001 - ein unerreichbarer Endpunkt ist kein Grün
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def affirms(payload: object) -> str | None:
    """Gibt das bejahende Feld zurück, oder None."""
    if not isinstance(payload, dict):
        return None
    for field in AFFIRMING:
        if payload.get(field) is True:
            return field
    grade = payload.get("grade")
    if isinstance(grade, str) and grade and grade.upper() != "REVOKED":
        return f"grade={grade}"
    return None


def main() -> int:
    try:
        rows = psql("SELECT did FROM agents WHERE revoked_at IS NOT NULL ORDER BY revoked_at")
    except Exception as exc:  # noqa: BLE001
        print(f"UNREADABLE: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(-1)
        return 2
    dids = [r[0] for r in rows if r and r[0].startswith("did:")]
    if not dids:
        print("UNREADABLE: keine widerrufene DID in der Datenbank — die Pruefung "
              "hat nichts geprueft", file=sys.stderr)
        print(-1)
        return 2

    print(f"{len(dids)} widerrufene DIDs, {len(ENDPOINTS)} Endpunkte")
    findings = 0
    unreachable = 0
    for did in dids:
        for name, tmpl in ENDPOINTS:
            status, payload = get(BASE + tmpl.format(did=did))
            if status == 0:
                unreachable += 1
                print(f"  UNERREICHBAR {name} fuer {did[-16:]}: {payload}")
                continue
            if status == 404:
                continue          # nicht gefuehrt ist keine Bejahung
            field = affirms(payload)
            if field:
                findings += 1
                print(f"  BEJAHT {name} fuer {did[-16:]} — {field}, HTTP {status}")
    if unreachable:
        # Ein Endpunkt, der nicht antwortet, beantwortet die Frage nicht.
        print(f"UNREADABLE: {unreachable} Abfragen ohne Antwort", file=sys.stderr)
        print(-1)
        return 2
    if not findings:
        print("  kein Endpunkt bejaht")
    print(findings)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
