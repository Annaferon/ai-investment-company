"""Stock Analyst — оценка акций РФ через LLM."""
import json
from datetime import datetime, timedelta
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db


SYSTEM_PROMPT = """Ты — старший аналитик акций российского фондового рынка.
Твоя задача: дать оценку перспектив каждой акции из списка.

Для каждой акции оцени:
- sentiment: bullish (рост) | neutral (боковик) | bearish (падение)
- score: от 0 до 10 (насколько привлекательна для покупки прямо сейчас)
- reasoning: 1-2 предложения — почему такая оценка

Учитывай:
- Текущую цену (она есть в промпте)
- Твои знания о компании (сектор, размер, репутация)
- Новостной фон (если передан)

Требования:
- Кратко, по делу, на русском.
- Если про компанию мало знаешь — ставь нейтральную оценку и confidence низкий.
- Не выдумывай точные цифры (P/E, выручку), которых нет в промпте.

Отвечай СТРОГО в формате JSON-массива, без пояснений:
[
  {"ticker": "SBER", "sentiment": "bullish", "score": 7.5, "reasoning": "..."},
  {"ticker": "GAZP", "sentiment": "bearish", "score": 3.0, "reasoning": "..."}
]
"""

# Сколько часов считаем данные свежими
MAX_DATA_AGE_HOURS = 6


class StockAnalyst(BaseAgent):
    """Аналитик акций РФ."""

    DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

    def __init__(self, name: str = "Stock-01") -> None:
        super().__init__(name=name, role="stock_analyst")

    # ---------- Получение данных ----------

    def get_stock_prices(self) -> dict[str, float]:
        """Свежие цены акций (asset_type = 'stock')."""
        cutoff = datetime.now() - timedelta(hours=MAX_DATA_AGE_HOURS)
        rows = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, price
               FROM market_prices
               WHERE asset_type = 'stock' AND updated_at >= %s
               ORDER BY ticker, updated_at DESC;""",
            (cutoff,),
        )
        return {r["ticker"]: float(r["price"]) for r in rows}

    def get_latest_news(self) -> dict[str, Any] | None:
        return db.fetch_one(
            """SELECT summary, sentiment, key_events, confidence
               FROM news_reports ORDER BY created_at DESC LIMIT 1;"""
        )

    def get_portfolio(self) -> list[dict[str, Any]]:
        return db.fetch_all("SELECT ticker, quantity, avg_price FROM portfolio;")

    # ---------- Анализ ----------

    def analyze(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        """Отдать акции в LLM и получить оценки."""
        news = self.get_latest_news()
        portfolio = self.get_portfolio()

        if news:
            news_block = f"""НОВОСТНОЙ ФОН:
Sentiment: {news['sentiment']}
Вывод: {news['summary']}"""
        else:
            news_block = "НОВОСТИ: нет данных."

        prompt = f"""Цены акций РФ (в рублях):
{json.dumps(prices, ensure_ascii=False, indent=2)}

Текущий портфель:
{json.dumps(portfolio, ensure_ascii=False, indent=2, default=float) if portfolio else "пусто"}

{news_block}

Оцени каждую акцию из списка выше."""

        raw = self.think(prompt=prompt, system=SYSTEM_PROMPT)

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").replace("json", "", 1).strip()

        try:
            results = json.loads(cleaned)
        except json.JSONDecodeError as e:
            self.log.error(f"Невалидный JSON: {raw[:300]}")
            raise ValueError(f"JSON parse error: {e}")

        if not isinstance(results, list):
            raise ValueError("LLM вернула не массив")

        return results

    def save_reports(self, reports: list[dict[str, Any]]) -> int:
        """Сохраняем отчёты в БД."""
        from datetime import date
        count = 0
        for r in reports:
            try:
                db.execute(
                    """INSERT INTO stock_reports
                       (agent_name, report_date, ticker, sentiment, score, reasoning, confidence)
                       VALUES (%s, %s, %s, %s, %s, %s, %s);""",
                    (
                        self.name,
                        date.today(),
                        r.get("ticker", "?"),
                        r.get("sentiment", "neutral"),
                        float(r.get("score", 5.0)),
                        r.get("reasoning", ""),
                        float(r.get("confidence", 0.5)),
                    ),
                )
                count += 1
            except Exception as e:
                self.log.error(f"Ошибка сохранения {r.get('ticker')}: {e}")
        return count

    # ---------- Основной цикл ----------

    def run(self) -> dict[str, Any]:
        self.log.info("Stock Analyst просыпается...")

        prices = self.get_stock_prices()
        if not prices:
            self.log.warning("Нет свежих цен акций")
            return {"error": "no_prices"}

        self.log.info(f"Анализируем {len(prices)} акций: {list(prices.keys())}")

        try:
            reports = self.analyze(prices)
        except Exception as e:
            self.log.error(f"Ошибка анализа: {e}")
            return {"error": str(e)}

        saved = self.save_reports(reports)
        self.log.info(f"Сохранено {saved} отчётов по акциям")

        # Логируем топ
        if reports:
            top = sorted(reports, key=lambda x: float(x.get("score", 0)), reverse=True)[:3]
            self.log.info(f"Топ-3: {[(r['ticker'], r['score']) for r in top]}")

        return {"saved": saved, "tickers": list(prices.keys())}
