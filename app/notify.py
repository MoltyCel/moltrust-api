"""Shared Telegram notification gate + a best-effort gated sender.

Telegram sending is controlled by its OWN flag, `MOLTRUST_NOTIFY`, decoupled
from `MOLTRUST_ENV` (which governs crypto posture: KMS-only signing in
kms_signer, PQC dev-key disable in dilithium). This split lets you turn alerts
on/off independently of the signing-key enforcement.

    send allowed  <=>  MOLTRUST_NOTIFY in {"1","true","on","yes","enabled","production"}

Resolution reads os.environ first, then falls back to the single MOLTRUST_NOTIFY
line in ~/.moltrust_secrets — so the standalone scripts that load secrets into
their own dict (not os.environ) resolve the same value instead of being wrongly
suppressed. Failure / unset => not allowed (fail-safe = do not send).

Channels
--------
Every message goes to exactly one of four chats:

    stats    periodic numbers nobody has to act on
    alerts   something is broken or needs a decision now
    money    payments, balances, payouts, budget
    worklog  what the agents did: posts, PRs, drafts, reviews, cleanups

One chat carried all four until 2026-09-21, which made the alerts unfindable
between the hourly numbers and meant a payout notice sat in the same scroll as a
draft tweet. `channel=` is a required keyword on every sender so a new call site
has to decide; `tests/test_notify_channels.py` fails the build on one that did
not.

Each channel resolves `TELEGRAM_CHAT_ID_<CHANNEL>` and falls back to the single
`TELEGRAM_CHAT_ID`. An unconfigured split therefore behaves exactly as before
rather than dropping messages, so the code can ship before the chats exist.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re

import requests

_logger = logging.getLogger("moltrust.notify")

_FLAG = "MOLTRUST_NOTIFY"
_TRUE = {"1", "true", "on", "yes", "enabled", "production"}
_CHUNK_LIMIT = 3900  # Telegram hard-caps at 4096; leave headroom.
_ENV_CACHE: dict[str, str] = {}

STATS = "stats"
ALERTS = "alerts"
MONEY = "money"
WORKLOG = "worklog"
CHANNELS = (STATS, ALERTS, MONEY, WORKLOG)


def _resolve(name: str) -> str:
    """`name` from os.environ, else a fallback read of ~/.moltrust_secrets.

    The fallback exists because several standalone scripts load the secrets
    file into a dict of their own and never touch os.environ. Without it they
    would resolve an empty value and suppress themselves.
    """
    v = os.environ.get(name, "")
    if v:
        return v
    if name in _ENV_CACHE:
        return _ENV_CACHE[name]
    resolved = ""
    try:
        path = os.environ.get("MOLTRUST_SECRETS_FILE", os.path.expanduser("~/.moltrust_secrets"))
        with open(path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(name + "="):
                    resolved = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except Exception:
        resolved = ""
    _ENV_CACHE[name] = resolved
    return resolved


def _resolve_flag() -> str:
    """MOLTRUST_NOTIFY from os.environ, else a fallback read of ~/.moltrust_secrets."""
    return _resolve(_FLAG)


def telegram_allowed(context: str = "", logger=None) -> bool:
    """THE shared Telegram gate. True iff MOLTRUST_NOTIFY is a truthy value.

    Decoupled from MOLTRUST_ENV on purpose (see module docstring). Outside an
    enabled state it logs a suppression notice and returns False so every caller
    can gate its real-send path with a single call.
    """
    if _resolve_flag().strip().lower() in _TRUE:
        return True
    (logger or _logger).info(
        "telegram suppressed (MOLTRUST_NOTIFY not enabled): %s", context
    )
    return False


def _chunk(text: str, limit: int = _CHUNK_LIMIT) -> list[str]:
    parts: list[str] = []
    buf = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(line[:limit])
            line = line[limit:]
        if buf and len(buf) + 1 + len(line) > limit:
            parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts


def chat_id_for(channel: str) -> str:
    """The chat a channel posts to, falling back to the undivided chat.

    Falling back rather than failing is deliberate: the routing ships before
    the four chats exist, and a message that would have been delivered
    yesterday must not be dropped because its channel has no id yet.
    """
    if channel not in CHANNELS:
        raise ValueError(f"unknown telegram channel {channel!r}; expected one of {CHANNELS}")
    specific = _resolve(f"TELEGRAM_CHAT_ID_{channel.upper()}").strip()
    if specific:
        return specific
    return _resolve("TELEGRAM_CHAT_ID").strip()


def send_telegram(text: str, *, channel: str, parse_mode: str | None = None,
                  chunk: bool = False, timeout: int = 15,
                  reply_markup: dict | None = None) -> bool:
    """Full gated sender for simple callers. Best-effort; never raises.

    `reply_markup` takes a Telegram markup object (an inline keyboard, say) and
    is JSON-encoded here so callers do not each remember to. It is attached to
    the first chunk only: a keyboard repeated under every part of a split
    message would offer the same decision several times.
    """
    return _deliver_telegram(text, channel=channel, parse_mode=parse_mode,
                             chunk=chunk, timeout=timeout,
                             reply_markup=reply_markup)[0]


def send_telegram_message(text: str, *, channel: str, parse_mode: str | None = None,
                          chunk: bool = False, timeout: int = 15,
                          reply_markup: dict | None = None) -> int | None:
    """send_telegram, but hands back the message_id of the first chunk.

    A sender that wants to edit its own message later — to write an outcome
    into it once that outcome is known — needs the id, and `bool` cannot carry
    it. Everything else is identical, including the gate and the plain-text
    retry.
    """
    return _deliver_telegram(text, channel=channel, parse_mode=parse_mode,
                             chunk=chunk, timeout=timeout,
                             reply_markup=reply_markup)[1]


def _deliver_telegram(text: str, *, channel: str, parse_mode: str | None,
                      chunk: bool, timeout: int,
                      reply_markup: dict | None) -> tuple[bool, int | None]:
    fp = fingerprint(text)
    if not telegram_allowed(f"notify.send_telegram[{channel}]"):
        _record_sent(channel, False, None, fp, text, grund="gate")
        return False, None
    # Ueber _resolve, wie die Chat-ID. Direkt aus os.environ gelesen war der
    # Token die einzige Stelle ohne den Rueckfall auf ~/.moltrust_secrets — und
    # genau daran ist am 09.10.2026 der erste Meldeweg von deploy.sh gescheitert:
    # Chat-ID gefunden, Token nicht, "token/chat missing", Meldung verloren.
    # _resolve existiert fuer Aufrufer, die die Secrets nicht in os.environ
    # legen; der Token ist der Wert, bei dem das am meisten zaehlt.
    token = _resolve("TELEGRAM_BOT_TOKEN")
    chat = chat_id_for(channel)
    if not token or not chat:
        _logger.warning("notify.send_telegram: token/chat missing for channel %s", channel)
        _record_sent(channel, False, None, fp, text, grund="kein token/chat")
        return False, None

    # Die Drosselung gilt fuer jeden Kanal, auch fuer ALERTS. Eine Wache, die
    # sich fuer zu wichtig zum Drosseln haelt, ist genau die, die am 08.10.
    # fuenfzehnmal dasselbe schickte.
    senden, fp, zusatz = throttle(text)
    if not senden:
        _record_sent(channel, False, None, fp, text, grund="gedrosselt")
        return False, None
    if zusatz:
        text = text + zusatz

    pieces = _chunk(text) if chunk else [text]
    ok = True
    message_id = None
    for index, piece in enumerate(pieces):
        data = {"chat_id": chat, "text": piece, "disable_web_page_preview": "true"}
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup and index == 0:
            data["reply_markup"] = json.dumps(reply_markup)
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              data=data, timeout=timeout)
            # A formatting error costs the whole message: Telegram answers 400
            # "can't parse entities" when the body carries a stray tag or a lone
            # `_`. Resending as plain text delivers it with the markup visible,
            # which beats losing it — auto_repair lost 23 messages that way and
            # threadwatch 7 before this existed.
            if r.status_code == 400 and parse_mode and b"parse entities" in r.content:
                _logger.warning("notify.send_telegram[%s]: %s rejected, resending as text",
                                channel, parse_mode)
                r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                  data={k: v for k, v in data.items() if k != "parse_mode"},
                                  timeout=timeout)
            ok = ok and (r.status_code == 200)
            if index == 0 and r.status_code == 200:
                message_id = (r.json().get("result") or {}).get("message_id")
        except Exception as e:
            _logger.warning("notify.send_telegram[%s] failed: %s", channel, type(e).__name__)
            ok = False
            status = None
        else:
            status = r.status_code
        if index == 0:
            erster_status = status
    _record_sent(channel, ok, locals().get("erster_status"), fp, text)
    return ok, message_id



# ── Sendeprotokoll und Drosselung ──────────────────────────────────────────
#
# Am 08.10.2026 schickte die R4-Wache fuenfzehn gleichlautende Fehlalarme in
# vier Stunden. Sie war von der Drosselung ausgenommen, weil sie als wichtig
# galt — und genau deshalb kam sie fuenfzehnmal durch. Wichtig und wiederholt
# sind zwei verschiedene Eigenschaften.
#
# Die Drosselung sitzt deshalb hier, an der einen Stelle, durch die jede
# Nachricht geht, und nicht in den einzelnen Wachen. Eine Ausnahme gibt es
# nicht: ein Alarm, der sich aendert, hat einen anderen Fingerabdruck und
# kommt sofort.

# Wo die beiden Zustandsdateien liegen. Umlenkbar, und zwar nicht aus Bequem-
# lichkeit: ohne das schreibt jeder Testlauf in das echte Sendeprotokoll und in
# den echten Fingerabdruck-Speicher. Am 09.10.2026 standen acht Zeilen mit dem
# Text "x" aus Testlaeufen im Protokoll, und "x" belegte einen echten
# Drosselungs-Platz — eine Meldung, die sich darauf abbildet, waere
# unterdrueckt worden, weil ein Test sie schon "gesendet" hatte. Ein Test, der
# Produktionszustand anfasst, ist kein Test mehr.
_STATE_DIR = os.environ.get("MOLTRUST_NOTIFY_STATE_DIR", "").strip() \
    or os.path.expanduser("~/selftest")

SENT_LOG = os.path.join(_STATE_DIR, "telegram-sent.jsonl")
FINGERPRINTS = os.path.join(_STATE_DIR, "telegram-fingerabdruecke.json")
REPEAT_EVERY = dt.timedelta(hours=1)
FORGET_AFTER = dt.timedelta(hours=24)

# Was aus einer Nachricht herausfaellt, bevor sie ihren Fingerabdruck bekommt.
# Als benannte Liste, damit jeder Eintrag einzeln begruendet ist und nicht in
# einem Sammelmuster verschwindet. Ohne das waere jede Nachricht neu, weil sie
# einen Zeitstempel traegt.
_VOLATIL = (
    ("ISO-Zeitstempel", r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?Z?([+-]\d{2}:\d{2})?"),
    ("Datum deutsch", r"\b\d{1,2}\.\d{1,2}\.(\d{4}|\d{2})?"),
    ("Uhrzeit mit Z", r"\b\d{1,2}:\d{2}(:\d{2})?Z\b"),
    ("Dauer h:mm:ss", r"\b\d+:\d{2}:\d{2}(\.\d+)?\b"),
    ("Dauer mit Einheit", r"\b\d+([.,]\d+)?\s?(ms|s|min|h|Stunden|Minuten)\b"),
    ("Git-Hash", r"\b[0-9a-f]{7,40}\b"),
    ("PID", r"\bPID \d+\b"),
    ("eigener Zaehler", r"\b\d+x seit\b[^\n]*"),
)


def _normalise(text: str) -> str:
    """Die Nachricht ohne das, was sich bei jedem Lauf aendert."""
    out = text
    for _name, muster in _VOLATIL:
        out = re.sub(muster, "·", out)
    return re.sub(r"\s+", " ", out).strip()


def fingerprint(text: str) -> str:
    return hashlib.sha256(_normalise(text).encode("utf-8")).hexdigest()[:16]


def _load_fingerprints(path: str = FINGERPRINTS) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        # Nicht lesbar heisst senden. Lieber eine Nachricht zu viel als eine
        # Drosselung, die hinter einer kaputten Datei alles verschluckt.
        return {}


def _save_fingerprints(d: dict, path: str = FINGERPRINTS) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1, ensure_ascii=False)
        os.chmod(path, 0o600)
    except OSError as e:
        _logger.warning("notify: Fingerabdruecke nicht schreibbar: %s", type(e).__name__)


def throttle(text: str, now: "dt.datetime | None" = None,
             path: str = FINGERPRINTS) -> tuple[bool, str, str]:
    """(senden, fingerabdruck, zusatz).

    `zusatz` ist die Zaehlerzeile, die an eine wiederholte Nachricht gehaengt
    wird — sie sagt, wie oft und seit wann, und ist der Grund, warum eine
    gedrosselte Meldung trotzdem vollstaendig informiert.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    fp = fingerprint(text)
    store = _load_fingerprints(path)
    e = store.get(fp)

    def _t(v):
        try:
            return dt.datetime.fromisoformat(str(v))
        except (ValueError, TypeError):
            return None

    zuletzt = _t(e.get("zuletzt_gesendet")) if e else None
    gesehen = _t(e.get("zuletzt_gesehen")) if e else None

    if e is None or gesehen is None or now - gesehen > FORGET_AFTER:
        store[fp] = {"erstmals": now.isoformat(timespec="seconds"),
                     "zuletzt_gesehen": now.isoformat(timespec="seconds"),
                     "zuletzt_gesendet": now.isoformat(timespec="seconds"),
                     "seit_letzter_meldung": 0, "gesamt": 1}
        _prune(store, now)
        _save_fingerprints(store, path)
        return True, fp, ""

    e["zuletzt_gesehen"] = now.isoformat(timespec="seconds")
    e["gesamt"] = int(e.get("gesamt") or 0) + 1
    e["seit_letzter_meldung"] = int(e.get("seit_letzter_meldung") or 0) + 1

    if zuletzt is None or now - zuletzt >= REPEAT_EVERY:
        n = e["seit_letzter_meldung"]
        seit = zuletzt or _t(e.get("erstmals")) or now
        zusatz = f"\n\n({n}x seit {seit:%H:%M}Z, gleichlautend)"
        e["zuletzt_gesendet"] = now.isoformat(timespec="seconds")
        e["seit_letzter_meldung"] = 0
        store[fp] = e
        _prune(store, now)
        _save_fingerprints(store, path)
        return True, fp, zusatz

    store[fp] = e
    _prune(store, now)
    _save_fingerprints(store, path)
    return False, fp, ""


