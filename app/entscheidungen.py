"""Decisions for Lars, bundled into one Telegram message a day (ops MELD-1 criterion 3).

Lars, 27.10.2026: decisions at most once a day, bundled in ONE message; per
point two sentences, a recommendation, the answer format "1A 2ja ..." and the
way to answer (the Console tab).

A sender with a decision does not send it. It queues one point here, either
directly with vormerken() or by sending a message whose art is bundled
(app/meldungsarten.py, zustellung "buendel"); notify then queues it. The
selftest digest at 08:00 UTC calls senden(): one message with every queued
point, numbered. The full texts go to the filing (app/ablage.py) under the
same numbers, so the Console tab that receives "1A" can look up what 1 was.

Back-channel (ops MELD-RUECKKANAL, 17.11.2026). Every decision message carries
the code ENTSCH-YYYYMMDD and says in one line how to answer straight from
Telegram: "ENTSCH-20261117 1A 2A 3B" sent to the bot. Numbers run on through
the day: each point a message asks about is written to a register under the
day's code with the next free number (registrieren), so a second bundle at
20:00 or a single decision such as scripts/zugang_ablauf.py never reuses a
number. konsumieren() (called every five minutes from reply_radar --consume)
claims such answers from telegram_inbox, resolves each choice against the
register and files it where the Console tab looks: one line per choice in
entscheidungen-antworten.jsonl next to the queue, and one section in the
filing (art entscheidungs-antwort) with the point's full text. The bot
confirms in one reply what it filed and what it could not read.

A reading list (leseliste_vormerken, ops MELD-LIST) is one point with a list
under it. Every run of a sender queues its own list; at send time all lists of
one art merge into one point, the upgraded entries first, so Lars gets the
list messages of a day once, not once per run.

The queue is emptied by id after the bundle was delivered, as the daily
report inbox is (app/tagesbericht_eingang.py): a point rather comes twice than
not at all. The file lives next to the send log (MOLTRUST_NOTIFY_STATE_DIR,
otherwise ~/selftest); tests redirect it.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import logging
import os
import re
import uuid

from app import ablage

_logger = logging.getLogger("moltrust.entscheidungen")

DATEI = "entscheidungen.jsonl"
REGISTER = "entscheidungen-register.jsonl"
ANTWORTEN = "entscheidungen-antworten.jsonl"
ART = "entscheidungs-buendel"
ANTWORT_ART = "entscheidungs-antwort"
KONSUMENT = "entscheidungen"
PRAEFIX = "ENTSCH-"

STANDARD_OPTIONEN = {"A": "ein Console-Tab übernimmt", "B": "liegen lassen"}
LISTE_OPTIONEN = {"A": "ein Console-Tab entwirft eine Antwort", "B": "gelesen, nichts zu tun"}
LISTE_MAX = 25          # list lines under one point; the rest is in the filing
LISTE_ZEILE_MAX = 110


def _verz() -> str:
    return os.environ.get("MOLTRUST_NOTIFY_STATE_DIR", "").strip() \
        or os.path.expanduser("~/selftest")


def pfad() -> str:
    return os.path.join(_verz(), DATEI)


def register_pfad() -> str:
    return os.path.join(_verz(), REGISTER)


def antworten_pfad() -> str:
    return os.path.join(_verz(), ANTWORTEN)


def kennung(tag: dt.date) -> str:
    """The code a decision message of this UTC day carries: ENTSCH-YYYYMMDD."""
    return f"{PRAEFIX}{tag:%Y%m%d}"


_SATZENDE = re.compile(r"(?<=[.!?])\s+")


def zwei_saetze(text: str) -> str:
    """The first two sentences of a plain text, on one line."""
    flach = " ".join(z.strip() for z in ablage.klartext(text).splitlines() if z.strip())
    saetze = [s for s in _SATZENDE.split(flach) if s]
    out = " ".join(saetze[:2]).strip()
    if out and out[-1] not in ".!?":
        out += "."
    return out


def vormerken(art: str, punkt: str, *, empfehlung: str = "A",
              optionen: dict | None = None, volltext: str = "",
              path: str | None = None, now: dt.datetime | None = None) -> bool:
    """Queue one point for the next bundle. True when it is on disk."""
    roh = punkt
    punkt = zwei_saetze(punkt)
    optionen = dict(optionen or STANDARD_OPTIONEN)
    if not punkt or empfehlung not in optionen:
        _logger.warning("entscheidungen: leerer Punkt oder Empfehlung ohne Option")
        return False
    path = path or pfad()
    now = now or dt.datetime.now(dt.timezone.utc)
    zeile = {"id": uuid.uuid4().hex, "ts": now.isoformat(timespec="seconds"),
             "art": art, "punkt": punkt, "empfehlung": empfehlung,
             "optionen": optionen, "volltext": ablage.klartext(volltext or roh)}
    return _anhaengen(zeile, path)


def _listen_punkt(titel: str, liste: list[dict]) -> str:
    n, k = len(liste), sum(1 for x in liste if x["hoch"])
    return (f"{titel}: {n} {'neuer Beitrag' if n == 1 else 'neue Beiträge'}, "
            f"{'keiner' if k == 0 else k} davon hochgestuft.")


def leseliste_vormerken(art: str, titel: str, eintraege: list[dict], *,
                        volltext: str = "", path: str | None = None,
                        now: dt.datetime | None = None) -> bool:
    """Queue a reading list: entries {"zeile": str, "hoch": bool}.

    True when it is on disk, and True for an empty list (nothing to queue).
    """
    liste = [{"zeile": " ".join(str(e.get("zeile", "")).split()), "hoch": bool(e.get("hoch"))}
             for e in eintraege]
    liste = [x for x in liste if x["zeile"]]
    if not liste:
        return True
    now = now or dt.datetime.now(dt.timezone.utc)
    zeile = {"id": uuid.uuid4().hex, "ts": now.isoformat(timespec="seconds"),
             "art": art, "titel": titel, "punkt": _listen_punkt(titel, liste),
             "empfehlung": "B", "optionen": dict(LISTE_OPTIONEN),
             "volltext": ablage.klartext(volltext), "liste": liste}
    return _anhaengen(zeile, path or pfad())


def _anhaengen(zeile: dict, path: str) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
    except OSError as e:
        _logger.warning("entscheidungen: nicht schreibbar: %s", type(e).__name__)
        return False
    return True


def lesen(path: str | None = None) -> list[dict]:
    path = path or pfad()
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for roh in fh:
                try:
                    e = json.loads(roh)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get("id") and e.get("punkt"):
                    out.append(e)
    except FileNotFoundError:
        return []
    return out


def leeren(ids, path: str | None = None) -> int:
    """Remove the entries with these ids; what was queued meanwhile stays."""
    path = path or pfad()
    ids = set(ids)
    try:
        with open(path, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            zeilen = fh.readlines()
            bleibt = []
            for roh in zeilen:
                try:
                    if json.loads(roh).get("id") in ids:
                        continue
                except ValueError:
                    pass
                bleibt.append(roh)
            fh.seek(0)
            fh.truncate()
            fh.writelines(bleibt)
    except FileNotFoundError:
        return 0
    return len(zeilen) - len(bleibt)


def zusammenfassen(eintraege: list[dict]) -> list[dict]:
    """Merge the reading lists of one art into one point, after the decisions.

    Each merged point carries "ids", the queue ids it stands for.
    """
    out, gruppen = [], {}
    for e in eintraege:
        if not isinstance(e.get("liste"), list):
            out.append(dict(e, ids=[e["id"]]))
            continue
        g = gruppen.get(e["art"])
        if g is None:
            g = gruppen[e["art"]] = dict(e, ids=[], liste=[], volltext="")
        g["ids"].append(e["id"])
        g["liste"] = g["liste"] + e["liste"]
        g["volltext"] = "\n\n".join(t for t in (g["volltext"], e.get("volltext", "")) if t)
    for g in gruppen.values():
        g["liste"] = [x for x in g["liste"] if x["hoch"]] + [x for x in g["liste"] if not x["hoch"]]
        g["punkt"] = _listen_punkt(g.get("titel") or g["art"], g["liste"])
        out.append(g)
    return out


def _listen_zeilen(liste: list[dict]) -> list[str]:
    def kurz(z):
        return z if len(z) <= LISTE_ZEILE_MAX else z[:LISTE_ZEILE_MAX - 1] + "…"
    zeilen = [f"   {'↑' if x['hoch'] else '·'} {kurz(x['zeile'])}" for x in liste[:LISTE_MAX]]
    if len(liste) > LISTE_MAX:
        zeilen.append(f"   … und {len(liste) - LISTE_MAX} weitere, alle in der Ablage (Volltexte).")
    return zeilen


def antwortzeile(kenn: str, nummern: list[int]) -> str:
    """The one line that says how to answer, with the real numbers as example."""
    beispiel = " ".join(f"{n}A" for n in nummern[:2])
    return f"Antwort hier im Chat: „{kenn} {beispiel}“"


def registrieren(punkte: list[dict], now: dt.datetime | None = None, *,
                 path: str | None = None) -> tuple[str, list[int]]:
    """Give each point the next free number under today's code and keep it.

    Returns the code and the numbers. Raises OSError when the register is not
    writable: a message whose numbers cannot be resolved later must not go out
    with an answer line that promises they can.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    kenn = kennung(now.date())
    path = path or register_pfad()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        hoechste = 0
        for roh in fh:
            try:
                e = json.loads(roh)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("kennung") == kenn:
                hoechste = max(hoechste, int(e.get("nr") or 0))
        nummern = []
        for i, p in enumerate(punkte, hoechste + 1):
            nummern.append(i)
            fh.write(json.dumps({
                "kennung": kenn, "nr": i, "ts": now.isoformat(timespec="seconds"),
                "art": p.get("art", ""), "punkt": p.get("punkt", ""),
                "empfehlung": p.get("empfehlung", ""), "optionen": p.get("optionen") or {},
                "volltext": p.get("volltext", "")}, ensure_ascii=False) + "\n")
    os.chmod(path, 0o600)
    return kenn, nummern


