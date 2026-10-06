"""Risk Manager — комплексная защита капитала.
Tier-система, целевые пропорции, cooldown, дневной лимит."""
import json
from datetime import datetime, timedelta
from typing import Any, Optional

from src.core.database import db
from src.core.logger import get_logger

log = get_logger("risk_manager")


# ---------- Глобальные лимиты ----------
MAX_DAILY_LOSS_PCT = 5.0
MIN_TRADE_SIZE = 500.0
MAX_DATA_AGE_HOURS = 6

# ---------- Целевые пропорции портфеля (по капиталу) ----------
TARGET_STOCK_PCT = 0.40      # 40% в акциях РФ
TARGET_METALS_PCT = 0.10     # 10% в металлах
TARGET_CRYPTO_PCT = 0.30     # 30% в крипте
TARGET_CASH_PCT = 0.20       # 20% в кэше

# ---------- Буферы (макс. превышение целевой доли) ----------
STOCK_MAX_PCT = 0.50         # Макс 50% в акциях
METALS_MAX_PCT = 0.15        # Макс 15% в металлах
CRYPTO_MAX_PCT = 0.40        # Макс 40% в крипте
CASH_MIN_PCT = 0.10          # Мин 10% кэша после сделки

# ---------- Лимиты количества позиций ----------
MAX_STOCK_POSITIONS = 8
MAX_CRYPTO_POSITIONS = 6
MAX_METALS_POSITIONS = 2

# ---------- Tier-система (для крипты) ----------
TIER1 = {"BTC", "ETH"}
TIER2 = {"SOL", "BNB", "LINK"}
TIER3 = {"DOGE", "SHIB", "PEPE", "WIF", "BONK"}

# Лимиты позиций по тирам (в % от капитала)
TIER1_MAX_POSITION_PCT = 0.15
TIER2_MAX_POSITION_PCT = 0.05
TIER3_MAX_POSITION_PCT = 0.03

# Стоп-лоссы
TIER1_STOP_LOSS_PCT = 0.30
TIER2_STOP_LOSS_PCT = 0.15
TIER3_STOP_LOSS_PCT = 0.10

# Тейк-профиты
TIER1_TAKE_PROFIT_PCT = 0.20
TIER2_TAKE_PROFIT_PCT = 0.25
TIER3_TAKE_PROFIT_PCT = 0.30

TIER1_PEAK_TO_SELL = 85.0

# ---------- Корреляция ----------
CORRELATION_GROUPS = {
    "bitcoin_ecosystem": {"BTC", "ETH", "SOL", "BNB"},
    "memecoins": {"DOGE", "SHIB", "PEPE", "WIF", "BONK"},
}

# ---------- Cooldown ----------
COOLDOWN_HOURS = 24

# Классификация тикеров по классам
STOCK_TICKERS = {"SBER", "GAZP", "LKOH", "GMKN", "ROSN", "NVTK", "TATN", "SNGS", "PLZL", "MTSS"}
METAL_TICKERS = {"GOLD", "SILVER"}


def _tier(ticker: str) -> int:
    if ticker in TIER1:
        return 1
    if ticker in TIER2:
        return 2
    if ticker in TIER3:
        return 3
    return 2  # акции и металлы как Tier 2


def _asset_class(ticker: str) -> str:
    """Возвращает класс актива: stock / metal / crypto / unknown."""
    if ticker in STOCK_TICKERS:
        return "stock"
    if ticker in METAL_TICKERS:
        return "metal"
    if ticker in (TIER1 | TIER2 | TIER3):
        return "crypto"
    return "unknown"


