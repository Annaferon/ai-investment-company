"""Risk Manager — комплексная защита капитала.
Tier-система, дневной лимит, корреляция, cooldown."""
import json
from datetime import date, datetime, timedelta
from typing import Any, Optional

from src.core.database import db
from src.core.logger import get_logger

log = get_logger("risk_manager")


# ---------- Глобальные лимиты ----------
MAX_RISK_PER_TRADE_PCT = 0.01      # 1% капитала риск на сделку
MAX_DAILY_LOSS_PCT = 5.0           # Дневной лимит убытка
MIN_TRADE_SIZE = 500.0             # Минимальная сумма сделки
MAX_DATA_AGE_HOURS = 6             # Свежесть цен

# ---------- Tier-система ----------
TIER1 = {"BTC", "ETH"}
TIER2 = {"SOL", "BNB", "LINK"}
TIER3 = {"DOGE", "SHIB", "PEPE", "WIF", "BONK"}

# Лимиты по тирам
TIER1_MAX_POSITION_PCT = 0.15     # 15% на позицию
TIER1_MAX_PORTFOLIO_PCT = 0.50    # 50% всего в Tier 1
TIER1_STOP_LOSS_PCT = 0.30        # Широкий стоп
TIER1_TAKE_PROFIT_PCT = 0.20      # Тейк +20%
TIER1_PEAK_TO_SELL = 85.0         # Продажа при пике 85%+

TIER2_MAX_POSITION_PCT = 0.05     # 5% на позицию
TIER2_MAX_PORTFOLIO_PCT = 0.30    # 30% всего в Tier 2
TIER2_STOP_LOSS_PCT = 0.15        # Стоп 15%
TIER2_TAKE_PROFIT_PCT = 0.25      # Тейк +25%

TIER3_MAX_POSITION_PCT = 0.03     # 3% на позицию
TIER3_MAX_PORTFOLIO_PCT = 0.10    # 10% всего в Tier 3
TIER3_STOP_LOSS_PCT = 0.10        # Жёсткий стоп 10%
TIER3_TAKE_PROFIT_PCT = 0.30      # Тейк +30%

# Корреляционные группы
CORRELATION_GROUPS = {
    "bitcoin_ecosystem": {"BTC", "ETH", "SOL", "BNB"},
    "memecoins": {"DOGE", "SHIB", "PEPE", "WIF", "BONK"},
}

# Cooldown после продажи
COOLDOWN_HOURS = 24

MAX_CRYPTO_POSITIONS = 5
MAX_STOCK_POSITIONS = 3


def _tier(ticker: str) -> int:
    if ticker in TIER1:
        return 1
    if ticker in TIER2:
        return 2
    if ticker in TIER3:
        return 3
    return 2  # акции, металлы — Tier 2


