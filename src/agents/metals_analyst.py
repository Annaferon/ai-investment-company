"""Metals Analyst — оценка драгметаллов через LLM. Работает 24/7."""
import json
from datetime import date, datetime, timedelta
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db


SYSTEM_PROMPT = """Ты — старший аналитик рынка драгоценных металлов.
Твоя задача: дать оценку перспектив каждого металла из списка.

Для каждого металла оцени:
- sentiment: bullish (рост) | neutral (боковик) | bearish (падение)
- score: от 0 до 10 (насколько привлекателен для покупки сейчас)
- confidence: 0.0-1.0 (твоя уверенность)
- reasoning: 1-2 предложения — почему такая оценка

Учитывай:
- Текущую цену за грамм (есть в промпте)
- Твои знания о металле (золото — защитный актив, серебро — промышленный + защитный)
- Макроэкономику: ставки ЦБ, инфляция, курс доллара (если есть новости)
- Спрос центральных банков (они активно покупают золото)
- Геополитику (металлы реагируют на риски)

Требования:
- Кратко, по делу, на русском.
- Драгметаллы — защитный актив. Используй для сохранения капитала.
- Не выдумывай точные цифры, которых нет в промпте.

Отвечай СТРОГО JSON-массивом:
[
  {"ticker": "GOLD", "sentiment": "bullish", "score": 7.5, "confidence": 0.7, "reasoning": "..."}
]
"""

MAX_DATA_AGE_HOURS = 72  # ЦБ не обновляет по выходным — даём больше окно


class MetalsAnalyst(BaseAgent):
    """Аналитик драгметаллов."""

    DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

    def __init__(self, name: str = "Metals-01") -> None:
        super().__init__(name=name, role="metals_analyst")

    def get_metals_prices(self) -> dict[str, float]:
        """Свежие цены драгметаллов из БД (с учётом выходных — 72 часа)."""
        cutoff = datetime.now() - timedelta(hours=MAX_DATA_AGE_HOURS)
        rows = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, price
               FROM market_prices
               WHERE asset_type = 'metal'
                 AND ticker IN ('GOLD', 'SILVER')
                 AND updated_at >= %s
               ORDER BY ticker, updated_at DESC;""",
            (cutoff,),
        )
        return {r["ticker"]: float(r["price"]) for r in rows}

    def get_latest_news(self) -> dict[str, Any] | None:
        return db.fetch_one(
            """SELECT summary, sentiment, key_events, confidence
               FROM news_reports ORDER BY created_at DESC LIMIT 1;"""
        )

    def get_latest_market(self) -> dict[str, Any] | None:
        return db.fetch_one(
            """SELECT overall_trend, volatility_level, summary
               FROM market_reports ORDER BY created_at DESC LIMIT 1;"""
        )

    def analyze(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        news = self.get_latest_news()
        market = self.get_latest_market()

        news_block = "НОВОСТИ: нет данных."
        if news:
            news_block = f"""НОВОСТНОЙ ФОН:
Sentiment: {news['sentiment']}
Вывод: {news['summary']}"""

        market_block = ""
        if market:
            market_block = f"""РЫНОК:
Тренд: {market['overall_trend']}
Волатильность: {market['volatility_level']}"""

        prompt = f"""Цены драгметаллов (в рублях за грамм):
{json.dumps(prices, ensure_ascii=False, indent=2, default=float)}

{news_block}

{market_block}

Оцени каждый металл."""

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
        count = 0
        for r in reports:
            try:
                db.execute(
                    """INSERT INTO metals_reports
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

    def run(self) -> dict[str, Any]:
        self.log.info("Metals Analyst просыпается...")

        prices = self.get_metals_prices()
        if not prices:
            self.log.warning("Нет свежих цен металлов")
            return {"error": "no_prices"}

        self.log.info(f"Анализируем {len(prices)} металлов")

        try:
            reports = self.analyze(prices)
        except Exception as e:
            self.log.error(f"Ошибка анализа: {e}")
            return {"error": str(e)}

        saved = self.save_reports(reports)
        self.log.info(f"Сохранено {saved} отчётов")

        if reports:
            top = sorted(reports, key=lambda x: float(x.get("score", 0)), reverse=True)
            self.log.info(f"Результат: {[(r['ticker'], r['score']) for r in top]}")

        return {"saved": saved, "tickers": list(prices.keys())}
