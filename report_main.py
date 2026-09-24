"""Запуск ежедневного отчёта в Telegram."""
import sys

from src.reporting.telegram_reporter import run

if __name__ == "__main__":
    sys.exit(run())