class RiskManager:
    def __init__(self, name: str = "Risk-01") -> None:
        self.name = name

    # ---------- Вспомогательные ----------

    def _get_cash(self) -> float:
        row = db.fetch_one("SELECT cash FROM account WHERE id = 1;")
        return float(row["cash"]) if row else 0.0

    def _get_capital(self) -> float:
        acc = db.fetch_one("SELECT cash FROM account WHERE id = 1;")
        if not acc:
            return 0.0
        cash = float(acc["cash"])
        positions = db.fetch_all("SELECT ticker, quantity, avg_price FROM portfolio;")
        assets = 0.0
        for p in positions:
            price_row = db.fetch_one(
                """SELECT price FROM market_prices
                   WHERE ticker = %s ORDER BY updated_at DESC LIMIT 1;""",
                (p["ticker"],),
            )
            assets += float(p["quantity"]) * (
                float(price_row["price"]) if price_row else float(p["avg_price"])
            )
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

    def _get_asset_type(self, ticker: str) -> str:
        row = db.fetch_one(
            """SELECT asset_type FROM market_prices
               WHERE ticker = %s ORDER BY updated_at DESC LIMIT 1;""",
            (ticker,),
        )
        return row["asset_type"] if row else "stock"

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
        row = db.fetch_one(
            """SELECT price FROM market_prices
               WHERE ticker = %s ORDER BY updated_at DESC LIMIT 1;""",
            (ticker,),
        )
        return float(row["price"]) * qty if row else 0.0

    def _get_tier_exposure(self, tier: int) -> float:
        """Суммарная стоимость позиций в данном Tier."""
        positions = self._get_portfolio_positions()
        total = 0.0
        for p in positions:
            if _tier(p["ticker"]) == tier:
                total += self._position_value(p["ticker"], float(p["quantity"]))
        return total

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

    # ---------- Дневной лимит ----------

    def _get_daily_loss(self) -> float:
        """Текущий % дневного P/L (отрицательный = убыток)."""
        # Считаем от стартового капитала дня
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        capital_now = self._get_capital()

        # Простая логика: считаем по текущему капиталу к начальному 10000
        acc = db.fetch_one("SELECT initial_capital FROM account WHERE id = 1;")
        initial = float(acc["initial_capital"]) if acc else 10000.0
        return (capital_now - initial) / initial * 100

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

        tier = _tier(ticker)
        asset_type = self._get_asset_type(ticker)
        capital = self._get_capital()
        daily_loss = self._get_daily_loss()

        # Дневной лимит убытка
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
            # Cooldown
            if self._is_cooling_down(ticker):
                return self._reject(
                    decision, f"{ticker} в cooldown после продажи",
                    ["cooldown"], tier=tier,
                )

            # Лимит по тиру
            if tier == 1:
                max_pos_pct = TIER1_MAX_POSITION_PCT
                max_tier_pct = TIER1_MAX_PORTFOLIO_PCT
            elif tier == 2:
                max_pos_pct = TIER2_MAX_POSITION_PCT
                max_tier_pct = TIER2_MAX_PORTFOLIO_PCT
            else:
                max_pos_pct = TIER3_MAX_POSITION_PCT
                max_tier_pct = TIER3_MAX_PORTFOLIO_PCT

            # Лимит на позицию
            max_cost = capital * max_pos_pct
            precision = 8 if asset_type == "crypto" else 0
            max_qty = round(max_cost / price, precision) if price > 0 else 0

            if quantity <= 0:
                quantity = max_qty
                rules.append(f"auto_quantity ({quantity})")

            if quantity > max_qty:
                quantity = max_qty
                rules.append(f"tier{tier}_position_limit (→{quantity})")

            cost = quantity * price

            # Минимум сделки
            if cost < MIN_TRADE_SIZE:
                min_qty = round(MIN_TRADE_SIZE / price + 10 ** (-precision), precision)
                if min_qty > max_qty or min_qty * price > self._get_cash():
                    return self._reject(
                        decision,
                        f"min {MIN_TRADE_SIZE}₽ не влезает в tier{tier} ({max_cost:.0f}₽)",
                        ["min_trade_size"], tier=tier,
                    )
                quantity = min_qty
                rules.append(f"min_trade_size ({quantity})")

            # Лимит на весь Tier
            tier_exposure = self._get_tier_exposure(tier)
            new_tier_pct = (tier_exposure + cost) / capital * 100 if capital > 0 else 0
            if new_tier_pct > max_tier_pct * 100:
                return self._reject(
                    decision,
                    f"лимит tier{tier} {max_tier_pct*100:.0f}% превышен",
                    [f"tier{tier}_portfolio_limit"], tier=tier,
                )

            # Корреляционный лимит
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
                            ["correlation_limit"], tier=tier,
                        )

            # Лимит по классам
            by_type = self._get_portfolio_positions()
            crypto_count = sum(1 for p in by_type if _tier(p["ticker"]) in (1, 2, 3) and p["ticker"] in (TIER1 | TIER2 | TIER3))
            stock_count = sum(1 for p in by_type if p["ticker"] not in (TIER1 | TIER2 | TIER3))

            existing = {p["ticker"] for p in by_type}
            if ticker not in existing:
                if asset_type == "crypto" and crypto_count >= MAX_CRYPTO_POSITIONS:
                    return self._reject(decision, f"лимит {MAX_CRYPTO_POSITIONS} крипто", ["max_crypto"], tier=tier)
                if asset_type == "stock" and stock_count >= MAX_STOCK_POSITIONS:
                    return self._reject(decision, f"лимит {MAX_STOCK_POSITIONS} акций", ["max_stocks"], tier=tier)

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

            # === TIER 1: BTC, ETH — не продаём в убыток ===
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
                        daily_loss=daily_loss,
                        reason=f"Tier 1: пик ({hist_pos:.0f}%)",
                    )

                # Если P/L упал больше чем на 30% — это критично, разрешаем
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

            # === TIER 2 / TIER 3 ===
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
