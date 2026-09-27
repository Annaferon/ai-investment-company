"""Risk Manager — проверяет и корректирует решения Trader. Поддержка дробных количеств."""
import json
from datetime import datetime, timedelta
from typing import Any

from src.core.config import config
from src.core.database import db
from src.core.logger import get_logger

log = get_logger("risk_manager")


# ---------- Правила ----------
MAX_POSITION_PCT = 0.20
MIN_TRADE_SIZE = 500.0
STOP_LOSS_PCT = 0.10
TAKE_PROFIT_PCT = 0.20
MAX_DATA_AGE_HOURS = 6
MAX_CRYPTO_POSITIONS = 5
MAX_STOCK_POSITIONS = 3

# Точность округления
PRECISION = {
    "crypto": 8,
    "stock": 0,
    "metal": 2,
}


class RiskManager:
    def __init__(self, name: str = "Risk-01") -> None:
        self.name = name

    def _get_cash(self) -> float:
        row = db.fetch_one("SELECT cash FROM account WHERE id = 1;")
        return float(row["cash"]) if row else 0.0

    def _get_portfolio_by_type(self) -> dict[str, list[dict[str, Any]]]:
        positions = db.fetch_all(
            """SELECT p.ticker, p.quantity, p.avg_price, m.asset_type
               FROM portfolio p
               LEFT JOIN (
                   SELECT DISTINCT ON (ticker) ticker, asset_type
                   FROM market_prices ORDER BY ticker, updated_at DESC
               ) m ON p.ticker = m.ticker;"""
        )
        result: dict[str, list[dict[str, Any]]] = {"stock": [], "crypto": [], "metal": []}
        for p in positions:
            atype = p.get("asset_type") or "stock"
            result.setdefault(atype, []).append(p)
        return result

    def _get_position(self, ticker: str) -> dict[str, Any] | None:
        return db.fetch_one(
            "SELECT ticker, quantity, avg_price FROM portfolio WHERE ticker = %s;",
            (ticker,),
        )

    def _has_fresh_price(self, ticker: str) -> bool:
        """Проверяем ТОЛЬКО свежесть — цену не возвращаем."""
        cutoff = datetime.now() - timedelta(hours=MAX_DATA_AGE_HOURS)
        row = db.fetch_one(
            """SELECT id FROM market_prices
               WHERE ticker = %s AND updated_at >= %s
               LIMIT 1;""",
            (ticker, cutoff),
        )
        return row is not None

    def _get_asset_type(self, ticker: str) -> str:
        row = db.fetch_one(
            """SELECT asset_type FROM market_prices
               WHERE ticker = %s ORDER BY updated_at DESC LIMIT 1;""",
            (ticker,),
        )
        return row["asset_type"] if row else "stock"

    def _round_quantity(self, quantity: float, asset_type: str) -> float:
        digits = PRECISION.get(asset_type, 0)
        return round(quantity, digits)

    def _save_check(
        self, ticker: str, action: str,
        original_qty: float, final_qty: float,
        approved: bool, was_adjusted: bool,
        reason: str, rules: list[str],
    ) -> None:
        db.execute(
            """INSERT INTO risk_checks
               (agent_name, ticker, action, original_quantity, final_quantity,
                approved, was_adjusted, reason, rules_triggered)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);""",
            (
                self.name, ticker, action,
                original_qty, final_qty,
                approved, was_adjusted,
                reason,
                json.dumps(rules, ensure_ascii=False),
            ),
        )

    def check(self, decision: dict[str, Any]) -> dict[str, Any]:
        ticker = str(decision.get("ticker", "")).upper()
        action = str(decision.get("action", "HOLD")).upper()
        quantity = float(decision.get("quantity", 0) or 0)
        # ВАЖНО: цена уже в рублях, конвертацию сделал Trader
        price = float(decision.get("price", 0) or 0)

        if ticker == "CASH" or action == "HOLD":
            return {**decision, "risk_check": {"status": "skipped"}}

        # 1. Свежесть данных
        if not self._has_fresh_price(ticker):
            return self._reject(decision, "no_fresh_price", ["fresh_data"])

        # 2. Цена должна быть передана Trader
        if price <= 0:
            return self._reject(decision, "no_price_from_trader", ["missing_price"])

        asset_type = self._get_asset_type(ticker)
        rules: list[str] = []

        # 3. BUY
        if action == "BUY":
            cash = self._get_cash()
            max_cost = cash * MAX_POSITION_PCT
            max_qty_raw = max_cost / price
            max_qty = self._round_quantity(max_qty_raw, asset_type)

            # Если Trader не указал quantity — авто из лимита
            if quantity <= 0:
                quantity = max_qty
                rules.append(f"auto_quantity ({quantity})")
                log.info(f"BUY {ticker}: quantity=0 → авто {quantity}")

            # Обрезаем по лимиту
            if quantity > max_qty:
                new_qty = max_qty
                rules.append(f"position_size_20% ({quantity}→{new_qty})")
                quantity = new_qty
                log.info(f"BUY {ticker}: скорректировано до {quantity}")

            # Пересчёт стоимости
            cost = quantity * price

            # Минимальный размер
            if cost < MIN_TRADE_SIZE:
                min_qty = self._round_quantity(MIN_TRADE_SIZE / price, asset_type)
                # Округляем вверх чтобы дойти до минимума
                if min_qty * price < MIN_TRADE_SIZE:
                    digits = PRECISION.get(asset_type, 0)
                    min_qty = round(MIN_TRADE_SIZE / price + 10**(-digits), digits)

                if min_qty > max_qty:
                    return self._reject(
                        decision,
                        f"минимум {MIN_TRADE_SIZE}₽ не влезает в 20% ({max_cost:.0f}₽)",
                        ["min_trade_size"],
                    )
                quantity = min_qty
                rules.append(f"min_trade_size ({quantity})")

            if quantity <= 0:
                return self._reject(decision, "quantity=0 после расчёта", ["zero_quantity"])

            decision = {**decision, "quantity": quantity, "price": price}

            # Лимиты классов
            by_type = self._get_portfolio_by_type()
            existing = {p["ticker"] for p in by_type.get(asset_type, [])}

            if ticker not in existing:
                if asset_type == "crypto" and len(by_type.get("crypto", [])) >= MAX_CRYPTO_POSITIONS:
                    return self._reject(decision, f"лимит {MAX_CRYPTO_POSITIONS} крипто", ["max_crypto"])
                if asset_type == "stock" and len(by_type.get("stock", [])) >= MAX_STOCK_POSITIONS:
                    return self._reject(decision, f"лимит {MAX_STOCK_POSITIONS} акций", ["max_stocks"])

            was_adjusted = bool(rules)
            return self._approve(
                decision, rules,
                "Скорректирован размер позиции" if was_adjusted else "Все проверки пройдены",
                was_adjusted=was_adjusted,
            )

        # 4. SELL
        if action == "SELL":
            pos = self._get_position(ticker)
            if not pos:
                return self._reject(decision, f"нет позиции {ticker}", ["no_position"])

            pos_qty = float(pos["quantity"])

            if quantity <= 0 or quantity > pos_qty:
                quantity = pos_qty
                rules.append(f"auto_sell_all ({quantity})")

            avg_price = float(pos["avg_price"])
            pnl_pct = (price - avg_price) / avg_price if avg_price > 0 else 0

            if pnl_pct <= -STOP_LOSS_PCT:
                rules.append(f"stop_loss ({pnl_pct*100:.1f}%)")
                return self._approve(
                    {**decision, "quantity": quantity, "price": price},
                    rules, "Сработал стоп-лосс", was_adjusted=False,
                )
            if pnl_pct >= TAKE_PROFIT_PCT:
                rules.append(f"take_profit ({pnl_pct*100:.1f}%)")
                return self._approve(
                    {**decision, "quantity": quantity, "price": price},
                    rules, "Сработал тейк-профит", was_adjusted=False,
                )

            reasoning = str(decision.get("reasoning", "")).lower()
            keywords = ["стоп", "фикс", "продаж", "выход", "закрыва", "убыт", "риск"]
            if any(k in reasoning for k in keywords):
                rules.append("reasoned_exit")
                return self._approve(
                    {**decision, "quantity": quantity, "price": price},
                    rules, "Обоснованный выход", was_adjusted=False,
                )

            return self._reject(
                decision,
                f"SELL без стоп-лосса/тейк-профита (P/L {pnl_pct*100:+.1f}%)",
                ["unjustified_sell"],
            )

        return self._reject(decision, f"неизвестное действие: {action}", ["unknown_action"])

    def _approve(self, decision: dict, rules: list[str], reason: str, was_adjusted: bool = False) -> dict:
        ticker = decision.get("ticker", "?")
        action = decision.get("action", "?")
        qty = float(decision.get("quantity", 0))

        self._save_check(
            ticker=ticker, action=action,
            original_qty=qty, final_qty=qty,
            approved=True, was_adjusted=was_adjusted,
            reason=reason, rules=rules,
        )
        log.info(f"APPROVED: {action} {ticker} qty={qty} — {reason}")

        return {
            **decision,
            "risk_check": {
                "status": "approved",
                "was_adjusted": was_adjusted,
                "reason": reason,
                "rules": rules,
            },
        }

    def _reject(self, decision: dict, reason: str, rules: list[str]) -> dict:
        ticker = decision.get("ticker", "?")
        action = decision.get("action", "?")
        qty = float(decision.get("quantity", 0))

        self._save_check(
            ticker=ticker, action=action,
            original_qty=qty, final_qty=0,
            approved=False, was_adjusted=False,
            reason=reason, rules=rules,
        )
        log.warning(f"REJECTED: {action} {ticker} — {reason}")

        return {
            **decision,
            "risk_check": {
                "status": "rejected",
                "reason": reason,
                "rules": rules,
            },
        }


risk_manager = RiskManager()
