"""Запуск загрузки истории цен."""
import os
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.oracle.history_fetcher import (
    run_history_loader,
    CRYPTO_DAYS_FULL,
    STOCK_DAYS_FULL,
)


def main() -> int:
    logger.info("=" * 60)
    logger.info("History Loader запускается...")
    logger.info("=" * 60)

    try:
        days = int(os.getenv("HISTORY_DAYS", "7"))
    except ValueError:
        days = 7
    logger.info(f"Запрошено дней: {days}")

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        notify("📜 HISTORY", f"❌ Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        notify("📜 HISTORY", "❌ Нет связи с Supabase")
        return 1

    notify("📜 HISTORY", f"🚀 Начинаем загрузку истории за {days} дней...\n"
                        f"Это может занять 60-90 минут.")

    try:
        result = run_history_loader(days)
    except Exception as e:
        logger.error(f"Загрузка истории упала: {e}")
        notify("📜 HISTORY", f"❌ Ошибка: {e}")
        return 1

    body = (
        f"✅ Загрузка истории завершена\n\n"
        f"📅 Запрошено дней: {result.get('days')}\n"
        f"₿ Крипта (макс {CRYPTO_DAYS_FULL}д): {result.get('crypto_saved', 0)} точек\n"
        f"📈 Акции (макс {STOCK_DAYS_FULL}д): {result.get('stock_saved', 0)} точек\n\n"
        f"Всего: {result.get('total', 0)} точек"
    )
    notify("📜 HISTORY", body)

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
