"""Crypto Analyst — оценка криптовалют через LLM. Работает 24/7."""
import json
from datetime import date, datetime, timedelta
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db


SYSTEM_PROMPT = """Ты — старший аналитик криптовалютного рынка.
Твоя задача: дать оценку перспектив каждой монеты из списка.

Для каждой монеты оцени:
- sentiment: bullish (рост) | neutral (боковик) | bearish (падение)
- score: от 0 до 10 (насколько привлекательна для покупки сейчас)
- confidence: 0.0-1.0 (твоя уверенность)
- reasoning: 1-2 предложения — почему такая оценка

Учитывай:
- Текущую цену (есть в промпте)
- Твои знания о монете (BTC как «цифровое золото», ETH как платформа, альткоины как высокорисковые)
- Общий крипто-рынок и новостной фон (если передан)
- Волатильность крипты (она всегда выше акций)

Требования:
- Кратко, по делу, на русском.
- Крипта — высокорисковый актив. Будь осторожен с высокими оценками.
- Не выдумывай точные цифры (капитализацию, объёмы), которых нет в промпте.

Отвечай СТРОГО JSON-массивом:
[
  {"ticker": "BTC", "sentiment": "bullish", "score": 7.5, "confidence": 0.7, "reasoning": "..."}
]
"""

MAX_DATA_AGE_HOURS = 6


class CryptoAnalyst(BaseAgent):
    """Аналитик криптовалют. Работает без выходных."""

    DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

    def __init__(self, name: str = "Crypto-01") -> None:
        super().__init__(name=name, role="crypto_analyst")

    def get_crypto_prices(self) -> dict[str, float]:
        """Свежие цены крипты. Конвертируем в рубли через USD_RUB."""
        cutoff = datetime.now() - timedelta(hours=MAX_DATA_AGE_HOURS)

        # Курс
        usd_row = db.fetch_one(
            """SELECT price FROM market_prices
               WHERE ticker = 'USD_RUB' AND updated_at >= %s
               ORDER BY updated_at DESC LIMIT 1;""",
            (cutoff,),
        )
        usd_rub = float(usd_row["price"]) if usd_row else 90.0

        # Крипта
        rows = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, price
               FROM market_prices
               WHERE asset_type = 'crypto' AND updated_at >= %s
               ORDER BY ticker, updated_at DESC;""",
            (cutoff,),
        )

        prices = {}
        for r in rows:
            # Цены в USD, конвертируем в рубли
            prices[r["ticker"]] = float(r["price"]) * usd_rub

        self.log.info(f"Крипто-цены (в ₽): {len(prices)}, курс USD/RUB={usd_rub:.2f}")
        return prices

    def get_latest_news(self) -> dict[str, Any] | None:
        return db.fetch_one(
            """SELECT summary, sentiment, key_events, confidence
               FROM news_reports ORDER BY created_at DESC LIMIT 1;"""
        )

    def analyze(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        news = self.get_latest_news()

        if news:
            news_block = f"""НОВОСТНОЙ ФОН:
Sentiment: {news['sentiment']}
Вывод: {news['summary']}"""
        else:
            news_block = "НОВОСТИ: нет данных."

        prompt = f"""Цены криптовалют (в рублях):
{json.dumps(prices, ensure_ascii=False, indent=2, default=float)}

{news_block}

Оцени каждую монету."""

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
                    """INSERT INTO crypto_reports
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
        self.log.info("Crypto Analyst просыпается...")

        # Работаем всегда — крипта 24/7

        prices = self.get_crypto_prices()
        if not prices:
            self.log.warning("Нет свежих крипто-цен")
            return {"error": "no_prices"}

        self.log.info(f"Анализируем {len(prices)} монет")

        try:
            reports = self.analyze(prices)
        except Exception as e:
            self.log.error(f"Ошибка анализа: {e}")
            return {"error": str(e)}

        saved = self.save_reports(reports)
        self.log.info(f"Сохранено {saved} отчётов")

        if reports:
            top = sorted(reports, key=lambda x: float(x.get("score", 0)), reverse=True)[:3]
            self.log.info(f"Топ-3: {[(r['ticker'], r['score']) for r in top]}")

        return {"saved": saved, "tickers": list(prices.keys())}
