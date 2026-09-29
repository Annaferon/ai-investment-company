"""Historical Analyst — анализ долгосрочной истории цен.
Считает динамику за 1д/7д/30д/90д/365д/720д и отдаёт LLM для оценки."""
import json
from datetime import date
from typing import Any, Optional

from src.agents.base import BaseAgent
from src.core.database import db


SYSTEM_PROMPT = """Ты — старший аналитик долгосрочных трендов инвестиционной компании.
Твоя задача: на основе истории цен за 2 года дать оценку каждой монете/акции.

Тебе даются ГОТОВЫЕ метрики:
- Текущая цена
- Изменения за: 1д / 7д / 30д / 90д / 365д / 720д
- Минимум и максимум за 720 дней
- Позиция в диапазоне (0% = дно, 100% = пик)
- Тренд (растёт / падает / боковик)

ПРАВИЛА АНАЛИЗА:
1. Позиция в диапазоне 720д:
   - 0-20% → историческое дно → зона покупки
   - 20-40% → ниже среднего → умеренная покупка
   - 40-60% → нейтрально
   - 60-80% → выше среднего → осторожно
   - 80-100% → исторический пик → зона продажи

2. Смотри на согласованность периодов:
   - Все периоды плюс → устойчивый рост
   - Краткосрочно +, долгосрочно − → отскок в падающем тренде (риск)
   - Краткосрочно −, долгосрочно + → коррекция в растущем тренде (возможность)

3. Тренд:
   - "bullish" — все периоды положительные или цена выше среднего
   - "bearish" — большинство периодов отрицательные
   - "sideways" — периоды разнонаправленные

Отвечай СТРОГО JSON-массивом:
[
  {
    "ticker": "BTC",
    "trend": "bullish" | "bearish" | "sideways",
    "sentiment": "bullish" | "neutral" | "bearish",
    "score": 0-10,
    "confidence": 0.0-1.0,
    "reasoning": "2-3 предложения на русском с опорой на метрики"
  }
]
"""


def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _pct_change(current: float, past: Optional[float]) -> Optional[float]:
    if past is None or past <= 0 or current is None:
        return None
    return (current - past) / past * 100


