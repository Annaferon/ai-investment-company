"""Crypto Analyst — технический анализ криптовалют. Работает 24/7."""
import json
from datetime import date, datetime, timedelta
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db
from src.oracle.technical_indicators import analyze_all, format_for_llm


SYSTEM_PROMPT = """Ты — профессиональный крипто-трейдер с 10-летним опытом.
Твоя задача: проанализировать технические данные по монетам и дать оценку.

Тебе даются ГОТОВЫЕ индикаторы (не считай их сам):
- Цена (current)
- Диапазон за год (min_year — max_year)
- Позиция в диапазоне (0% = дно, 100% = пик)
- RSI (>70 = перекуплен, <30 = перепродан)
- MA20, MA50, MA200 (скользящие средние)
- ATR% (волатильность)
- Изменения за 24ч / 7д / 30д / 365д

ПРАВИЛА АНАЛИЗА:
1. Позиция в диапазоне:
   - 0-20% → СИЛЬНАЯ зона покупки (дно)
   - 20-40% → зона покупки
   - 40-60% → нейтрально
   - 60-80% → зона продажи
   - 80-100% → СИЛЬНАЯ зона продажи (пик)

2. RSI:
   - <30 → перепродан, возможен отскок → покупка
   - 30-50 → слабость
   - 50-70 → норма
   - >70 → перекуплен → продажа или ожидание

3. MA-тренды:
   - Цена > MA200 → долгосрочный бычий тренд
   - Цена < MA200 → долгосрочный медвежий тренд
   - MA20 > MA50 → краткосрочный bullish
   - MA20 < MA50 → краткосрочный bearish

4. Волатильность (ATR%):
   - >5% → очень рискованно, малый размер позиции
   - 2-5% → нормально
   - <2% → стабильно

5. Комбинированный сигнал:
   - Дно + RSI<30 + цена у MA200 → STRONG BUY
   - Пик + RSI>70 → SELL
   - Нейтрально → HOLD

ОТДЕЛЬНО ПРО МЕМКОИНЫ (DOGE, SHIB, PEPE, WIF, BONK):
- Очень высокий риск
- Только малые позиции (макс 1-2% капитала)
- Жёсткий стоп-лосс
- Их score не должен превышать 6.0 обычно

Отвечай СТРОГО JSON-массивом:
[
  {
    "ticker": "BTC",
    "sentiment": "bullish" | "neutral" | "bearish",
    "score": 0-10,
    "confidence": 0.0-1.0,
    "entry_point": true|false,
    "reasoning": "развёрнутое объяснение на основе индикаторов (3-5 предложений)"
  }
]
"""

MAX_DATA_AGE_HOURS = 6
CRYPTO_TICKERS = ["BTC", "ETH", "SOL", "BNB", "LINK",
                  "DOGE", "SHIB", "PEPE", "WIF", "BONK"]


class CryptoAnalyst(BaseAgent):
    """Аналитик криптовалют с техническим анализом."""

    DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

    def __init__(self, name: str = "Crypto-01") -> None:
        super().__init__(name=name, role="crypto_analyst")

    def get_crypto_prices(self) -> dict[str, float]:
        """Свежие цены крипты в рублях."""
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

    def get_latest_news(self) -> dict[str, Any] | None:
        return db.fetch_one(
            """SELECT summary, sentiment, key_events, confidence
               FROM news_reports ORDER BY created_at DESC LIMIT 1;"""
        )

    def analyze(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        """Технический анализ + LLM."""
        # 1. Считаем технические индикаторы
        analysis = analyze_all(CRYPTO_TICKERS)
        indicators_text = format_for_llm(analysis)

        # 2. Новости
        news = self.get_latest_news()
        if news:
            news_block = f"""НОВОСТНОЙ ФОН:
Sentiment: {news['sentiment']}
Вывод: {news['summary']}"""
        else:
            news_block = "НОВОСТИ: нет данных."

        # 3. Цены в рублях
        prices_block = "\n".join(f"  • {k}: {v:,.2f} ₽" for k, v in prices.items())

        prompt = f"""ТЕКУЩИЕ ЦЕНЫ (₽):
{prices_block}

ТЕХНИЧЕСКИЙ АНАЛИЗ (годовая история):
{indicators_text}

{news_block}

Дай оценку каждой монете. Обрати особое внимание на точки входа (entry_point=true,
если монета в зоне покупки: низкая позиция в диапазоне + RSI<40 + другие сигналы)."""

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

        # Ищем точки входа
        entries = [r for r in reports if r.get("entry_point")]
        if entries:
            self.log.info(f"🎯 ТОЧКИ ВХОДА: {[r['ticker'] for r in entries]}")

        if reports:
            top = sorted(reports, key=lambda x: float(x.get("score", 0)), reverse=True)[:3]
            self.log.info(f"Топ-3: {[(r['ticker'], r['score']) for r in top]}")

        return {
            "saved": saved,
            "tickers": list(prices.keys()),
            "entries": [r["ticker"] for r in entries],
        }
