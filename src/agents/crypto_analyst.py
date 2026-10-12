"""Crypto Analyst — прогноз направления криптовалют на 3-7 дней вперёд.
Работает 24/7. Монеты без истории не анализируются."""
import json
from datetime import date, datetime, timedelta
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db
from src.oracle.technical_indicators import analyze_ticker, format_for_llm


SYSTEM_PROMPT = """Ты — прогнозист криптовалютного рынка.
Твоя задача: ПРЕДСКАЗАТЬ направление цены на ближайшие 3-7 дней.

⚠️ КРИТИЧНО: sentiment — это ПРОГНОЗ, а не описание текущего момента.

ПРАВИЛА ВЫБОРА SENTIMENT:

🟢 bullish — ты считаешь, что через 3-7 дней цена БУДЕТ ВЫШЕ текущей на >2%.
   Признаки: RSI < 40 (перепродан), позиция в диапазоне < 30% (дно),
   MA20 > MA50 (тренд вверх), цена у MA200 (поддержка).

🔴 bearish — ты считаешь, что через 3-7 дней цена БУДЕТ НИЖЕ текущей на >2%.
   Признаки: RSI > 70 (перекуплен), позиция > 80% (пик),
   MA20 < MA50 (тренд вниз), пробой MA200 вниз.

⚪ neutral — используй ТОЛЬКО если уверен, что цена будет ±2% от текущей.
   Признаки: RSI 40-60, позиция 40-60%, тренды противоречат друг другу.
   ⚠️ Не более 20% сигналов могут быть neutral. Чаще выбирай сторону.

ЛОГИКА ПРОГНОЗА:
1. Сначала определи — есть ли чёткий сигнал:
   • Дно (позиция <20%) + RSI<40 → СИЛЬНЫЙ bullish
   • Пик (позиция >80%) + RSI>70 → СИЛЬНЫЙ bearish
   • Коррекция в тренде вверх (MA20>MA50, RSI 30-50) → bullish
   • Отскок в тренде вниз (MA20<MA50, RSI 50-70) → bearish
2. Если сигнал слабый — снижай confidence, но выбирай направление.
3. Пиши reasoning как ПРОГНОЗ: «Ожидаю рост на X% за счёт Y».

ОТДЕЛЬНО ПРО МЕМКОИНЫ (DOGE, SHIB, PEPE):
- Очень высокая волатильность
- Прогнозы могут быть ошибочными → confidence ≤ 0.6
- Score обычно ≤ 6.0

ФОРМАТ ОТВЕТА — строго JSON (без пояснений):
[
  {
    "ticker": "BTC",
    "sentiment": "bullish" | "bearish" | "neutral",
    "score": 0-10,
    "confidence": 0.0-1.0,
    "entry_point": true|false,
    "reasoning": "прогноз на 3-7 дней, 2-4 предложения с опорой на индикаторы"
  }
]
"""

MAX_DATA_AGE_HOURS = 6
ALL_CRYPTO_TICKERS = ["BTC", "ETH", "SOL", "BNB", "LINK",
                      "DOGE", "SHIB", "PEPE", "WIF", "BONK"]


class CryptoAnalyst(BaseAgent):
    """Аналитик-прогнозист криптовалют."""

    DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

    def __init__(self, name: str = "Crypto-01") -> None:
        super().__init__(name=name, role="crypto_analyst")

    def get_crypto_prices(self) -> dict[str, float]:
        cutoff = datetime.now() - timedelta(hours=MAX_DATA_AGE_HOURS)

        usd_row = db.fetch_one(
            """SELECT price FROM market_prices
               WHERE ticker = 'USD_RUB' AND updated_at >= %s
               ORDER BY updated_at DESC LIMIT 1;""",
            (cutoff,),
        )
        usd_rub = float(usd_row["price"]) if usd_row else 90.0

        rows = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, price
               FROM market_prices
               WHERE asset_type = 'crypto' AND updated_at >= %s
               ORDER BY ticker, updated_at DESC;""",
            (cutoff,),
        )

        prices = {}
        for r in rows:
            prices[r["ticker"]] = float(r["price"]) * usd_rub

        self.log.info(f"Крипто-цены (₽): {len(prices)}, курс USD/RUB={usd_rub:.2f}")
        return prices

    def get_tickers_with_history(self, min_points: int = 30) -> list[str]:
        rows = db.fetch_all(
            """SELECT ticker, COUNT(*) as cnt
               FROM price_history
               WHERE asset_type = 'crypto'
               GROUP BY ticker
               HAVING COUNT(*) >= %s;""",
            (min_points,),
        )
        return [r["ticker"] for r in rows]

    def get_latest_news(self) -> dict[str, Any] | None:
        return db.fetch_one(
            """SELECT summary, sentiment, key_events, confidence
               FROM news_reports ORDER BY created_at DESC LIMIT 1;"""
        )

    def analyze(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        tickers_with_history = self.get_tickers_with_history()
        if not tickers_with_history:
            self.log.warning("Нет монет с историей")
            return []

        tickers = [t for t in ALL_CRYPTO_TICKERS if t in tickers_with_history]
        skipped = [t for t in ALL_CRYPTO_TICKERS if t not in tickers_with_history]

        self.log.info(f"Анализируем: {tickers}")
        if skipped:
            self.log.info(f"Пропускаем (нет истории): {skipped}")

        analysis = {}
        for t in tickers:
            try:
                analysis[t] = analyze_ticker(t)
            except Exception as e:
                self.log.error(f"Ошибка индикаторов {t}: {e}")

        indicators_text = format_for_llm(analysis)

        news = self.get_latest_news()
        if news:
            news_block = f"""НОВОСТНОЙ ФОН:
