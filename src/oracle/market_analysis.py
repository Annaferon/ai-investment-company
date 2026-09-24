"""Расчёты по рыночным данным для Market Analyst."""
from datetime import datetime, timedelta
from typing import Any

from src.core.database import db
from src.core.logger import get_logger

log = get_logger("market_analysis")


def get_price_changes(hours: int = 24) -> list[dict[str, Any]]:
    """Для каждого тикера: текущая цена vs цена N часов назад."""
    cutoff = datetime.now() - timedelta(hours=hours)

    # Все тикеры в базе
    tickers = db.fetch_all("SELECT DISTINCT ticker, asset_type FROM market_prices;")

    changes = []
    for row in tickers:
        ticker = row["ticker"]
        asset_type = row["asset_type"]

        # Последняя цена
        latest = db.fetch_one(
            """SELECT price, updated_at FROM market_prices
               WHERE ticker = %s ORDER BY updated_at DESC LIMIT 1;""",
            (ticker,),
        )
        # Цена N часов назад (или самая старая доступная)
        old = db.fetch_one(
            """SELECT price, updated_at FROM market_prices
               WHERE ticker = %s AND updated_at <= %s
               ORDER BY updated_at DESC LIMIT 1;""",
            (ticker, cutoff),
        )
        if not old:
            old = db.fetch_one(
                """SELECT price, updated_at FROM market_prices
                   WHERE ticker = %s ORDER BY updated_at ASC LIMIT 1;""",
                (ticker,),
            )

        if not latest or not old or float(old["price"]) == 0:
            continue

        latest_price = float(latest["price"])
        old_price = float(old["price"])
        change_pct = (latest_price - old_price) / old_price * 100

        changes.append({
            "ticker": ticker,
            "asset_type": asset_type,
            "price": latest_price,
            "old_price": old_price,
            "change_pct": round(change_pct, 2),
        })

    return changes


def summarize_changes(changes: list[dict[str, Any]]) -> str:
    """Текстовое резюме для LLM."""
    if not changes:
        return "Нет данных о динамике цен."

    lines = []
    for c in changes:
        sign = "+" if c["change_pct"] >= 0 else ""
        lines.append(
            f"{c['ticker']} ({c['asset_type']}): {c['price']:.2f} "
            f"({sign}{c['change_pct']}% за 24ч)"
        )
    return "\n".join(lines)


def get_top_movers(changes: list[dict[str, Any]], n: int = 5) -> list[dict[str, Any]]:
    """Топ-N по росту и топ-N по падению."""
    sorted_by_change = sorted(changes, key=lambda x: x["change_pct"], reverse=True)
    top_up = sorted_by_change[:n]
    top_down = sorted_by_change[-n:]
    return top_up + top_down
