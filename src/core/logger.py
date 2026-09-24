"""Логирование компании: в консоль и в файл."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler


# Создаём папку для логов, если её нет
LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "company.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Формат: время | уровень | имя логгера | сообщение
FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _build_logger() -> logging.Logger:
    """Создаём и настраиваем логгер компании."""
    log = logging.getLogger("ai_company")
    
    # Чтобы не дублировать хендлеры при повторном импорте
    if log.handlers:
        return log
    
    log.setLevel(LOG_LEVEL)
    log.propagate = False
    
    formatter = logging.Formatter(FORMAT, datefmt=DATE_FORMAT)
    
    # 1. Вывод в консоль (важно для Render — там видно логи в реальном времени)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    log.addHandler(console_handler)
    
    # 2. Запись в файл (с ротацией: 5 МБ на файл, до 3 бэкапов)
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        log.addHandler(file_handler)
    except Exception as e:
        # Если не можем писать в файл (например, на Render), не падаем
        log.warning(f"Не удалось открыть файл логов: {e}")
    
    return log


# Глобальный логгер
logger = _build_logger()


def get_logger(name: str) -> logging.Logger:
    """Получить дочерний логгер с собственным именем (например, 'trader')."""
    return logger.getChild(name)
