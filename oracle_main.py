"""Запуск Оракула."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.oracle.market_data_fetcher import run_oracle


def _format_prices(prices: dict[str, float], emoji: str, title: str) -> str:
    """Форматируем цены для Telegram."""
    if not prices:
        return f"{emoji} {title}: нет данных"
    lines = [f"{emoji} {title}"]
    for ticker, price in sorted(prices.items()):
        lines.append(f"  • {ticker}: {price:,.2f}")
    return "\n".join(lines)


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

    parts = []
    parts.append(_format_prices(result.get("moex", {}), "📈", "Акции MOEX (₽)"))
    parts.append(_format_prices(result.get("crypto", {}), "₿", "Крипта (USD)"))
    parts.append(_format_prices(result.get("metals", {}), "🥇", "Металлы + курс"))
    parts.append(f"Всего: {result.get('total', 0)} цен")

    if result.get("weekend_mode"):
        parts.append("🏖 Выходной режим (MOEX закрыт)")

    notify("🔮 ORACLE", "\n\n".join(parts))

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
