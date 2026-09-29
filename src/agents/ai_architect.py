"""AI Architect — анализирует работу агентов и предлагает улучшения."""
import json
from datetime import date
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db


SYSTEM_PROMPT = """Ты — AI Architect инвестиционной компании.
Твоя задача: проанализировать работу агентов и предложить улучшения.

ТИПЫ ПРЕДЛОЖЕНИЙ (proposal_type):
- "hire" — нанять нового агента
- "fire" — уволить
- "modify" — доработать агента
- "consolidate" — объединить
- "observe" — наблюдать

ПРАВИЛА:
1. Если оценённых сигналов < 100 — не давай оценок «кого уволить».
2. Увольнение: точность < 40% И минимум 20 оценённых сигналов.
3. Дубли: если 2 агента делают похожее — объединить.
4. Не больше 3 предложений за раз.
5. Будь конкретен.

Отвечай СТРОГО JSON:
{
  "summary": "1-2 предложения состояния компании",
  "proposals": [
    {
      "proposal_type": "hire",
      "target_agent": "...",
      "title": "...",
      "reasoning": "...",
      "confidence": 0.0-1.0
    }
  ]
}
"""

EXPECTED_AGENTS = [
    "News Analyst", "Market Analyst", "Stock Analyst", "Crypto Analyst",
    "Metals Analyst", "Historical Analyst", "Macro Economist",
    "Trader", "Risk Manager", "Auditor",
    "Opportunity Hunter", "Portfolio Manager", "Investment Committee",
]

CANDIDATE_AGENTS = [
    "Geopolitical Analyst", "Bonds Analyst", "Forex Analyst",
    "Commodities Analyst", "On-Chain Analyst",
]


