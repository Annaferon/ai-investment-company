"""Trader-01 — первый агент компании. Принимает торговые решения."""
import json
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db
from src.broker.virtual_broker import broker


SYSTEM_PROMPT = """Ты — профессиональный трейдер виртуальной инвестиционной компании.
Твоя задача: на основе рыночных цен и текущего портфеля предложить ОДНО действие.

Правила:
- Активы: акции РФ (MOEX), криптовалюты (Binance), драгметаллы.
- Стартовый капитал: 10 000 ₽.
- Комиссия брокера: 0.05% от сделки.
- Не рискуй более 20% капитала в одной сделке.
- Если не уверен — выбирай HOLD.

Отвечай СТРОГО в формате JSON, без пояснений:
{
  "ticker": "SBER",
  "action": "BUY" | "SELL" | "HOLD",
  "quantity": 5,
  "confidence": 0.0-1.0,
  "reasoning": "краткое объяснение на русском, 1-2 предложения"
}
"""


class Trader(BaseAgent):
    """Первый сотрудник компании."""

    DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

    def __init__(self, name: str = "Trader-01") -> None:
        super().__init__(name=name, role="trader")

    # ---------- Получение данных ----------

    def get_market_prices(self) -> dict[str, float]:
        """Получить текущие цены активов (пока заглушка)."""
        return {
            "SBER": 285.50,
            "GAZP": 132.40,
            "BTC": 6_200_000.0,
            "ETH": 210_000.0,
            "GOLD": 7_500.0,
        }

    def get_portfolio(self) -> list[dict[str, Any]]:
        """Что сейчас в портфеле."""
        return db.fetch_all("SELECT ticker, quantity, avg_price FROM portfolio;")

    def get_cash(self) -> float:
        """Свободные деньги из таблицы account."""
        row = db.fetch_one("SELECT cash FROM account WHERE id = 1;")
        return float(row["cash"]) if row else 0.0

    # ---------- Принятие решения ----------

    def decide(self) -> dict[str, Any]:
        """Спросить LLM, что делать (с fallback)."""
        prices = self.get_market_prices()
        portfolio = self.get_portfolio()
        cash = self.get_cash()

        prompt = f"""Текущие цены:
{json.dumps(prices, ensure_ascii=False, indent=2)}

Текущий портфель:
{json.dumps(portfolio, ensure_ascii=False, indent=2) if portfolio else "пусто"}

Свободные деньги: {cash:.2f} ₽

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

        # Подставляем цену из рынка
        prices = self.get_market_prices()
        ticker = decision.get("ticker", "?").upper()
        decision["price"] = prices.get(ticker, 0)

        # Записываем решение в БД
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

        # Исполняем через брокера
        execution = broker.execute(decision)
        self.log.info(f"Брокер: {execution}")

        return {**decision, "execution": execution}
