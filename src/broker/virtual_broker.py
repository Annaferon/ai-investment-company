"""Виртуальный брокер: исполняет решения Trader."""
from typing import Any, Optional

from src.core.config import config
from src.core.database import db
from src.core.logger import get_logger

log = get_logger("broker")


# Какие активы к какой группе относим
ASSET_TYPES = {
    "SBER": "stock",
    "GAZP": "stock",
    "LKOH": "stock",
    "BTC": "crypto",
    "ETH": "crypto",
    "GOLD": "metal",
    "SILVER": "metal",
}


class VirtualBroker:
    """Исполняет торговые решения на виртуальном рынке."""

    def execute(self, decision: dict[str, Any]) -> dict[str, Any]:
        """Принимает решение Trader и исполняет его."""
        action = str(decision.get("action", "HOLD")).upper()
        ticker = str(decision.get("ticker", "")).upper()
        quantity = int(decision.get("quantity", 0))
        price = float(decision.get("price", 0))

        if action == "HOLD" or not ticker or quantity <= 0:
            log.info("HOLD / пустое решение — ничего не делаем")
            return {"executed": False, "reason": "HOLD"}

        if price <= 0:
            log.warning("Цена не указана — не исполняем")
            return {"executed": False, "reason": "no_price"}

        if action == "BUY":
            return self._buy(ticker, quantity, price)
        if action == "SELL":
            return self._sell(ticker, quantity, price)

        log.warning(f"Неизвестное действие: {action}")
        return {"executed": False, "reason": "unknown_action"}

    # ---------- Служебные ----------

    def _get_cash(self) -> float:
        row = db.fetch_one("SELECT cash FROM account WHERE id = 1;")
        return float(row["cash"]) if row else 0.0

    def _update_cash(self, delta: float) -> None:
        """delta < 0 — списываем, delta > 0 — пополняем."""
        db.execute(
            "UPDATE account SET cash = cash + %s, updated_at = NOW() WHERE id = 1;",
            (delta,),
        )

    def _get_position(self, ticker: str) -> Optional[dict]:
        return db.fetch_one(
            "SELECT ticker, quantity, avg_price FROM portfolio WHERE ticker = %s;",
            (ticker,),
        )

    def _asset_type(self, ticker: str) -> str:
        return ASSET_TYPES.get(ticker, "stock")

    # ---------- Покупка ----------

    def _buy(self, ticker: str, quantity: int, price: float) -> dict:
        total = quantity * price
        commission = total * config.BROKER_COMMISSION
        cost = total + commission
        cash = self._get_cash()

        if cash < cost:
            log.warning(
                f"BUY {ticker}: недостаточно денег "
                f"(нужно {cost:.2f}, есть {cash:.2f})"
            )
            return {"executed": False, "reason": "insufficient_funds"}

        # 1. Сделка
        db.execute(
            """INSERT INTO trades
               (agent_name, ticker, asset_type, action, quantity, price, total, commission)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s);""",
            ("Trader-01", ticker, self._asset_type(ticker), "BUY",
             quantity, price, total, commission),
        )

        # 2. Портфель (усредняем цену)
        position = self._get_position(ticker)
        if position:
            old_qty = float(position["quantity"])
            old_avg = float(position["avg_price"])
            new_qty = old_qty + quantity
            new_avg = (old_qty * old_avg + quantity * price) / new_qty
            db.execute(
                """UPDATE portfolio
                   SET quantity = %s, avg_price = %s, updated_at = NOW()
                   WHERE ticker = %s;""",
                (new_qty, new_avg, ticker),
            )
        else:
            db.execute(
                """INSERT INTO portfolio (ticker, asset_type, quantity, avg_price)
                   VALUES (%s, %s, %s, %s);""",
                (ticker, self._asset_type(ticker), quantity, price),
            )

        # 3. Деньги
        self._update_cash(-cost)

        log.info(
            f"КУПЛЕНО: {ticker} × {quantity} по {price:.2f} ₽ "
            f"(комиссия {commission:.2f})"
        )
        return {
            "executed": True,
            "action": "BUY",
            "ticker": ticker,
            "quantity": quantity,
            "price": price,
            "total": total,
            "commission": commission,
        }

    # ---------- Продажа ----------

    def _sell(self, ticker: str, quantity: int, price: float) -> dict:
        position = self._get_position(ticker)
        if not position or float(position["quantity"]) < quantity:
            log.warning(f"SELL {ticker}: недостаточно актива в портфеле")
            return {"executed": False, "reason": "insufficient_position"}

        total = quantity * price
        commission = total * config.BROKER_COMMISSION
        proceeds = total - commission

        db.execute(
            """INSERT INTO trades
               (agent_name, ticker, asset_type, action, quantity, price, total, commission)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s);""",
            ("Trader-01", ticker, self._asset_type(ticker), "SELL",
             quantity, price, total, commission),
        )

        old_qty = float(position["quantity"])
        new_qty = old_qty - quantity
        if new_qty <= 0:
            db.execute("DELETE FROM portfolio WHERE ticker = %s;", (ticker,))
        else:
            db.execute(
                "UPDATE portfolio SET quantity = %s, updated_at = NOW() WHERE ticker = %s;",
                (new_qty, ticker),
            )

        self._update_cash(proceeds)

        log.info(
            f"ПРОДАНО: {ticker} × {quantity} по {price:.2f} ₽ "
            f"(комиссия {commission:.2f})"
        )
        return {
            "executed": True,
            "action": "SELL",
            "ticker": ticker,
            "quantity": quantity,
            "price": price,
            "total": total,
            "commission": commission,
        }


broker = VirtualBroker()
