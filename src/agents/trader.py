"""Trader-01 — первый агент компании."""
import json
from datetime import datetime, timedelta
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db
from src.broker.virtual_broker import broker
from src.risk.risk_manager import risk_manager


SYSTEM_PROMPT = """Ты — профессиональный трейдер виртуальной инвестиционной компании.
Твоя задача: на основе рыночных цен, портфеля и ОТЧЁТОВ АНАЛИТИКОВ предложить ОДНО действие.

ВАЖНО ПРО РЕЖИМ РАБОТЫ:
- Акции РФ (MOEX): Пн–Пт 07:00–23:50 МСК. В выходные закрыты.
- КРИПТА: торгуется КРУГЛОСУТОЧНО.
- Количество крипты — ДРОБНОЕ (0.00028 BTC). Акций — целое.
- Если не знаешь quantity — ставь quantity: 0 (Risk Manager рассчитает).

ПРИОРИТЕТ ИСТОЧНИКОВ:
1. HISTORICAL — долгосрочный контекст (позиция в 720-дневном диапазоне)
2. CRYPTO/STOCK/METALS — оценки активов
3. MARKET — общий тренд
4. NEWS — новостной фон

ЛОГИКА HISTORICAL:
- Позиция <20% (дно) + bullish → СИЛЬНАЯ покупка
- Позиция 20-40% + bullish → умеренная покупка
- Позиция >80% (пик) + bearish → продажа

ОБЩИЕ ПРАВИЛА:
- Все цены — в рублях.
- Комиссия брокера: 0.05%.
- Не рискуй более 20% капитала в одной сделке.
- Не покупай в bearish-тренде рынка.
- Если хочешь остаться в деньгах — ticker "CASH", action "HOLD".

Отвечай СТРОГО в формате JSON (без пояснений, без markdown):
{
  "ticker": "SBER" | "BTC" | "ETH" | "SOL" | "LINK" | "DOGE" | "SHIB" | "PEPE" | "GAZP" | "LKOH" | "GOLD" | "CASH",
  "action": "BUY" | "SELL" | "HOLD",
  "quantity": 0,
  "confidence": 0.0-1.0,
  "reasoning": "объяснение на русском, 2-4 предложения"
}
"""

MAX_DATA_AGE_HOURS = 6

# Актуальные бесплатные модели (обновлено 29.09.2026)
FALLBACK_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-31b-it:free",
    "inclusionai/ling-3.0-flash-fin:free",
    "openai/gpt-oss-20b:free",
    "cohere/north-mini-code:free",
    "openrouter/free",
]


class Trader(BaseAgent):
    DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

    def __init__(self, name: str = "Trader-01") -> None:
        super().__init__(name=name, role="trader")

    # ---------- Данные ----------

    def get_market_prices(self) -> dict[str, float]:
        cutoff = datetime.now() - timedelta(hours=MAX_DATA_AGE_HOURS)
        rows = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, price, asset_type
               FROM market_prices
               WHERE updated_at >= %s
               ORDER BY ticker, updated_at DESC;""",
            (cutoff,),
        )
        if not rows:
            return {}

        raw = {}
        usd_rub = 90.0
        for r in rows:
            ticker = r["ticker"]
            price = float(r["price"])
            atype = r["asset_type"]
            if ticker == "USD_RUB":
                usd_rub = price
            else:
                raw[ticker] = (price, atype)

        prices = {}
        for ticker, (price, atype) in raw.items():
            prices[ticker] = price * usd_rub if atype == "crypto" else price

        self.log.info(f"Цен: {len(prices)} | USD/RUB: {usd_rub:.2f}")
        return prices

    def get_portfolio(self) -> list[dict[str, Any]]:
        return db.fetch_all("SELECT ticker, quantity, avg_price FROM portfolio;")

    def get_cash(self) -> float:
        row = db.fetch_one("SELECT cash FROM account WHERE id = 1;")
        return float(row["cash"]) if row else 0.0

    def get_latest_news(self) -> dict[str, Any] | None:
        return db.fetch_one(
            """SELECT summary, sentiment, confidence, created_at
               FROM news_reports ORDER BY created_at DESC LIMIT 1;"""
        )

    def get_latest_market(self) -> dict[str, Any] | None:
        return db.fetch_one(
            """SELECT summary, overall_trend, volatility_level, confidence, created_at
               FROM market_reports ORDER BY created_at DESC LIMIT 1;"""
        )

    def get_analysts_block(self) -> str:
        blocks = []

        historical = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, trend, sentiment, score,
                      range_position, reasoning
               FROM historical_reports
               WHERE created_at >= NOW() - INTERVAL '24 hours'
               ORDER BY ticker, created_at DESC;"""
        )
        if historical:
            lines = ["=== HISTORICAL (720 дней) ==="]
            for h in historical:
                lines.append(
                    f"  • {h['ticker']}: {h['sentiment']} (score {h['score']}) "
                    f"| позиция {h['range_position']}% | тренд {h['trend']}"
                )
            blocks.append("\n".join(lines))

        stocks = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, sentiment, score, reasoning
               FROM stock_reports
               WHERE created_at >= NOW() - INTERVAL '24 hours'
               ORDER BY ticker, created_at DESC;"""
        )
        if stocks:
            lines = ["=== АКЦИИ РФ ==="]
            for s in stocks:
                lines.append(f"  • {s['ticker']}: {s['sentiment']} ({s['score']})")
            blocks.append("\n".join(lines))

        cryptos = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, sentiment, score, reasoning
               FROM crypto_reports
               WHERE created_at >= NOW() - INTERVAL '24 hours'
               ORDER BY ticker, created_at DESC;"""
        )
        if cryptos:
            lines = ["=== КРИПТА ==="]
            for c in cryptos:
                lines.append(f"  • {c['ticker']}: {c['sentiment']} ({c['score']})")
            blocks.append("\n".join(lines))

        metals = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, sentiment, score, reasoning
               FROM metals_reports
               WHERE created_at >= NOW() - INTERVAL '48 hours'
               ORDER BY ticker, created_at DESC;"""
        )
        if metals:
            lines = ["=== МЕТАЛЛЫ ==="]
            for m in metals:
                lines.append(f"  • {m['ticker']}: {m['sentiment']} ({m['score']})")
            blocks.append("\n".join(lines))

        if not blocks:
            return "ОТЧЁТЫ АНАЛИТИКОВ: нет данных."

        return "ОТЧЁТЫ АНАЛИТИКОВ:\n\n" + "\n\n".join(blocks)

    # ---------- Решение ----------

    def decide(self) -> dict[str, Any]:
        prices = self.get_market_prices()
        if not prices:
            raise RuntimeError("Нет свежих рыночных данных")

        portfolio = self.get_portfolio()
        cash = self.get_cash()
        news = self.get_latest_news()
        market = self.get_latest_market()
        analysts_block = self.get_analysts_block()

        news_block = "НОВОСТИ: нет данных."
        if news:
            news_block = f"НОВОСТИ:\nSentiment: {news['sentiment']}\n{news['summary']}"

        market_block = "РЫНОК: нет данных."
        if market:
            market_block = (
                f"РЫНОК:\nТренд: {market['overall_trend']}\n"
                f"Волатильность: {market['volatility_level']}\n{market['summary']}"
            )

        weekend_hint = ""
        if datetime.now().weekday() >= 5:
            weekend_hint = "\nВАЖНО: Выходной. Доступна только крипта."

        prompt = f"""Цены (₽):
{json.dumps(prices, ensure_ascii=False, indent=2, default=float)}

