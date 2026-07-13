"""One-way Telegram summary (existing MoltrustStats bot). No buttons, no getUpdates."""
import httpx

from . import config


def send_summary(secrets: dict, text: str) -> None:
    token = secrets.get("TELEGRAM_BOT_TOKEN", "")
    chat = secrets.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return
    try:
        httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   data={"chat_id": chat, "text": text, "disable_web_page_preview": "true"},
                   timeout=15, headers={"User-Agent": config.USER_AGENT})
    except Exception:
        pass


def _split(text: str, limit: int) -> list:
    """Split on line boundaries into <=limit chunks; hard-chunk any over-long line."""
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if buf:
                parts.append(buf); buf = ""
            parts.append(line[:limit]); line = line[limit:]
        if buf and len(buf) + 1 + len(line) > limit:
            parts.append(buf); buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts


def send_message(secrets: dict, text: str, label: str = "") -> list:
    """One-way send of a (possibly long) message, split into <=4096-char parts on
    line boundaries. Numbered when split. No buttons, no getUpdates (stage 1).
    Returns the Telegram message_id(s) of the parts actually sent (empty on
    failure / no creds) — captured so a future pass can editMessageText in place."""
    token = secrets.get("TELEGRAM_BOT_TOKEN", "")
    chat = secrets.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return []
    parts = _split(text, 3900)  # headroom under Telegram's 4096 for the part prefix
    n = len(parts)
    ids = []
    for i, part in enumerate(parts, 1):
        out = part if n == 1 else f"({label or 'msg'} {i}/{n})\n{part}"
        try:
            resp = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              data={"chat_id": chat, "text": out,
                                    "disable_web_page_preview": "true"},
                              timeout=20, headers={"User-Agent": config.USER_AGENT})
            if resp.status_code == 200:
                mid = resp.json().get("result", {}).get("message_id")
                if mid is not None:
                    ids.append(mid)
        except Exception:
            pass
    return ids