def _prune(store: dict, now: "dt.datetime") -> None:
    """Was 24 h nicht auftrat, faellt raus — und gilt beim naechsten Mal
    wieder als erstmals."""
    tot = []
    for fp, e in store.items():
        try:
            if now - dt.datetime.fromisoformat(str(e["zuletzt_gesehen"])) > FORGET_AFTER:
                tot.append(fp)
        except (ValueError, TypeError, KeyError):
            tot.append(fp)
    for fp in tot:
        store.pop(fp, None)


# Der Grund des letzten Nichtsendens, fuer Aufrufer, die ihn brauchen.
# Prozesslokal und absichtlich schlicht: wer ihn liest, liest ihn direkt nach
# seinem eigenen send_telegram().
_LETZTER_GRUND = ""


def letzter_grund() -> str:
    """Warum der letzte Versand nicht stattfand. Leer, wenn er stattfand."""
    return _LETZTER_GRUND


def _record_sent(kanal: str, erfolg: bool, http_status, fp: str, text: str,
                 grund: str = "", path: str = SENT_LOG) -> None:
    """Eine Zeile je Versuch, auch bei Fehlschlag und bei Unterdrueckung.

    Bis zum 08.10.2026 gab es keine. Ob eine Vorwarnung rausgegangen war,
    liess sich nur daraus ableiten, dass ein Flag gesetzt wurde — und ein
    abgeleiteter Beleg traegt keine Aussage ueber den Versand.
    """
    global _LETZTER_GRUND
    _LETZTER_GRUND = grund if not erfolg else ""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        zeile = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                 "kanal": kanal, "erfolg": bool(erfolg),
                 "http_status": http_status, "fingerabdruck": fp,
                 "text_anfang": text[:80]}
        if grund:
            zeile["grund"] = grund
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
    except OSError as e:
        _logger.warning("notify: Sendeprotokoll nicht schreibbar: %s", type(e).__name__)