def einzelpunkt(art: str, punkt: str, *, optionen: dict | None = None,
                empfehlung: str = "A", volltext: str = "",
                now: dt.datetime | None = None) -> str:
    """Register one decision a message sends at once; return its answer line.

    For a sender of its own (not the bundle) that asks Lars to choose, e.g.
    scripts/zugang_ablauf.py. The line names code, number and options.
    """
    optionen = dict(optionen or STANDARD_OPTIONEN)
    kenn, (nr,) = registrieren([{"art": art, "punkt": zwei_saetze(punkt),
                                 "empfehlung": empfehlung, "optionen": optionen,
                                 "volltext": ablage.klartext(volltext or punkt)}], now)
    opt = ", ".join(f"{k} {v}" for k, v in optionen.items())
    return f"Antwort hier im Chat: „{kenn} {nr}{empfehlung}“ ({opt})"


def buendel_text(eintraege: list[dict], heute: dt.date,
                 nummern: list[int] | None = None) -> str:
    """One message: a head line with code and answer format, then one line per point."""
    nummern = nummern or list(range(1, len(eintraege) + 1))
    kenn = kennung(heute)
    kopf = (f"Entscheidungen vom {heute:%d.%m.}: {len(eintraege)} "
            f"{'Punkt' if len(eintraege) == 1 else 'Punkte'}, {kenn}. "
            f"{antwortzeile(kenn, nummern)}.")
    zeilen = [kopf]
    for i, e in zip(nummern, eintraege):
        opt = ", ".join(f"{k} {v}" for k, v in e["optionen"].items())
        zeilen.append(f"{i}. {e['punkt']} Empfehlung: {e['empfehlung']}. ({opt})")
        if e.get("liste"):
            zeilen += _listen_zeilen(e["liste"])
    return "\n".join(zeilen)


