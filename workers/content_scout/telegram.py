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
