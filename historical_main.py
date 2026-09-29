"""Запуск Historical Analyst."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.agents.historical_analyst import HistoricalAnalyst


def main() -> int:
    logger.info("=" * 60)
    logger.info("Historical Analyst запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        notify("📜 HISTORICAL-01", f"❌ Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        notify("📜 HISTORICAL-01", "❌ Нет связи с Supabase")
        return 1

    analyst = HistoricalAnalyst(name="Historical-01")
    result = analyst.run()

    if "error" in result:
        notify("📜 HISTORICAL-01", f"❌ Ошибка: {result['error']}")
    else:
        reports = db.fetch_all(
            """SELECT ticker, trend, sentiment, score, range_position
               FROM historical_reports
               WHERE created_at >= NOW() - INTERVAL '10 minutes'
               ORDER BY score DESC;"""
        )
        if reports:
            lines = []
            for r in reports:
                emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(r["sentiment"], "⚪")
                lines.append(
                    f"{emoji} {r['ticker']}: {r['trend']} | "
                    f"score {r['score']} | позиция {r['range_position']}%"
                )
            notify("📜 HISTORICAL-01", "\n".join(lines))
        else:
            notify("📜 HISTORICAL-01", f"Сохранено {result.get('saved', 0)} отчётов")

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
