"""Запуск очистки БД."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.cleanup.db_cleaner import run_cleanup


def main() -> int:
    logger.info("=" * 60)
    logger.info("Cleanup запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        notify("🧹 CLEANUP", f"❌ Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        notify("🧹 CLEANUP", "❌ Нет связи с Supabase")
        return 1

    try:
        result = run_cleanup()
    except Exception as e:
        logger.error(f"Cleanup упал: {e}")
        notify("🧹 CLEANUP", f"❌ Ошибка: {e}")
        return 1

    lines = ["🧹 Очистка БД завершена", ""]
    for r in result.get("results", []):
        lines.append(
            f"📊 {r['table']}: удалено {r['deleted']} записей\n"
            f"   Освобождено: {r['freed_mb']} МБ\n"
            f"   Хранение: {r['retention_days']} дней"
        )
    lines.append("")
    lines.append(f"Всего удалено: {result.get('total_deleted', 0)} записей")

    notify("🧹 CLEANUP", "\n".join(lines))

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
