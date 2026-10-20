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

Extended 2026-10-20 (fix/rev-svg): the two SVG badges, the wallet and ERC-8004
lookups, /trust/gate, /x402/verify and /swarm/propagate. An SVG affirms when it
does not say REVOKED; /trust/gate affirms with `decision: ALLOW`; /x402/verify
with `payment_ready: true`. Nested statements count too: `trust.verified` on the
A2A card, `moltrust_verified` and `moltrust_trust_score.grade` on the ERC-8004
reverse lookup, and the node of the asked DID in /swarm/graph — the first
version read only top-level fields, so it never actually checked the card or
the graph. Routes keyed by a wallet or an ERC-8004 id are asked only for
revoked DIDs that have one; a DID without one is not a finding either way.

Extended 2026-10-20 (fix/rev-badge-cache): a badge that says REVOKED to the
check can still show a grade to everyone else, from a cache. Both SVG badges
went out with `max-age=3600, s-maxage=3600`, so a browser or an image proxy
kept "85 / A" for up to an hour after a revocation. The three badge routes are
now also judged on their Cache-Control: anything a cache may reuse without
revalidating is a finding (`cache_allows_stale`). And /attestations/<id>, which
read the grade from trust_score_cache alone, is asked too: a revocation by hand
leaves that row in place.

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

# Jeder Eintrag: (Name, URL-Vorlage). `{did}` wird eingesetzt, `{wallet}` und
# `{erc8004}` nur, wenn die widerrufene DID eine gebundene Wallet bzw. eine
# ERC-8004-ID hat; sonst wird der Eintrag fuer diese DID uebersprungen.
ENDPOINTS = (
    ("/identity/verify/<did>", "/identity/verify/{did}"),
    ("/identity/badge/<did>", "/identity/badge/{did}"),
    ("/identity/badge/<did>.svg", "/identity/badge/{did}.svg"),
    ("/badge/<did>", "/badge/{did}"),
    ("/skill/trust-score/<did>", "/skill/trust-score/{did}"),
    ("/a2a/agent-card/<did>", "/a2a/agent-card/{did}"),
    ("/swarm/graph/<did>", "/swarm/graph/{did}"),
    ("/trust/gate/<did>", "/trust/gate/{did}"),
    ("/x402/verify?did=<did>", "/x402/verify?did={did}"),
    ("/agents/<did>/erc8004", "/agents/{did}/erc8004"),
    ("/wallet/<address>", "/wallet/{wallet}"),
    ("/identity/erc8004/<address>", "/identity/erc8004/{wallet}"),
    ("/resolve/erc8004/<agent_id>", "/resolve/erc8004/{erc8004}"),
    ("/attestations/<id>?did=<did>", "/attestations/{tail}?did={did}"),
)
# Routes meant to be embedded or shown by a page: their answer must also not be
# reusable from a cache once the DID is revoked.
REVALIDATED = {"/identity/badge/<did>", "/identity/badge/<did>.svg", "/badge/<did>"}
# Directives that let some cache show a stored answer without asking first.
_STALE_DIRECTIVES = ("s-maxage", "stale-while-revalidate", "stale-if-error", "immutable")
# Not asked: /swarm/propagate/<did> deletes and rewrites the score cache, and a
# check does not write. It shares app/revocation.py with the routes above and is
# covered by tests/test_revoked_did_every_route.py.

# Felder, die eine Bejahung ausdrücken, und was als Widerruf gilt.
AFFIRMING = ("verified", "valid", "is_valid", "active", "trusted",
             "payment_ready", "moltrust_verified")
# Nested objects that carry their own statement about the same DID.
NESTED = ("trust", "moltrust_trust_score")


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-t", "-F", "\x1f"],
        input=sql, capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:200])
    return [ln.split("\x1f") for ln in out.stdout.splitlines() if ln.strip()]