class RiskManager:
    def __init__(self, name: str = "Risk-01") -> None:
        self.name = name

    # ---------- Вспомогательные ----------

    def _get_cash(self) -> float:
        row = db.fetch_one("SELECT cash FROM account WHERE id = 1;")
        return float(row["cash"]) if row else 0.0

    def _get_position_price(self, ticker: str) -> Optional[float]:
        row = db.fetch_one(
            """SELECT price FROM market_prices
               WHERE ticker = %s ORDER BY updated_at DESC LIMIT 1;""",
            (ticker,),
        )
        return float(row["price"]) if row else None

    def _get_capital(self) -> float:
        acc = db.fetch_one("SELECT cash FROM account WHERE id = 1;")
        if not acc:
            return 0.0
        cash = float(acc["cash"])
        positions = db.fetch_all("SELECT ticker, quantity, avg_price FROM portfolio;")
        assets = 0.0
        for p in positions:
            price = self._get_position_price(p["ticker"])
            if price is None:
                price = float(p["avg_price"])
            assets += float(p["quantity"]) * price
        return cash + assets

    def _get_position(self, ticker: str) -> Optional[dict]:
        return db.fetch_one(
            "SELECT ticker, quantity, avg_price FROM portfolio WHERE ticker = %s;",
            (ticker,),
        )

    def _has_fresh_price(self, ticker: str) -> bool:
        cutoff = datetime.now() - timedelta(hours=MAX_DATA_AGE_HOURS)
        row = db.fetch_one(
            """SELECT id FROM market_prices
               WHERE ticker = %s AND updated_at >= %s LIMIT 1;""",
            (ticker, cutoff),
        )
        return row is not None

    def _get_historical_position(self, ticker: str) -> Optional[float]:
        row = db.fetch_one(
            """SELECT range_position FROM historical_reports
               WHERE ticker = %s ORDER BY created_at DESC LIMIT 1;""",
            (ticker,),
        )
        return float(row["range_position"]) if row else None

    def _is_cooling_down(self, ticker: str) -> bool:
        cutoff = datetime.now() - timedelta(hours=COOLDOWN_HOURS)
        row = db.fetch_one(
            """SELECT id FROM trades
               WHERE ticker = %s AND action = 'SELL' AND created_at >= %s
               ORDER BY created_at DESC LIMIT 1;""",
            (ticker, cutoff),
        )
        return row is not None

    def _get_portfolio_positions(self) -> list[dict]:
        return db.fetch_all("SELECT ticker, quantity FROM portfolio;")

    def _position_value(self, ticker: str, qty: float) -> float:
        price = self._get_position_price(ticker)
        return (price or 0.0) * qty

    def _get_class_exposure(self, asset_class: str) -> dict:
        """Возвращает долю класса в капитале и количество позиций."""
        positions = self._get_portfolio_positions()
        total = 0.0
        count = 0
        for p in positions:
            cls = _asset_class(p["ticker"])
            if cls == asset_class:
                total += self._position_value(p["ticker"], float(p["quantity"]))
                count += 1
        return {"value": total, "count": count}

    def _get_daily_loss(self) -> float:
        capital_now = self._get_capital()
        acc = db.fetch_one(
            """SELECT initial_capital,
                      COALESCE(total_deposits, 0) AS deposits
               FROM account WHERE id = 1;"""
        )
        if not acc:
            return 0.0
        invested = float(acc["initial_capital"]) + float(acc["deposits"])
        if invested <= 0:
            return 0.0
        return (capital_now - invested) / invested * 100

    # ---------- Сохранение проверки ----------

    def _save_check(self, **kwargs):
        db.execute(
            """INSERT INTO risk_checks
               (agent_name, ticker, action, original_quantity, final_quantity,
                approved, was_adjusted, reason, rules_triggered,
                tier, pnl_pct_at_check, daily_loss_pct, was_dca)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);""",
            (
                self.name,
                kwargs.get("ticker", "?"),
                kwargs.get("action", "?"),
                kwargs.get("original_qty", 0),
                kwargs.get("final_qty", 0),
                kwargs.get("approved", False),
                kwargs.get("was_adjusted", False),
                kwargs.get("reason", ""),
                json.dumps(kwargs.get("rules", []), ensure_ascii=False),
                kwargs.get("tier", 2),
                kwargs.get("pnl_pct", 0.0),
                kwargs.get("daily_loss", 0.0),
                kwargs.get("was_dca", False),
            ),
        )

    # ---------- Основная проверка ----------

    def check(self, decision: dict[str, Any]) -> dict[str, Any]:
        ticker = str(decision.get("ticker", "")).upper()
        action = str(decision.get("action", "HOLD")).upper()
        quantity = float(decision.get("quantity", 0) or 0)
        price = float(decision.get("price", 0) or 0)

        if ticker == "CASH" or action == "HOLD":
            return {**decision, "risk_check": {"status": "skipped"}}

        if not self._has_fresh_price(ticker):
            return self._reject(decision, "no_fresh_price", ["fresh_data"])

        if price <= 0:
            return self._reject(decision, "no_price", ["missing_price"])

        asset_class = _asset_class(ticker)
        tier = _tier(ticker)
        capital = self._get_capital()
        daily_loss = self._get_daily_loss()

        if daily_loss <= -MAX_DAILY_LOSS_PCT:
            return self._reject(
                decision,
                f"Дневной убыток {daily_loss:.2f}% — торговля заблокирована",
                ["daily_loss_limit"],
                tier=tier, daily_loss=daily_loss,
            )

        rules: list[str] = []

        # ---------- BUY ----------
        if action == "BUY":
            if self._is_cooling_down(ticker):
                return self._reject(
                    decision, f"{ticker} в cooldown после продажи",
                    ["cooldown"], tier=tier,
                )

            # 1. Проверка целевого класса
            class_data = self._get_class_exposure(asset_class)
            class_value = class_data["value"]
            class_count = class_data["count"]

            if asset_class == "stock":
                max_pct = STOCK_MAX_PCT
                max_positions = MAX_STOCK_POSITIONS
            elif asset_class == "metal":
                max_pct = METALS_MAX_PCT
                max_positions = MAX_METALS_POSITIONS
            elif asset_class == "crypto":
                max_pct = CRYPTO_MAX_PCT
                max_positions = MAX_CRYPTO_POSITIONS
            else:
                max_pct = 0.10
                max_positions = 5

            # 2. Лимит количества позиций в классе
            existing_tickers = {p["ticker"] for p in self._get_portfolio_positions()}
            if ticker not in existing_tickers and class_count >= max_positions:
                return self._reject(
                    decision,
                    f"лимит {max_positions} позиций в классе {asset_class}",
                    [f"max_{asset_class}_positions"],
                    tier=tier,
                )

            # 3. Лимит на позицию (Tier для крипты, 5% для остальных)
            if asset_class == "crypto":
                if tier == 1:
                    max_pos_pct = TIER1_MAX_POSITION_PCT
                elif tier == 2:
                    max_pos_pct = TIER2_MAX_POSITION_PCT
                else:
                    max_pos_pct = TIER3_MAX_POSITION_PCT
            elif asset_class == "stock":
                max_pos_pct = 0.05  # 5% на акцию
            elif asset_class == "metal":
                max_pos_pct = 0.07  # 7% на металл
            else:
                max_pos_pct = 0.05

            max_position_value = capital * max_pos_pct
            precision = 8 if asset_class == "crypto" else 0

            if quantity <= 0:
                quantity = round(max_position_value / price, precision)
                rules.append(f"auto_quantity ({quantity})")

            position_cost = quantity * price
            if position_cost > max_position_value:
                quantity = round(max_position_value / price, precision)
                position_cost = quantity * price
                rules.append(f"position_limit_{max_pos_pct*100:.0f}% (→{quantity})")

            # 4. Лимит класса
            new_class_pct = (class_value + position_cost) / capital if capital > 0 else 0
            if new_class_pct > max_pct:
                return self._reject(
                    decision,
                    f"лимит класса {asset_class} {max_pct*100:.0f}% превышен "
                    f"(сейчас {class_value/capital*100:.1f}%)",
                    [f"{asset_class}_portfolio_limit"],
                    tier=tier,
                )

            # 5. Мин. сумма сделки
            if position_cost < MIN_TRADE_SIZE:
                min_qty = round(MIN_TRADE_SIZE / price + 10 ** (-precision), precision)
                min_cost = min_qty * price
                new_class_pct_min = (class_value + min_cost) / capital if capital > 0 else 0
                if min_cost > max_position_value or new_class_pct_min > max_pct:
                    return self._reject(
                        decision,
                        f"min {MIN_TRADE_SIZE}₽ не влезает в лимиты",
                        ["min_trade_size"],
                        tier=tier,
                    )
                quantity = min_qty
                position_cost = min_cost
                rules.append(f"min_trade_size ({quantity})")

            # 6. Кэш-минимум
            cash = self._get_cash()
            cash_after = cash - position_cost
            cash_pct_after = cash_after / capital if capital > 0 else 0
            if cash_pct_after < CASH_MIN_PCT:
                max_spend = cash - capital * CASH_MIN_PCT
                if max_spend < MIN_TRADE_SIZE:
                    return self._reject(
                        decision,
                        f"кэш не может упасть ниже {CASH_MIN_PCT*100:.0f}%",
                        ["cash_reserve"],
                        tier=tier,
                    )
                quantity = round(max_spend / price, precision)
                position_cost = quantity * price
                rules.append(f"cash_reserve_limit (→{quantity})")

            # 7. Корреляция (для крипты)
            if asset_class == "crypto":
                for group_name, group_tickers in CORRELATION_GROUPS.items():
                    if ticker in group_tickers:
                        group_positions = [
                            p for p in self._get_portfolio_positions()
                            if p["ticker"] in group_tickers and p["ticker"] != ticker
                        ]
                        if len(group_positions) >= 2:
                            return self._reject(
                                decision,
                                f"корреляционный лимит ({group_name})",
                                ["correlation_limit"],
                                tier=tier,
                            )

            decision = {**decision, "quantity": quantity, "price": price}
            was_adjusted = bool(rules)

            return self._approve(
                decision, rules, tier=tier, daily_loss=daily_loss,
                reason="Скорректирован размер" if was_adjusted else "Проверки пройдены",
                was_adjusted=was_adjusted,
            )

        # ---------- SELL ----------
        if action == "SELL":
            pos = self._get_position(ticker)
            if not pos:
                return self._reject(decision, f"нет позиции {ticker}", ["no_position"], tier=tier)

            pos_qty = float(pos["quantity"])
            if quantity <= 0 or quantity > pos_qty:
                quantity = pos_qty
                rules.append(f"auto_sell_all ({quantity})")

            avg_price = float(pos["avg_price"])
            pnl_pct = (price - avg_price) / avg_price * 100 if avg_price > 0 else 0

            decision = {**decision, "quantity": quantity, "price": price}

            if tier == 1:
                hist_pos = self._get_historical_position(ticker)

                if pnl_pct >= TIER1_TAKE_PROFIT_PCT * 100:
                    rules.append(f"tier1_take_profit ({pnl_pct:+.1f}%)")
                    return self._approve(
                        decision, rules, tier=tier, pnl_pct=pnl_pct,
                        daily_loss=daily_loss, reason="Tier 1: тейк-профит",
                    )

                if hist_pos is not None and hist_pos >= TIER1_PEAK_TO_SELL:
                    rules.append(f"tier1_peak ({hist_pos:.0f}%)")
                    return self._approve(
                        decision, rules, tier=tier, pnl_pct=pnl_pct,
                        daily_loss=daily_loss, reason=f"Tier 1: пик ({hist_pos:.0f}%)",
                    )

                if pnl_pct <= -TIER1_STOP_LOSS_PCT * 100:
                    rules.append(f"tier1_critical_stop ({pnl_pct:+.1f}%)")
                    return self._approve(
                        decision, rules, tier=tier, pnl_pct=pnl_pct,
                        daily_loss=daily_loss, reason="Tier 1: критический стоп",
                    )

                return self._reject(
                    decision,
                    f"Tier 1 ({ticker}): не продаём в убыток ({pnl_pct:+.1f}%)",
                    ["tier1_no_sell_loss"], tier=tier,
                    pnl_pct=pnl_pct, daily_loss=daily_loss,
                )

            if tier == 2:
                stop_loss = TIER2_STOP_LOSS_PCT * 100
                take_profit = TIER2_TAKE_PROFIT_PCT * 100
            else:
                stop_loss = TIER3_STOP_LOSS_PCT * 100
                take_profit = TIER3_TAKE_PROFIT_PCT * 100

            if pnl_pct <= -stop_loss:
                rules.append(f"tier{tier}_stop_loss ({pnl_pct:+.1f}%)")
                return self._approve(
                    decision, rules, tier=tier, pnl_pct=pnl_pct,
                    daily_loss=daily_loss, reason=f"Tier {tier}: стоп-лосс",
                )

            if pnl_pct >= take_profit:
                rules.append(f"tier{tier}_take_profit ({pnl_pct:+.1f}%)")
                return self._approve(
                    decision, rules, tier=tier, pnl_pct=pnl_pct,
                    daily_loss=daily_loss, reason=f"Tier {tier}: тейк-профит",
                )

            reasoning = str(decision.get("reasoning", "")).lower()
            keywords = ["стоп", "фикс", "продаж", "выход", "закрыва", "убыт", "риск"]
            if any(k in reasoning for k in keywords):
                rules.append("reasoned_exit")
                return self._approve(
                    decision, rules, tier=tier, pnl_pct=pnl_pct,
                    daily_loss=daily_loss, reason="Обоснованный выход",
                )

            return self._reject(
                decision,
                f"SELL без причины (P/L {pnl_pct:+.1f}%)",
                ["unjustified_sell"], tier=tier,
                pnl_pct=pnl_pct, daily_loss=daily_loss,
            )

        return self._reject(decision, f"неизвестное действие: {action}", ["unknown_action"], tier=tier)

    # ---------- Хелперы ----------

    def _approve(self, decision: dict, rules: list[str], **kwargs) -> dict:
        ticker = decision.get("ticker", "?")
        action = decision.get("action", "?")
        qty = float(decision.get("quantity", 0))

        self._save_check(
            ticker=ticker, action=action,
            original_qty=qty, final_qty=qty,
            approved=True, was_adjusted=kwargs.get("was_adjusted", False),
            reason=kwargs.get("reason", ""), rules=rules,
            tier=kwargs.get("tier", 2),
            pnl_pct=kwargs.get("pnl_pct", 0.0),
            daily_loss=kwargs.get("daily_loss", 0.0),
        )
        log.info(f"APPROVED: {action} {ticker} qty={qty} tier={kwargs.get('tier')} — {kwargs.get('reason')}")

        return {
            **decision,
            "risk_check": {
                "status": "approved",
                "tier": kwargs.get("tier", 2),
                "was_adjusted": kwargs.get("was_adjusted", False),
                "reason": kwargs.get("reason", ""),
                "rules": rules,
            },
        }

    def _reject(self, decision: dict, reason: str, rules: list[str], **kwargs) -> dict:
        ticker = decision.get("ticker", "?")
        action = decision.get("action", "?")
        qty = float(decision.get("quantity", 0))

        self._save_check(
            ticker=ticker, action=action,
            original_qty=qty, final_qty=0,
            approved=False, was_adjusted=False,
            reason=reason, rules=rules,
            tier=kwargs.get("tier", 2),
            pnl_pct=kwargs.get("pnl_pct", 0.0),
            daily_loss=kwargs.get("daily_loss", 0.0),
        )
        log.warning(f"REJECTED: {action} {ticker} tier={kwargs.get('tier')} — {reason}")

        return {
            **decision,
            "risk_check": {
                "status": "rejected",
                "tier": kwargs.get("tier", 2),
                "reason": reason,
                "rules": rules,
            },
        }


risk_manager = RiskManager()
