#!/usr/bin/env python3
"""Woechentliche Erinnerung: Journal-Update faellig.

Ersetzt Crontab-Zeile 78, die den Text selbst per curl schickte und den Token
mit `grep TELEGRAM_BOT_TOKEN ~/.moltrust_secrets | cut -d= -f2` aus der
Secrets-Datei zog — mitten in der Crontab-Zeile. Dieser Programmtext stand in
keiner Datei: weder die URL-Durchsicht vom 07.10. noch die Telegram-Sperre in
tests/test_telegram_eine_sendestelle.py konnten ihn sehen, und ein Sucher, der
das ganze Dateisystem liest, auch nicht.

Ziel unveraendert: der ungeteilte Chat. `chat_id_for(WORKLOG)` faellt darauf
zurueck, solange TELEGRAM_CHAT_ID_WORKLOG nicht gesetzt ist — am 09.10.2026
war nur TELEGRAM_CHAT_ID_ALERTS vergeben, und deren Wert ist derselbe.
"""
import os
import sys

# Drei Ebenen: diese Datei liegt in ops/cron/, nicht in scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from app import notify  # noqa: E402

TEXT = ("Journal-Update faellig — Chats der letzten Woche zusammenfassen")


def main() -> int:
    if notify.send_telegram(TEXT, channel=notify.WORKLOG):
        print("gesendet")
        return 0
    grund = notify.letzter_grund() or "unbekannt"
    print(f"nicht gesendet: {grund}")
    # Gate aus und gedrosselt sind Entscheidungen, kein Fehler.
    return 0 if grund in ("gate", "gedrosselt") else 1


if __name__ == "__main__":
    raise SystemExit(main())
