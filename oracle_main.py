"""Запуск Оракула."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.oracle.market_data_fetcher import run_oracle


def main() -> int:
    logger.info("=" * 60)
    logger.info("Oracle запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        notify("🔮 ORACLE", f"❌ Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        notify("🔮 ORACLE", "❌ Нет связи с Supabase")
        return 1

    try:
        result = run_oracle()
    except Exception as e:
        logger.error(f"Оракул упал: {e}")
        notify("🔮 ORACLE", f"❌ Оракул упал: {e}")
        return 1

    # Формируем тело уведомления
    body = (
        f"📊 Получено цен:\n"
        f"• MOEX (акции): {result.get('moex', 0)}\n"
        f"• CoinGecko (крипта): {result.get('crypto', 0)}\n"
        f"• ЦБ (металлы + USD/RUB): {result.get('metals', 0)}\n"
        f"\n*Всего: {result.get('total', 0)}*"
    )
    if result.get("weekend_mode"):
        body += "\n\n🏖 Выходной режим (MOEX закрыт)"

    notify("🔮 ORACLE", body)

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
