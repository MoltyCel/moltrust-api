#!/usr/bin/env python3
"""The weekly payout of the standing round (ops G4-5). Pays only after "ok".

Lars, 27.10.2026: one Telegram message a week with the sum and the
recipients; the payout runs only after he answers "ok". This program is the
only path from the weekly settlement (scripts/dauerrunde_woche.py, G4-3) to a
transfer, and every step of it checks the release again.

One run, every ten minutes from cron:

1. Ask. For every week in ~/dauerrunde/<KW>/ whose newest result proposes
   rows payable on Base, send one message to the STATS chat (Lars' private
   chat with the bot) unless that version was already asked. The message
   names week, version, sum, recipients (shortened) and the deadline.
2. Read the answers. Telegram's webhook stores every message in
   telegram_inbox; this run claims only messages that reply to one of its
   own request messages (`claim(reply_to=...)`). A reply releases the payout
   only when all of these hold:
   - it is in the chat the request went to, that chat is private, and the
     sender is the person of that chat and no bot ("foreign" otherwise);
   - it replies to the request of the current version of that week;
   - its Telegram date lies between the request and its deadline;
   - its text is "ok" (case and a trailing . or ! aside).
   Anything else is answered with the reason and changes nothing.
3. Pay. A release is written once (`freigabe.json`, created exclusively) and
   bound to week, version and the sha256 of the full-address rows. Before
   the first transfer the run checks: the bound rows are still the rows on
   disk, the published result.json is still that version and proposes them,
   no payment of this week is running or done, no stop file, none of our own
   addresses, the WM-1 budget covers the sum. Then
   scripts/dauerrunde_zahle.cjs sends one transfer per row from 0xa175 and
   appends one proof line with the tx hash per row.
4. Book. Every tx goes into pool_spend in the same step (CLAUDE.md,
   Kontenabgleich), one line per tx into budget-zeilen.jsonl with the rest of
   the WM-1 budget, and the week's result.json is replaced by a new version
   that names each payment. The Mac copies auszahlung.jsonl and the budget
   lines into MoltyCel/moltrust-security (ops/dauer-buchung.sh), because the
   server's token cannot write there.

Exactly once: `zahlung.beginnt` is created exclusively before the payer
starts and never removed. A second "ok", a rerun, a crash: none of them can
start a second payment of the same week. A run that finds `beginnt` without
`fertig` stops and reports; a person looks at the proof file before anything
else happens.

Stop: a file ~/dauerrunde/STOP holds every payment (asking and reading go on).

    python3 scripts/dauerrunde_auszahlung.py              one run
    python3 scripts/dauerrunde_auszahlung.py --trocken    show state, send/claim/pay nothing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.own_addresses import OWN_ADDRESSES  # noqa: E402
from scripts import dauerrunde as D  # noqa: E402
from scripts import dauerrunde_woche as W  # noqa: E402

WURZEL = pathlib.Path(__file__).resolve().parent.parent
ZAHLER_SKRIPT = WURZEL / "scripts" / "dauerrunde_zahle.cjs"
HEARTBEAT = WURZEL / "data" / "dauerrunde_auszahlung_heartbeat.json"
KONSUMENT = "dauerrunde_freigabe"
#: How long a request can be released: one week, until the next settlement.
FRIST = timedelta(days=7)
#: A reply dated this much before its request is not an answer to it
#: (Telegram dates are whole seconds, our clock may differ slightly).
UHR_TOLERANZ = timedelta(seconds=120)
#: The paying address (G4 "direkt", Lars 19.10.2026), taken from the list of
#: our own addresses so it is written in one place.
ZAHLER = next(a for a in OWN_ADDRESSES if a.startswith("0xa175d51b"))
WM1_BUDGET = Decimal("20")
POOL = "taskmarket"
OK_TEXT = re.compile(r"^\s*ok\s*[.!]?\s*$", re.IGNORECASE)


class Gestoppt(RuntimeError):
    """A check before the first transfer failed; nothing was sent."""


# --- Small helpers -----------------------------------------------------------

def utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def lies_json(p: pathlib.Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def schreibe_json(p: pathlib.Path, doc, modus: int = 0o644) -> None:
    W._schreibe(p, json.dumps(doc, indent=1, ensure_ascii=False) + "\n", modus)


def exklusiv(p: pathlib.Path, doc) -> bool:
    """Create `p` only if it does not exist. False when it already did."""
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return True


def sha256_datei(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def neueste_version(d: pathlib.Path) -> int | None:
    vs = [int(m[1]) for f in d.glob("zeilen-v*.json")
          if (m := re.fullmatch(r"zeilen-v(\d+)\.json", f.name))]
    return max(vs) if vs else None


def zahlbar(zeilen: list[dict]) -> list[dict]:
    """The rows that can be paid on Base: an EVM address, not Solana."""
    return [z for z in zeilen
            if (z.get("kette") or "base") != "solana"
            and re.fullmatch(r"0x[0-9a-fA-F]{40}", str(z.get("adresse") or ""))]


# --- 1. Ask -------------------------------------------------------------------

def anfrage_text(kw: str, version: int, doc: dict, zeilen: list[dict],
                 frist: datetime) -> str:
    z_ok = zahlbar(zeilen)
    summe = sum(int(z["betrag_mikro"]) for z in z_ok)
    rest = [z for z in zeilen if z not in z_ok]
    teile = [f"Dauerrunde {kw}: Auszahlung bitte freigeben.",
             f"{D.usdc(summe)} USDC an {len(z_ok)} Adressen, USDC auf Base "
             f"aus {W.ZAHLER_KURZ}:"]
    for z in z_ok:
        teile.append(f"- {W.kurz(z['adresse'])}  {D.usdc(int(z['betrag_mikro']))}  "
                     f"({', '.join(z.get('rollen') or [])}, "
                     f"{len(z.get('aktivierungen') or [])} Aktivierungen)")
    if rest:
        teile.append("Nicht zahlbar auf Base, wird nicht ueberwiesen: "
                     + ", ".join(f"{W.kurz(z['adresse'])} {z['betrag']} "
                                 f"({z.get('kette')})" for z in rest))
    teile += [f"Ergebnis: {W.ERGEBNIS_URL.format(kw=kw)} (Version {version}). "
              f"{doc.get('abgleich', '')}",
              f"Freigabe: auf diese Nachricht mit ok antworten, bis "
              f"{frist.strftime('%d.%m.%Y %H:%M')} UTC. Ohne ok wird nichts gezahlt."]
    return "\n".join(teile)


def frage(kw: str, d: pathlib.Path, version: int, jetzt: datetime, *,
          senden, chat_id: str) -> dict | None:
    """Send the request for (kw, version) unless it was sent. The record."""
    anfrage = lies_json(d / "anfrage.json")
    if anfrage and anfrage.get("version") == version:
        return anfrage
    if anfrage and anfrage.get("status") in ("bezahlt", "zahlung_laeuft"):
        # A paid week is never asked again; dauerrunde_woche refuses to
        # replace it, so a newer version here is a fault to look at.
        raise Gestoppt(f"{kw}: Version {version} nach bezahlter Version "
                       f"{anfrage.get('version')}")
    zeilen_pfad = d / f"zeilen-v{version}.json"
    zeilen = json.loads(zeilen_pfad.read_text(encoding="utf-8"))
    if not zahlbar(zeilen):
        return None
    doc = lies_json(d / f"result-v{version}.json") or {}
    frist = jetzt + FRIST
    msg_id = senden(anfrage_text(kw, version, doc, zeilen, frist))
    if not msg_id:
        print(f"{kw}: Anfrage nicht gesendet, naechster Lauf versucht es neu")
        return anfrage
    frueher = list((anfrage or {}).get("fruehere") or [])
    if anfrage:
        frueher.append({k: anfrage.get(k) for k in
                        ("version", "message_id", "status", "gesendet")})
    neu = {"kw": kw, "version": version, "status": "offen",
           "zeilen_sha256": sha256_datei(zeilen_pfad),
           "summe_mikro": sum(int(z["betrag_mikro"]) for z in zahlbar(zeilen)),
           "adressen": len(zahlbar(zeilen)),
           "chat_id": str(chat_id), "message_id": int(msg_id),
           "gesendet": iso(jetzt), "gueltig_bis": iso(frist),
           "fruehere": frueher}
    schreibe_json(d / "anfrage.json", neu)
    print(f"{kw}: Anfrage Version {version} gesendet (Nachricht {msg_id})")
    return neu


# --- 2. Read the answers ------------------------------------------------------

def bewerte(update: dict, anfrage: dict) -> tuple[str, str]:
    """('ok', '') for a valid release of `anfrage`, else (urteil, grund)."""
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    von = msg.get("from") or {}
    if str(chat.get("id")) != str(anfrage.get("chat_id")):
        return "fremd", "anderer Chat"
    if chat.get("type") != "private" or von.get("is_bot") \
            or str(von.get("id")) != str(chat.get("id")):
        return "fremd", "nicht vom Inhaber des Chats"
    antwort_auf = (msg.get("reply_to_message") or {}).get("message_id")
    if antwort_auf != anfrage.get("message_id"):
        return "veraltet", "Antwort auf eine fruehere Anfrage dieser Woche"
    if anfrage.get("status") != "offen":
        return "erledigt", f"Anfrage steht auf {anfrage.get('status')}"
    try:
        datum = datetime.fromtimestamp(int(msg["date"]), timezone.utc)
    except (KeyError, TypeError, ValueError):
        return "fremd", "ohne Datum"
    if datum < utc(anfrage["gesendet"]) - UHR_TOLERANZ:
        return "fremd", "aelter als die Anfrage"
    if datum > utc(anfrage["gueltig_bis"]):
        return "abgelaufen", f"Frist {anfrage['gueltig_bis']} verstrichen"
    if not OK_TEXT.match(msg.get("text") or ""):
        return "kein_ok", "Text ist nicht ok"
    return "ok", ""


def lies_antworten(wochen: dict, jetzt: datetime, *, holen, melden) -> list[str]:
    """Claim the replies to our requests and record releases. Returns the weeks
    released in this run."""
    nach_id = {}
    for kw, (d, a) in wochen.items():
        if not a:
            continue
        nach_id[int(a["message_id"])] = kw
        for f in a.get("fruehere") or []:
            if f.get("message_id"):
                nach_id[int(f["message_id"])] = kw
    if not nach_id:
        return []
    frei = []
    for u in holen(sorted(nach_id)):
        msg = u.get("message") or {}
        kw = nach_id.get((msg.get("reply_to_message") or {}).get("message_id"))
        if not kw:
            continue
        d, a = wochen[kw]
        a = lies_json(d / "anfrage.json")  # fresh: an earlier update may have changed it
        urteil, grund = bewerte(u, a)
        protokoll(d, {"ts": iso(jetzt), "update_id": u.get("update_id"),
                      "urteil": urteil, "grund": grund,
                      "text": (msg.get("text") or "")[:40]})
        if urteil != "ok":
            if urteil != "fremd":  # a stranger gets no answer from us
                melden(f"Dauerrunde {kw}: Antwort nicht als Freigabe gewertet "
                       f"({grund}). Gezahlt wird nichts.")
            continue
        freigabe = {"kw": kw, "version": a["version"],
                    "zeilen_sha256": a["zeilen_sha256"],
                    "summe_mikro": a["summe_mikro"],
                    "update_id": u.get("update_id"),
                    "message_id": msg.get("message_id"),
                    "antwort_auf": a["message_id"],
                    "datum": iso(datetime.fromtimestamp(int(msg["date"]), timezone.utc)),
                    "erfasst": iso(jetzt)}
        if not exklusiv(d / "freigabe.json", freigabe):
            melden(f"Dauerrunde {kw}: weiteres ok ignoriert, die Woche ist "
                   f"bereits freigegeben.")
            continue
        a["status"] = "freigegeben"
        schreibe_json(d / "anfrage.json", a)
        frei.append(kw)
    return frei


def protokoll(d: pathlib.Path, zeile: dict) -> None:
    with open(d / "antworten.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")


def laufe_ab(kw: str, d: pathlib.Path, a: dict, jetzt: datetime, melden) -> None:
    if a and a.get("status") == "offen" and jetzt > utc(a["gueltig_bis"]):
        a["status"] = "abgelaufen"
        schreibe_json(d / "anfrage.json", a)
        melden(f"Dauerrunde {kw}: Frist ohne ok verstrichen "
               f"({a['gueltig_bis']}). Gezahlt wird nichts; die Woche bleibt "
               f"\"nicht gezahlt\".")


# --- 3. Pay -------------------------------------------------------------------

def pruefe_vor_zahlung(kw: str, d: pathlib.Path, ausgang: pathlib.Path, *,
                       web, budget_rest_mikro) -> tuple[dict, dict, list[dict]]:
    """Every check before the first transfer. Raises Gestoppt, sends nothing."""
    if (ausgang / "STOP").exists():
        raise Gestoppt("Stoppdatei ~/dauerrunde/STOP liegt")
    f = lies_json(d / "freigabe.json")
    a = lies_json(d / "anfrage.json")
    if not f or not a:
        raise Gestoppt("keine Freigabe")
    if (f["version"], f["zeilen_sha256"]) != (a["version"], a["zeilen_sha256"]):
        raise Gestoppt("Freigabe gehoert zu einer anderen Anfrage")
    zp = d / f"zeilen-v{f['version']}.json"
    if not zp.exists() or sha256_datei(zp) != f["zeilen_sha256"]:
        raise Gestoppt("Zeilen geaendert seit der Anfrage")
    if neueste_version(d) != f["version"]:
        raise Gestoppt("neuere Version der Woche seit der Anfrage")
    live = web(kw)
    if not live or live.get("version") != f["version"] \
            or live.get("stand") != "vorgeschlagen":
        raise Gestoppt(f"veroeffentlichte result.json ist nicht Version "
                       f"{f['version']} mit Stand vorgeschlagen")
    zeilen = zahlbar(json.loads(zp.read_text(encoding="utf-8")))
    if not zeilen:
        raise Gestoppt("keine auf Base zahlbare Zeile")
    eigene = {x.lower() for x in OWN_ADDRESSES}
    if any(z["adresse"].lower() in eigene for z in zeilen):
        raise Gestoppt("Empfaenger ist eine eigene Adresse")
    summe = sum(int(z["betrag_mikro"]) for z in zeilen)
    if summe != f["summe_mikro"]:
        raise Gestoppt("Summe weicht von der Freigabe ab")
    rest = budget_rest_mikro()
    if rest < summe:
        raise Gestoppt(f"WM-1-Budget reicht nicht: frei {D.usdc(rest)}, "
                       f"Summe {D.usdc(summe)} USDC")
    return f, live, zeilen


def plan_fuer(kw: str, f: dict, zeilen: list[dict]) -> dict:
    return {"kw": kw, "version": f["version"],
            "zweck": f"Dauerrunde G4 {kw} Direktzahlung aus 0xa175 nach "
                     f"Freigabe Lars (Telegram {f['datum']}), Budget WM-1",
            "zahler": ZAHLER, "eigene": sorted(OWN_ADDRESSES),
            "freigabe": {"update_id": f["update_id"],
                         "zeilen_sha256": f["zeilen_sha256"]},
            "zeilen": [{"adresse": z["adresse"], "betrag_mikro": str(z["betrag_mikro"]),
                        "grundlage": ", ".join(z.get("dids") or [])}
                       for z in zeilen]}


def node_zahler(plan_pfad: pathlib.Path, nachweis: pathlib.Path) -> int:
    """The real payer. Called by `zahle` only, after every check."""
    p = subprocess.run(["node", str(ZAHLER_SKRIPT), str(plan_pfad),
                        str(nachweis), "--echt"],
                       capture_output=True, text=True, timeout=1800)
    print(p.stdout[-4000:])
    if p.returncode:
        print(p.stderr[-2000:])
    return p.returncode


def nachweise(d: pathlib.Path) -> list[dict]:
    p = d / "auszahlung.jsonl"
    if not p.exists():
        return []
    out = []
    for z in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(z)
        except ValueError:
            continue
        if r.get("tx"):
            out.append(r)
    return out


def zahle(kw: str, d: pathlib.Path, ausgang: pathlib.Path, jetzt: datetime, *,
          web, budget_rest_mikro, zahler, buchen, veroeffentlichen, melden) -> str:
    """Pay one released week, at most once. Returns a one-line outcome."""
    if (d / "zahlung.fertig").exists():
        return f"{kw}: bereits bezahlt"
    if (d / "zahlung.beginnt").exists():
        msg = (f"Dauerrunde {kw}: Zahlung begonnen und nicht abgeschlossen. "
               f"Keine weitere Zahlung; auszahlung.jsonl von Hand pruefen.")
        melden(msg, alarm=True)
        return msg
    try:
        f, live, zeilen = pruefe_vor_zahlung(
            kw, d, ausgang, web=web, budget_rest_mikro=budget_rest_mikro)
    except Gestoppt as exc:
        msg = f"Dauerrunde {kw}: nicht gezahlt, {exc}."
        if "Stoppdatei" in str(exc):
            # Held on purpose; the release stays and pays once STOP is gone.
            return msg
        a = lies_json(d / "anfrage.json") or {}
        a.update(status="gestoppt", grund=str(exc))
        schreibe_json(d / "anfrage.json", a)
        melden(msg, alarm=True)
        return msg
    plan = plan_fuer(kw, f, zeilen)
    plan_pfad = d / f"plan-v{f['version']}.json"
    schreibe_json(plan_pfad, plan, 0o600)
    if not exklusiv(d / "zahlung.beginnt", {"ts": iso(jetzt), "kw": kw,
                                             "version": f["version"],
                                             "update_id": f["update_id"]}):
        return f"{kw}: Zahlung laeuft bereits"
    a = lies_json(d / "anfrage.json")
    a["status"] = "zahlung_laeuft"
    schreibe_json(d / "anfrage.json", a)

    rc = zahler(plan_pfad, d / "auszahlung.jsonl")
    txs = [r for r in nachweise(d) if r.get("kw") == kw]
    rest = budget_rest_mikro()  # before booking: pool_spend does not hold them yet
    gebucht = []
    for r in txs:
        rest -= int(r["betrag_mikro"])
        gebucht.append(buchen(kw, r, rest))
    if rc == 3:
        msg = (f"Dauerrunde {kw}: Bestand auf {W.ZAHLER_KURZ} reicht nicht fuer "
               f"{D.usdc(f['summe_mikro'])} USDC (Base). Nichts ueberwiesen. "
               f"Nachladen ist Lars' Schritt.")
        melden(msg, alarm=True)
        return msg
    bezahlt = {r["adresse"].lower() for r in txs}
    offen = [z for z in zeilen if z["adresse"].lower() not in bezahlt]
    if rc != 0 or offen:
        msg = (f"Dauerrunde {kw}: Zahlung unvollstaendig (Exit {rc}), "
               f"{len(txs)} von {len(zeilen)} Zeilen mit tx. Keine weitere "
               f"Zahlung; von Hand pruefen.")
        melden(msg, alarm=True)
        return msg

    exklusiv(d / "zahlung.fertig", {"ts": iso(jetzt), "tx": len(txs)})
    a["status"] = "bezahlt"
    schreibe_json(d / "anfrage.json", a)
    neu = ausgezahlt(live, f, txs)
    pub = veroeffentlichen(kw, {"version": neu["version"], "dateien": {
        f"result-{live['version']}.json": live, "result.json": neu}})
    zeile = (f"MolTrust - Dauerrunde {kw}: {len(txs)} Zahlungen, zusammen "
             f"{D.usdc(sum(int(r['betrag_mikro']) for r in txs))} USDC aus "
             f"{W.ZAHLER_KURZ}, freigegeben {f['datum']}. Ergebnis Version "
             f"{neu['version']}: {W.ERGEBNIS_URL.format(kw=kw)}"
             + (f" (web#{pub['pr']})" if pub else " (NICHT veroeffentlicht)")
             + ". tx: " + ", ".join(r["tx"][:10] + "…" for r in txs))
    melden(zeile, alarm=not pub)
    return zeile


def ausgezahlt(live: dict, f: dict, txs: list[dict]) -> dict:
    """The next version of the week's result: the same week, now paid."""
    v = int(live["version"])
    doc = {k: val for k, val in live.items()}
    doc.update({
        "version": v + 1, "ersetzt": f"result-{v}.json",
        "stand": "ausgezahlt",
        "auszahlung": {
            "stand": "ausgezahlt", "weg": "direkt", "zahler": W.ZAHLER_KURZ,
            "netz": "Base, USDC",
            "freigabe": f"Lars, Telegram {f['datum']}, Version {f['version']}",
            "summe": D.usdc(sum(int(r["betrag_mikro"]) for r in txs)),
            "zahlungen": [{"adresse": W.kurz(r["adresse"]), "betrag": r["betrag"],
                           "tx": r["tx"], "block": r.get("block")} for r in txs],
            "nachweis": f"ops/runden/dauer/{live['kw']}/auszahlung.jsonl",
        },
    })
    return doc


