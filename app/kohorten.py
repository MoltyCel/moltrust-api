"""Four figures per registration week (ops G6, strategy ops/wachstum.md).

registered · of them activated · of them back after seven days · of them
brought someone. Each figure carries its population and the cut it was read
at, and all four come out of app/sql/registry_export.sql, the query behind the
registry proof. A second query for the same agents is how the proof page and a
report came to say two different things on 2026-09-21; there is one.

Definitions, as the query computes them:

- registered: counted bucket (bounty, partner, organic), not revoked, at least
  one anchored credential, registered before the cut.
- activated: at least one authenticated call to an endpoint no bounty task
  text prescribed, before the cut. Population: registered.
- back after seven days: such a call at least seven days after registration.
  Population: the activated agents of the week that are seven days old at the
  cut. A younger agent cannot have come back yet, and counting it in the
  population would read as a drop that is only the calendar.
- brought: at least one counted referral recorded before the cut. Which
  referral counts is decided in app/referral.py — a self-referral, a mutual
  pair, an unknown and a revoked DID do not. Population: registered.

A week without a registration is a row of zeros, not a gap and not an error.
"""
from __future__ import annotations

import datetime as dt

from app import referral, registry_export

SOURCE = "app/sql/registry_export.sql"
LEGEND = "registriert · aktiviert · zurück nach 7 T (von reifen Aktivierten) · mitgebracht"


