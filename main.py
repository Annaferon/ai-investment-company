"""Точка входа AI Investment Company."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.agents.trader import Trader


def ensure_account() -> None:
    """Если таблица account пуста — заводим стартовый капитал."""
    row = db.fetch_one("SELECT id FROM account LIMIT 1;")
    if not row:
        logger.info(f"Создаём счёт с капиталом {config.STARTING_CAPITAL} ₽")
        db.execute(
            "INSERT INTO account (cash, initial_capital) VALUES (%s, %s);",
            (config.STARTING_CAPITAL, config.STARTING_CAPITAL),
        )


def main() -> int:
    logger.info("=" * 60)
    logger.info("AI Investment Company запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
        logger.info("Конфигурация OK")
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase. Проверь DATABASE_URL.")
        return 1
    logger.info("Связь с Supabase OK")

    ensure_account()

    trader = Trader(name="Trader-01")
    logger.info("Запуск одного цикла Trader...")
    result = trader.run()

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
