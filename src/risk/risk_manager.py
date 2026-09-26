"""Risk Manager — проверяет и корректирует решения Trader."""
import json
from datetime import datetime, timedelta
from typing import Any

from src.core.config import config
from src.core.database import db
from src.core.logger import get_logger

log = get_logger("risk_manager")


# ---------- Правила ----------
MAX_POSITION_PCT = 0.20      # макс 20% капитала в одну позицию
MIN_TRADE_SIZE = 500.0       # мин 500 ₽ на сделку
STOP_LOSS_PCT = 0.10         # стоп-лосс −10%
TAKE_PROFIT_PCT = 0.20       # тейк-профит +20%
MAX_DATA_AGE_HOURS = 6
MAX_CRYPTO_POSITIONS = 5     # макс 5 крипто-позиций одновременно
MAX_STOCK_POSITIONS = 3      # макс 3 акции одновременно


class RiskManager:
    """Проверяет сделки Trader и корректирует их под лимиты."""

    def __init__(self, name: str = "Risk-01") -> None:
        self.name = name

    # ---------- Утилиты ----------

    def _get_cash(self) -> float:
        row = db.fetch_one("SELECT cash FROM account WHERE id = 1;")
        return float(row["cash"]) if row else 0.0

    def _get_portfolio(self) -> list[dict[str, Any]]:
        return db.fetch_all("SELECT ticker, quantity, avg_price FROM portfolio;")

    def _get_portfolio_by_type(self) -> dict[str, list[dict[str, Any]]]:
        """Портфель сгруппированный по типам активов."""
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

    def _get_fresh_price(self, ticker: str) -> float | None:
        cutoff = datetime.now() - timedelta(hours=MAX_DATA_AGE_HOURS)
        row = db.fetch_one(
            """SELECT price FROM market_prices
               WHERE ticker = %s AND updated_at >= %s
               ORDER BY updated_at DESC LIMIT 1;""",
            (ticker, cutoff),
        )
        return float(row["price"]) if row else None

    def _get_asset_type(self, ticker: str) -> str:
        row = db.fetch_one(
            """SELECT asset_type FROM market_prices
               WHERE ticker = %s ORDER BY updated_at DESC LIMIT 1;""",
            (ticker,),
        )
        return row["asset_type"] if row else "stock"

    def _save_check(
        self,
        ticker: str,
        action: str,
        original_qty: float,
        final_qty: float,
        approved: bool,
        was_adjusted: bool,
        reason: str,
        rules: list[str],
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

    # ---------- Основная проверка ----------

    def check(self, decision: dict[str, Any]) -> dict[str, Any]:
        """
        Проверяет решение Trader.
        Возвращает скорректированное решение либо rejected.
        """
        ticker = str(decision.get("ticker", "")).upper()
        action = str(decision.get("action", "HOLD")).upper()
        quantity = int(decision.get("quantity", 0) or 0)
        price = float(decision.get("price", 0) or 0)

        # CASH / HOLD — пропускаем
        if ticker == "CASH" or action == "HOLD":
            return {**decision, "risk_check": {"status": "skipped"}}

        if quantity <= 0:
            return self._reject(decision, "quantity_zero", ["quantity <= 0"])

        # 1. Свежесть цены
        fresh = self._get_fresh_price(ticker)
        if not fresh or fresh <= 0:
            return self._reject(decision, "no_fresh_price", ["fresh_data"])

        # Используем свежую цену
        price = fresh
        rules: list[str] = []

        # 2. Для BUY — проверяем размер позиции
        if action == "BUY":
            cash = self._get_cash()
            max_cost = cash * MAX_POSITION_PCT
            max_qty = int(max_cost // price)

            if max_qty < 1:
                return self._reject(
                    decision,
                    f"позиция слишком мала (макс {max_cost:.0f} ₽ < 1 шт)",
                    ["min_position"],
                )

            if quantity > max_qty:
                # Корректируем вниз
                new_qty = max_qty
                rules.append(f"position_size_20% ({quantity}→{new_qty})")
                cost = new_qty * price
                if cost < MIN_TRADE_SIZE:
                    return self._reject(
                        decision,
                        f"после корректировки сумма {cost:.0f} ₽ < {MIN_TRADE_SIZE} ₽",
                        ["min_trade_size"],
                    )
                log.info(
                    f"BUY {ticker}: скорректировано {quantity} → {new_qty} "
                    f"({cost:.2f} ₽, лимит {MAX_POSITION_PCT*100:.0f}%)"
                )
                decision = {**decision, "quantity": new_qty, "price": price}
                return self._approve(decision, rules, "Скорректирован размер позиции")

            # Проверка минимальной суммы
            cost = quantity * price
            if cost < MIN_TRADE_SIZE:
                return self._reject(
                    decision,
                    f"сумма {cost:.0f} ₽ < минимума {MIN_TRADE_SIZE} ₽",
                    ["min_trade_size"],
                )

            # Проверка количества позиций по классам
            by_type = self._get_portfolio_by_type()
            atype = self._get_asset_type(ticker)
            existing = {p["ticker"] for p in by_type.get(atype, [])}

            if ticker not in existing:
                if atype == "crypto" and len(by_type.get("crypto", [])) >= MAX_CRYPTO_POSITIONS:
                    return self._reject(
                        decision,
                        f"уже {MAX_CRYPTO_POSITIONS} крипто-позиций — лимит",
                        ["max_crypto_positions"],
                    )
                if atype == "stock" and len(by_type.get("stock", [])) >= MAX_STOCK_POSITIONS:
                    return self._reject(
                        decision,
                        f"уже {MAX_STOCK_POSITIONS} акций — лимит",
                        ["max_stock_positions"],
                    )

            return self._approve(decision, rules, "Все проверки пройдены")

        # 3. Для SELL — проверяем, есть ли позиция и есть ли причина
        if action == "SELL":
            pos = self._get_position(ticker)
            if not pos or float(pos["quantity"]) < quantity:
                return self._reject(
                    decision,
                    f"нет позиции {ticker} или недостаточно для продажи",
                    ["no_position"],
                )

            avg_price = float(pos["avg_price"])
            pnl_pct = (price - avg_price) / avg_price if avg_price > 0 else 0

            # Разрешаем SELL при стоп-лоссе / тейк-профите
            if pnl_pct <= -STOP_LOSS_PCT:
                rules.append(f"stop_loss ({pnl_pct*100:.1f}%)")
                return self._approve(decision, rules, "Сработал стоп-лосс")
            if pnl_pct >= TAKE_PROFIT_PCT:
                rules.append(f"take_profit ({pnl_pct*100:.1f}%)")
                return self._approve(decision, rules, "Сработал тейк-профит")

            # Иначе — проверяем, что Trader обосновал
            reasoning = str(decision.get("reasoning", "")).lower()
            keywords = ["стоп", "фикс", "продаж", "выход", "закрыва", "убыт", "риск"]
            if any(k in reasoning for k in keywords):
                rules.append("reasoned_exit")
                return self._approve(decision, rules, "Обоснованный выход")

            return self._reject(
                decision,
                f"SELL без стоп-лосса/тейк-профита (P/L {pnl_pct*100:+.1f}%) и без обоснования",
                ["unjustified_sell"],
            )

        return self._reject(decision, f"неизвестное действие: {action}", ["unknown_action"])

    # ---------- Хелперы ----------

    def _approve(self, decision: dict, rules: list[str], reason: str) -> dict:
        ticker = decision.get("ticker", "?")
        action = decision.get("action", "?")
        qty = float(decision.get("quantity", 0))
        was_adjusted = bool(rules and any("position_size" in r for r in rules))

        self._save_check(
            ticker=ticker, action=action,
            original_qty=qty, final_qty=float(decision.get("quantity", qty)),
            approved=True, was_adjusted=was_adjusted,
            reason=reason, rules=rules,
        )
        log.info(f"APPROVED: {action} {ticker} — {reason}")

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


# Синглтон
risk_manager = RiskManager()
