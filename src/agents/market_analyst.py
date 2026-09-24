"""Market Data Analyst — технический анализ рыночных данных."""
import json
from datetime import date
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db
from src.oracle.market_analysis import (
    get_price_changes,
    summarize_changes,
    get_top_movers,
)


SYSTEM_PROMPT = """Ты — технический аналитик инвестиционной компании.
Твоя задача: проанализировать динамику цен активов за последние 24 часа.

Оцени:
- Общий тренд рынка: bullish | bearish | sideways
- Уровень волатильности: low | medium | high
- Какие активы растут и падают сильнее всего
- Есть ли тревожные сигналы (резкие падения, аномалии)

Требования:
- Кратко, по делу, на русском.
- Не выдумывай данные, которых нет.
- Если данных мало — так и скажи.

Отвечай СТРОГО в формате JSON, без пояснений:
{
  "overall_trend": "bullish" | "bearish" | "sideways",
  "volatility_level": "low" | "medium" | "high",
  "summary": "2-3 предложения общего вывода",
  "key_movers": [
    {"ticker": "SBER", "change_pct": -2.5, "comment": "краткий комментарий"}
  ],
  "confidence": 0.0-1.0
}
"""


class MarketAnalyst(BaseAgent):
    """Аналитик рыночных данных."""

    DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

    def __init__(self, name: str = "Market-01") -> None:
        super().__init__(name=name, role="market_analyst")

    def collect(self) -> list[dict[str, Any]]:
        """Считаем динамику цен за 24 часа."""
        return get_price_changes(hours=24)

    def analyze(self, changes: list[dict[str, Any]]) -> dict[str, Any]:
        """Отдать данные в LLM и получить структурированный анализ."""
        text = summarize_changes(changes)
        movers = get_top_movers(changes, n=3)
        movers_text = summarize_changes(movers)

        prompt = f"""Динамика цен за последние 24 часа:

{text}

Топ-движения:
{movers_text}

Проанализируй рынок."""

        raw = self.think(prompt=prompt, system=SYSTEM_PROMPT)

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").replace("json", "", 1).strip()

        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as e:
            self.log.error(f"LLM вернул невалидный JSON: {raw[:300]}")
            raise ValueError(f"Невалидный JSON: {e}")

        return result

    def save_report(self, analysis: dict[str, Any]) -> None:
        db.execute(
            """INSERT INTO market_reports
               (agent_name, report_date, summary, overall_trend,
                key_movers, volatility_level, confidence)
               VALUES (%s, %s, %s, %s, %s, %s, %s);""",
            (
                self.name,
                date.today(),
                analysis.get("summary", ""),
                analysis.get("overall_trend", "sideways"),
                json.dumps(analysis.get("key_movers", []), ensure_ascii=False),
                analysis.get("volatility_level", "medium"),
                float(analysis.get("confidence", 0.0)),
            ),
        )
        self.log.info("Рыночный отчёт сохранён в БД")

    def run(self) -> dict[str, Any]:
        self.log.info("Market Analyst просыпается...")

        changes = self.collect()
        if not changes:
            self.log.warning("Нет рыночных данных для анализа")
            return {"error": "no_data"}

        self.log.info(f"Проанализируем {len(changes)} активов")

        try:
            analysis = self.analyze(changes)
        except Exception as e:
            self.log.error(f"Не смог проанализировать: {e}")
            return {"error": str(e)}

        self.save_report(analysis)

        self.log.info(
            f"Trend: {analysis.get('overall_trend')}, "
            f"volatility: {analysis.get('volatility_level')}"
        )
        return analysis
