#!/usr/bin/env python3
"""The weekly settlement of the standing round (ops G4-3). Pays nothing.

Monday 06:00 UTC, for the ISO week that ended before it:

1. gather the referral cases of the week (app/sql/dauerrunde_faelle.sql),
2. split the budget with `scripts/dauerrunde.abrechnung` - the rule lives
   there and nowhere else,
3. publish /rounds/dauer/<KW>/result.json through a commit in
   MoltyCel/moltrust-web and its deploy, never into the web root,
4. leave the payout proposal for Issue #1 in ~/dauerrunde/<KW>/.

Paying is G4-5 and stays with Lars. This program holds no key that moves
money and calls nothing that does.

Why the proposal does not go to Issue #1 from here: the server's GitHub token
cannot read MoltyCel/moltrust-security (404, measured 27.10.2026; see
app/sql/wm1_budget.sql). The Mac posts it - moltrust-security
ops/dauer-vorschlag.sh, launchd ch.moltrust.dauer-vorschlag - from the files
this run leaves in ~/dauerrunde/<KW>/.

Order and failure: the result is published first, the proposal written after.
A failed publish leaves no proposal and sends ALERTS, so nothing can be
proposed that the public result does not show. Every write is to a temporary
name and renamed.

    python3 scripts/dauerrunde_woche.py                 the week before now
    python3 scripts/dauerrunde_woche.py --kw 2026-W44   a given week
    python3 scripts/dauerrunde_woche.py --trocken ...   print, publish nothing
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import dauerrunde as D  # noqa: E402

WURZEL = pathlib.Path(__file__).resolve().parent.parent
SQL = WURZEL / "app" / "sql" / "dauerrunde_faelle.sql"

#: The first settled week. G4-4 published the rule on 27.10.2026, in W44; the
#: week starts with the budget and no carry-over.
START_KW = "2026-W44"
#: The paying address (G4 "direkt", Lars 19.10.2026), shortened: a public file
#: carries no full wallet address.
ZAHLER_KURZ = "0xa175…9eb9"
WEB_REPO = "MoltyCel/moltrust-web"
ERGEBNIS_URL = "https://moltrust.ch/rounds/dauer/{kw}/result.json"
AUSGANG = pathlib.Path(os.path.expanduser("~/dauerrunde"))
#: {timestamp, status, detail}; read by scripts/selftest.py (config/expectations.yaml).
HEARTBEAT = WURZEL / "data" / "dauerrunde_heartbeat.json"
AUTOR = {"name": "Lars Kroehl", "email": "lars@moltrust.ch"}
#: Fields a rerun of the same week may change without making a new version.
FLUECHTIG = ("erzeugt", "version", "ersetzt")


class NichtVeroeffentlicht(RuntimeError):
    """The result did not reach moltrust-web; nothing may be proposed."""


# --- Weeks ------------------------------------------------------------------

def abrechnungswoche(jetzt: datetime) -> str:
    """The ISO week that ended before `jetzt` ('2026-W44' on 2026-11-02)."""
    j = jetzt.astimezone(timezone.utc)
    sonntag = j.date() - timedelta(days=j.isoweekday())
    jahr, woche, _ = sonntag.isocalendar()
    return f"{jahr}-W{woche:02d}"


def vorwoche(kw: str) -> str:
    von, _ = D.wochenfenster(kw)
    jahr, woche, _ = (von.date() - timedelta(days=1)).isocalendar()
    return f"{jahr}-W{woche:02d}"


def stichtag(kw: str, jetzt: datetime) -> datetime:
    """The cut of the cohort block: Monday 06:00 after the week, or now if
    that is earlier (a dry run during the week)."""
    _, bis = D.wochenfenster(kw)
    return min(bis + timedelta(hours=6), jetzt.astimezone(timezone.utc))


# --- Facts ------------------------------------------------------------------

def netz24(ip) -> str | None:
    """The /24 of an IPv4 address, the /48 of an IPv6 one, None without."""
    s = str(ip or "").strip()
    if not s:
        return None
    if "." in s and ":" not in s:
        teile = s.split(".")
        return ".".join(teile[:3]) + ".0/24" if len(teile) == 4 else None
    if ":" in s:
        return ":".join(s.split(":")[:3]) + "::/48"
    return None


def faelle_aus_zeilen(zeilen: list[dict]) -> list[dict]:
    """The SQL rows as `abrechnung` takes them: the IP cut to its network."""
    raus = []
    for z in zeilen:
        f = json.loads(json.dumps(z))
        for seite in ("empfohlen", "empfehlend"):
            f[seite]["netz"] = netz24((f.get(seite) or {}).get("netz"))
        raus.append(f)
    return raus


def faelle(kw: str) -> list[dict]:
    von, bis = D.wochenfenster(kw)
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-t", "-P", "pager=off", "-v", "ON_ERROR_STOP=1",
         "-v", f"VON={von.isoformat()}", "-v", f"BIS={bis.isoformat()}",
         "-f", str(SQL)],
        capture_output=True, text=True, timeout=300)
    if out.returncode:
        raise RuntimeError(f"psql: {out.stderr[:400]}")
    return faelle_aus_zeilen(
        [json.loads(z) for z in out.stdout.splitlines() if z.strip()])


# --- The document -----------------------------------------------------------

def kurz(adresse) -> str | None:
    """0x26d6bd4C…67aa -> '0x26d6…67aa'. Anything else unchanged but cut."""
    a = str(adresse or "")
    if not a:
        return None
    return f"{a[:6]}…{a[-4:]}" if len(a) > 12 else a


def _ketten(faelle_liste) -> dict:
    k = {}
    for f in faelle_liste or []:
        for seite in ("empfohlen", "empfehlend"):
            s = f.get(seite) or {}
            if s.get("adresse"):
                k[str(s["adresse"]).strip().lower()] = s.get("kette") or "base"
    return k


def ergebnis_satz(e: dict) -> str:
    vortrag = D.usdc(e["vortrag_aus_mikro"])
    if not e["zeilen"]:
        return f"nichts zu zahlen, Vortrag {vortrag} USDC"
    return (f"{len(e['zeilen'])} Zeilen, zusammen {D.usdc(e['summe_mikro'])} "
            f"USDC vorgeschlagen, Vortrag {vortrag} USDC")


def dokument(e: dict, faelle_liste, *, kohorten: dict, erzeugt: str,
             vortrag_quelle: str) -> dict:
    """The public result of one week, without version fields.

    Wallet addresses appear shortened only. The full ones stay on the server
    (`zeilen_privat`), for the payment that G4-5 makes after Lars' release.
    """
    ketten = _ketten(faelle_liste)
    zeilen = []
    for z in e["zeilen"]:
        kette = ketten.get(z["adresse"], "base")
        zeilen.append({
            "adresse": kurz(z["adresse"]),
            "dids": z["dids"],
            "rollen": z["rollen"],
            "aktivierungen": len(z["aktivierungen"]),
            "betrag": z["betrag"],
            "kette": kette,
            "auf_base_zahlbar": kette != "solana",
        })
    return {
        "runde": "dauer",
        "kw": e["kw"],
        "von": e["von"],
        "bis": e["bis"],
        "stand": "vorgeschlagen" if zeilen else "nichts_zu_zahlen",
        "erzeugt": erzeugt,
        "quelle": "scripts/dauerrunde_woche.py",
        "seite": "https://moltrust.ch/rounds/dauer/",
        "weg": "direkt",
        "zahler": ZAHLER_KURZ,
        "netz": "Base, USDC",
        "regel": e["regel"],
        "budget": D.usdc(e["budget_mikro"]),
        "vortrag_ein": D.usdc(e["vortrag_ein_mikro"]),
        "vortrag_quelle": vortrag_quelle,
        "verfuegbar": D.usdc(e["verfuegbar_mikro"]),
        "je_aktivierung": D.usdc(e["je_aktivierung_mikro"]),
        "bezahlt": [{"did": p["did"], "referrer_did": p["referrer_did"],
                     "aktiviert_am": p["aktiviert_am"],
                     "betrag": D.usdc(p.get("betrag_mikro", 0)),
                     "netz_gleich": p["netz_gleich"]} for p in e["bezahlt"]],
        "abgewiesen": [{"did": p["did"], "referrer_did": p["referrer_did"],
                        "aktiviert_am": p["aktiviert_am"],
                        "klasse": p["klasse"], "text": p.get("text")}
                       for p in e["abgewiesen"]],
        "vermerk_netz_gleich": e["vermerk_netz_gleich"],
        "zeilen": zeilen,
        "summe": D.usdc(e["summe_mikro"]),
        "eigen_nicht_ausgezahlt": D.usdc(e["eigen_nicht_ausgezahlt_mikro"]),
        "vortrag_aus": D.usdc(e["vortrag_aus_mikro"]),
        "abgleich": e["abgleich"],
        "ergebnis": ergebnis_satz(e),
        "auszahlung": {
            "stand": "nicht gezahlt",
            "hinweis": "Each payment is released by hand after this result "
                       "is published; this file is then replaced by a new "
                       "version with the transaction hash of each payment.",
        },
        "kohorten": kohorten,
    }


def zeilen_privat(e: dict, faelle_liste) -> list[dict]:
    """The rows with full addresses, for the server only (mode 600)."""
    ketten = _ketten(faelle_liste)
    return [{"adresse": z["adresse"], "kette": ketten.get(z["adresse"], "base"),
             "betrag": z["betrag"], "betrag_mikro": z["betrag_mikro"],
             "dids": z["dids"], "rollen": z["rollen"],
             "aktivierungen": z["aktivierungen"]} for z in e["zeilen"]]


# --- Versions ---------------------------------------------------------------

def _kern(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in FLUECHTIG}


def plane(alt: dict | None, neu: dict) -> dict | None:
    """Which files a publish writes, or None when the week is unchanged.

    First run: result.json, version 1. A rerun with the same content writes
    nothing. A changed week moves the old file to result-<v>.json and writes
    version v+1 that names it in `ersetzt`, as rounds/r5 does.
    """
    if alt is None:
        doc = dict(neu, version=1, ersetzt=None)
        return {"version": 1, "dateien": {"result.json": doc}}
    if alt.get("stand") == "ausgezahlt":
        # G4-5: a paid week carries its tx hashes. Recomputing it would
        # publish "nicht gezahlt" over a payment that happened.
        raise RuntimeError(f"{alt.get('kw')} ist ausgezahlt (Version "
                           f"{alt.get('version')}), wird nicht neu berechnet")
    if _kern(alt) == _kern(neu):
        return None
    v = int(alt.get("version") or 1)
    doc = dict(neu, version=v + 1, ersetzt=f"result-{v}.json")
    return {"version": v + 1,
            "dateien": {f"result-{v}.json": alt, "result.json": doc}}


def json_text(doc: dict) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


# --- GitHub (moltrust-web) --------------------------------------------------

def _api(methode: str, pfad: str, body=None, token: str = ""):
    req = urllib.request.Request(
        "https://api.github.com" + pfad, method=methode,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310 - fixed https host
            roh = r.read()
            return r.status, (json.loads(roh) if roh else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300].decode("utf-8", "replace")


def lies_web(kw: str, token: str) -> dict | None:
    """rounds/dauer/<KW>/result.json on moltrust-web main, None if absent."""
    st, b = _api("GET", f"/repos/{WEB_REPO}/contents/rounds/dauer/{kw}/"
                 f"result.json?ref=main", token=token)
    if st == 404:
        return None
    if st != 200:
        raise RuntimeError(f"GitHub contents {kw}: HTTP {st} {b!r:.200}")
    return json.loads(base64.b64decode(b["content"]).decode("utf-8"))


def vortrag(kw: str, token: str, lesen=lies_web) -> tuple[int, str]:
    """Carry-over into `kw` and where it came from.

    The first week starts at zero. Every later week reads `vortrag_aus` of the
    week before from moltrust-web; a missing week stops the run - guessing a
    carry-over would be a number nothing measured.
    """
    if kw <= START_KW:
        return 0, f"erste Woche ({START_KW}), kein Vortrag"
    vor = vorwoche(kw)
    alt = lesen(vor, token)
    if alt is None:
        raise RuntimeError(f"Vorwoche {vor} hat kein result.json; "
                           f"Vortrag nicht bestimmbar")
    return (D.mikro(Decimal(alt["vortrag_aus"])),
            f"rounds/dauer/{vor}/result.json Version {alt.get('version')}")


def veroeffentliche(kw: str, plan: dict, token: str) -> dict:
    """One commit on a branch, a PR, squash merge. The merge to main starts
    moltrust-web's deploy.yml, which ships rounds/dauer/<KW>/*.json."""
    def ok(st, b, was):
        if st not in (200, 201):
            raise NichtVeroeffentlicht(f"{was}: HTTP {st} {b!r:.200}")
        return b

    v = plan["version"]
    ast = f"rounds/dauer-{kw}-v{v}"
    ref = ok(*_api("GET", f"/repos/{WEB_REPO}/git/ref/heads/main",
                   token=token), "ref main")
    basis = ref["object"]["sha"]
    commit = ok(*_api("GET", f"/repos/{WEB_REPO}/git/commits/{basis}",
                      token=token), "commit main")
    baum = ok(*_api("POST", f"/repos/{WEB_REPO}/git/trees", {
        "base_tree": commit["tree"]["sha"],
        "tree": [{"path": f"rounds/dauer/{kw}/{name}", "mode": "100644",
                  "type": "blob", "content": json_text(doc)}
                 for name, doc in plan["dateien"].items()]},
        token=token), "tree")
    titel = f"rounds/dauer: {kw} result.json version {v} (ops G4-3)"
    jetzt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    neu = ok(*_api("POST", f"/repos/{WEB_REPO}/git/commits", {
        "message": titel, "tree": baum["sha"], "parents": [basis],
        "author": dict(AUTOR, date=jetzt), "committer": dict(AUTOR, date=jetzt)},
        token=token), "commit")
    ok(*_api("POST", f"/repos/{WEB_REPO}/git/refs",
             {"ref": f"refs/heads/{ast}", "sha": neu["sha"]}, token=token),
       "branch")
    pr = ok(*_api("POST", f"/repos/{WEB_REPO}/pulls", {
        "title": titel, "head": ast, "base": "main",
        "body": ("Weekly settlement of the standing round, written by "
                 "scripts/dauerrunde_woche.py in MoltyCel/moltrust-api "
                 "(ops G4-3). Nothing is paid by this change.\n\n"
                 f"Result: {ERGEBNIS_URL.format(kw=kw)}")},
        token=token), "pull request")
    m = ok(*_api("PUT", f"/repos/{WEB_REPO}/pulls/{pr['number']}/merge",
                 {"merge_method": "squash", "commit_title":
                  f"{titel} (#{pr['number']})"}, token=token), "merge")
    _api("DELETE", f"/repos/{WEB_REPO}/git/refs/heads/{ast}", token=token)
    return {"pr": pr["number"], "merge": m.get("sha"), "version": v}