Портфель:
{json.dumps(portfolio, ensure_ascii=False, indent=2, default=float) if portfolio else "пусто"}

Свободные деньги: {cash:.2f} ₽

{news_block}

{market_block}

{analysts_block}
{weekend_hint}

Что делаем? Отвечай JSON без пояснений."""

        last_error = None
        for model in FALLBACK_MODELS:
            try:
                self.log.info(f"Пробую модель: {model}")
                self.model = model
                raw = self.think(prompt=prompt, system=SYSTEM_PROMPT)

                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.strip("`").replace("json", "", 1).strip()

                # Обрезаем всё, что до первой {
                first_brace = cleaned.find("{")
                last_brace = cleaned.rfind("}")
                if first_brace != -1 and last_brace > first_brace:
                    cleaned = cleaned[first_brace:last_brace + 1]

                decision = json.loads(cleaned)
                self.log.info(f"✓ Модель ответила: {model}")
                return decision

            except json.JSONDecodeError as e:
                self.log.warning(f"Модель {model} — не JSON: {str(e)[:100]}")
                last_error = e
                continue
            except Exception as e:
                self.log.warning(f"Модель {model} недоступна: {str(e)[:150]}")
                last_error = e
                continue

        raise RuntimeError(f"Все модели недоступны. Последняя ошибка: {last_error}")

    # ---------- Цикл ----------

    def run(self) -> dict[str, Any]:
        self.log.info("Trader просыпается...")

        try:
            decision = self.decide()
        except Exception as e:
            self.log.error(f"Не смог принять решение: {e}")
            return {"error": str(e)}

        ticker = decision.get("ticker", "CASH").upper()
        action = decision.get("action", "HOLD").upper()

        self.record_decision(
            ticker=ticker, action=action,
            confidence=float(decision.get("confidence", 0.0)),
            reasoning=decision.get("reasoning", ""),
        )

        self.log.info(f"Решение: {action} {ticker} ({decision.get('confidence')})")

        if ticker == "CASH" or action == "HOLD":
            return {**decision, "execution": {"executed": False, "reason": "cash_hold"}}

        prices = self.get_market_prices()
        decision["price"] = prices.get(ticker, 0)

        if decision["price"] <= 0:
            self.log.warning(f"Нет цены для {ticker}")
            return {**decision, "execution": {"executed": False, "reason": "no_price"}}

        checked = risk_manager.check(decision)
        if checked.get("risk_check", {}).get("status") == "rejected":
            reason = checked["risk_check"]["reason"]
            return {**checked, "execution": {"executed": False, "reason": "risk_rejected", "detail": reason}}

        execution = broker.execute(checked)
        self.log.info(f"Брокер: {execution}")

        return {**checked, "execution": execution}