def get(url: str) -> tuple[int, object, dict]:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:  # noqa: S310  # nosec B310 - BASE is the literal https API host
            body = r.read().decode("utf-8", "replace")
            headers = {k.lower(): v for k, v in r.headers.items()}
            try:
                return r.status, json.loads(body or "{}"), headers
            except json.JSONDecodeError:
                # An SVG badge is read whole: what it shows is the finding.
                return r.status, body[:20000], headers
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        headers = {k.lower(): v for k, v in e.headers.items()}
        try:
            return e.code, json.loads(body or "{}"), headers
        except json.JSONDecodeError:
            return e.code, body[:200], headers
    except Exception as exc:  # noqa: BLE001 - ein unerreichbarer Endpunkt ist kein Grün
        return 0, {"error": f"{type(exc).__name__}: {exc}"}, {}


def cache_allows_stale(cache_control: str | None) -> str | None:
    """Why a cache may reuse this answer without asking, or None.

    Fine is `no-cache` or `no-store` with none of the directives that let a
    cache serve a stored copy anyway. A missing header is a finding: then a
    cache decides on its own (heuristic freshness, RFC 9111 4.2.2).
    """
    if not cache_control:
        return "kein Cache-Control"
    directives = {d.strip().split("=")[0].lower() for d in cache_control.split(",") if d.strip()}
    for d in _STALE_DIRECTIVES:
        if d in directives:
            return f"Cache-Control {d}"
    if not directives & {"no-cache", "no-store"}:
        return f"Cache-Control ohne no-cache: {cache_control}"
    return None


def _affirms_flat(payload: dict) -> str | None:
    for field in AFFIRMING:
        if payload.get(field) is True:
            return field
    if payload.get("decision") == "ALLOW":
        return "decision=ALLOW"
    grade = payload.get("grade")
    if isinstance(grade, str) and grade and grade.upper() != "REVOKED":
        return f"grade={grade}"
    return None


def affirms(payload: object, did: str | None = None) -> str | None:
    """Gibt das bejahende Feld zurück, oder None."""
    if isinstance(payload, str):
        # An SVG badge: anything but REVOKED on it is a grade or a score.
        if payload.lstrip().startswith("<svg") and "REVOKED" not in payload:
            return "svg ohne REVOKED"
        return None
    if not isinstance(payload, dict):
        return None
    field = _affirms_flat(payload)
    if field:
        return field
    for key in NESTED:
        inner = payload.get(key)
        if isinstance(inner, dict):
            field = _affirms_flat(inner)
            if field:
                return f"{key}.{field}"
    if did is not None and isinstance(payload.get("nodes"), list):
        for node in payload["nodes"]:
            if isinstance(node, dict) and node.get("did") == did:
                field = _affirms_flat(node)
                if field:
                    return f"nodes[{did[-8:]}].{field}"
    return None


def main() -> int:
    try:
        rows = psql("SELECT did, coalesce(wallet_address, ''), "
                    "coalesce(erc8004_agent_id::text, '') "
                    "FROM agents WHERE revoked_at IS NOT NULL ORDER BY revoked_at")
    except Exception as exc:  # noqa: BLE001
        print(f"UNREADABLE: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(-1)
        return 2
    subjects = [
        {"did": r[0],
         "wallet": r[1] if len(r) > 1 and r[1] else None,
         "erc8004": r[2] if len(r) > 2 and r[2] else None,
         "tail": r[0].rsplit(":", 1)[-1]}
        for r in rows if r and r[0].startswith("did:")
    ]
    dids = [s["did"] for s in subjects]
    if not dids:
        print("UNREADABLE: keine widerrufene DID in der Datenbank — die Pruefung "
              "hat nichts geprueft", file=sys.stderr)
        print(-1)
        return 2

    print(f"{len(dids)} widerrufene DIDs, {len(ENDPOINTS)} Endpunkte")
    findings = 0
    unreachable = 0
    for subj in subjects:
        did = subj["did"]
        for name, tmpl in ENDPOINTS:
            if ("{wallet}" in tmpl and not subj["wallet"]) or \
               ("{erc8004}" in tmpl and not subj["erc8004"]):
                continue
            status, payload, headers = get(BASE + tmpl.format(**subj))
            if status == 0:
                unreachable += 1
                print(f"  UNERREICHBAR {name} fuer {did[-16:]}: {payload}")
                continue
            if status == 404:
                continue          # nicht gefuehrt ist keine Bejahung
            field = affirms(payload, did)
            if not field and name in REVALIDATED:
                field = cache_allows_stale(headers.get("cache-control"))
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
