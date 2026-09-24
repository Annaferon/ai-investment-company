"""Trader-01 — первый агент компании. Принимает торговые решения."""
import json
from datetime import datetime, timedelta
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db
from src.broker.virtual_broker import broker


SYSTEM_PROMPT = """Ты — профессиональный трейдер виртуальной инвестиционной компании.
Твоя задача: на основе рыночных цен, текущего портфеля и НОВОСТНОГО ФОНА предложить ОДНО действие.

Правила:
- Активы: акции РФ (MOEX), криптовалюты (Binance), драгметаллы.
- Стартовый капитал: 10 000 ₽.
- Комиссия брокера: 0.05% от сделки.
- Не рискуй более 20% капитала в одной сделке.
- Если не уверен — выбирай HOLD.
- Учитывай новостной фон: если sentiment негативный — будь осторожнее с покупками.
- Если новостей нет — работай только по техническим данным.

Отвечай СТРОГО в формате JSON, без пояснений:
{
  "ticker": "SBER",
  "action": "BUY" | "SELL" | "HOLD",
  "quantity": 5,
  "confidence": 0.0-1.0,
  "reasoning": "краткое объяснение на русском, 1-2 предложения"
}
"""

# Сколько часов считаем данные «свежими»
MAX_DATA_AGE_HOURS = 6


class Trader(BaseAgent):
    """Первый сотрудник компании."""

    DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

    def __init__(self, name: str = "Trader-01") -> None:
        super().__init__(name=name, role="trader")

    # ---------- Получение данных ----------

    def get_market_prices(self) -> dict[str, float]:
        """Свежие цены от Оракула. Если данных нет — возвращаем пустой dict."""
        cutoff = datetime.now() - timedelta(hours=MAX_DATA_AGE_HOURS)
        rows = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, price, updated_at
               FROM market_prices
               WHERE updated_at >= %s
               ORDER BY ticker, updated_at DESC;""",
            (cutoff,),
        )
        if not rows:
            self.log.error(
                f"Нет свежих цен от Оракула (старше {MAX_DATA_AGE_HOURS}ч). "
                f"Работа невозможна."
            )
            return {}
        prices = {r["ticker"]: float(r["price"]) for r in rows}
        self.log.info(f"Получено {len(prices)} свежих цен от Оракула")
        return prices

    def get_portfolio(self) -> list[dict[str, Any]]:
        """Что сейчас в портфеле."""
        return db.fetch_all("SELECT ticker, quantity, avg_price FROM portfolio;")

    def get_cash(self) -> float:
        """Свободные деньги из таблицы account."""
        row = db.fetch_one("SELECT cash FROM account WHERE id = 1;")
        return float(row["cash"]) if row else 0.0

    def get_latest_news(self) -> dict[str, Any] | None:
        """Последний новостной отчёт от News Analyst."""
        return db.fetch_one(
            """SELECT summary, sentiment, key_events, confidence, created_at
               FROM news_reports
               ORDER BY created_at DESC
               LIMIT 1;"""
        )

    # ---------- Принятие решения ----------

    def decide(self) -> dict[str, Any]:
        """Спросить LLM, что делать. Требует свежих цен от Оракула."""
        prices = self.get_market_prices()
        if not prices:
            raise RuntimeError(
                "Нет свежих рыночных данных. Trader не может принять решение."
            )

        portfolio = self.get_portfolio()
        cash = self.get_cash()
        news = self.get_latest_news()

        if news:
            news_block = f"""НОВОСТНОЙ ФОН (от {news['created_at']}):
Sentiment: {news['sentiment']}
Краткий вывод: {news['summary']}
Ключевые события:
{news['key_events']}
Уверенность News Analyst: {news['confidence']}"""
        else:
            news_block = "НОВОСТНОЙ ФОН: нет данных."

        prompt = f"""Текущие цены (реальные, от Оракула):
{json.dumps(prices, ensure_ascii=False, indent=2, default=float)}

Текущий портфель:
{json.dumps(portfolio, ensure_ascii=False, indent=2, default=float) if portfolio else "пусто"}

Свободные деньги: {cash:.2f} ₽

{news_block}

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
            raise RuntimeError(f"Все модели недоступны. Последняя ошибка: {last_error}")

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").replace("json", "", 1).strip()

        try:
            decision = json.loads(cleaned)
        except json.JSONDecodeError as e:
            self.log.error(f"Не смог распарсить JSON: {raw}")
            raise ValueError(f"LLM вернул невалидный JSON: {e}")

        return decision

    # ---------- Основной цикл ----------

    def run(self) -> dict[str, Any]:
        """Один цикл работы Trader."""
        self.log.info("Trader просыпается...")

        try:
            decision = self.decide()
        except Exception as e:
            self.log.error(f"Не смог принять решение: {e}")
            return {"error": str(e)}

        # Подставляем цену из свежих данных
        prices = self.get_market_prices()
        ticker = decision.get("ticker", "?").upper()
        decision["price"] = prices.get(ticker, 0)

        if decision["price"] <= 0:
            self.log.warning(f"Нет цены для {ticker} — сделку не исполняем")
            return {"error": f"no_price_for_{ticker}", "decision": decision}

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
