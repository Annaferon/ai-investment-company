"""AI Architect — анализирует работу агентов и предлагает улучшения.
Пока данных мало — только наблюдает. Когда накопит статистику — даёт предложения."""
import json
from datetime import date, timedelta
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db


SYSTEM_PROMPT = """Ты — AI Architect инвестиционной компании.
Твоя задача: проанализировать работу агентов и предложить улучшения.

ТИПЫ ПРЕДЛОЖЕНИЙ (proposal_type):
- "hire" — нанять нового агента (например, Geopolitical Analyst)
- "fire" — уволить (например, агент с точностью <40% и 20+ сигналами)
- "modify" — доработать агента (например, разделить Crypto на Stable/Meme)
- "consolidate" — объединить (например, если 2 агента делают похожее)
- "observe" — наблюдать (мало данных, но есть вопрос)

ПРАВИЛА:
1. Если оценённых сигналов < 100 — не давай оценок «кого уволить».
   Вместо этого — предложения "observe" и "hire" (каких агентов не хватает).
2. Увольнение: агент с точностью < 40% И минимум 20 оценённых сигналов.
3. Дубли: если 2 агента делают похожее — предложи объединить.
4. Не предлагай больше 3 предложений за раз. Только самые важные.
5. Будь конкретен. Не «улучшить анализ» — а «разделить Crypto на Stable (BTC/ETH) и Meme (DOGE/SHIB)».

Отвечай СТРОГО JSON:
{
  "summary": "1-2 предложения общего состояния компании",
  "proposals": [
    {
      "proposal_type": "hire" | "fire" | "modify" | "consolidate" | "observe",
      "target_agent": "название агента или роль",
      "title": "краткое название предложения",
      "reasoning": "2-4 предложения обоснования",
      "confidence": 0.0-1.0
    }
  ]
}
"""

# Агенты из roadmap — которых ещё нет (для проверки «hire»)
EXPECTED_AGENTS = [
    "News Analyst",
    "Market Analyst",
    "Stock Analyst",
    "Crypto Analyst",
    "Metals Analyst",
    "Historical Analyst",
    "Macro Economist",
    "Trader",
    "Risk Manager",
    "Auditor",
    "Opportunity Hunter",
    "Portfolio Manager",
    "Investment Committee",
]

# Кандидаты на добавление (из roadmap)
CANDIDATE_AGENTS = [
    "Geopolitical Analyst",
    "Bonds Analyst",
    "Forex Analyst",
    "Commodities Analyst",
    "On-Chain Analyst",
]


class AIArchitect(BaseAgent):
    """AI Architect — мета-агент, анализирует систему."""

    DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

    def __init__(self, name: str = "Architect-01") -> None:
        super().__init__(name=name, role="ai_architect")

    # ---------- Данные ----------

    def get_agent_stats(self) -> list[dict]:
        """Точность каждого агента из signal_outcomes."""
        rows = db.fetch_all(
            """SELECT agent_name,
                      COUNT(*) as total,
                      SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) as correct,
                      SUM(CASE WHEN is_correct IS NULL THEN 1 ELSE 0 END) as pending
               FROM signal_outcomes
               GROUP BY agent_name
               ORDER BY agent_name;"""
        )
        result = []
        for r in rows:
            total = int(r["total"])
            correct = int(r["correct"] or 0)
            evaluated = total - int(r["pending"] or 0)
            result.append({
                "agent_name": r["agent_name"],
                "total": total,
                "evaluated": evaluated,
                "correct": correct,
                "accuracy": correct / evaluated * 100 if evaluated > 0 else 0,
            })
        return result

    def get_trader_stats(self) -> dict:
        row = db.fetch_one(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) as correct,
                      SUM(CASE WHEN is_correct IS NULL THEN 1 ELSE 0 END) as pending
               FROM trade_outcomes;"""
        )
        if not row:
            return {"total": 0, "evaluated": 0, "correct": 0, "accuracy": 0}
        total = int(row["total"])
        correct = int(row["correct"] or 0)
        evaluated = total - int(row["pending"] or 0)
        return {
            "total": total,
            "evaluated": evaluated,
            "correct": correct,
            "accuracy": correct / evaluated * 100 if evaluated > 0 else 0,
        }

    def get_risk_stats(self) -> dict:
        row = db.fetch_one(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN approved THEN 1 ELSE 0 END) as approved,
                      SUM(CASE WHEN NOT approved THEN 1 ELSE 0 END) as rejected,
                      SUM(CASE WHEN was_adjusted THEN 1 ELSE 0 END) as adjusted
               FROM risk_checks;"""
        )
        if not row:
            return {"total": 0, "approved": 0, "rejected": 0, "adjusted": 0}
        return {
            "total": int(row["total"] or 0),
            "approved": int(row["approved"] or 0),
            "rejected": int(row["rejected"] or 0),
            "adjusted": int(row["adjusted"] or 0),
        }

    def get_report_tables(self) -> list[str]:
        rows = db.fetch_all(
            """SELECT table_name FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name LIKE '%_reports'
               ORDER BY table_name;"""
        )
        return [r["table_name"] for r in rows]

    def get_total_evaluated(self) -> int:
        row = db.fetch_one(
            """SELECT COUNT(*) as cnt FROM signal_outcomes
               WHERE is_correct IS NOT NULL;"""
        )
        return int(row["cnt"]) if row else 0

    # ---------- Анализ ----------

    def analyze(self) -> dict[str, Any]:
        total_evaluated = self.get_total_evaluated()
        agents = self.get_agent_stats()
        trader = self.get_trader_stats()
        risk = self.get_risk_stats()
        tables = self.get_report_tables()

        # Формируем контекст для LLM
        lines = [f"СТАТИСТИКА СИСТЕМЫ:"]
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
            f"  • Всего проверок: {risk['total']}, "
            f"одобрено {risk['approved']}, отклонено {risk['rejected']}, "
            f"скорректировано {risk['adjusted']}"
        )
        lines.append("")

        lines.append("НА РАССМОТРЕНИИ — какие агенты есть в roadmap, но не в системе:")
        for agent in EXPECTED_AGENTS:
            # Проверяем по имени в таблицах reports
            found = any(agent.lower().replace(" ", "_").replace("-", "_")[:8] in t for t in tables)
            if not found and agent not in ("Trader", "Risk Manager", "Auditor"):
                lines.append(f"  • {agent} — нет таблицы reports")
        lines.append("")

        lines.append(f"КАНДИДАТЫ НА ДОБАВЛЕНИЕ: {', '.join(CANDIDATE_AGENTS)}")

        prompt = "\n".join(lines)

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
            self.log.error(f"Ошибка анализа: {e}")
            return {"error": str(e)}

        saved = self.save_proposals(analysis)
        self.log.info(f"Сохранено предложений: {saved}")

        return {
            "summary": analysis.get("summary"),
            "proposals": analysis.get("proposals"),
            "saved": saved,
            "total_evaluated": total_evaluated,
        }