def silence_http_request_logs() -> None:
    """Stop the HTTP clients from logging request URLs at INFO.

    httpx logs `HTTP Request: POST <url> "HTTP/1.1 200 OK"` at INFO, and a
    Telegram send puts the bot token in the path. On 2026-09-20 that had written
    the token into logs/watchdog.log 220 times in clear text, because watchdog.py
    combines httpx with basicConfig(level=INFO). Any module that sends through
    this gate is about to put a secret in a URL, so it calls this first.

    Errors still surface: this lowers INFO chatter, not warnings.
    """
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ── Formatting safety ──

def escape_html(text: str) -> str:
    """Escape text for Telegram's HTML parse mode.

    Telegram needs `&`, `<` and `>` escaped and nothing else. Anything
    interpolated into an HTML-mode message goes through this — user agents,
    IP org names, file names and model output all carry characters that would
    otherwise be read as markup and rejected with a 400.

    Markdown mode has the same trap and no safe escape: a lone `_` or `*` from
    an interpolated value breaks the whole message, and Telegram's Markdown v1
    has no escaping rule that covers every case. Use HTML mode, or send plain
    text by leaving parse_mode unset.
    """
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ── Token redaction ──

# `bot<id>:<secret>` as it appears in every Telegram API URL, plus the bare
# `<id>:<secret>` form for a token logged on its own.
_TOKEN_RE = re.compile(r"(bot)?(\d{6,}):([A-Za-z0-9_-]{30,})")
_REDACTION_INSTALLED = False