# --- The proposal (Issue #1, posted by the Mac) -----------------------------

def vorschlag_text(doc: dict, *, version: int, budget_wm1: dict) -> str:
    """Payout proposal for Issue #1. Addresses shortened, German, terse."""
    url = ERGEBNIS_URL.format(kw=doc["kw"])
    zeilen = ["| Adresse | Rolle | Aktivierungen | USDC | Kette |",
              "|---|---|---|---|---|"]
    for z in doc["zeilen"]:
        kette = z["kette"] if z["auf_base_zahlbar"] else \
            f"{z['kette']}, auf Base nicht zahlbar"
        zeilen.append(f"| `{z['adresse']}` | {', '.join(z['rollen'])} | "
                      f"{z['aktivierungen']} | {z['betrag']} | {kette} |")
    try:
        rest = Decimal(budget_wm1["budget"]) - Decimal(budget_wm1["ausgegeben"])
        bestand = (f"Freies WM-1-Budget laut pool_spend: {rest:f} USDC davor, "
                   f"{rest - Decimal(doc['summe']):f} USDC danach.")
    except Exception:  # noqa: BLE001 - the line says it could not read
        bestand = ("Freies WM-1-Budget nicht lesbar: "
                   f"{budget_wm1.get('fehler', 'Feld fehlt')}.")
    return "\n".join([
        f"**Dauerrunde {doc['kw']}: Auszahlungsvorschlag, bitte freigeben.**",
        "",
        f"Ergebnis: {url} (Version {version}). Abgleich: {doc['abgleich']}.",
        "",
        *zeilen,
        "",
        f"Summe {doc['summe']} USDC an {len(doc['zeilen'])} Adressen, "
        f"je Aktivierung {doc['je_aktivierung']} USDC, Vortrag in die "
        f"nächste Woche {doc['vortrag_aus']} USDC. Zahler {ZAHLER_KURZ}, "
        f"USDC auf Base.",
        bestand,
        f"Volle Adressen am Server in ~/dauerrunde/{doc['kw']}/"
        f"zeilen-v{version}.json (Modus 600).",
        "",
        "Überwiesen ist nichts. Gezahlt wird erst nach Freigabe durch Lars "
        "(G4-5); danach ersetzt eine neue Version der result.json diese, mit "
        "dem tx-Hash je Zahlung.",
        "",
    ])


