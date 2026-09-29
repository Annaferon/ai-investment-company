"""Ежедневный отчёт в Telegram."""
import os
from datetime import date, datetime, timedelta

import requests

from src.core.database import db
from src.core.logger import get_logger

log = get_logger("reporter")

TG_API = "https://api.telegram.org/bot{token}/sendMessage"


def get_daily_stats() -> dict:
    period_end = datetime.now()
    period_start = period_end - timedelta(hours=24)

    # Кэш + вложенный капитал
    acc = db.fetch_one(
        "SELECT cash, initial_capital, COALESCE(total_deposits, 0) AS deposits FROM account WHERE id = 1;"
    )
    cash = float(acc["cash"]) if acc else 0.0
    initial = float(acc["initial_capital"]) if acc else 0.0
    deposits = float(acc["deposits"]) if acc else 0.0
    invested_capital = initial + deposits

    # USD/RUB
    usd_row = db.fetch_one(
        """SELECT price FROM market_prices
           WHERE ticker = 'USD_RUB' ORDER BY updated_at DESC LIMIT 1;"""
    )
    usd_rub = float(usd_row["price"]) if usd_row else 90.0

    # Цены
    price_rows = db.fetch_all(
        """SELECT DISTINCT ON (ticker) ticker, price, asset_type
           FROM market_prices ORDER BY ticker, updated_at DESC;"""
    )
    prices = {}
    for r in price_rows:
        ticker = r["ticker"]
        price = float(r["price"])
        if r["asset_type"] == "crypto":
            price = price * usd_rub
        prices[ticker] = price

    # Портфель
    positions = db.fetch_all("SELECT ticker, quantity, avg_price FROM portfolio;")
    assets_value = 0.0
    enriched_positions = []
    for p in positions:
        ticker = p["ticker"]
        qty = float(p["quantity"])
        avg = float(p["avg_price"])
        current = prices.get(ticker, avg)
        value = qty * current
        assets_value += value
        enriched_positions.append({
            "ticker": ticker,
            "quantity": qty,
            "avg_price": avg,
            "current_price": current,
            "value": value,
        })

    # Сделки за 24ч
    trades = db.fetch_all(
        """SELECT ticker, action, quantity, price, total, commission
           FROM trades WHERE created_at BETWEEN %s AND %s ORDER BY created_at;""",
        (period_start, period_end),
    )

    # Решения
    decisions = db.fetch_all(
        """SELECT ticker, action, confidence, reasoning
           FROM decisions WHERE created_at BETWEEN %s AND %s ORDER BY created_at;""",
        (period_start, period_end),
    )

    return {
        "cash": cash,
        "initial": initial,
        "deposits": deposits,
        "invested_capital": invested_capital,
        "assets": assets_value,
        "total": cash + assets_value,
        "pnl": (cash + assets_value) - invested_capital,
        "positions": enriched_positions,
        "trades": trades,
        "decisions": decisions,
    }


def _fmt_qty(qty: float) -> str:
    if qty >= 1:
        return f"{qty:.2f}"
    return f"{qty:.8f}".rstrip("0").rstrip(".")


def format_report(s: dict) -> str:
    pnl_sign = "🟢" if s["pnl"] >= 0 else "🔴"

    invested_text = f"{s['invested_capital']:,.0f} ₽"
    if s.get("deposits", 0) > 0:
        invested_text += f" (старт {s['initial']:,.0f} + пополнения {s['deposits']:,.0f})"

    lines = [
        "🏦 *AI Investment Company*",
        f"📅 {date.today().strftime('%d.%m.%Y')}",
        "",
        "💰 *КАПИТАЛ*",
        f"• Свободные деньги: {s['cash']:,.2f} ₽",
        f"• В активах: {s['assets']:,.2f} ₽",
        f"• *ИТОГО: {s['total']:,.2f} ₽*",
        f"💵 Вложено: {invested_text}",
        f"{pnl_sign} P/L: {s['pnl']:+,.2f} ₽",
        "",
    ]

    if s["positions"]:
        lines.append("📊 *ПОРТФЕЛЬ*")
        for p in s["positions"]:
            lines.append(
                f"• {p['ticker']}: {_fmt_qty(p['quantity'])} шт. "
                f"| средняя {p['avg_price']:,.2f} ₽ "
                f"| текущая {p['current_price']:,.2f} ₽ "
                f"| стоимость {p['value']:,.2f} ₽"
            )
    else:
        lines.append("📊 Портфель пуст")
    lines.append("")

    if s["trades"]:
        lines.append("💼 *СДЕЛКИ ЗА 24Ч*")
        for t in s["trades"]:
            lines.append(
                f"• {t['action']} {t['ticker']} × {_fmt_qty(float(t['quantity']))} "
                f"по {float(t['price']):,.2f} ₽ "
                f"| итого {float(t['total']):,.2f} ₽ "
                f"| комиссия {float(t['commission']):,.2f} ₽"
            )
    else:
        lines.append("💼 Сделок за 24ч не было")
    lines.append("")

    if s["decisions"]:
        lines.append("🧠 *РЕШЕНИЯ TRADER-01*")
        for d in s["decisions"]:
            lines.append(
                f"• {d['action']} {d['ticker']} "
                f"(уверенность {float(d['confidence']):.2f})"
            )
            if d.get("reasoning"):
                lines.append(f"  _{d['reasoning']}_")
    else:
        lines.append("🧠 Решений за 24ч не было")

    return "\n".join(lines)


def send_message(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        resp = requests.post(
            TG_API.format(token=token),
            json={"chat_id": chat_id, "text": text,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=30,
        )
        if resp.status_code != 200:
            log.error(f"Telegram {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"Ошибка отправки: {e}")
        return False


def run() -> int:
    log.info("Формируем ежедневный отчёт...")
    stats = get_daily_stats()
    report = format_report(stats)
    log.info(f"Отчёт:\n{report}")
    return 0 if send_message(report) else 1
