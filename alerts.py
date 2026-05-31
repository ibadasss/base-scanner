"""
Alert delivery for the Base scanner (currently: Telegram).

Telegram setup (free, 2 minutes):
  1. In Telegram, message @BotFather -> /newbot -> copy the bot TOKEN.
  2. Message your new bot once (say "hi") so it can DM you.
  3. Get your chat id: open
     https://api.telegram.org/bot<TOKEN>/getUpdates  and read result[].message.chat.id
     (or message @userinfobot which replies with your id).
  4. Put both in .env:
       TELEGRAM_BOT_TOKEN=123456:ABC...
       TELEGRAM_CHAT_ID=123456789
"""
from __future__ import annotations

from typing import Tuple

import requests

import config


def telegram_configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def send_telegram(text: str, parse_mode: str = "HTML") -> Tuple[bool, str]:
    """Send a message to the configured Telegram chat. Returns (ok, detail)."""
    if not telegram_configured():
        return False, "Telegram not configured (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)"
    url = config.TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN, method="sendMessage")
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=config.REQUEST_TIMEOUT,
        )
        body = resp.json()
        if resp.ok and body.get("ok"):
            return True, "sent"
        return False, str(body)[:200]
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def test_telegram() -> Tuple[bool, str]:
    return send_telegram(
        "\u2705 <b>Base Scanner</b> connected.\n"
        "Smart Money Monitor consensus alerts will arrive here.")