# --- 4. Book ------------------------------------------------------------------

def psql(sql: str, *vars_) -> str:
    args = ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
            "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1"]
    for i, v in enumerate(vars_):
        args += ["-v", f"p{i}={v}"]
    p = subprocess.run(args, input=sql, capture_output=True, text=True, timeout=60)
    if p.returncode:
        raise RuntimeError(f"psql: {p.stderr[:300]}")
    return p.stdout.strip()


def budget_rest_mikro_db() -> int:
    """20 USDC minus every WM-1 payment booked in pool_spend.

    Same filter as app/sql/wm1_budget.sql, without its cut: before a payment
    everything booked so far counts."""
    aus = psql("SELECT coalesce(sum(usdc),0) FROM pool_spend "
               "WHERE purpose LIKE '%Budget WM-1%' "
               "AND spent_at >= '2026-10-21T00:00:00Z';")
    return D.mikro(WM1_BUDGET) - D.mikro(Decimal(aus))


def buche_db(kw: str, r: dict, rest_mikro: int, d: pathlib.Path | None = None) -> dict:
    """pool_spend row and budget line for one tx. Idempotent by tx hash."""
    zweck = (f"Dauerrunde G4 {kw} Direktzahlung aus 0xa175 nach Freigabe Lars, "
             f"Budget WM-1, an {W.kurz(r['adresse'])}")
    psql("INSERT INTO pool_spend (pool, usdc, tx_hash, purpose, spent_at) "
         "VALUES (:'p0', :'p1'::numeric, :'p2', :'p3', :'p4'::timestamptz) "
         "ON CONFLICT DO NOTHING;", POOL, r["betrag"], r["tx"], zweck, r["ts"])
    return budget_zeile(kw, r, rest_mikro, d)