def senden(now: dt.datetime | None = None, *, path: str | None = None, send=None) -> bool | None:
    """Deliver the bundle. None when nothing is queued, else whether it went out."""
    from app import notify
    now = now or dt.datetime.now(dt.timezone.utc)
    eintraege = zusammenfassen(lesen(path))
    if not eintraege:
        return None
    punkte = [dict(e, volltext=e.get("volltext", "") + (
        "\n" + "\n".join(x["zeile"] for x in e["liste"]) if e.get("liste") else ""))
        for e in eintraege]
    try:
        kenn, nummern = registrieren(punkte, now)
    except OSError as e:
        _logger.warning("entscheidungen: Register nicht schreibbar: %s", type(e).__name__)
        return False
    text = buendel_text(eintraege, now.date(), nummern)
    voll = [text, ""] + [f"{i}. [{e['art']}] {e['volltext']}" for i, e in zip(nummern, eintraege)]
    ablage.ablegen(ART, notify.STATS, "\n".join(voll), now=now, vermerk=f"Volltexte {kenn}")
    send = send or notify.send_telegram
    ok = send(text, channel=notify.STATS, art="entscheidungs-buendel")
    if ok:
        leeren({i for e in eintraege for i in e["ids"]}, path)
    return bool(ok)


# ── Back-channel: answers from Telegram (ops MELD-RUECKKANAL) ──

