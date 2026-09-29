"""Запуск Macro Economist."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.agents.macro_economist import MacroEconomist


def main() -> int:
    logger.info("=" * 60)
    logger.info("Macro Economist запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        notify("🌍 MACRO-01", f"❌ Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        notify("🌍 MACRO-01", "❌ Нет связи с Supabase")
        return 1

    economist = MacroEconomist(name="Macro-01")
    result = economist.run()

    if "error" in result:
        notify("🌍 MACRO-01", f"❌ Ошибка: {result['error']}")
    else:
        regime = result.get("regime", "neutral")
        emoji = {"tight": "🔴", "neutral": "⚪", "loose": "🟢"}.get(regime, "⚪")

        impls = result.get("implications") or []
        impl_lines = []
        for i in impls[:5]:
            if isinstance(i, dict):
                sector = i.get("sector", "?")
                outlook = i.get("outlook", "?")
                s_emoji = {"positive": "🟢", "neutral": "⚪", "negative": "🔴"}.get(outlook, "⚪")
                impl_lines.append(f"  {s_emoji} {sector}: {outlook}")

        body = (
            f"{emoji} Режим: {regime.upper()}\n"
            f"Уверенность: {result.get('confidence', '?')}\n\n"
            f"📌 {result.get('summary', '')}\n"
        )
        if impl_lines:
            body += "\n🎯 Влияние на сектора:\n" + "\n".join(impl_lines)

        notify("🌍 MACRO-01", body)

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