def budget_zeile(kw: str, r: dict, rest_mikro: int, d: pathlib.Path | None) -> dict:
    zeile = {"ts": r["ts"], "zweck": f"Dauerrunde G4 {kw}, Freigabe Lars per Telegram",
             "betrag": r["betrag"], "empfänger": r["adresse"], "tx": r["tx"],
             "rest": D.usdc(rest_mikro)}
    if d is not None:
        vorhanden = (d / "budget-zeilen.jsonl").read_text(encoding="utf-8") \
            if (d / "budget-zeilen.jsonl").exists() else ""
        if r["tx"] not in vorhanden:
            with open(d / "budget-zeilen.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    return zeile


# --- Run ----------------------------------------------------------------------

def wochen_im_ausgang(ausgang: pathlib.Path) -> dict:
    out = {}
    if not ausgang.exists():
        return out
    for d in sorted(ausgang.iterdir()):
        if d.is_dir() and re.fullmatch(r"\d{4}-W\d{2}", d.name):
            out[d.name] = d
    return out


def lauf(jetzt: datetime, *, ausgang: pathlib.Path, senden, chat_id: str,
         holen, web, budget_rest_mikro, zahler, buchen, veroeffentlichen,
         melden) -> list[str]:
    ergebnisse = []
    wochen = {}
    for kw, d in wochen_im_ausgang(ausgang).items():
        v = neueste_version(d)
        if v is None:
            continue
        try:
            a = frage(kw, d, v, jetzt, senden=senden, chat_id=chat_id)
        except Gestoppt as exc:
            melden(f"Dauerrunde {kw}: {exc}", alarm=True)
            continue
        wochen[kw] = (d, a)
    lies_antworten(wochen, jetzt, holen=holen, melden=melden)
    for kw, (d, _a) in wochen.items():
        a = lies_json(d / "anfrage.json")
        laufe_ab(kw, d, a, jetzt, melden)
        a = lies_json(d / "anfrage.json")
        if a and a.get("status") in ("freigegeben", "zahlung_laeuft") \
                and not (d / "zahlung.fertig").exists():
            ergebnisse.append(zahle(
                kw, d, ausgang, jetzt, web=web,
                budget_rest_mikro=budget_rest_mikro, zahler=zahler,
                buchen=lambda k, r, rest, _d=d: buchen(k, r, rest, _d),
                veroeffentlichen=veroeffentlichen, melden=melden))
    return ergebnisse


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dauerrunde_auszahlung")
    ap.add_argument("--trocken", action="store_true",
                    help="show the requests and their state; send, claim and pay nothing")
    a = ap.parse_args(argv)
    jetzt = datetime.now(timezone.utc)

    if a.trocken:
        for kw, d in wochen_im_ausgang(W.AUSGANG).items():
            anf = lies_json(d / "anfrage.json") or {}
            print(f"{kw}: Version {neueste_version(d)}, Anfrage "
                  f"{anf.get('status', 'keine')} {anf.get('gueltig_bis', '')}, "
                  f"Freigabe {'ja' if (d / 'freigabe.json').exists() else 'nein'}, "
                  f"bezahlt {'ja' if (d / 'zahlung.fertig').exists() else 'nein'}")
        return 0

    from app import gh, notify
    from app import telegram_inbox as TI

    token = gh.require("dauerrunde_auszahlung")

    def senden(text):
        return notify.send_telegram_message(text, channel=notify.STATS)

    def melden(text, alarm=False):
        print(text)
        notify.send_telegram(text, channel=notify.ALERTS if alarm else notify.STATS)

    def holen(ids):
        return TI.claim(KONSUMENT, ["message"], limit=50, reply_to=ids)

    def veroeffentlichen(kw, plan):
        try:
            return W.veroeffentliche(kw, plan, token)
        except Exception as exc:  # noqa: BLE001 - reported by the caller's line
            print(f"{kw}: result.json nicht veroeffentlicht: {exc}")
            return None

    try:
        erg = lauf(jetzt, ausgang=W.AUSGANG, senden=senden,
                   chat_id=notify.chat_id_for(notify.STATS), holen=holen,
                   web=lambda kw: W.lies_web(kw, token),
                   budget_rest_mikro=budget_rest_mikro_db, zahler=node_zahler,
                   buchen=buche_db, veroeffentlichen=veroeffentlichen,
                   melden=melden)
    except Exception as exc:  # noqa: BLE001
        msg = f"MolTrust - Dauerrunde Auszahlung: Lauf gescheitert: {type(exc).__name__}: {str(exc)[:300]}"
        print(msg)
        W.herzschlag("fehler", msg, HEARTBEAT)
        notify.send_telegram(msg, channel=notify.ALERTS)
        return 2
    satz = "; ".join(erg) if erg else "keine freigegebene Woche, nichts gezahlt"
    print(f"Dauerrunde Auszahlung: {satz}")
    W.herzschlag("ok", f"Dauerrunde Auszahlung: {satz}", HEARTBEAT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
