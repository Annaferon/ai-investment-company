"""Macro Economist — макроэкономический контекст для Trader.
Оценивает среду: ставка ЦБ, инфляция, рубль, нефть, ФРС."""
import json
from datetime import date, datetime, timedelta
from typing import Any, Optional

from src.agents.base import BaseAgent
from src.core.config import config
from src.core.database import db


SYSTEM_PROMPT = """Ты — главный макроэкономист инвестиционной компании.
Твоя задача: описать текущую макроэкономическую среду и дать рекомендации Trader'у.

ОПРЕДЕЛИ РЕЖИМ:
- "tight" (жёсткий): ставка ЦБ высокая, инфляция высокая → давит на акции
- "neutral" (нейтральный): ставка умеренная → нет чёткого влияния
- "loose" (мягкий): ставка снижается, инфляция под контролем → поддержка акций

ВЛИЯНИЕ НА СЕКТОРА (implications):
- Банки (SBER, MTSS): высокие ставки = маржа растёт, но риски дефолтов
- Экспортёры (LKOH, GAZP, TATN, GMKN): слабый рубль + высокая нефть = плюс
- Металлы (GMKN, PLZL): зависят от мировых цен
- Крипта: растёт при снижении ФРС и слабом долларе
- Золото: защита при инфляции и геополитике

ТРЕБОВАНИЯ:
- Кратко, по делу, на русском.
- 2-4 предложения в summary.
- implications — массив объектов: [{"sector": "banks", "outlook": "negative", "reasoning": "..."}]

Отвечай СТРОГО JSON:
{
  "regime": "tight" | "neutral" | "loose",
  "summary": "2-4 предложения общей картины",
  "implications": [
    {"sector": "banks", "outlook": "negative"|"neutral"|"positive", "reasoning": "..."},
    {"sector": "exporters", "outlook": "...", "reasoning": "..."},
    {"sector": "crypto", "outlook": "...", "reasoning": "..."},
    {"sector": "gold", "outlook": "...", "reasoning": "..."}
  ],
  "confidence": 0.0-1.0
}
"""


def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