_ANTWORT = re.compile(r"^\s*ENTSCH-(\d{8})\b(.*)$", re.IGNORECASE | re.DOTALL)
_WAHL = re.compile(r"^(\d{1,3})([A-Za-z])$")


def zerlegen(text: str) -> tuple[str, list[tuple[int, str]], list[str]] | None:
    """"ENTSCH-20261117 1A 2a, 3B" -> (code, [(1,"A"),(2,"A"),(3,"B")], unreadable)."""
    m = _ANTWORT.match(text or "")
    if not m:
        return None
    wahlen, rest = [], []
    for tok in re.split(r"[\s,;]+", m.group(2).strip()):
        if not tok:
            continue
        w = _WAHL.match(tok)
        if w:
            wahlen.append((int(w.group(1)), w.group(2).upper()))
        else:
            rest.append(tok)
    return f"{PRAEFIX}{m.group(1)}", wahlen, rest


def _register_lesen(kenn: str, path: str | None = None) -> dict[int, dict]:
    out: dict[int, dict] = {}
    try:
        with open(path or register_pfad(), encoding="utf-8") as fh:
            for roh in fh:
                try:
                    e = json.loads(roh)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get("kennung") == kenn:
                    out[int(e["nr"])] = e
    except FileNotFoundError:
        pass
    return out


