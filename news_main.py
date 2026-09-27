"""Запуск News Analyst."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.agents.news_analyst import NewsAnalyst


def main() -> int:
    logger.info("=" * 60)
    logger.info("News Analyst запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        notify("📰 NEWS-01", f"❌ Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        notify("📰 NEWS-01", "❌ Нет связи с Supabase")
        return 1

    analyst = NewsAnalyst(name="News-01")
    result = analyst.run()

    if "error" in result:
        notify("📰 NEWS-01", f"❌ Ошибка: {result['error']}")
    else:
        sentiment = result.get("sentiment", "?")
        emoji = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(sentiment, "⚪")
        events = result.get("key_events", [])
        events_text = "\n".join(f"  • {e.get('title', '?')}" for e in events[:7])

        body = (
            f"{emoji} Sentiment: {sentiment}\n"
            f"Уверенность: {result.get('confidence', '?')}\n\n"
            f"📌 Вывод:\n{result.get('summary', '')}\n\n"
            f"🔑 События:\n{events_text}"
        )
        notify("📰 NEWS-01", body)

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