def _week_key(d: dt.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _week_start(key: str) -> dt.date:
    y, w = key.split("-W")
    return dt.date.fromisocalendar(int(y), int(w), 1)


def weeks(raw: list[dict], as_of: str, since: dt.date | None = None,
          until: dt.date | None = None) -> list[dict]:
    """Cohort rows per ISO week from the export's raw rows, gaps filled."""
    counted = [r for r in raw if r.get("bucket") in registry_export.COUNTED]
    by: dict[str, dict] = {}
    for r in counted:
        k = r["cohort_week"]
        w = by.setdefault(k, {"registriert": 0, "aktiviert": 0, "reif_aktiviert": 0,
                              "zurueck": 0, "mitgebracht": 0, "vor_telemetrie": 0})
        w["registriert"] += 1
        w["vor_telemetrie"] += 1 if r.get("before_telemetry_cutoff") else 0
        if r.get("activated"):
            w["aktiviert"] += 1
            if r.get("mature_7d"):
                w["reif_aktiviert"] += 1
                w["zurueck"] += 1 if r.get("returned_7d") else 0
        w["mitgebracht"] += 1 if referral.counts(
            referral.facts_of(r))["brought"] > 0 else 0

    keys = sorted(by)
    first = _week_start(_week_key(since)) if since else (
        _week_start(keys[0]) if keys else None)
    last = _week_start(_week_key(until)) if until else (
        _week_start(keys[-1]) if keys else None)
    out = []
    d = first
    while d is not None and last is not None and d <= last:
        k = _week_key(d)
        w = by.get(k) or {"registriert": 0, "aktiviert": 0, "reif_aktiviert": 0,
                          "zurueck": 0, "mitgebracht": 0, "vor_telemetrie": 0}
        out.append({
            "woche": k,
            "stichtag": as_of,
            "registriert": {"wert": w["registriert"], "von": None,
                            "grundgesamtheit": "gezaehlte Agenten mit verankertem Credential"},
            "aktiviert": {"wert": w["aktiviert"], "von": w["registriert"]},
            "zurueck_nach_7_tagen": {"wert": w["zurueck"], "von": w["reif_aktiviert"]},
            "mitgebracht": {"wert": w["mitgebracht"], "von": w["registriert"]},
            "vor_telemetrie_stichtag": w["vor_telemetrie"],
        })
        d += dt.timedelta(days=7)
    return out


def block(as_of: str | None = None, since: dt.date | None = None,
          until: dt.date | None = None) -> dict:
    """The cohort block for /rounds/dauer/<KW>/result.json."""
    as_of = as_of or dt.datetime.now(dt.timezone.utc).replace(
        second=0, microsecond=0).isoformat()
    raw = registry_export._psql(as_of)
    return {
        "stichtag": as_of,
        "quelle": SOURCE,
        "definitionen": {
            "registriert": "Bucket bounty, partner oder organic, nicht widerrufen, "
                           "mindestens ein verankertes Credential",
            "aktiviert": "mindestens ein authentifizierter Aufruf an einen Endpunkt, "
                         "den kein Aufgabentext vorschreibt",
            "zurueck_nach_7_tagen": "ein solcher Aufruf frühestens sieben Tage nach der "
                                    "Registrierung; Grundgesamtheit: Aktivierte der Woche, "
                                    "die am Stichtag sieben Tage alt sind",
            "mitgebracht": "mindestens eine gezählte Empfehlung in agent_referrals; "
                           "Selbstempfehlung, gegenseitiges Paar, unbekannte und "
                           "widerrufene DID zählen nicht (Regel app/referral.py)",
        },
        "wochen": weeks(raw, as_of, since, until),
    }


def _fmt(w: dict) -> str:
    return (f"{w['woche'].split('-')[1]} {w['registriert']['wert']} · "
            f"{w['aktiviert']['wert']}/{w['aktiviert']['von']} · "
            f"{w['zurueck_nach_7_tagen']['wert']}/{w['zurueck_nach_7_tagen']['von']} · "
            f"{w['mitgebracht']['wert']}/{w['mitgebracht']['von']}")


def digest_line(rows: list[dict], slot_at: dt.datetime) -> str:
    """One line: the two registration weeks completed before the slot."""
    this_monday = slot_at.date() - dt.timedelta(days=slot_at.weekday())
    wanted = [_week_key(this_monday - dt.timedelta(days=7)),
              _week_key(this_monday - dt.timedelta(days=14))]
    have = {w["woche"]: w for w in rows}
    parts = [_fmt(have[k]) if k in have else f"{k.split('-')[1]} 0 · 0/0 · 0/0 · 0/0"
             for k in wanted]
    return (f"Kohorten ({LEGEND}), Stichtag {slot_at:%d.%m. %H:%MZ}: "
            + " — ".join(parts))


def digest_lines(slot_at: dt.datetime, fetch=None) -> list[str]:
    """The Monday 08:00 UTC line of the Sammelmeldung; nothing in other slots."""
    if slot_at.weekday() != 0 or slot_at.hour != 8:
        return []
    this_monday = slot_at.date() - dt.timedelta(days=slot_at.weekday())
    since = this_monday - dt.timedelta(days=14)
    b = (fetch or block)(slot_at.isoformat(), since,
                         this_monday - dt.timedelta(days=1))
    return [digest_line(b["wochen"], slot_at)]


def markdown(b: dict) -> str:
    """The back-calculation table, one row per week."""
    head = ("| Woche | registriert | aktiviert | zurück nach 7 T | mitgebracht | "
            "davon vor Telemetrie-Stichtag 14.09. |\n|---|---:|---:|---:|---:|---:|\n")
    body = "".join(
        f"| {w['woche']} | {w['registriert']['wert']} | "
        f"{w['aktiviert']['wert']} von {w['aktiviert']['von']} | "
        f"{w['zurueck_nach_7_tagen']['wert']} von {w['zurueck_nach_7_tagen']['von']} | "
        f"{w['mitgebracht']['wert']} von {w['mitgebracht']['von']} | "
        f"{w['vor_telemetrie_stichtag']} |\n" for w in b["wochen"])
    defs = "".join(f"- **{k}**: {v}\n" for k, v in b["definitionen"].items())
    return (f"# Kohorten je Registrierungswoche\n\nStichtag {b['stichtag']}, Quelle "
            f"`{b['quelle']}` (dieselbe Abfrage wie der Registry-Beweis).\n\n"
            f"{head}{body}\n{defs}")