Sentiment: {news['sentiment']}
Вывод: {news['summary']}"""
        else:
            news_block = "НОВОСТИ: нет данных."

        prices_block = "\n".join(
            f"  • {k}: {prices[k]:,.2f} ₽"
            for k in tickers if k in prices
        )

        prompt = f"""ТЕКУЩИЕ ЦЕНЫ (₽):
{prices_block}

ТЕХНИЧЕСКИЙ АНАЛИЗ (история 720 дней):
{indicators_text}

{news_block}

⚠️ ЗАДАЧА: дай ПРОГНОЗ направления цены на 3-7 дней вперёд
для каждой монеты ({', '.join(tickers)}).

Не описывай текущее состояние — предсказывай БУДУЩЕЕ.
Используй neutral ТОЛЬКО если уверен, что цена останется ±2%.
Чаще выбирай bullish или bearish.

entry_point=true — если считаешь, что СЕЙЧАС хорошая точка входа
(позиция <20% + RSI<40 + другие сигналы разворота)."""

        raw = self.think(prompt=prompt, system=SYSTEM_PROMPT)

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").replace("json", "", 1).strip()

        first_bracket = cleaned.find("[")
        last_bracket = cleaned.rfind("]")
        if first_bracket != -1 and last_bracket > first_bracket:
            cleaned = cleaned[first_bracket:last_bracket + 1]

        try:
            results = json.loads(cleaned)
        except json.JSONDecodeError as e:
            self.log.error(f"Невалидный JSON: {raw[:300]}")
            raise ValueError(f"JSON parse error: {e}")

        if not isinstance(results, list):
            raise ValueError("LLM вернула не массив")

        results = [r for r in results if isinstance(r, dict) and r.get("ticker") in tickers]

        # Считаем neutral — не больше 20%
        total = len(results)
        neutral = sum(1 for r in results if r.get("sentiment") == "neutral")
        if total > 0 and neutral / total > 0.2:
            self.log.warning(
                f"Слишком много neutral: {neutral}/{total} "
                f"({neutral/total*100:.0f}%). Норма ≤20%."
            )

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

        prices = self.get_crypto_prices()
        if not prices:
            self.log.warning("Нет свежих крипто-цен")
            return {"error": "no_prices"}

        try:
            reports = self.analyze(prices)
        except Exception as e:
            self.log.error(f"Ошибка анализа: {e}")
            return {"error": str(e)}

        if not reports:
            self.log.warning("Нет монет для анализа")
            return {"error": "no_tickers_with_history"}

        saved = self.save_reports(reports)
        self.log.info(f"Сохранено {saved} отчётов")

        entries = [r for r in reports if r.get("entry_point")]
        if entries:
            self.log.info(f"🎯 ТОЧКИ ВХОДА: {[r['ticker'] for r in entries]}")

        if reports:
            top = sorted(reports, key=lambda x: float(x.get("score", 0)), reverse=True)[:3]
            self.log.info(f"Топ-3: {[(r['ticker'], r['score']) for r in top]}")

        # Статистика по sentiment
        from collections import Counter
        sentiments = Counter(r.get("sentiment") for r in reports)
        self.log.info(f"Sentiment: {dict(sentiments)}")

        return {
            "saved": saved,
            "tickers": [r["ticker"] for r in reports],
            "entries": [r["ticker"] for r in entries],
            "sentiments": dict(sentiments),
        }
