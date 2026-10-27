"""One-way Telegram summary (existing MoltrustStats bot). No buttons, no getUpdates."""

from app import notify


notify.silence_http_request_logs()


def send_summary(secrets: dict, text: str) -> None:
    # Through app/notify.py since ops NOTIFY-2 (27.10.2026): gate, throttle and
    # the send log apply here too. `secrets` stays for the callers; notify
    # resolves the token itself.
    notify.send_telegram(text, channel=notify.WORKLOG)


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
    failure / no creds) — captured so a future pass can editMessageText in place.

    Through app/notify.py since ops NOTIFY-2 (27.10.2026): gate, throttle and
    the send log apply to every part. `secrets` stays for the callers."""
    parts = _split(text, 3900)  # headroom under Telegram's 4096 for the part prefix
    n = len(parts)
    ids = []
    for i, part in enumerate(parts, 1):
        out = part if n == 1 else f"({label or 'msg'} {i}/{n})\n{part}"
        mid = notify.send_telegram_message(out, channel=notify.WORKLOG)
        if mid is not None:
            ids.append(mid)
    return ids
