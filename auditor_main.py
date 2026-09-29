"""Запуск Auditor.
Ежедневно — оценка сигналов. Воскресенье — недельный отчёт."""
import sys
from datetime import datetime, timedelta, timezone

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.agents.auditor import Auditor

MSK = timezone(timedelta(hours=3))


def main() -> int:
    logger.info("=" * 60)
    logger.info("Auditor запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        return 1

    auditor = Auditor(name="Auditor-01")

    # 1. Ежедневный сбор
    try:
        daily = auditor.run_daily()
        logger.info(f"Daily результат: {daily}")
    except Exception as e:
        logger.error(f"Ошибка daily: {e}")
        notify("📊 AUDITOR", f"❌ Ошибка daily: {e}")
        return 1

    # 2. Воскресенье — недельный отчёт
    now = datetime.now(MSK)
    is_sunday = now.weekday() == 6

    if is_sunday:
        logger.info("Воскресенье — формируем недельный отчёт")
        try:
            weekly = auditor.run_weekly()
            report = weekly.get("report", "")
            if report:
                notify("📊 AUDITOR", report, force=True)
                logger.info("Отчёт отправлен в Telegram")
        except Exception as e:
            logger.error(f"Ошибка weekly: {e}")
            notify("📊 AUDITOR", f"❌ Ошибка weekly: {e}")
            return 1

    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