def verarbeiten(text: str, *, message_id: int | None = None,
                now: dt.datetime | None = None) -> dict | None:
    """File one answer. None when the text is not an answer.

    Returns {"kennung", "abgelegt": [(nr, wahl)], "unbekannt": [nr],
    "ungueltig": ["nrX"], "unlesbar": [token], "geschrieben": bool}.
    """
    z = zerlegen(text)
    if z is None:
        return None
    kenn, wahlen, unlesbar = z
    now = now or dt.datetime.now(dt.timezone.utc)
    reg = _register_lesen(kenn)
    erg = {"kennung": kenn, "abgelegt": [], "unbekannt": [], "ungueltig": [],
           "unlesbar": unlesbar, "geschrieben": True}
    zeilen, abschnitt = [], [f"Antwort von Lars aus Telegram: {' '.join(text.split())}"]
    for nr, wahl in wahlen:
        e = reg.get(nr)
        if e is None:
            erg["unbekannt"].append(nr)
            continue
        if wahl not in (e.get("optionen") or {}):
            erg["ungueltig"].append(f"{nr}{wahl}")
            continue
        erg["abgelegt"].append((nr, wahl))
        option = e["optionen"][wahl]
        zeilen.append({"ts": now.isoformat(timespec="seconds"), "kennung": kenn, "nr": nr,
                       "wahl": wahl, "option": option, "art": e.get("art", ""),
                       "punkt": e.get("punkt", ""), "empfehlung": e.get("empfehlung", ""),
                       "message_id": message_id})
        abschnitt += ["", f"{kenn} {nr}{wahl}: {option}", f"[{e.get('art', '')}] {e.get('punkt', '')}",
                      e.get("volltext", "")]
    if zeilen:
        ok = all(_anhaengen(x, antworten_pfad()) for x in zeilen)
        from app import notify
        ok = ablage.ablegen(ANTWORT_ART, notify.STATS, "\n".join(abschnitt).rstrip(),
                            now=now, vermerk=kenn) and ok
        erg["geschrieben"] = ok
    return erg


def bestaetigung(erg: dict) -> str:
    """The bot's one reply: what was filed, what was not."""
    k = erg["kennung"]
    if not erg["geschrieben"]:
        return f"{k}: nicht abgelegt, die Ablage ist nicht schreibbar. Bitte später noch einmal senden."
    zeilen = []
    if erg["abgelegt"]:
        zeilen.append(f"{k}: abgelegt " + ", ".join(f"{n}{w}" for n, w in erg["abgelegt"]) + ".")
    else:
        zeilen.append(f"{k}: nichts abgelegt.")
    if erg["unbekannt"]:
        zeilen.append(f"Unter {k} gibt es keinen Punkt " + ", ".join(map(str, erg["unbekannt"])) + ".")
    if erg["ungueltig"]:
        zeilen.append("Option gibt es nicht: " + ", ".join(erg["ungueltig"]) + ".")
    if erg["unlesbar"]:
        zeilen.append("Nicht gelesen: " + " ".join(erg["unlesbar"][:5])
                      + f". Form „{k} 1A 2B“.")
    return "\n".join(zeilen[:4])


def von_lars(m: dict) -> bool:
    """Lars in the private chat: sender id == chat id == the stats chat, never a bot."""
    from app import notify
    sender = m.get("from") or {}
    chat = str(notify.chat_id_for(notify.STATS) or "").strip()
    return (bool(chat) and not sender.get("is_bot")
            and str(sender.get("id", "")) == chat
            and str((m.get("chat") or {}).get("id", "")) == chat)


def konsumieren(*, claim=None, send=None) -> int:
    """Answers waiting in telegram_inbox. Returns how many were filed."""
    from app import notify, telegram_inbox
    claim = claim or telegram_inbox.claim
    send = send or notify.send_telegram_message
    n = 0
    for u in claim(KONSUMENT, ["message"], limit=20, text_prefix=PRAEFIX):
        m = u.get("message") or {}
        if not von_lars(m):
            _logger.info("entscheidungen: Antwort eines anderen Absenders, ignoriert")
            continue
        erg = verarbeiten(m.get("text") or "", message_id=m.get("message_id"))
        if erg is None:
            continue
        send(bestaetigung(erg), channel=notify.STATS, reply_to=m.get("message_id"),
             art="befehl-antwort")
        n += 1 if erg["abgelegt"] else 0
    return n


