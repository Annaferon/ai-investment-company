"""News Analyst — собирает новости и анализирует их через LLM."""
import json
from datetime import date
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db
from src.oracle.news_fetcher import fetch_recent_news, news_to_text


SYSTEM_PROMPT = """Ты — старший новостной аналитик инвестиционной компании.
Твоя задача: проанализировать новости за сутки и выделить ГЛАВНОЕ, что может влиять на российские акции, криптовалюты и драгметаллы.

Требования:
- Отсекай шум. Оставляй только то, что реально влияет на рынки.
- Учитывай контекст: заявления ЦБ РФ, санкции, геополитика, сырьевые цены, отчётности крупных компаний.
- Пиши на русском, кратко и по делу.

Отвечай СТРОГО в формате JSON, без пояснений:
{
  "sentiment": "positive" | "negative" | "neutral",
  "summary": "2-3 предложения общего вывода по дню",
  "key_events": [
    {"title": "краткое название события", "impact": "positive|negative|neutral", "assets": ["SBER", "GAZP"]}
  ],
  "confidence": 0.0-1.0
}
"""


def _safe_str(value: Any, default: str = "") -> str:
    """Преобразует значение в строку, защита от None."""
    if value is None:
        return default
    return str(value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Преобразует значение в float, защита от None."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


class NewsAnalyst(BaseAgent):
    """Аналитик новостей — «глаза и уши» компании."""

    DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

    def __init__(self, name: str = "News-01") -> None:
        super().__init__(name=name, role="news_analyst")

    def collect(self) -> list[dict[str, Any]]:
        return fetch_recent_news(hours=24, max_per_source=15)

    def analyze(self, news: list[dict[str, Any]]) -> dict[str, Any]:
        text = news_to_text(news)
        prompt = f"Новости за последние 24 часа:\n\n{text}\n\nПроанализируй."

        raw = self.think(prompt=prompt, system=SYSTEM_PROMPT)

        if not raw:
            raise ValueError("LLM вернул пустой ответ")

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").replace("json", "", 1).strip()

        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as e:
            self.log.error(f"LLM вернул невалидный JSON: {cleaned[:300]}")
            raise ValueError(f"Невалидный JSON от LLM: {e}")

        if not isinstance(result, dict):
            raise ValueError("LLM вернул не объект")

        # Нормализуем поля
        result["sentiment"] = _safe_str(result.get("sentiment"), "neutral")
        result["summary"] = _safe_str(result.get("summary"), "")
        result["confidence"] = _safe_float(result.get("confidence"), 0.5)

        # key_events — обязательно список словарей
        events = result.get("key_events")
        if not isinstance(events, list):
            events = []
        clean_events = []
        for e in events:
            if isinstance(e, dict):
                clean_events.append({
                    "title": _safe_str(e.get("title"), "?"),
                    "impact": _safe_str(e.get("impact"), "neutral"),
                    "assets": e.get("assets") if isinstance(e.get("assets"), list) else [],
                })
        result["key_events"] = clean_events

        return result

    def save_report(self, analysis: dict[str, Any], sources_count: int) -> None:
        """Сохраняем отчёт в БД."""
        summary = _safe_str(analysis.get("summary"), "")
        sentiment = _safe_str(analysis.get("sentiment"), "neutral")
        confidence = _safe_float(analysis.get("confidence"), 0.5)
        events = analysis.get("key_events") or []

        try:
            events_json = json.dumps(events, ensure_ascii=False)
        except Exception:
            events_json = "[]"

        db.execute(
            """INSERT INTO news_reports
               (agent_name, report_date, summary, sentiment, key_events, sources_count, confidence)
               VALUES (%s, %s, %s, %s, %s, %s, %s);""",
            (
                self.name,
                date.today(),
                summary,
                sentiment,
                events_json,
                sources_count,
                confidence,
            ),
        )
        self.log.info("Новостной отчёт сохранён в БД")

    def run(self) -> dict[str, Any]:
        self.log.info("News Analyst просыпается...")

        news = self.collect()
        if not news:
            self.log.warning("Новостей не найдено — отчёт не создаём")
            return {"error": "no_news"}

        try:
            analysis = self.analyze(news)
        except Exception as e:
            self.log.error(f"Не смог проанализировать: {e}")
            return {"error": str(e)}

        try:
            self.save_report(analysis, sources_count=len(news))
        except Exception as e:
            self.log.error(f"Ошибка сохранения отчёта: {e}")
            return {"error": f"save_failed: {e}"}

        self.log.info(
            f"Sentiment: {analysis.get('sentiment')}, "
            f"событий: {len(analysis.get('key_events', []))}"
        )
        return analysis
