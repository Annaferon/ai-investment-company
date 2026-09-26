"""Trader-01 — первый агент компании. Принимает торговые решения."""
import json
from datetime import datetime, timedelta
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db
from src.broker.virtual_broker import broker


SYSTEM_PROMPT = """Ты — профессиональный трейдер виртуальной инвестиционной компании.
Твоя задача: на основе рыночных цен, портфеля и ОТЧЁТОВ АНАЛИТИКОВ предложить ОДНО действие.

Правила:
- Активы: акции РФ (MOEX), криптовалюты, драгметаллы.
- Все цены — в рублях.
- Комиссия брокера: 0.05%.
- Не рискуй более 20% капитала в одной сделке.
- При негативном новостном фоне — осторожнее с покупками.
- При bearish-тренде — не покупай. Sideways — умеренно. Bullish — можно активнее.
- При высокой волатильности снижай размер позиции.
- Если хочешь остаться в деньгах — ticker "CASH", action "HOLD".

Если есть отчёты аналитиков (Stock, Crypto, Metals) — учитывай их оценки (score 0-10 и sentiment):
- score >= 7 — сильный сигнал
- score 4-7 — нейтральный
- score < 4 — избегай

Отвечай СТРОГО в формате JSON:
{
  "ticker": "SBER" | "GAZP" | "BTC" | "ETH" | "GOLD" | "CASH",
  "action": "BUY" | "SELL" | "HOLD",
  "quantity": 5,
  "confidence": 0.0-1.0,
  "reasoning": "объяснение на русском, 1-2 предложения"
}
"""

MAX_DATA_AGE_HOURS = 6


class Trader(BaseAgent):
    DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

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
            self.log.error("Нет свежих цен")
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
            if atype == "crypto":
                prices[ticker] = price * usd_rub
            else:
                prices[ticker] = price

        self.log.info(f"Цен: {len(prices)} | Курс USD/RUB: {usd_rub:.2f}")
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
        """Сводка по всем аналитикам активов (Stock, Crypto, Metals)."""
        blocks = []

        # Stock
        stocks = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, sentiment, score, reasoning
               FROM stock_reports
               WHERE created_at >= NOW() - INTERVAL '24 hours'
               ORDER BY ticker, created_at DESC;"""
        )
        if stocks:
            lines = ["АКЦИИ РФ:"]
            for s in stocks:
                lines.append(f"  • {s['ticker']}: {s['sentiment']} (score {s['score']}) — {s['reasoning'][:100]}")
            blocks.append("\n".join(lines))

        # Crypto
        cryptos = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, sentiment, score, reasoning
               FROM crypto_reports
               WHERE created_at >= NOW() - INTERVAL '24 hours'
               ORDER BY ticker, created_at DESC;"""
        )
        if cryptos:
            lines = ["КРИПТОВАЛЮТЫ:"]
            for c in cryptos:
                lines.append(f"  • {c['ticker']}: {c['sentiment']} (score {c['score']}) — {c['reasoning'][:100]}")
            blocks.append("\n".join(lines))

        # Metals
        metals = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, sentiment, score, reasoning
               FROM metals_reports
               WHERE created_at >= NOW() - INTERVAL '48 hours'
               ORDER BY ticker, created_at DESC;"""
        )
        if metals:
            lines = ["ДРАГМЕТАЛЛЫ:"]
            for m in metals:
                lines.append(f"  • {m['ticker']}: {m['sentiment']} (score {m['score']}) — {m['reasoning'][:100]}")
            blocks.append("\n".join(lines))

        if not blocks:
            return "ОТЧЁТЫ АНАЛИТИКОВ: пока нет данных."

        return "ОТЧЁТЫ АНАЛИТИКОВ:\n" + "\n\n".join(blocks)

    # ---------- Принятие решения ----------

    def decide(self) -> dict[str, Any]:
        prices = self.get_market_prices()
        if not prices:
            raise RuntimeError("Нет свежих рыночных данных")

        portfolio = self.get_portfolio()
        cash = self.get_cash()
        news = self.get_latest_news()
        market = self.get_latest_market()
        analysts_block = self.get_analysts_block()

        if news:
            news_block = f"""НОВОСТНОЙ ФОН (от {news['created_at']}):
Sentiment: {news['sentiment']}
Вывод: {news['summary']}
Уверенность: {news['confidence']}"""
        else:
            news_block = "НОВОСТИ: нет данных."

        if market:
            market_block = f"""РЫНОЧНЫЙ АНАЛИЗ (от {market['created_at']}):
Тренд: {market['overall_trend']}
Волатильность: {market['volatility_level']}
Вывод: {market['summary']}
Уверенность: {market['confidence']}"""
        else:
            market_block = "РЫНОК: нет данных."

        # Подсказка про выходные
        weekend_hint = ""
        if datetime.now().weekday() >= 5:
            weekend_hint = "\nВАЖНО: Сегодня выходной. MOEX закрыт — акции РФ и металлы не торгуются. Можно торговать только криптой (BTC, ETH) или оставаться в CASH."

        prompt = f"""Текущие цены (в рублях):
{json.dumps(prices, ensure_ascii=False, indent=2, default=float)}

Портфель:
{json.dumps(portfolio, ensure_ascii=False, indent=2, default=float) if portfolio else "пусто"}

Свободные деньги: {cash:.2f} ₽

{news_block}

{market_block}

{analysts_block}
{weekend_hint}

Что делаем? Если ничего не покупаем — используй ticker "CASH", action "HOLD"."""

        fallback_models = [
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "qwen/qwen3-coder:free",
            "openrouter/free",
        ]

        raw = None
        last_error = None

        for model in fallback_models:
            try:
                self.log.info(f"Пробую модель: {model}")
                self.model = model
                raw = self.think(prompt=prompt, system=SYSTEM_PROMPT)
                if raw:
                    self.log.info(f"Модель ответила: {model}")
                    break
            except Exception as e:
                last_error = e
                self.log.warning(f"Модель {model} недоступна: {e}")
                continue

        if not raw:
            raise RuntimeError(f"Все модели недоступны: {last_error}")

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").replace("json", "", 1).strip()

        try:
            decision = json.loads(cleaned)
        except json.JSONDecodeError as e:
            self.log.error(f"Невалидный JSON: {raw}")
            raise ValueError(f"JSON parse error: {e}")

        return decision

    # ---------- Основной цикл ----------

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
            ticker=ticker,
            action=action,
            confidence=float(decision.get("confidence", 0.0)),
            reasoning=decision.get("reasoning", ""),
        )

        self.log.info(
            f"Решение: {action} {ticker} "
            f"(уверенность {decision.get('confidence')})"
        )

        if ticker == "CASH" or action == "HOLD":
            self.log.info("Остаёмся в кэше — сделки нет")
            return {**decision, "execution": {"executed": False, "reason": "cash_hold"}}

        prices = self.get_market_prices()
        decision["price"] = prices.get(ticker, 0)

        if decision["price"] <= 0:
            self.log.warning(f"Нет цены для {ticker} — сделку не исполняем")
            return {**decision, "execution": {"executed": False, "reason": "no_price"}}

        execution = broker.execute(decision)
        self.log.info(f"Брокер: {execution}")

        return {**decision, "execution": execution}
