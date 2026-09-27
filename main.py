"""Точка входа AI Investment Company."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.agents.trader import Trader


def ensure_account() -> None:
    row = db.fetch_one("SELECT id FROM account LIMIT 1;")
    if not row:
        logger.info(f"Создаём счёт с капиталом {config.STARTING_CAPITAL} ₽")
        db.execute(
            "INSERT INTO account (cash, initial_capital) VALUES (%s, %s);",
            (config.STARTING_CAPITAL, config.STARTING_CAPITAL),
        )


def main() -> int:
    logger.info("=" * 60)
    logger.info("AI Investment Company запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        notify("💼 TRADER-01", f"❌ Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        notify("💼 TRADER-01", "❌ Нет связи с Supabase")
        return 1
    logger.info("Связь с Supabase OK")

    ensure_account()

    trader = Trader(name="Trader-01")
    logger.info("Запуск одного цикла Trader...")
    result = trader.run()

    # Формируем сообщение
    if "error" in result:
        notify("💼 TRADER-01", f"❌ Ошибка: {result['error']}")
    else:
        action = result.get("action", "?")
        ticker = result.get("ticker", "?")
        confidence = result.get("confidence", "?")
        reasoning = result.get("reasoning", "")
        execution = result.get("execution", {})

        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(action, "⚪")

        body = (
            f"{emoji} Решение: {action} {ticker}\n"
            f"Уверенность: {confidence}\n\n"
            f"🧠 {reasoning}\n"
        )

        if execution.get("executed"):
            body += (
                f"\n✅ Исполнено:\n"
                f"  • Кол-во: {execution.get('quantity')}\n"
                f"  • Цена: {execution.get('price', 0):,.2f} ₽\n"
                f"  • Сумма: {execution.get('total', 0):,.2f} ₽\n"
                f"  • Комиссия: {execution.get('commission', 0):,.2f} ₽"
            )
        elif execution.get("reason") == "cash_hold":
            body += "\n💤 Остаёмся в кэше"
        elif execution.get("reason") == "risk_rejected":
            body += f"\n⚠️ Risk Manager отклонил: {execution.get('detail', '')}"
        elif execution.get("reason") == "no_price":
            body += "\n⚠️ Нет цены — сделка не исполнена"

        # Портфель и капитал
        acc = db.fetch_one("SELECT cash, initial_capital FROM account WHERE id = 1;")
        if acc:
            cash = float(acc["cash"])
            initial = float(acc["initial_capital"])
            body += f"\n\n💰 Кэш: {cash:,.2f} ₽ (от {initial:,.0f} ₽)"

        notify("💼 TRADER-01", body)

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
