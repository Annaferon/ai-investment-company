"""Ежедневный отчёт в Telegram."""
import os
from datetime import date, datetime

import requests

from src.core.database import db
from src.core.logger import get_logger

log = get_logger("reporter")

TG_API = "https://api.telegram.org/bot{token}/sendMessage"

# Текущие цены (пока заглушка, потом заменим на API)
MARKET_PRICES = {
    "SBER": 285.50,
    "GAZP": 132.40,
    "BTC": 6_200_000.0,
    "ETH": 210_000.0,
    "GOLD": 7_500.0,
}


def get_daily_stats() -> dict:
    """Собираем статистику: капитал, портфель, сделки, решения."""
    # Окно: последние 24 часа (а не «сегодня»)
from datetime import timedelta
today_end = datetime.now()
today_start = today_end - timedelta(hours=24)
    # --- Кэш из account ---
    acc = db.fetch_one("SELECT cash, initial_capital FROM account WHERE id = 1;")
    cash = float(acc["cash"]) if acc else 0.0
    initial = float(acc["initial_capital"]) if acc else 0.0

    # --- Портфель: считаем стоимость по текущим ценам ---
    positions = db.fetch_all(
        "SELECT ticker, quantity, avg_price FROM portfolio;"
    )
    assets_value = 0.0
    enriched_positions = []
    for p in positions:
        ticker = p["ticker"]
        qty = float(p["quantity"])
        avg = float(p["avg_price"])
        current = MARKET_PRICES.get(ticker, avg)
        value = qty * current
        assets_value += value
        enriched_positions.append({
            "ticker": ticker,
            "quantity": qty,
            "avg_price": avg,
            "current_price": current,
            "value": value,
        })

    # --- Сделки за сегодня ---
    trades = db.fetch_all(
        "SELECT ticker, action, quantity, price, total, commission FROM trades "
        "WHERE created_at BETWEEN %s AND %s ORDER BY created_at;",
        (today_start, today_end),
    )

    # --- Решения за сегодня ---
    decisions = db.fetch_all(
        "SELECT ticker, action, confidence, reasoning FROM decisions "
        "WHERE created_at BETWEEN %s AND %s ORDER BY created_at;",
        (today_start, today_end),
    )

    return {
        "cash": cash,
        "initial": initial,
        "assets": assets_value,
        "total": cash + assets_value,
        "pnl": (cash + assets_value) - initial,
        "positions": enriched_positions,
        "trades": trades,
        "decisions": decisions,
    }


def format_report(s: dict) -> str:
    """Собираем текст отчёта."""
    pnl_sign = "🟢" if s["pnl"] >= 0 else "🔴"
    lines = [
        "🏦 *AI Investment Company*",
        f"📅 {date.today().strftime('%d.%m.%Y')}",
        "",
        "💰 *КАПИТАЛ*",
        f"• Свободные деньги: {s['cash']:.2f} ₽",
        f"• В активах: {s['assets']:.2f} ₽",
        f"• *ИТОГО: {s['total']:.2f} ₽*",
        f"{pnl_sign} P/L: {s['pnl']:+.2f} ₽ (от {s['initial']:.0f} ₽)",
        "",
    ]

    if s["positions"]:
        lines.append("📊 *ПОРТФЕЛЬ*")
        for p in s["positions"]:
            lines.append(
                f"• {p['ticker']}: {p['quantity']:.0f} шт. "
                f"| средняя {p['avg_price']:.2f} ₽ "
                f"| текущая {p['current_price']:.2f} ₽ "
                f"| стоимость {p['value']:.2f} ₽"
            )
    else:
        lines.append("📊 Портфель пуст")
    lines.append("")

    if s["trades"]:
        lines.append("💼 *СДЕЛКИ ЗА ДЕНЬ*")
        for t in s["trades"]:
            lines.append(
                f"• {t['action']} {t['ticker']} × {t['quantity']} "
                f"по {float(t['price']):.2f} ₽ "
                f"| итого {float(t['total']):.2f} ₽ "
                f"| комиссия {float(t['commission']):.2f} ₽"
            )
    else:
        lines.append("💼 Сделок сегодня не было")
    lines.append("")

    if s["decisions"]:
        lines.append("🧠 *РЕШЕНИЯ TRADER-01*")
        for d in s["decisions"]:
            lines.append(
                f"• {d['action']} {d['ticker']} "
                f"(уверенность {float(d['confidence']):.2f})"
            )
            if d.get("reasoning"):
                lines.append(f"  _{d['reasoning'][:150]}_")
    else:
        lines.append("🧠 Решений сегодня не было")

    return "\n".join(lines)


def send_message(text: str) -> bool:
    """Отправляем сообщение через Telegram Bot API."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы")
        return False

    try:
        resp = requests.post(
            TG_API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        resp.raise_for_status()
        log.info("Отчёт отправлен в Telegram")
        return True
    except Exception as e:
        log.error(f"Ошибка отправки в Telegram: {e}")
        return False


def run() -> int:
    """Точка входа для отчёта."""
    log.info("Формируем ежедневный отчёт...")
    stats = get_daily_stats()
    report = format_report(stats)
    log.info(f"Отчёт:\n{report}")
    return 0 if send_message(report) else 1
