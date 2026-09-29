"""Запуск News Analyst."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.agents.news_analyst import NewsAnalyst


def _safe(value, default=""):
    """Защита от None."""
    return value if value is not None else default


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

    # result может быть None (на всякий случай)
    result = result if isinstance(result, dict) else {}

    if "error" in result:
        notify("📰 NEWS-01", f"❌ Ошибка: {_safe(result.get('error'), 'unknown')}")
    else:
        sentiment = _safe(result.get("sentiment"), "neutral")
        emoji = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(sentiment, "⚪")

        events = result.get("key_events") or []
        event_lines = []
        for e in events[:7]:
            if isinstance(e, dict):
                event_lines.append(f"  • {_safe(e.get('title'), '?')}")
        events_text = "\n".join(event_lines) if event_lines else "  (нет событий)"

        body = (
            f"{emoji} Sentiment: {sentiment}\n"
            f"Уверенность: {_safe(result.get('confidence'), '?')}\n\n"
            f"📌 Вывод:\n{_safe(result.get('summary'), '(нет вывода)')}\n\n"
            f"🔑 События:\n{events_text}"
        )
        notify("📰 NEWS-01", body)

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