def _schreibe(pfad: pathlib.Path, text: str, modus: int = 0o644) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_name(pfad.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, modus)
    os.replace(tmp, pfad)


def lege_ab(kw: str, version: int, doc: dict, privat: list, vorschlag: str | None,
            ausgang: pathlib.Path = AUSGANG) -> pathlib.Path:
    """~/dauerrunde/<KW>/: result-v<N>.json, zeilen-v<N>.json (600) and, with
    rows to pay, vorschlag.md - the file the Mac posts to Issue #1."""
    d = ausgang / kw
    _schreibe(d / f"result-v{version}.json", json_text(doc))
    _schreibe(d / f"zeilen-v{version}.json",
              json.dumps(privat, indent=1) + "\n", 0o600)
    if vorschlag:
        _schreibe(d / "vorschlag.md", vorschlag)
    return d


# --- Run --------------------------------------------------------------------

def herzschlag(status: str, detail: str, pfad: pathlib.Path = HEARTBEAT) -> None:
    """ok, unveraendert or fehler, with the sentence the log prints."""
    try:
        _schreibe(pfad, json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status, "detail": detail[:500]}) + "\n")
    except OSError as exc:
        print(f"heartbeat nicht geschrieben: {exc}")


def rechne(kw: str, *, faelle_liste, vortrag_mikro: int, vortrag_quelle: str,
           kohorten: dict, erzeugt: str) -> tuple[dict, dict]:
    e = D.abrechnung(faelle_liste, kw=kw, vortrag_mikro=vortrag_mikro)
    return e, dokument(e, faelle_liste, kohorten=kohorten, erzeugt=erzeugt,
                       vortrag_quelle=vortrag_quelle)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dauerrunde_woche")
    ap.add_argument("--kw", help="ISO week like 2026-W44; default the week "
                                 "before now")
    ap.add_argument("--trocken", action="store_true",
                    help="compute and print; publish, store and send nothing")
    ap.add_argument("--vortrag", default=None,
                    help="carry-over in USDC instead of reading the week "
                         "before (only with --trocken)")
    a = ap.parse_args(argv)

    jetzt = datetime.now(timezone.utc)
    kw = a.kw or abrechnungswoche(jetzt)
    D.wochenfenster(kw)  # rejects a malformed week
    if a.vortrag is not None and not a.trocken:
        print("--vortrag nur mit --trocken: ein Vortrag von Hand ist keine "
              "Messung")
        return 2

    from app import kohorten as K  # needs psql; imported at run time
    from app import gh, notify

    token = "" if a.trocken and a.vortrag is not None else gh.require(
        "dauerrunde_woche")
    try:
        if a.vortrag is not None:
            v_mikro, v_quelle = D.mikro(Decimal(a.vortrag)), "von Hand (--trocken)"
        else:
            v_mikro, v_quelle = vortrag(kw, token)
        liste = faelle(kw)
        block = K.block(stichtag(kw, jetzt).replace(second=0, microsecond=0)
                        .isoformat())
        e, doc = rechne(kw, faelle_liste=liste, vortrag_mikro=v_mikro,
                        vortrag_quelle=v_quelle, kohorten=block,
                        erzeugt=jetzt.replace(microsecond=0).isoformat())
    except Exception as exc:  # noqa: BLE001 - reported, then exit 2
        msg = f"MolTrust - Dauerrunde {kw}: Abrechnung gescheitert: {type(exc).__name__}: {str(exc)[:300]}"
        print(msg)
        if not a.trocken:
            herzschlag("fehler", msg)
            notify.send_telegram(msg, channel=notify.ALERTS)
        return 2

    print(f"Dauerrunde {kw}: {doc['abgleich']}")
    print(f"  {doc['ergebnis']}")
    if a.trocken:
        print(json_text(dict(doc, kohorten="(gekuerzt)")))
        return 0

    try:
        plan = plane(lies_web(kw, token), doc)
        if plan is None:
            satz = (f"Dauerrunde {kw} unveraendert gegenueber "
                    f"{ERGEBNIS_URL.format(kw=kw)}, nichts veroeffentlicht")
            print(f"  {satz}")
            herzschlag("unveraendert", satz)
            return 0
        pub = veroeffentliche(kw, plan, token)
    except Exception as exc:  # noqa: BLE001
        msg = (f"MolTrust - Dauerrunde {kw}: result.json NICHT veroeffentlicht, "
               f"kein Vorschlag: {type(exc).__name__}: {str(exc)[:300]}")
        print(msg)
        herzschlag("fehler", msg)
        notify.send_telegram(msg, channel=notify.ALERTS)
        return 2

    v = pub["version"]
    vorschlag = (vorschlag_text(plan["dateien"]["result.json"], version=v,
                                budget_wm1=block.get("budget_wm1") or {})
                 if doc["zeilen"] else None)
    ablage = lege_ab(kw, v, plan["dateien"]["result.json"],
                     zeilen_privat(e, liste), vorschlag)
    zeile = (f"MolTrust - Dauerrunde {kw}: {doc['ergebnis']}. "
             f"{doc['abgleich']}. Ergebnis Version {v}: "
             f"{ERGEBNIS_URL.format(kw=kw)} (web#{pub['pr']})")
    if vorschlag:
        zeile += f"; Vorschlag fuer Issue #1 liegt in {ablage}/vorschlag.md"
    print(zeile)
    herzschlag("ok", zeile)
    notify.send_telegram(zeile, channel=notify.STATS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
