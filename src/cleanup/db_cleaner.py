"""Очистка оперативных данных из БД.
Удаляем только market_prices старше N дней.
Все данные для анализа (price_history, *_outcomes, *_reports) НЕ трогаем."""
from typing import Any

from src.core.database import db
from src.core.logger import get_logger

log = get_logger("cleaner")

# Что чистим (оперативные данные)
CLEANUP_RULES = [
    {
        "table": "market_prices",
        "date_column": "updated_at",
        "retention_days": 7,
        "description": "Свежие цены от Oracle",
    },
]


def _get_table_size_mb(table: str) -> float:
    """Размер таблицы в МБ (приблизительно)."""
    row = db.fetch_one(
        """SELECT pg_total_relation_size(%s) AS size_bytes;""",
        (table,),
    )
    if not row or not row.get("size_bytes"):
        return 0.0
    return float(row["size_bytes"]) / (1024 * 1024)


def _count_to_delete(table: str, date_column: str, days: int) -> int:
    row = db.fetch_one(
        f"""SELECT COUNT(*) AS cnt FROM {table}
            WHERE {date_column} < NOW() - INTERVAL '{days} days';"""
    )
    return int(row["cnt"]) if row else 0


def _delete_old(table: str, date_column: str, days: int) -> int:
    """Удаляем старые записи. Возвращаем количество удалённых."""
    before = _count_to_delete(table, date_column, days)
    if before == 0:
        return 0

    db.execute(
        f"""DELETE FROM {table}
            WHERE {date_column} < NOW() - INTERVAL '{days} days';"""
    )
    return before


def run_cleanup() -> dict[str, Any]:
    """Главный цикл очистки."""
    log.info("Cleanup запускается...")

    results = []
    total_deleted = 0

    for rule in CLEANUP_RULES:
        table = rule["table"]
        date_col = rule["date_column"]
        days = rule["retention_days"]

        size_before = _get_table_size_mb(table)

        deleted = _delete_old(table, date_col, days)
        total_deleted += deleted

        size_after = _get_table_size_mb(table)

        log.info(
            f"{table}: удалено {deleted} записей "
            f"({size_before:.2f} МБ → {size_after:.2f} МБ)"
        )

        results.append({
            "table": table,
            "deleted": deleted,
            "size_before_mb": round(size_before, 2),
            "size_after_mb": round(size_after, 2),
            "freed_mb": round(size_before - size_after, 2),
            "retention_days": days,
        })

    log.info(f"Cleanup завершён. Всего удалено: {total_deleted}")

    return {
        "results": results,
        "total_deleted": total_deleted,
    }
