"""Запуск News Analyst."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.agents.news_analyst import NewsAnalyst


def main() -> int:
    logger.info("=" * 60)
    logger.info("News Analyst запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        return 1

    analyst = NewsAnalyst(name="News-01")
    result = analyst.run()

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
