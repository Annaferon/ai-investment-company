"""Notifier — отправка сообщений в Telegram из любого агента."""
import os
from datetime import datetime

import requests

from src.core.logger import get_logger

log = get_logger("notifier")

TG_API = "https://api.telegram.org/bot{token}/sendMessage"


def _is_debug_enabled() -> bool:
    """Проверяем переменную DEBUG_NOTIFY."""
    val = os.getenv("DEBUG_NOTIFY", "0").strip().lower()
    return val in ("1", "true", "yes", "on")


def notify(title: str, body: str, force: bool = False) -> bool:
    """
    Отправить сообщение в Telegram.
    
    Args:
        title: Заголовок (например "🔮 ORACLE")
        body: Основной текст
        force: Если True — отправить даже при DEBUG_NOTIFY=0
    
    Returns:
        True если отправлено успешно, иначе False.
    """
    # Если debug выключен и не force — не отправляем
    if not force and not _is_debug_enabled():
        log.debug(f"DEBUG_NOTIFY=0, пропускаем: {title}")
        return False

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        log.warning("TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы")
        return False

    # Формируем сообщение
    now_msk = datetime.now().strftime("%H:%M МСК")
    text = f"*{title}* — {now_msk}\n\n{body}"

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
