"""Запуск Market Analyst."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.agents.market_analyst import MarketAnalyst


def main() -> int:
    logger.info("=" * 60)
    logger.info("Market Analyst запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        notify("📊 MARKET-01", f"❌ Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        notify("📊 MARKET-01", "❌ Нет связи с Supabase")
        return 1

    analyst = MarketAnalyst(name="Market-01")
    result = analyst.run()

    if "error" in result:
        notify("📊 MARKET-01", f"❌ Ошибка: {result['error']}")
    else:
        trend = result.get("overall_trend", "?")
        trend_emoji = {"bullish": "📈", "bearish": "📉", "sideways": "➡️"}.get(trend, "➡️")
        vol = result.get("volatility_level", "?")
        movers = result.get("key_movers", [])
        movers_text = "\n".join(
            f"  • {m.get('ticker', '?')}: {m.get('change_pct', 0):+.2f}%"
            for m in movers[:7]
        )

        body = (
            f"{trend_emoji} Тренд: {trend}\n"
            f"Волатильность: {vol}\n"
            f"Уверенность: {result.get('confidence', '?')}\n\n"
            f"📌 Вывод:\n{result.get('summary', '')}\n\n"
            f"🔥 Движения:\n{movers_text}"
        )
        notify("📊 MARKET-01", body)

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