def _redact(value):
    if isinstance(value, str) and ":" in value:
        return _TOKEN_RE.sub(lambda m: f"{m.group(1) or ''}{m.group(2)}:<redacted>", value)
    return value


def install_token_redaction() -> None:
    """Strip bot tokens out of every log record in this process.

    silence_http_request_logs() stops the one library known to log a token.
    This is the other half: whatever still reaches the logging module gets the
    secret removed rather than the line dropped, so the request stays visible
    and greppable and the token does not.

    Implemented as a record factory rather than a handler filter because
    handlers are usually installed after import — a filter attached now would
    miss everything a later basicConfig() sets up.
    """
    global _REDACTION_INSTALLED
    if _REDACTION_INSTALLED:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            record.msg = _redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: _redact(v) for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(_redact(a) for a in record.args)
        except Exception:  # noqa: BLE001 — logging must never raise
            pass
        return record

    logging.setLogRecordFactory(factory)
    _REDACTION_INSTALLED = True


# Process-wide from the moment anything imports the Telegram gate. Unlike
# silencing a logger this removes no information, so it is safe to do on import.
install_token_redaction()


# -- Aufruf von der Kommandozeile -------------------------------------------
#
#   printf '%s' "$text" | python -m app.notify --channel alerts --stdin
#
# Fuer Shell-Aufrufer. Bis zum 09.10.2026 gab es keinen: deploy.sh baute die
# Telegram-URL selbst und ging damit an der Drosselung und am Sendeprotokoll
# vorbei. Eine Ausnahme haelt sich, solange kein begehbarer Weg daneben liegt.
#
# Der Text kommt ueber stdin, nicht als Argument: eine Deploy-Meldung traegt
# Zeilenumbrueche und fremde Zeichen, und als Argument muesste sie durch jede
# Zitierungsstufe der rufenden Shell. Ueber stdin kommt sie unveraendert an.
#
# Rueckgaben, damit der Aufrufer unterscheiden kann:
#   0  gesendet
#   3  Gate aus (MOLTRUST_NOTIFY nicht gesetzt) — kein Fehler
#   4  gedrosselt, dieselbe Meldung stand schon — kein Fehler
#   5  kein Token oder keine Chat-ID
#   1  Transport gescheitert
#   2  Aufruf falsch

