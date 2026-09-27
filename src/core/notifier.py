"""Notifier — отправка сообщений в Telegram из любого агента."""
import os
from datetime import datetime, timedelta, timezone

import requests

from src.core.logger import get_logger

log = get_logger("notifier")

TG_API = "https://api.telegram.org/bot{token}/sendMessage"

# МСК = UTC+3
MSK = timezone(timedelta(hours=3))


def _is_debug_enabled() -> bool:
    val = os.getenv("DEBUG_NOTIFY", "0").strip().lower()
    return val in ("1", "true", "yes", "on")


def _now_msk() -> str:
    """Текущее время в МСК (не UTC)."""
    return datetime.now(MSK).strftime("%H:%M МСК")


def notify(title: str, body: str, force: bool = False) -> bool:
    """Отправить сообщение в Telegram."""
    if not force and not _is_debug_enabled():
        log.debug(f"DEBUG_NOTIFY=0, пропускаем: {title}")
        return False

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        log.warning("TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы")
        return False

    text = f"*{title}* — {_now_msk()}\n\n{body}"

    try:
        resp = requests.post(
            TG_API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        resp.raise_for_status()
        log.info(f"Notifier отправил: {title}")
        return True
    except Exception as e:
        log.error(f"Ошибка отправки в Telegram: {e}")
        return False
