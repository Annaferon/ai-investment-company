"""Запуск AI Architect."""
import sys

from src.core.config import config
from src.core.database import db
from src.core.logger import logger
from src.core.notifier import notify
from src.agents.ai_architect import AIArchitect


def main() -> int:
    logger.info("=" * 60)
    logger.info("AI Architect запускается...")
    logger.info("=" * 60)

    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        notify("🧠 ARCHITECT", f"❌ Ошибка конфигурации: {e}")
        return 1

    if not db.health_check():
        logger.error("Нет связи с Supabase")
        notify("🧠 ARCHITECT", "❌ Нет связи с Supabase")
        return 1

    architect = AIArchitect(name="Architect-01")
    result = architect.run()

    if "error" in result:
        notify("🧠 ARCHITECT", f"❌ Ошибка: {result['error']}")
    else:
        summary = result.get("summary", "")
        proposals = result.get("proposals") or []
        total_evaluated = result.get("total_evaluated", 0)

        body = (
            f"📊 Оценённых сигналов в системе: {total_evaluated}\n\n"
            f"📌 {summary}\n"
        )

        if proposals:
            body += f"\n💡 Предложения ({len(proposals)}):\n"
            for p in proposals[:5]:
                if not isinstance(p, dict):
                    continue
                emoji = {
                    "hire": "🟢", "fire": "🔴", "modify": "🟡",
                    "consolidate": "🔵", "observe": "⚪",
                }.get(p.get("proposal_type"), "⚪")
                body += (
                    f"\n{emoji} [{p.get('proposal_type', '?')}] "
                    f"{p.get('title', '?')}\n"
                    f"   {p.get('reasoning', '')[:250]}\n"
                )
        else:
            body += "\n(пока нет предложений)"

        notify("🧠 ARCHITECT", body)

    logger.info("=" * 60)
    logger.info(f"Результат: {result}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
