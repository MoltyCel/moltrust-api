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
  Referrals are recorded since referral.CAPTURE_SINCE. A week whose window
  begins before that carries `erfasst_seit`, and every rendering prints it
  beside the figure: a zero there is partly a zero nobody could measure (ops
  KM1). The window of a week runs from its Monday to the cut; weeks from the
  first Monday after the capture start on carry no mark.

A week without a registration is a row of zeros, not a gap and not an error.

Per channel (ops AMB-1): the same rows, filtered, so a channel's figures are
the four above over a subset and cannot disagree with them. Moltbook is an
agent whose recorded referrer is the Ambassador's DID AND whose agent_source
says `moltbook` — the pair /join?ref=<ambassador>&src=moltbook produces. The
channel spends no money (WM-1, "ohne Geld"), so its cost is written as 0 USDC,
and the cost per activation is 0 USDC or, with nothing activated, a dash: a
quotient over zero activations is not a number.

Budget (ops WM-1): 20 USDC from 21.10.2026. The Monday line states what has
been paid out of it by the cut, the rest, and the cost per activation across
all channels: spend over the agents registered since the mandate began and
activated by the cut. The payments come from pool_spend (app/sql/wm1_budget.sql
says why not the ledger file); a read that fails says so in the line instead of
leaving it out.
"""
from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal, InvalidOperation

from app import referral, registry_export

SOURCE = "app/sql/registry_export.sql"
LEGEND = "registriert · aktiviert · zurück nach 7 T (von reifen Aktivierten) · mitgebracht"

# The Ambassador's DID, the referrer every Moltbook invitation carries. Pinned
# equal to agents/ambassador.py by tests/test_ambassador_did.py.
MOLTBOOK_REFERRER_DID = "did:moltrust:214298170282752d"
# Channels: name -> (referrer DID, agent_source value, spend in USDC). Spend is
# a declaration, not a measurement: a channel that starts paying gets its
# figure here, and the line shows it.
CHANNELS = {
    "Moltbook": (MOLTBOOK_REFERRER_DID, "moltbook", 0),
}
CHANNEL_LEGEND = "registriert · aktiviert · zurück nach 7 T · Kosten je Aktivierung"

# WM-1 (Lars, 21.10.2026): 20 USDC from 21.10.2026, paid from 0xa175.
WM1_BUDGET_USDC = Decimal("20")
WM1_SINCE = dt.date(2026, 10, 21)
WM1_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql", "wm1_budget.sql")


def wm1_spend(as_of: str) -> dict:
    """Payments booked against WM-1 before the cut: count and sum."""
    rows = registry_export._psql(as_of, sql_path=WM1_SQL)
    if len(rows) != 1 or not {"zahlungen", "ausgegeben"} <= set(rows[0]):
        raise ValueError(f"unexpected answer from wm1_budget.sql: {len(rows)} rows")
    return {"zahlungen": int(rows[0]["zahlungen"]),
            "ausgegeben": str(Decimal(str(rows[0]["ausgegeben"])))}


def activated_since(raw: list[dict], since: dt.date) -> int:
    """Counted agents registered on or after `since` and activated by the cut."""
    return sum(1 for r in raw
               if r.get("bucket") in registry_export.COUNTED
               and r.get("activated")
               and str(r.get("registered") or "") >= since.isoformat())


def budget_block(raw: list[dict], as_of: str, spend=None) -> dict:
    """The WM-1 figures for the block; a failed read is carried, not dropped."""
    out = {"budget": str(WM1_BUDGET_USDC), "seit": WM1_SINCE.isoformat(),
           "aktiviert_seit": activated_since(raw, WM1_SINCE)}
    try:
        out.update((spend or wm1_spend)(as_of))
    except Exception as exc:  # noqa: BLE001 - the line names the failure
        out["fehler"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out


def budget_line(b: dict) -> str:
    """Budget, spend, rest, and cost per activation over all channels."""
    seit = dt.date.fromisoformat(b["seit"])
    head = f"Budget WM-1 ({Decimal(b['budget']):f} USDC ab {seit:%d.%m.%Y})"
    if b.get("fehler"):
        return f"{head}: Ausgaben nicht lesbar ({b['fehler']})"
    try:
        spent = Decimal(b["ausgegeben"])
    except (InvalidOperation, KeyError, TypeError):
        return f"{head}: Ausgaben nicht lesbar (ausgegeben={b.get('ausgegeben')!r})"
    budget = Decimal(b["budget"])
    rest = budget - spent
    pct = int((spent * 100 / budget).to_integral_value()) if budget else 0
    act = int(b.get("aktiviert_seit") or 0)
    n = int(b.get("zahlungen") or 0)
    return (f"{head}: ausgegeben {spent:f} USDC in {n} {'Zahlung' if n == 1 else 'Zahlungen'}, "
            f"Rest {rest:f} USDC ({pct} % verbraucht); Kosten je Aktivierung über "
            f"alle Kanäle {cost_per_activation(float(spent), act)} "
            f"({act} aktiviert seit {seit:%d.%m.})")


def channel_rows(raw: list[dict], name: str) -> list[dict]:
    """The export rows one channel produced: its referrer and its source, both."""
    referrer, source, _ = CHANNELS[name]
    return [r for r in raw
            if r.get("referrer_did") == referrer and r.get("source") == source]


def cost_per_activation(spend_usdc: float, activated: int) -> str:
    """Spend over activations; a dash when nothing activated."""
    if not activated:
        return "—"
    per = spend_usdc / activated
    return f"{per:g} USDC"


def _week_key(d: dt.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _week_start(key: str) -> dt.date:
    y, w = key.split("-W")
    return dt.date.fromisocalendar(int(y), int(w), 1)


def _capture_mark(week_start: dt.date) -> str | None:
    """`erfasst_seit` for a week whose window lies before or across the start
    of referral capture, None for a week observed in full.

    The window starts on the week's Monday at 00:00Z and ends at the cut, so
    the Monday alone decides: a cut before the capture start leaves the window
    entirely before it, a later cut has it cross the start.
    """
    start = dt.datetime.combine(week_start, dt.time(0), tzinfo=dt.timezone.utc)
    if start < referral.CAPTURE_SINCE:
        return referral.CAPTURE_SINCE.strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _mark_text(w: dict) -> str:
    seit = w["mitgebracht"].get("erfasst_seit")
    return _mark_of(seit)


def _mark_of(seit: str | None) -> str:
    if not seit:
        return ""
    at = dt.datetime.strptime(seit, "%Y-%m-%dT%H:%M:%SZ")
    return f" (erfasst ab {at:%d.%m. %H:%M}Z)"


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
            "mitgebracht": {"wert": w["mitgebracht"], "von": w["registriert"],
                            "erfasst_seit": _capture_mark(d)},
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
                           "widerrufene DID zählen nicht (Regel app/referral.py). "
                           "Erfasst seit "
                           + referral.CAPTURE_SINCE.strftime("%d.%m.%Y %H:%M:%SZ")
                           + "; eine Woche, deren Fenster davor beginnt, trägt "
                           "erfasst_seit",
        },
        "wochen": weeks(raw, as_of, since, until),
        "kanaele": {name: weeks(channel_rows(raw, name), as_of, since, until)
                    for name in CHANNELS},
        "budget_wm1": budget_block(raw, as_of),
    }


def _fmt(w: dict) -> str:
    return (f"{w['woche'].split('-')[1]} {w['registriert']['wert']} · "
            f"{w['aktiviert']['wert']}/{w['aktiviert']['von']} · "
            f"{w['zurueck_nach_7_tagen']['wert']}/{w['zurueck_nach_7_tagen']['von']} · "
            f"{w['mitgebracht']['wert']}/{w['mitgebracht']['von']}{_mark_text(w)}")


def digest_line(rows: list[dict], slot_at: dt.datetime) -> str:
    """One line: the two registration weeks completed before the slot."""
    this_monday = slot_at.date() - dt.timedelta(days=slot_at.weekday())
    wanted = [_week_key(this_monday - dt.timedelta(days=7)),
              _week_key(this_monday - dt.timedelta(days=14))]
    have = {w["woche"]: w for w in rows}
    parts = [_fmt(have[k]) if k in have else
             f"{k.split('-')[1]} 0 · 0/0 · 0/0 · 0/0{_mark_of(_capture_mark(_week_start(k)))}"
             for k in wanted]
    return (f"Kohorten ({LEGEND}), Stichtag {slot_at:%d.%m. %H:%MZ}: "
            + " — ".join(parts))


def _wanted_weeks(slot_at: dt.datetime) -> list[str]:
    this_monday = slot_at.date() - dt.timedelta(days=slot_at.weekday())
    return [_week_key(this_monday - dt.timedelta(days=7)),
            _week_key(this_monday - dt.timedelta(days=14))]


def channel_line(name: str, rows: list[dict], slot_at: dt.datetime) -> str:
    """One channel, the same two weeks as the cohort line, cost stated."""
    spend = CHANNELS[name][2]
    have = {w["woche"]: w for w in rows}
    parts = []
    for k in _wanted_weeks(slot_at):
        w = have.get(k)
        reg = w["registriert"]["wert"] if w else 0
        act, act_von = (w["aktiviert"]["wert"], w["aktiviert"]["von"]) if w else (0, 0)
        back, back_von = ((w["zurueck_nach_7_tagen"]["wert"],
                           w["zurueck_nach_7_tagen"]["von"]) if w else (0, 0))
        parts.append(f"{k.split('-')[1]} {reg} · {act}/{act_von} · {back}/{back_von} · "
                     f"{cost_per_activation(spend, act)}")
    return (f"Kanal {name} ({CHANNEL_LEGEND}), Kosten {spend:g} USDC: "
            + " — ".join(parts))


def digest_lines(slot_at: dt.datetime, fetch=None) -> list[str]:
    """The Monday 08:00 UTC lines of the Sammelmeldung; nothing in other slots."""
    if slot_at.weekday() != 0 or slot_at.hour != 8:
        return []
    this_monday = slot_at.date() - dt.timedelta(days=slot_at.weekday())
    since = this_monday - dt.timedelta(days=14)
    b = (fetch or block)(slot_at.isoformat(), since,
                         this_monday - dt.timedelta(days=1))
    # A fetch that predates the channel block yields the cohort line alone
    # rather than a channel line of zeros nobody measured.
    lines = [digest_line(b["wochen"], slot_at)] + [
        channel_line(name, rows, slot_at) for name, rows in (b.get("kanaele") or {}).items()]
    if "budget_wm1" in b:
        lines.append(budget_line(b["budget_wm1"]))
    return lines


def markdown(b: dict) -> str:
    """The back-calculation table, one row per week."""
    head = ("| Woche | registriert | aktiviert | zurück nach 7 T | mitgebracht | "
            "davon vor Telemetrie-Stichtag 14.09. |\n|---|---:|---:|---:|---:|---:|\n")
    body = "".join(
        f"| {w['woche']} | {w['registriert']['wert']} | "
        f"{w['aktiviert']['wert']} von {w['aktiviert']['von']} | "
        f"{w['zurueck_nach_7_tagen']['wert']} von {w['zurueck_nach_7_tagen']['von']} | "
        f"{w['mitgebracht']['wert']} von {w['mitgebracht']['von']}{_mark_text(w)} | "
        f"{w['vor_telemetrie_stichtag']} |\n" for w in b["wochen"])
    defs = "".join(f"- **{k}**: {v}\n" for k, v in b["definitionen"].items())
    return (f"# Kohorten je Registrierungswoche\n\nStichtag {b['stichtag']}, Quelle "
            f"`{b['quelle']}` (dieselbe Abfrage wie der Registry-Beweis).\n\n"
            f"{head}{body}\n{defs}")
