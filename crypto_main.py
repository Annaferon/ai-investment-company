"""Запуск Crypto Analyst."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.agents.crypto_analyst import CryptoAnalyst


def main() -> int:
    logger.info("=" * 60)
    logger.info("Crypto Analyst запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        notify("₿ CRYPTO-01", f"❌ Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        notify("₿ CRYPTO-01", "❌ Нет связи с Supabase")
        return 1

    analyst = CryptoAnalyst(name="Crypto-01")
    result = analyst.run()

    if "error" in result:
        notify("₿ CRYPTO-01", f"❌ Ошибка: {result['error']}")
    else:
        reports = db.fetch_all(
            """SELECT ticker, sentiment, score, reasoning
               FROM crypto_reports
               WHERE created_at >= NOW() - INTERVAL '10 minutes'
               ORDER BY score DESC;"""
        )
        if reports:
            lines = []
            for r in reports:
                emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(r["sentiment"], "⚪")
                lines.append(
                    f"{emoji} {r['ticker']}: {r['sentiment']} (score {r['score']})\n"
                    f"{r['reasoning']}"
                )
            notify("₿ CRYPTO-01", "\n\n".join(lines))
        else:
            notify("₿ CRYPTO-01", f"Сохранено {result.get('saved', 0)} отчётов")

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
