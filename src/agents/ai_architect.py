"""AI Architect — анализирует работу агентов и предлагает улучшения."""
import json
from datetime import date
from typing import Any

from src.agents.base import BaseAgent
from src.core.database import db


SYSTEM_PROMPT = """Ты — AI Architect инвестиционной компании.
Твоя задача: проанализировать работу агентов и предложить улучшения.

⚠️ КРИТИЧЕСКОЕ ПРАВИЛО:
Поле "proposal_type" ДОЛЖНО быть ТОЧНО ОДНИМ ИЗ:
- "hire" (нанять) — НЕ пиши "нанять", только "hire"
- "fire" (уволить) — НЕ пиши "уволить", только "fire"
- "modify" (доработать) — НЕ пиши "доработать", только "modify"
- "consolidate" (объединить) — НЕ пиши "объединить", только "consolidate"
- "observe" (наблюдать) — НЕ пиши "наблюдать", только "observe"

Остальные поля (title, reasoning, target_agent) — НА РУССКОМ языке.

ПРАВИЛА:
1. Если оценённых сигналов < 100 — только "observe".
2. Увольнение: точность < 40% И минимум 20 оценённых сигналов.
3. Дубли: если 2 агента делают похожее — объединить.
4. Не больше 3 предложений за раз.
5. Будь конкретен. Не «улучшить анализ» — а «разделить Crypto на Stable и Meme».

Отвечай СТРОГО JSON:
{
  "summary": "1-2 предложения состояния компании на русском",
  "proposals": [
    {
      "proposal_type": "fire",
      "target_agent": "Crypto-01",
      "title": "Уволить агента Crypto-01",
      "reasoning": "Точность 22.9% при 35 сигналах, что ниже порога 40%.",
      "confidence": 0.9
    }
  ]
}
"""

CANDIDATE_AGENTS = [
    "Геополитический аналитик",
    "Аналитик облигаций (ОФЗ)",
    "Аналитик Forex",
    "Аналитик сырья (нефть, газ)",
    "On-Chain аналитик",
]

MIN_EVALUATED_FOR_PROPOSALS = 100
MIN_EVALUATED_PER_AGENT = 20

# Допустимые типы (для валидации)
VALID_TYPES = {"hire", "fire", "modify", "consolidate", "observe"}


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
                 AND table_name LIKE '%%_reports'
               ORDER BY table_name;"""
        )
        return [r.get("table_name") or "?" for r in rows]

    def get_total_evaluated(self) -> int:
        row = db.fetch_one(
            """SELECT COUNT(*) as cnt FROM signal_outcomes
               WHERE is_correct IS NOT NULL;"""
        )
        return int(row.get("cnt") or 0) if row else 0

    # ---------- Сбор контекста ----------

    def _build_context(self) -> tuple[str, int, bool]:
        total_evaluated = self.get_total_evaluated()
        agents = self.get_agent_stats()
        trader = self.get_trader_stats()
        risk = self.get_risk_stats()
        tables = self.get_report_tables()

        watch_mode = total_evaluated < MIN_EVALUATED_FOR_PROPOSALS

        lines = ["СТАТИСТИКА СИСТЕМЫ:"]
        lines.append(f"Всего оценённых сигналов: {total_evaluated}")
        lines.append(f"Порог для предложений: {MIN_EVALUATED_FOR_PROPOSALS}")
        lines.append(f"Режим: {'НАБЛЮДЕНИЕ' if watch_mode else 'РАБОТА'}")
        lines.append(f"Таблиц отчётов: {len(tables)}")
        lines.append("")

        lines.append("АГЕНТЫ (статистика):")
        for a in agents:
            lines.append(
                f"  • {a['agent_name']}: всего {a['total']}, "
                f"оценено {a['evaluated']}, точность {a['accuracy']:.1f}%"
            )
        lines.append("")

        lines.append("TRADER:")
        lines.append(
            f"  • всего {trader['total']}, оценено {trader['evaluated']}, "
            f"точность {trader['accuracy']:.1f}%"
        )
        lines.append("")

        lines.append("RISK MANAGER:")
        lines.append(
            f"  • проверок: {risk['total']}, одобрено {risk['approved']}, "
            f"отклонено {risk['rejected']}, скорректировано {risk['adjusted']}"
        )
        lines.append("")

        if not watch_mode:
            lines.append("КАНДИДАТЫ НА ДОБАВЛЕНИЕ:")
            for agent in CANDIDATE_AGENTS:
                lines.append(f"  • {agent}")

        return "\n".join(lines), total_evaluated, watch_mode

    # ---------- Анализ ----------

    def analyze(self) -> dict[str, Any]:
        prompt, total_evaluated, watch_mode = self._build_context()

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

        proposals = result.get("proposals") or []

        # Нормализация proposal_type
        normalized = []
        for p in proposals:
            if not isinstance(p, dict):
                continue

            ptype = str(p.get("proposal_type", "")).lower().strip()

            # Если на русском — переводим
            ru_to_en = {
                "нанять": "hire",
                "уволить": "fire",
                "доработать": "modify",
                "объединить": "consolidate",
                "наблюдать": "observe",
            }
            if ptype in ru_to_en:
                ptype = ru_to_en[ptype]

            # Если невалидно — пропускаем
            if ptype not in VALID_TYPES:
                self.log.warning(f"Невалидный proposal_type: {p.get('proposal_type')} — пропускаем")
                continue

            # В режиме наблюдения — только observe
            if watch_mode and ptype != "observe":
                continue

            p["proposal_type"] = ptype
            normalized.append(p)

        return {
            "summary": str(result.get("summary", "")),
            "proposals": normalized,
            "total_evaluated": total_evaluated,
            "watch_mode": watch_mode,
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
        self.log.info(
            f"Сохранено: {saved} | режим: "
            f"{'НАБЛЮДЕНИЕ' if analysis.get('watch_mode') else 'РАБОТА'}"
        )

        return {
            "summary": analysis.get("summary"),
            "proposals": analysis.get("proposals"),
            "saved": saved,
            "total_evaluated": total_evaluated,
            "watch_mode": analysis.get("watch_mode", True),
        }