class MacroEconomist(BaseAgent):
    """Макроэкономист — определяет среду для Trader."""

    DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

    def __init__(self, name: str = "Macro-01") -> None:
        super().__init__(name=name, role="macro_economist")

    # ---------- Данные ----------

    def _get_current(self, ticker: str) -> Optional[float]:
        row = db.fetch_one(
            """SELECT price FROM market_prices
               WHERE ticker = %s ORDER BY updated_at DESC LIMIT 1;""",
            (ticker,),
        )
        return float(row["price"]) if row else None

    def _get_price_n_days_ago(self, ticker: str, n: int) -> Optional[float]:
        """Цена N дней назад (из price_history)."""
        target = date.today() - timedelta(days=n)
        row = db.fetch_one(
            """SELECT price FROM price_history
               WHERE ticker = %s AND price_date <= %s
               ORDER BY price_date DESC LIMIT 1;""",
            (ticker, target),
        )
        return float(row["price"]) if row else None

    def get_macro_metrics(self) -> dict:
        """Собираем макро-метрики."""
        usd_rub_now = self._get_current("USD_RUB") or 0.0
        usd_rub_30d = self._get_price_n_days_ago("USD_RUB", 30) or usd_rub_now
        usd_rub_change_30d = (
            (usd_rub_now - usd_rub_30d) / usd_rub_30d * 100
            if usd_rub_30d > 0 else 0
        )

        gold_now = self._get_current("GOLD") or 0.0
        gold_30d = self._get_price_n_days_ago("GOLD", 30) or gold_now
        gold_change_30d = (
            (gold_now - gold_30d) / gold_30d * 100
            if gold_30d > 0 else 0
        )

        return {
            "usd_rub": usd_rub_now,
            "usd_rub_change_30d": usd_rub_change_30d,
            "gold": gold_now,
            "gold_change_30d": gold_change_30d,
            "key_rate": config.KEY_RATE,
            "inflation": config.INFLATION,
            "fed_rate": config.FED_RATE,
            "brent": config.BRENT_PRICE,
        }

    def get_recent_news(self, days: int = 3) -> list[dict]:
        rows = db.fetch_all(
            """SELECT summary, sentiment, created_at
               FROM news_reports
               WHERE created_at >= NOW() - INTERVAL '%s days'
               ORDER BY created_at DESC LIMIT 5;""",
            (days,),
        )
        return [dict(r) for r in rows]

    # ---------- Анализ ----------

    def analyze(self) -> dict[str, Any]:
        metrics = self.get_macro_metrics()
        news = self.get_recent_news()

        news_block = "НОВОСТИ: нет данных."
        if news:
            lines = ["СВЕЖИЕ НОВОСТИ:"]
            for n in news:
                lines.append(f"  • [{n['sentiment']}] {n['summary'][:200]}")
            news_block = "\n".join(lines)

        prompt = f"""МАКРО-МЕТРИКИ:

Ключевая ставка ЦБ РФ: {metrics['key_rate']}%
Инфляция: {metrics['inflation']}%
Ставка ФРС: {metrics['fed_rate']}%
Курс USD/RUB: {metrics['usd_rub']:.2f} (за 30 дней: {metrics['usd_rub_change_30d']:+.1f}%)
Нефть Brent: ${metrics['brent']}
Золото: {metrics['gold']:.0f} ₽/г (за 30 дней: {metrics['gold_change_30d']:+.1f}%)

{news_block}

Определи макро-режим и дай рекомендации Trader'у."""

        raw = self.think(prompt=prompt, system=SYSTEM_PROMPT)

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").replace("json", "", 1).strip()

        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first != -1 and last > first:
            cleaned = cleaned[first:last + 1]

        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as e:
            self.log.error(f"Невалидный JSON: {raw[:300]}")
            raise ValueError(f"JSON parse error: {e}")

        if not isinstance(result, dict):
            raise ValueError("LLM вернула не объект")

        return {
            "regime": str(result.get("regime", "neutral")),
            "summary": str(result.get("summary", "")),
            "implications": result.get("implications") or [],
            "confidence": _safe_float(result.get("confidence"), 0.5),
        }

    # ---------- Сохранение ----------

    def save_report(self, analysis: dict, metrics: dict) -> None:
        try:
            impl_json = json.dumps(analysis.get("implications", []), ensure_ascii=False)
        except Exception:
            impl_json = "[]"

        db.execute(
            """INSERT INTO macro_reports
               (agent_name, report_date, regime, key_rate, inflation,
                usd_rub, brent, fed_rate, summary, implications, confidence)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);""",
            (
                self.name,
                date.today(),
                analysis.get("regime", "neutral"),
                metrics.get("key_rate"),
                metrics.get("inflation"),
                metrics.get("usd_rub"),
                metrics.get("brent"),
                metrics.get("fed_rate"),
                analysis.get("summary", ""),
                impl_json,
                analysis.get("confidence", 0.5),
            ),
        )
        self.log.info("Макро-отчёт сохранён")

    # ---------- Запуск ----------

    def run(self) -> dict[str, Any]:
        self.log.info("Macro Economist просыпается...")

        try:
            metrics = self.get_macro_metrics()
            analysis = self.analyze()
        except Exception as e:
            self.log.error(f"Ошибка анализа: {e}")
            return {"error": str(e)}

        try:
            self.save_report(analysis, metrics)
        except Exception as e:
            self.log.error(f"Ошибка сохранения: {e}")
            return {"error": f"save_failed: {e}"}

        self.log.info(
            f"Режим: {analysis.get('regime')}, "
            f"уверенность: {analysis.get('confidence')}"
        )

        return {
            "regime": analysis.get("regime"),
            "summary": analysis.get("summary"),
            "implications": analysis.get("implications"),
            "confidence": analysis.get("confidence"),
        }
