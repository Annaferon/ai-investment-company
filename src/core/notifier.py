"""Notifier — отправка сообщений в Telegram из любого агента.
Поддерживает автоматическое разбиение длинных сообщений (лимит 4096)."""
import os
from datetime import datetime, timedelta, timezone

import requests

from src.core.logger import get_logger

log = get_logger("notifier")

TG_API = "https://api.telegram.org/bot{token}/sendMessage"

# МСК = UTC+3
MSK = timezone(timedelta(hours=3))

# Лимит Telegram на одно сообщение
MAX_MSG_LEN = 4096
# Отправляем с запасом, чтобы избежать проблем с эмодзи и форматированием
SAFE_LEN = 3900


def _is_debug_enabled() -> bool:
    val = os.getenv("DEBUG_NOTIFY", "0").strip().lower()
    return val in ("1", "true", "yes", "on")


def _now_msk() -> str:
    return datetime.now(MSK).strftime("%H:%M МСК")


def _split_message(text: str, max_len: int = SAFE_LEN) -> list[str]:
    """Разбивает текст на части, не разрывая абзацы и слова.
    
    Приоритет: абзацы (\n\n) → строки (\n) → предложения (. ) → слова ( ) → жёсткий разрез.
    """
    if len(text) <= max_len:
        return [text]

    parts = []
    remaining = text

    while len(remaining) > max_len:
        # Ищем последний абзац, который влезает
        cut = remaining.rfind("\n\n", 0, max_len)
        if cut == -1:
            # Не нашли абзац — ищем перенос строки
            cut = remaining.rfind("\n", 0, max_len)
        if cut == -1:
            # Не нашли перенос — ищем конец предложения
            cut = remaining.rfind(". ", 0, max_len)
        if cut == -1:
            # Не нашли предложение — ищем пробел
            cut = remaining.rfind(" ", 0, max_len)
        if cut == -1:
            # Совсем не нашли — жёсткий разрез
            cut = max_len

        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()

    if remaining:
        parts.append(remaining)

    return parts


def _send_one(token: str, chat_id: str, text: str) -> bool:
    """Отправить одно сообщение."""
    try:
        resp = requests.post(
            TG_API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            log.error(f"Telegram {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"Ошибка отправки: {e}")
        return False


def notify(title: str, body: str, force: bool = False) -> bool:
    """Отправить сообщение в Telegram (с разбивкой длинных)."""
    if not force and not _is_debug_enabled():
        log.debug(f"DEBUG_NOTIFY=0, пропускаем: {title}")
        return False

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        log.warning("TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы")
        return False

    header = f"{title} — {_now_msk()}\n\n"
    full_text = header + body

    # Разбиваем если слишком длинное
    parts = _split_message(full_text)

    if len(parts) > 1:
        log.info(f"Сообщение {len(full_text)} симв. разбито на {len(parts)} частей")
        # Добавляем нумерацию если частей больше 1
        for i, part in enumerate(parts, 1):
            part_header = f"[{i}/{len(parts)}] " if i > 1 else ""
            if not _send_one(token, chat_id, part_header + part):
                return False
        return True

    return _send_one(token, chat_id, full_text)