def _cli(argv=None) -> int:
    import argparse
    import sys as _s

    p = argparse.ArgumentParser(
        prog="python -m app.notify",
        description="Eine Telegram-Meldung durch Gate, Drosselung und "
                    "Sendeprotokoll schicken.")
    p.add_argument("--channel", required=True, choices=list(CHANNELS))
    p.add_argument("--text", help="Meldetext. Mehrzeilig besser ueber --stdin.")
    p.add_argument("--stdin", action="store_true",
                   help="Text von der Standardeingabe lesen.")
    p.add_argument("--parse-mode", default=None,
                   choices=["HTML", "Markdown", "MarkdownV2"])
    p.add_argument("--chunk", action="store_true",
                   help="Zu lange Texte an Zeilengrenzen teilen.")
    a = p.parse_args(argv)

    if a.stdin == (a.text is not None):
        print("notify: entweder --text oder --stdin, nicht beides und nicht "
              "keines", file=_s.stderr)
        return 2
    text = _s.stdin.read() if a.stdin else a.text
    if not text or not text.strip():
        print("notify: leerer Text", file=_s.stderr)
        return 2

    if send_telegram(text, channel=a.channel, parse_mode=a.parse_mode,
                     chunk=a.chunk):
        return 0

    grund = letzter_grund()
    if grund:
        print(f"notify: nicht gesendet ({grund})", file=_s.stderr)
    return {"gate": 3, "gedrosselt": 4, "kein token/chat": 5}.get(grund, 1)


if __name__ == "__main__":
    raise SystemExit(_cli())
