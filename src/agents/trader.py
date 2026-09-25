"""Trader-01 — первый агент компании. Принимает торговые решения."""
import json
from datetime import datetime, timedelta
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db
from src.broker.virtual_broker import broker


SYSTEM_PROMPT = """Ты — профессиональный трейдер виртуальной инвестиционной компании.
Твоя задача: на основе рыночных цен, портфеля, НОВОСТНОГО ФОНА и РЫНОЧНОГО АНАЛИЗА предложить ОДНО действие.

Правила:
- Активы: акции РФ (MOEX), криптовалюты, драгметаллы.
- Все цены — в рублях (крипта сконвертирована по курсу USD/RUB).
- Комиссия брокера: 0.05% от сделки.
- Не рискуй более 20% капитала в одной сделке.
- Если не уверен — выбирай HOLD.
- При негативном новостном фоне — осторожнее с покупками.
- При bearish-тренде — не покупай. При sideways — умеренно. При bullish — можно активнее.
- При высокой волатильности снижай размер позиции.

Отвечай СТРОГО в формате JSON:
{
  "ticker": "SBER",
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

    # ---------- Получение данных ----------

    def get_market_prices(self) -> dict[str, float]:
        """Свежие цены. Крипта конвертируется в рубли по курсу USD/RUB."""
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

        self.log.info(
            f"Цен: {len(prices)} | Курс USD/RUB: {usd_rub:.2f}"
        )
        return prices

    def get_portfolio(self) -> list[dict[str, Any]]:
        return db.fetch_all("SELECT ticker, quantity, avg_price FROM portfolio;")

    def get_cash(self) -> float:
        row = db.fetch_one("SELECT cash FROM account WHERE id = 1;")
        return float(row["cash"]) if row else 0.0

    def get_latest_news(self) -> dict[str, Any] | None:
        return db.fetch_one(
            """SELECT summary, sentiment, key_events, confidence, created_at
               FROM news_reports ORDER BY created_at DESC LIMIT 1;"""
        )

    def get_latest_market(self) -> dict[str, Any] | None:
        return db.fetch_one(
            """SELECT summary, overall_trend, volatility_level, key_movers,
                      confidence, created_at
               FROM market_reports ORDER BY created_at DESC LIMIT 1;"""
        )

    # ---------- Принятие решения ----------

    def decide(self) -> dict[str, Any]:
        prices = self.get_market_prices()
        if not prices:
            raise RuntimeError("Нет свежих рыночных данных")

        portfolio = self.get_portfolio()
        cash = self.get_cash()
        news = self.get_latest_news()
        market = self.get_latest_market()

        if news:
            news_block = f"""НОВОСТНОЙ ФОН (от {news['created_at']}):
Sentiment: {news['sentiment']}
Вывод: {news['summary']}
События: {news['key_events']}
Уверенность: {news['confidence']}"""
        else:
            news_block = "НОВОСТИ: нет данных."

        if market:
            market_block = f"""РЫНОЧНЫЙ АНАЛИЗ (от {market['created_at']}):
Тренд: {market['overall_trend']}
Волатильность: {market['volatility_level']}
Вывод: {market['summary']}
Key movers: {market['key_movers']}
Уверенность: {market['confidence']}"""
        else:
            market_block = "РЫНОК: нет данных."

        prompt = f"""Текущие цены (в рублях):
{json.dumps(prices, ensure_ascii=False, indent=2, default=float)}

Портфель:
{json.dumps(portfolio, ensure_ascii=False, indent=2, default=float) if portfolio else "пусто"}

Свободные деньги: {cash:.2f} ₽

{news_block}

{market_block}

Что делаем?"""

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

        prices = self.get_market_prices()
        ticker = decision.get("ticker", "?").upper()
        decision["price"] = prices.get(ticker, 0)

        if decision["price"] <= 0:
            self.log.warning(f"Нет цены для {ticker} — сделку не исполняем")
            return {"error": f"no_price_{ticker}", "decision": decision}

        self.record_decision(
            ticker=ticker,
            action=decision.get("action", "HOLD"),
            confidence=float(decision.get("confidence", 0.0)),
            reasoning=decision.get("reasoning", ""),
        )

        self.log.info(
            f"Решение: {decision.get('action')} {ticker} "
            f"(уверенность {decision.get('confidence')})"
        )

        execution = broker.execute(decision)
        self.log.info(f"Брокер: {execution}")

        return {**decision, "execution": execution}