# ── Free text (21.11.2026) ──
#
# Lars answered a gate in his own words, "CB-BAZAAR 0,05 USDC freigegeben",
# at 09:50Z on 21.11.2026. No consumer recognised it: every claim above is
# narrow (ENTSCH- prefix, a reply to a draft, a "/" command). The update
# stayed unclaimed, nothing was filed and the bot said nothing, so Lars could
# not tell whether it had arrived. The rule since: every message from Lars
# gets a reply. What no narrower consumer takes within FREI_MIN_ALTER is
# filed as a free-text answer, and the bot says so.

FREI_KONSUMENT = "entscheidungen_frei"
FREI_ART = "telegram-freitext"
# Narrower consumers run every five minutes (reply_radar --consume); two
# turns are theirs before the fallback takes a message.
FREI_MIN_ALTER = 600
# A backlog of old chat is not an answer to anything that is open today.
FREI_MAX_ALTER = 7 * 86400
FREI_ZITAT = 120


def frei_ablegen(text: str, *, message_id: int | None = None,
                 now: dt.datetime | None = None) -> bool:
    """File one free-text message from Lars. True when both writes landed."""
    now = now or dt.datetime.now(dt.timezone.utc)
    zeile = {"ts": now.isoformat(timespec="seconds"), "kennung": "FREI", "nr": None,
             "wahl": None, "option": None, "art": FREI_ART, "text": text,
             "message_id": message_id}
    ok = _anhaengen(zeile, antworten_pfad())
    from app import notify
    ok = ablage.ablegen(FREI_ART, notify.STATS,
                        f"Freitext von Lars aus Telegram: {text}",
                        now=now, vermerk="FREI") and ok
    return ok


def frei_bestaetigung(text: str, ok: bool) -> str:
    kurz = " ".join(text.split())
    if len(kurz) > FREI_ZITAT:
        kurz = kurz[:FREI_ZITAT - 1] + "…"
    if not ok:
        return f"Nicht abgelegt, die Ablage ist nicht schreibbar: „{kurz}“. Bitte später noch einmal senden."
    return (f"Erhalten und abgelegt: „{kurz}“.\n"
            "Der Koordinator überträgt es beim nächsten Lauf nach #14.")


def frei_konsumieren(*, claim=None, send=None) -> int:
    """Messages from Lars that no other consumer took. Returns how many were filed.

    Runs last in reply_radar --consume, after every narrower consumer.
    """
    from app import notify, telegram_inbox
    claim = claim or telegram_inbox.claim
    send = send or notify.send_telegram_message
    n = 0
    for u in claim(FREI_KONSUMENT, ["message"], limit=20,
                   leftover=(FREI_MIN_ALTER, FREI_MAX_ALTER)):
        m = u.get("message") or {}
        text = (m.get("text") or "").strip()
        if not text or not von_lars(m):
            continue
        ok = frei_ablegen(text, message_id=m.get("message_id"))
        send(frei_bestaetigung(text, ok), channel=notify.STATS,
             reply_to=m.get("message_id"), art="befehl-antwort")
        n += 1 if ok else 0
    return n


def liegengeblieben(min_alter: int = 1800) -> int:
    """Unclaimed text messages older than `min_alter` seconds, last seven days.

    The watch for the rule above (docs/invariants/c-telegram-jede-nachricht-beantwortet.yaml).
    The fallback claims after ten minutes, so anything still open after thirty
    means the consumer did not run or did not claim.
    """
    import psycopg2
    from app import telegram_inbox
    conn = psycopg2.connect(telegram_inbox.DB_DSN)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM telegram_inbox WHERE consumed_by IS NULL "
                "AND payload ? 'message' AND payload->'message'->>'text' IS NOT NULL "
                "AND ts < now() - make_interval(secs => %s) "
                "AND ts > now() - make_interval(secs => %s)",
                (int(min_alter), FREI_MAX_ALTER))
            return int(cur.fetchone()[0])
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    import sys
    if sys.argv[1:] == ["--liegengeblieben"]:
        n = liegengeblieben()
        print(f"liegengeblieben={n}")
        sys.exit(1 if n else 0)
    sys.exit(2)
