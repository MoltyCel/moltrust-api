#!/usr/bin/env python3
"""The standing round (ops G4-2): rule and split of one week, without money.

One pure function, `abrechnung`, turns the referral cases of one week into the
list of payout rows. It reads no database, holds no key, sends nothing and is
not on a cron. Gathering the cases (request_log, agents, agent_referrals) and
proposing the payout is G4-3; paying is G4-5 and stays with Lars.

The rule, as ops G4 states it:

- An activation is the first authenticated call of a referred agent to an
  endpoint that no task text prescribed, at most 14 days after the referred
  agent registered. "Prescribed" is the `scripted` list in
  app/sql/registry_export.sql, read from that file, so the cohort figure
  "activated" (G6) and the paid activation cannot drift apart.
- The referral must count under app/referral.py (`pair_counts`): no
  self-referral, no mutual pair, no unknown and no revoked referred DID.
- Referrer and referred differ in address, key and operator_did. A missing
  value is not a difference: it is a measurement we do not have, and a
  payout on a missing measurement is the fallback in the permissive
  direction. Such a case is refused with its own class, which the agent can
  fix (declare an operator, bind a wallet). The same /24 is noted, never
  refused - no agent could check that in advance.
- An operator_did on config/ausgeschlossene-betreiber.json, on either side,
  is refused. An unreadable list stops the run (`AusschlussUnlesbar`).
- At most three paid activations per operator_did and week, counted on both
  sides, in order of activation time. Later ones are qualified and unpaid.
- The weekly budget plus the carry-over is split equally over the paid
  activations, at most 0.05 USDC each, half to the referred agent and half
  to the referrer. Whatever is not paid carries over, including a share that
  would go to one of our own addresses.

Amounts are integer micro-USDC (6 decimals) throughout; a float never touches
money here.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_DOWN, Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.own_addresses import OWN_ADDRESSES  # noqa: E402
from app.referral import pair_counts  # noqa: E402
from scripts import gruppen as G  # noqa: E402

WURZEL = pathlib.Path(__file__).resolve().parent.parent

# --- The constants of the rule ----------------------------------------------
# Every figure the public text (G4-4) names comes from here. The pre-check
# (scripts/runde_vorpruefung.py, H2) reads them through `messwerte()`.

#: Weekly budget. ops G4 named 1.626 USDC; WM-1 (Lars 21.10.2026) replaced it
#: with 5 USDC a week.
WOCHENBUDGET_USDC = Decimal("5")
#: Cap per activation, both halves together.
DECKEL_JE_AKTIVIERUNG_USDC = Decimal("0.05")
#: Activation window after registration.
FENSTER_TAGE = 14
#: Paid activations per operator_did and week.
BEZAHLT_JE_BETREIBER = 3
#: Settlement: Monday 06:00 UTC, for the ISO week that ended before it.
ABRECHNUNG_WOCHENTAG = "Monday"
ABRECHNUNG_UTC = "06:00"
#: Where the result of a week is published (G4-3).
ERGEBNIS_URL = "https://moltrust.ch/rounds/dauer/<KW>/result.json"

MIKRO = 1_000_000
MERKMALE = ("adresse", "schluessel", "betreiber")


def mikro(usdc: Decimal) -> int:
    """USDC as integer micro-USDC, rounded down."""
    return int((Decimal(usdc) * MIKRO).to_integral_value(rounding=ROUND_DOWN))


def usdc(m: int) -> str:
    """Integer micro-USDC as a fixed six-decimal string."""
    return f"{Decimal(m) / MIKRO:.6f}"


WOCHENBUDGET_MIKRO = mikro(WOCHENBUDGET_USDC)
DECKEL_JE_AKTIVIERUNG_MIKRO = mikro(DECKEL_JE_AKTIVIERUNG_USDC)

# --- Refusal classes --------------------------------------------------------
# A fixed class per reason, as in rounds 4 and 5. `text` is what result.json
# says; `im_text` are the patterns by which the pre-check (H7) finds the
# condition in the public text. A class whose condition the text does not
# state is a refusal the agent could not foresee.
KLASSEN = {
    "empfehlung_zaehlt_nicht": {
        "text": "The referral does not count: self-referral, mutual pair, "
                "unknown or revoked DID",
        "im_text": [r"self-referral", r"mutual"],
    },
    "keine_aktivierung": {
        "text": "No authenticated call to an endpoint no task text prescribes",
        "im_text": [r"authenticated call",
                    (r"no task text prescribes|not prescribed|"
                     r"prescribed by (a|any) task")],
    },
    "aktivierung_zu_spaet": {
        "text": f"First independent call more than {FENSTER_TAGE} days after "
                "registration",
        "im_text": [rf"\b{FENSTER_TAGE} days\b", r"registration"],
    },
    "betreiber_ausgeschlossen": {
        "text": "An operator_did is on the exclusion list",
        "im_text": [r"ausgeschlossene-betreiber\.json|exclusion list"],
    },
    "merkmal_fehlt": {
        "text": "Address, key or operator_did missing on one side",
        "im_text": [r"operator_did", r"wallet|address"],
    },
    "nicht_unabhaengig": {
        "text": "Referrer and referred share an address, a key or an "
                "operator_did",
        "im_text": [r"different (address|wallet)|share an? (address|wallet)",
                    r"key", r"operator_did"],
    },
    "deckel_betreiber": {
        "text": f"More than {BEZAHLT_JE_BETREIBER} paid activations for one "
                "operator_did this week",
        "im_text": [r"(three|3) paid activations", r"operator_did",
                    r"\bweek\b"],
    },
}

# --- What a task text prescribed -------------------------------------------

_SCRIPTED = re.compile(r"scripted\s*\(\s*pats\s*\)\s*AS\s*\(\s*VALUES\s*\(\s*"
                       r"ARRAY\s*\[(.*?)\]", re.S | re.I)


def vorgeschrieben(pfad=None) -> tuple:
    """The LIKE patterns of the prescribed endpoints, from the SQL file.

    Read from app/sql/registry_export.sql and not copied: a second list here
    would be a second derivation of "activated", and the two drift silently.
    A file without the list stops the run - an empty list would make every
    call an activation.
    """
    p = pathlib.Path(pfad or WURZEL / "app" / "sql" / "registry_export.sql")
    treffer = _SCRIPTED.search(p.read_text(encoding="utf-8"))
    if not treffer:
        raise ValueError(f"{p}: no scripted(pats) list found")
    muster = tuple(re.findall(r"'([^']*)'", treffer.group(1)))
    if not muster:
        raise ValueError(f"{p}: scripted(pats) list is empty")
    return muster


def _like(muster: str) -> re.Pattern:
    """A SQL LIKE pattern as an anchored regex (% any run, _ one char)."""
    teile = []
    for z in muster:
        teile.append(".*" if z == "%" else "." if z == "_" else re.escape(z))
    return re.compile("^" + "".join(teile) + "$", re.S)


def ist_vorgeschrieben(endpunkt: str, muster) -> bool:
    return any(_like(m).match(endpunkt or "") for m in muster)


# --- Time -------------------------------------------------------------------

def _utc(wert) -> datetime | None:
    if wert is None:
        return None
    if isinstance(wert, datetime):
        t = wert
    else:
        try:
            t = datetime.fromisoformat(str(wert).replace("Z", "+00:00"))
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def wochenfenster(kw: str) -> tuple[datetime, datetime]:
    """'2026-W44' -> [Monday 00:00 UTC, next Monday 00:00 UTC)."""
    m = re.fullmatch(r"(\d{4})-W(\d{2})", kw or "")
    if not m:
        raise ValueError(f"calendar week must look like 2026-W44, not {kw!r}")
    von = datetime.combine(date.fromisocalendar(int(m[1]), int(m[2]), 1),
                           time(0), tzinfo=timezone.utc)
    return von, von + timedelta(days=7)


def aktivierung(aufrufe, registriert_am, muster) -> datetime | None:
    """The first authenticated call to an endpoint no task text prescribed.

    `aufrufe` are the referred agent's request_log rows that carry its
    agent_did - the column is set only for a call that authenticated as
    that DID. Each is {"ts": ..., "endpoint": ...}. None if there is none.
    """
    reg = _utc(registriert_am)
    zeiten = sorted(
        t for t in (_utc(a.get("ts")) for a in aufrufe or []
                    if not ist_vorgeschrieben(a.get("endpoint") or "", muster))
        if t is not None and (reg is None or t >= reg))
    return zeiten[0] if zeiten else None


# --- The week ---------------------------------------------------------------

def _wert(seite: dict, merkmal: str):
    w = (seite or {}).get(merkmal)
    return str(w).strip().lower() if w not in (None, "") else None


def pruefe_fall(fall: dict, muster, ausgeschlossen: set,
                von: datetime, bis: datetime) -> dict:
    """One referral case against the rule, without the weekly cap.

    Returns {"stand": "qualifiziert"|"abgewiesen"|"andere_woche", ...}. The
    first failing class wins, in the order an agent could act on it.
    """
    emp = fall.get("empfohlen") or {}
    geb = fall.get("empfehlend") or {}
    raus = {"did": emp.get("did"), "referrer_did": geb.get("did"),
            "aktiviert_am": None, "klasse": None, "netz_gleich": False}

    def nein(klasse):
        raus.update(stand="abgewiesen", klasse=klasse,
                    text=KLASSEN[klasse]["text"])
        return raus

    reg = _utc(emp.get("registriert_am"))
    akt = aktivierung(fall.get("aufrufe"), reg, muster)
    raus["aktiviert_am"] = akt.isoformat() if akt else None
    # A case belongs to the week of its activation. Without one it belongs
    # to the week in which its activation window closed, so it is refused
    # exactly once and not in every week after the registration.
    stichtag = akt or ((reg + timedelta(days=FENSTER_TAGE)) if reg else None)
    if stichtag is None or not (von <= stichtag < bis):
        raus["stand"] = "andere_woche"
        return raus

    if not pair_counts(fall.get("fakt") or {}):
        return nein("empfehlung_zaehlt_nicht")
    if akt is None:
        return nein("keine_aktivierung")
    if reg is None or akt > reg + timedelta(days=FENSTER_TAGE):
        return nein("aktivierung_zu_spaet")
    if {_wert(emp, "betreiber"), _wert(geb, "betreiber")} & ausgeschlossen:
        return nein("betreiber_ausgeschlossen")
    if any(_wert(s, m) is None for s in (emp, geb) for m in MERKMALE):
        return nein("merkmal_fehlt")
    if any(_wert(emp, m) == _wert(geb, m) for m in MERKMALE):
        return nein("nicht_unabhaengig")
    netz = _wert(emp, "netz")
    raus["netz_gleich"] = bool(netz and netz == _wert(geb, "netz"))
    raus["stand"] = "qualifiziert"
    return raus


def abrechnung(faelle, *, kw: str, vortrag_mikro: int = 0,
               budget_mikro: int = WOCHENBUDGET_MIKRO,
               ausgeschlossen=None, eigene_adressen=None,
               muster=None) -> dict:
    """The payout rows of one week. Pays nothing.

    `faelle` is one dict per referred agent with a row in agent_referrals:
    {"empfohlen": {did, registriert_am, adresse, schluessel, betreiber, netz},
     "empfehlend": {did, adresse, schluessel, betreiber, netz},
     "fakt": {self, mutual, unknown, revoked} (as app/referral.py reads it),
     "aufrufe": [{"ts", "endpoint"}, ...]}.

    `ausgeschlossen` defaults to config/ausgeschlossene-betreiber.json, and an
    unreadable list raises instead of paying on an empty one.
    """
    von, bis = wochenfenster(kw)
    muster = tuple(muster) if muster is not None else vorgeschrieben()
    if ausgeschlossen is None:
        ausgeschlossen = G.ausgeschlossene()
    ausgeschlossen = {str(x).strip().lower() for x in ausgeschlossen}
    eigene = {str(a).lower() for a in (OWN_ADDRESSES if eigene_adressen is None
                                       else eigene_adressen)}
    if vortrag_mikro < 0 or budget_mikro < 0:
        raise ValueError("budget and carry-over cannot be negative")

    gepruefte = [(f, pruefe_fall(f, muster, ausgeschlossen, von, bis))
                 for f in faelle or []]
    woche = [(f, p) for f, p in gepruefte if p["stand"] != "andere_woche"]
    abgewiesen = [p for _f, p in woche if p["stand"] == "abgewiesen"]

    # The weekly cap per operator_did, both sides, by activation time.
    zaehler: dict = defaultdict(int)
    bezahlt = []
    for f, p in sorted(((f, p) for f, p in woche
                        if p["stand"] == "qualifiziert"),
                       key=lambda fp: (fp[1]["aktiviert_am"], fp[1]["did"])):
        ops = {_wert(f["empfohlen"], "betreiber"),
               _wert(f["empfehlend"], "betreiber")}
        if any(zaehler[o] >= BEZAHLT_JE_BETREIBER for o in ops):
            p.update(stand="abgewiesen", klasse="deckel_betreiber",
                     text=KLASSEN["deckel_betreiber"]["text"])
            abgewiesen.append(p)
            continue
        for o in ops:
            zaehler[o] += 1
        bezahlt.append((f, p))

    verfuegbar = budget_mikro + vortrag_mikro
    je = 0
    if bezahlt:
        je = min(DECKEL_JE_AKTIVIERUNG_MIKRO, verfuegbar // len(bezahlt))
        je -= je % 2          # two exact halves
    haelfte = je // 2

    zeilen_je: dict = {}
    eigen_mikro = 0
    for f, p in bezahlt:
        p["betrag_mikro"] = je
        for rolle, seite in (("empfohlen", f["empfohlen"]),
                             ("empfehlend", f["empfehlend"])):
            adr = _wert(seite, "adresse")
            if adr in eigene:
                eigen_mikro += haelfte
                continue
            z = zeilen_je.setdefault(adr, {"adresse": adr, "dids": [],
                                           "rollen": [], "aktivierungen": [],
                                           "betrag_mikro": 0})
            if seite.get("did") not in z["dids"]:
                z["dids"].append(seite.get("did"))
            if rolle not in z["rollen"]:
                z["rollen"].append(rolle)
            z["aktivierungen"].append(p["did"])
            z["betrag_mikro"] += haelfte
    zeilen = sorted(zeilen_je.values(), key=lambda z: z["adresse"])
    for z in zeilen:
        z["betrag"] = usdc(z["betrag_mikro"])

    summe = sum(z["betrag_mikro"] for z in zeilen)
    vortrag_aus = verfuegbar - summe
    gezaehlt = len(bezahlt) + len(abgewiesen)
    return {
        "kw": kw,
        "von": von.isoformat(),
        "bis": bis.isoformat(),
        "regel": regel(),
        "budget_mikro": budget_mikro,
        "vortrag_ein_mikro": vortrag_mikro,
        "verfuegbar_mikro": verfuegbar,
        "je_aktivierung_mikro": je,
        "bezahlt": [p for _f, p in bezahlt],
        "abgewiesen": sorted(abgewiesen, key=lambda p: (p["klasse"],
                                                        p["did"] or "")),
        "vermerk_netz_gleich": sorted(p["did"] for _f, p in bezahlt
                                      if p["netz_gleich"]),
        "zeilen": zeilen,
        "summe_mikro": summe,
        "eigen_nicht_ausgezahlt_mikro": eigen_mikro,
        "vortrag_aus_mikro": vortrag_aus,
        "abgleich": f"{gezaehlt} von {gezaehlt} Faellen der Woche zugeordnet "
                    f"({len(bezahlt)} bezahlt, {len(abgewiesen)} abgewiesen); "
                    f"{len(gepruefte) - len(woche)} gehoeren in eine andere "
                    f"Woche",
    }


def regel() -> dict:
    """The constants as they go into result.json."""
    return {
        "wochenbudget": usdc(WOCHENBUDGET_MIKRO),
        "deckel_je_aktivierung": usdc(DECKEL_JE_AKTIVIERUNG_MIKRO),
        "anteil": "half to the referred agent, half to the referrer",
        "fenster_tage": FENSTER_TAGE,
        "bezahlt_je_betreiber_und_woche": BEZAHLT_JE_BETREIBER,
        "abrechnung": f"{ABRECHNUNG_WOCHENTAG} {ABRECHNUNG_UTC} UTC",
        "ausschlussliste": "config/ausgeschlossene-betreiber.json",
        "vortrag": "what is not paid carries over to the next week",
        "klassen": {k: v["text"] for k, v in KLASSEN.items()},
    }


def messwerte() -> list:
    """The figures the public text may name, each with its source line.

    For the pre-check H2: a number in the text that matches none of these is
    not measured, and an unmeasured number is not green.
    """
    quelle = pathlib.Path(__file__).read_text(encoding="utf-8").splitlines()

    def zeile(name):
        for i, z in enumerate(quelle, 1):
            if z.startswith(f"{name} ="):
                return f"scripts/dauerrunde.py:{i}"
        return "scripts/dauerrunde.py"

    return [
        {"wert": WOCHENBUDGET_USDC, "einheit": ("usdc",),
         "quelle": zeile("WOCHENBUDGET_USDC")},
        {"wert": DECKEL_JE_AKTIVIERUNG_USDC, "einheit": ("usdc",),
         "quelle": zeile("DECKEL_JE_AKTIVIERUNG_USDC")},
        {"wert": DECKEL_JE_AKTIVIERUNG_USDC / 2, "einheit": ("usdc",),
         "quelle": zeile("DECKEL_JE_AKTIVIERUNG_USDC") + " (half)"},
        {"wert": Decimal(FENSTER_TAGE), "einheit": ("days", "day"),
         "quelle": zeile("FENSTER_TAGE")},
        {"wert": Decimal(BEZAHLT_JE_BETREIBER),
         "einheit": ("paid", "activations", "activation"),
         "quelle": zeile("BEZAHLT_JE_BETREIBER")},
    ]


def felder() -> dict:
    """The deadline of a week as a field, for the pre-check H5."""
    return {"stichtag": f"{ABRECHNUNG_WOCHENTAG} {ABRECHNUNG_UTC} UTC",
            "fenster_tage": FENSTER_TAGE}