class HistoricalAnalyst(BaseAgent):
    """Аналитик долгосрочных трендов."""

    DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

    def __init__(self, name: str = "Historical-01") -> None:
        super().__init__(name=name, role="historical_analyst")

    # ---------- Данные ----------

    def get_tickers_with_history(self, min_points: int = 60) -> list[dict]:
        rows = db.fetch_all(
            """SELECT ticker, asset_type, COUNT(*) as cnt
               FROM price_history
               GROUP BY ticker, asset_type
               HAVING COUNT(*) >= %s
               ORDER BY ticker;""",
            (min_points,),
        )
        return [dict(r) for r in rows]

    def get_history(self, ticker: str) -> list[dict]:
        rows = db.fetch_all(
            """SELECT price_date, price FROM price_history
               WHERE ticker = %s
               ORDER BY price_date ASC;""",
            (ticker,),
        )
        return [{"date": r["price_date"], "price": float(r["price"])} for r in rows]

    def get_current_price(self, ticker: str) -> Optional[float]:
        row = db.fetch_one(
            """SELECT price FROM market_prices
               WHERE ticker = %s ORDER BY updated_at DESC LIMIT 1;""",
            (ticker,),
        )
        return float(row["price"]) if row else None

    def calc_metrics(self, ticker: str, asset_type: str) -> Optional[dict]:
        history = self.get_history(ticker)
        if len(history) < 30:
            return None

        prices = [h["price"] for h in history]
        current = self.get_current_price(ticker) or prices[-1]

        def price_n_days_ago(n: int) -> Optional[float]:
            if len(prices) <= n:
                return None
            return prices[-(n + 1)]

        change_1d = _pct_change(current, price_n_days_ago(1))
        change_7d = _pct_change(current, price_n_days_ago(7))
        change_30d = _pct_change(current, price_n_days_ago(30))
        change_90d = _pct_change(current, price_n_days_ago(90))
        change_365d = _pct_change(current, price_n_days_ago(365))
        change_720d = _pct_change(current, price_n_days_ago(720))

        lo, hi = min(prices), max(prices)
        range_pos = ((current - lo) / (hi - lo) * 100) if hi > lo else 50.0

        long_changes = [c for c in [change_90d, change_365d, change_720d] if c is not None]
        if long_changes and all(c > 5 for c in long_changes):
            trend = "bullish"
        elif long_changes and all(c < -5 for c in long_changes):
            trend = "bearish"
        else:
            trend = "sideways"

        return {
            "ticker": ticker,
            "asset_type": asset_type,
            "current": current,
            "change_1d": change_1d,
            "change_7d": change_7d,
            "change_30d": change_30d,
            "change_90d": change_90d,
            "change_365d": change_365d,
            "change_720d": change_720d,
            "min_720d": lo,
            "max_720d": hi,
            "range_position": round(range_pos, 1),
            "trend": trend,
        }

    # ---------- Анализ ----------

    def _fmt_pct(self, v: Optional[float]) -> str:
        if v is None:
            return "—"
        return f"{v:+.1f}%"

    def _format_metrics(self, metrics: list[dict]) -> str:
        lines = []
        for m in metrics:
            lines.append(f"\n=== {m['ticker']} ({m['asset_type']}) ===")
            lines.append(f"Цена: {m['current']:,.4f}")
            lines.append(
                f"Изменения: 1д {self._fmt_pct(m['change_1d'])} | "
                f"7д {self._fmt_pct(m['change_7d'])} | "
                f"30д {self._fmt_pct(m['change_30d'])} | "
                f"90д {self._fmt_pct(m['change_90d'])} | "
                f"365д {self._fmt_pct(m['change_365d'])} | "
                f"720д {self._fmt_pct(m['change_720d'])}"
            )
            lines.append(
                f"Диапазон 720д: {m['min_720d']:,.4f} — {m['max_720d']:,.4f} "
                f"(позиция: {m['range_position']}%)"
            )
            lines.append(f"Тренд: {m['trend']}")
        return "\n".join(lines)

    def analyze(self, metrics: list[dict]) -> list[dict[str, Any]]:
        text = self._format_metrics(metrics)
        prompt = f"Исторический анализ за 720 дней:\n\n{text}\n\nДай оценку каждой монете/акции."

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

    # ---------- Сохранение ----------

    def save_reports(self, metrics: list[dict], analysis: list[dict]) -> int:
        metrics_by_ticker = {m["ticker"]: m for m in metrics}
        count = 0

        for a in analysis:
            ticker = a.get("ticker")
            if not ticker or ticker not in metrics_by_ticker:
                continue
            m = metrics_by_ticker[ticker]

            try:
                db.execute(
                    """INSERT INTO historical_reports
                       (agent_name, report_date, ticker, asset_type,
                        current_price, change_1d, change_7d, change_30d,
                        change_90d, change_365d, change_720d,
                        min_720d, max_720d, range_position, trend,
                        sentiment, score, reasoning, confidence)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                               %s, %s, %s, %s, %s, %s, %s, %s);""",
                    (
                        self.name, date.today(), ticker, m["asset_type"],
                        m["current"],
                        m["change_1d"], m["change_7d"], m["change_30d"],
                        m["change_90d"], m["change_365d"], m["change_720d"],
                        m["min_720d"], m["max_720d"], m["range_position"], m["trend"],
                        a.get("sentiment", "neutral"),
                        _safe_float(a.get("score"), 5.0),
                        a.get("reasoning", ""),
                        _safe_float(a.get("confidence"), 0.5),
                    ),
                )
                count += 1
            except Exception as e:
                self.log.error(f"Ошибка сохранения {ticker}: {e}")

        return count

    # ---------- Запуск ----------

    def run(self) -> dict[str, Any]:
        self.log.info("Historical Analyst просыпается...")

        tickers = self.get_tickers_with_history()
        if not tickers:
            self.log.warning("Нет тикеров с историей")
            return {"error": "no_history"}

        metrics = []
        for t in tickers:
            try:
                m = self.calc_metrics(t["ticker"], t["asset_type"])
                if m:
                    metrics.append(m)
            except Exception as e:
                self.log.error(f"Ошибка метрик {t['ticker']}: {e}")

        if not metrics:
            return {"error": "no_metrics"}

        self.log.info(f"Посчитано метрик: {len(metrics)}")

        try:
            analysis = self.analyze(metrics)
        except Exception as e:
            self.log.error(f"Ошибка анализа: {e}")
            return {"error": str(e)}

        saved = self.save_reports(metrics, analysis)
        self.log.info(f"Сохранено {saved} отчётов")

        if analysis:
            top = sorted(analysis, key=lambda x: _safe_float(x.get("score")), reverse=True)[:3]
            self.log.info(f"Топ-3: {[(r.get('ticker'), r.get('score')) for r in top]}")

        return {
            "saved": saved,
            "tickers": [m["ticker"] for m in metrics],
        }