class AIArchitect(BaseAgent):
    """AI Architect — мета-агент."""

    DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

    def __init__(self, name: str = "Architect-01") -> None:
        super().__init__(name=name, role="ai_architect")

    # ---------- Данные ----------

    def get_agent_stats(self) -> list[dict]:
        rows = db.fetch_all(
            """SELECT agent_name,
                      COUNT(*) as total,
                      COALESCE(SUM(CASE WHEN is_correct THEN 1 ELSE 0 END), 0) as correct,
                      COALESCE(SUM(CASE WHEN is_correct IS NULL THEN 1 ELSE 0 END), 0) as pending
               FROM signal_outcomes
               GROUP BY agent_name
               ORDER BY agent_name;"""
        )
        result = []
        for r in rows:
            total = int(r.get("total") or 0)
            correct = int(r.get("correct") or 0)
            pending = int(r.get("pending") or 0)
            evaluated = total - pending
            result.append({
                "agent_name": r.get("agent_name") or "?",
                "total": total,
                "evaluated": evaluated,
                "correct": correct,
                "accuracy": correct / evaluated * 100 if evaluated > 0 else 0,
            })
        return result

    def get_trader_stats(self) -> dict:
        row = db.fetch_one(
            """SELECT COUNT(*) as total,
                      COALESCE(SUM(CASE WHEN is_correct THEN 1 ELSE 0 END), 0) as correct,
                      COALESCE(SUM(CASE WHEN is_correct IS NULL THEN 1 ELSE 0 END), 0) as pending
               FROM trade_outcomes;"""
        )
        if not row:
            return {"total": 0, "evaluated": 0, "correct": 0, "accuracy": 0}
        total = int(row.get("total") or 0)
        correct = int(row.get("correct") or 0)
        pending = int(row.get("pending") or 0)
        evaluated = total - pending
        return {
            "total": total,
            "evaluated": evaluated,
            "correct": correct,
            "accuracy": correct / evaluated * 100 if evaluated > 0 else 0,
        }

    def get_risk_stats(self) -> dict:
        row = db.fetch_one(
            """SELECT COUNT(*) as total,
                      COALESCE(SUM(CASE WHEN approved THEN 1 ELSE 0 END), 0) as approved,
                      COALESCE(SUM(CASE WHEN NOT approved THEN 1 ELSE 0 END), 0) as rejected,
                      COALESCE(SUM(CASE WHEN was_adjusted THEN 1 ELSE 0 END), 0) as adjusted
               FROM risk_checks;"""
        )
        if not row:
            return {"total": 0, "approved": 0, "rejected": 0, "adjusted": 0}
        return {
            "total": int(row.get("total") or 0),
            "approved": int(row.get("approved") or 0),
            "rejected": int(row.get("rejected") or 0),
            "adjusted": int(row.get("adjusted") or 0),
        }

    def get_report_tables(self) -> list[str]:
        rows = db.fetch_all(
            """SELECT table_name FROM information_schema.tables
               WHERE table_schema = 'public'
                 AND table_name LIKE '%_reports'
               ORDER BY table_name;"""
        )
        return [r.get("table_name") or "?" for r in rows]

    def get_total_evaluated(self) -> int:
        row = db.fetch_one(
            """SELECT COUNT(*) as cnt FROM signal_outcomes
               WHERE is_correct IS NOT NULL;"""
        )
        return int(row.get("cnt") or 0) if row else 0

    # ---------- Анализ ----------

    def analyze(self) -> dict[str, Any]:
        self.log.info("Шаг 1: get_total_evaluated")
        total_evaluated = self.get_total_evaluated()
        self.log.info(f"  → {total_evaluated}")

        self.log.info("Шаг 2: get_agent_stats")
        agents = self.get_agent_stats()
        self.log.info(f"  → {len(agents)} агентов")

        self.log.info("Шаг 3: get_trader_stats")
        trader = self.get_trader_stats()
        self.log.info(f"  → {trader}")

        self.log.info("Шаг 4: get_risk_stats")
        risk = self.get_risk_stats()
        self.log.info(f"  → {risk}")

        self.log.info("Шаг 5: get_report_tables")
        tables = self.get_report_tables()
        self.log.info(f"  → {tables}")

        self.log.info("Шаг 6: формирование промпта")
        lines = ["СТАТИСТИКА СИСТЕМЫ:"]
        lines.append(f"Всего оценённых сигналов: {total_evaluated}")
        lines.append(f"Таблиц отчётов: {len(tables)}")
        lines.append(f"Таблицы: {', '.join(tables)}")
        lines.append("")

        lines.append("АГЕНТЫ (точность):")
        for a in agents:
            lines.append(
                f"  • {a['agent_name']}: {a['accuracy']:.1f}% "
                f"({a['correct']}/{a['evaluated']}, ожидает {a['total'] - a['evaluated']})"
            )
        lines.append("")

        lines.append("TRADER:")
        lines.append(
            f"  • Точность: {trader['accuracy']:.1f}% "
            f"({trader['correct']}/{trader['evaluated']})"
        )
        lines.append("")

        lines.append("RISK MANAGER:")
        lines.append(
            f"  • Проверок: {risk['total']}, "
            f"одобрено {risk['approved']}, отклонено {risk['rejected']}, "
            f"скорректировано {risk['adjusted']}"
        )
        lines.append("")

        lines.append("КАНДИДАТЫ НА ДОБАВЛЕНИЕ:")
        for agent in CANDIDATE_AGENTS:
            lines.append(f"  • {agent}")

        prompt = "\n".join(lines)
        self.log.info(f"  → промпт: {len(prompt)} символов")

        self.log.info("Шаг 7: вызов LLM")
        raw = self.think(prompt=prompt, system=SYSTEM_PROMPT)
        self.log.info(f"  → получено: {len(raw)} символов")

        self.log.info("Шаг 8: парсинг JSON")
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
            "summary": str(result.get("summary", "")),
            "proposals": result.get("proposals") or [],
        }

    # ---------- Сохранение ----------

    def save_proposals(self, analysis: dict) -> int:
        count = 0
        for p in analysis.get("proposals", []):
            if not isinstance(p, dict):
                continue
            try:
                db.execute(
                    """INSERT INTO architect_proposals
                       (report_date, proposal_type, target_agent, title,
                        reasoning, evidence, confidence, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending');""",
                    (
                        date.today(),
                        str(p.get("proposal_type", "observe")),
                        str(p.get("target_agent", "")),
                        str(p.get("title", "")),
                        str(p.get("reasoning", "")),
                        json.dumps({"source": "ai_architect"}, ensure_ascii=False),
                        float(p.get("confidence", 0.5) or 0.5),
                    ),
                )
                count += 1
            except Exception as e:
                self.log.error(f"Ошибка сохранения proposal: {e}")
        return count

    # ---------- Запуск ----------

    def run(self) -> dict[str, Any]:
        self.log.info("AI Architect просыпается...")

        total_evaluated = self.get_total_evaluated()
        self.log.info(f"Оценённых сигналов: {total_evaluated}")

        try:
            analysis = self.analyze()
        except Exception as e:
            self.log.exception(f"Ошибка анализа: {e}")
            return {"error": str(e)}

        saved = self.save_proposals(analysis)
        self.log.info(f"Сохранено предложений: {saved}")

        return {
            "summary": analysis.get("summary"),
            "proposals": analysis.get("proposals"),
            "saved": saved,
            "total_evaluated": total_evaluated,
        }
