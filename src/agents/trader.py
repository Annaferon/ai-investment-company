"""Trader-01 — первый агент компании. Принимает торговые решения."""
import json
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db


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
    
    # Trader использует модель посильнее — ему нужно думать
    DEFAULT_MODEL = "anthropic/claude-3.5-sonnet"
    
    def __init__(self, name: str = "Trader-01") -> None:
        super().__init__(name=name, role="trader")
    
    # ---------- Получение данных ----------
    
    def get_market_prices(self) -> dict[str, float]:
        """
        Получить текущие цены активов.
        Пока — заглушка с фиксированными ценами.
        Позже заменим на реальные API MOEX / Binance / ЦБ.
        """
        return {
            "SBER": 285.50,     # акция Сбербанка, ₽
            "GAZP": 132.40,     # акция Газпрома, ₽
            "BTC": 6_200_000.0, # биткоин, ₽
            "ETH": 210_000.0,   # эфир, ₽
            "GOLD": 7_500.0,    # золото (грамм), ₽
        }
    
    def get_portfolio(self) -> list[dict[str, Any]]:
        """Что сейчас в портфеле."""
        return db.fetch_all("SELECT ticker, quantity, avg_price FROM portfolio;")
    
    def get_cash(self) -> float:
        """
        Свободный кэш. Пока считаем просто:
        10 000 ₽ минус стоимость всех позиций.
        Позже сделаем нормальный бухгалтерский учёт.
        """
        from src.core.config import config
        positions = self.get_portfolio()
        invested = sum(float(p["quantity"]) * float(p["avg_price"]) for p in positions)
        return config.STARTING_CAPITAL - invested
    
    # ---------- Принятие решения ----------
    
    def decide(self) -> dict[str, Any]:
        """Спросить LLM, что делать."""
        prices = self.get_market_prices()
        portfolio = self.get_portfolio()
        cash = self.get_cash()
        
        prompt = f"""Текущие цены:
{json.dumps(prices, ensure_ascii=False, indent=2)}

Текущий портфель:
{json.dumps(portfolio, ensure_ascii=False, indent=2) if portfolio else "пусто"}

Свободный кэш: {cash:.2f} ₽

Что делаем?"""
        
        raw = self.think(prompt=prompt, system=SYSTEM_PROMPT)
        
        # LLM иногда оборачивает JSON в ```json ... ```
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
        
        # Записываем решение в БД
        self.record_decision(
            ticker=decision.get("ticker", "?"),
            action=decision.get("action", "HOLD"),
            confidence=float(decision.get("confidence", 0.0)),
            reasoning=decision.get("reasoning", ""),
        )
        
        self.log.info(
            f"Решение: {decision.get('action')} {decision.get('ticker')} "
            f"(уверенность {decision.get('confidence')})"
        )
        return decision
