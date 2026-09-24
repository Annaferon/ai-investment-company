"""Точка входа AI Investment Company.

Этот файл запускает одного агента — Trader-01 — для теста.
Позже здесь будет оркестратор всех агентов.
"""
import sys
from time import sleep

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.agents.trader import Trader


def main() -> int:
    """Главный цикл. Пока — один запуск Trader для проверки."""
    logger.info("=" * 60)
    logger.info("AI Investment Company запускается...")
    logger.info("=" * 60)
    
    # 1. Проверяем конфиг
    try:
        config.validate()
        logger.info("Конфигурация OK")
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        return 1
    
    # 2. Проверяем базу данных
    if not db.health_check():
        logger.error("Нет связи с Supabase. Проверь DATABASE_URL.")
        return 1
    logger.info("Связь с Supabase OK")
    
    # 3. Создаём Trader-01
    trader = Trader(name="Trader-01")
    
    # 4. Запускаем один цикл работы
    logger.info("Запуск одного цикла Trader...")
    result = trader.run()
    
    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
