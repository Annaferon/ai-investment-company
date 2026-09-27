"""Запуск Stock Analyst."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.agents.stock_analyst import StockAnalyst


def main() -> int:
    logger.info("=" * 60)
    logger.info("Stock Analyst запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        notify("📈 STOCK-01", f"❌ Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        notify("📈 STOCK-01", "❌ Нет связи с Supabase")
        return 1

    analyst = StockAnalyst(name="Stock-01")
    result = analyst.run()

    status = result.get("status")
    if status == "skipped_weekend":
        notify("📈 STOCK-01", "🏖 Выходной — Stock Analyst отдыхает")
    elif "error" in result:
        notify("📈 STOCK-01", f"❌ Ошибка: {result['error']}")
    else:
        # Получим свежие отчёты
        reports = db.fetch_all(
            """SELECT ticker, sentiment, score, reasoning
               FROM stock_reports
               WHERE created_at >= NOW() - INTERVAL '10 minutes'
               ORDER BY score DESC;"""
        )
        if reports:
            lines = []
            for r in reports:
                emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(r["sentiment"], "⚪")
                lines.append(
                    f"{emoji} {r['ticker']}: {r['sentiment']} (score {r['score']})\n"
                    f"   {r['reasoning'][:120]}"
                )
            notify("📈 STOCK-01", "\n\n".join(lines))
        else:
            notify("📈 STOCK-01", f"Сохранено {result.get('saved', 0)} отчётов")

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
