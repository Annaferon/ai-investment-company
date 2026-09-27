"""Запуск Оракула. Уведомление в Telegram при каждом запуске."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.oracle.market_data_fetcher import run_oracle


def _format_prices(prices: dict[str, float], emoji: str, title: str) -> str:
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
        notify("🔮 ORACLE", f"❌ Ошибка конфигурации: {e}", force=True)
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        notify("🔮 ORACLE", "❌ Нет связи с Supabase", force=True)
        return 1

    try:
        result = run_oracle()
    except Exception as e:
        logger.error(f"Оракул упал: {e}")
        notify("🔮 ORACLE", f"❌ Оракул упал: {e}", force=True)
        return 1

    # ВСЕГДА отправляем уведомление
    parts = []
    parts.append(_format_prices(result.get("moex", {}), "📈", "Акции MOEX (₽)"))
    parts.append(_format_prices(result.get("crypto", {}), "₿", "Крипта (USD)"))
    parts.append(_format_prices(result.get("metals", {}), "🥇", "Металлы + курс"))
    parts.append(f"Всего: {result.get('total', 0)} цен")

    if not result.get("moex_open"):
        parts.append("🏖 MOEX закрыт — акции пропущены")

    notify("🔮 ORACLE", "\n\n".join(parts))

    logger.info("=" * 60)
    logger.info(f"Результат: total={result.get('total', 0)}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
