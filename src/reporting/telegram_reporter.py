"""Ежедневный отчёт в Telegram."""
import os
from datetime import date, datetime

import requests

from src.core.config import config
from src.core.database import db
from src.core.logger import get_logger

log = get_logger("reporter")

TG_API = "https://api.telegram.org/bot{token}/sendMessage"


def get_daily_stats() -> dict:
    """Собираем статистику: капитал, портфель, сделки, решения."""
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())

    positions = db.fetch_all(
        "SELECT ticker, quantity, avg_price FROM portfolio;"
    )
    invested = sum(
        float(p["quantity"]) * float(p["avg_price"]) for p in positions
    )
    cash = config.STARTING_CAPITAL - invested

    trades = db.fetch_all(
        "SELECT ticker, action, quantity, total FROM trades "
        "WHERE created_at BETWEEN %s AND %s ORDER BY created_at;",
        (today_start, today_end),
    )

    decisions = db.fetch_all(
        "SELECT ticker, action, confidence, reasoning FROM decisions "
        "WHERE created_at BETWEEN %s AND %s ORDER BY created_at;",
        (today_start, today_end),
    )

    return {
        "cash": cash,
        "invested": invested,
        "total": cash + invested,
        "positions": positions,
        "trades": trades,
        "decisions": decisions,
    }


def format_report(s: dict) -> str:
    """Собираем текст отчёта."""
    lines = [
        "🏦 AI Investment Company",
        f"📅 {date.today().strftime('%d.%m.%Y')}",
        "",
        "💰 КАПИТАЛ",
        f"• Свободные деньги: {s['cash']:.2f} ₽",
        f"• В активах: {s['invested']:.2f} ₽",
        f"• ИТОГО: {s['total']:.2f} ₽",
        "",
    ]

    if s["positions"]:
        lines.append("📊 ПОРТФЕЛЬ")
        for p in s["positions"]:
            lines.append(
                f"• {p['ticker']}: {p['quantity']} шт. "
                f"(средняя {float(p['avg_price']):.2f} ₽)"
            )
    else:
        lines.append("📊 Портфель пуст")
    lines.append("")

    if s["trades"]:
        lines.append("💼 СДЕЛКИ ЗА ДЕНЬ")
        for t in s["trades"]:
            lines.append(
                f"• {t['action']} {t['ticker']} × {t['quantity']} "
                f"на {float(t['total']):.2f} ₽"
            )
    else:
        lines.append("💼 Сделок сегодня не было")
    lines.append("")

    if s["decisions"]:
        lines.append("🧠 РЕШЕНИЯ TRADER-01")
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
