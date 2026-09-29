"""Конфигурация проекта. Все секреты — из переменных окружения."""
import os
from dotenv import load_dotenv

# Загружаем переменные из .env (локально) или из environment (на Render)
load_dotenv()


class Config:
    """Настройки компании."""
    
    # --- OpenRouter (LLM) ---
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    
    # --- Supabase (PostgreSQL) ---
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    
    # --- Telegram (позже) ---
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # --- Макро-параметры (обновлять вручную при изменениях) ---
    KEY_RATE: float = 18.0       # Ключевая ставка ЦБ РФ, %
    INFLATION: float = 8.5       # Инфляция РФ, %
    FED_RATE: float = 4.5        # Ставка ФРС, %
    BRENT_PRICE: float = 85.0    # Нефть Brent, $
   
    # --- Виртуальный капитал ---
    STARTING_CAPITAL: float = 10_000.0  # ₽
    
    # --- Комиссии (упрощённые, позже уточним) ---
    BROKER_COMMISSION: float = 0.0005   # 0.05% от сделки
    TAX_ON_PROFIT: float = 0.13         # 13% НДФЛ
    
    @classmethod
    def validate(cls) -> None:
        """Проверяем, что все критичные переменные заданы."""
        missing = []
        if not cls.OPENROUTER_API_KEY:
            missing.append("OPENROUTER_API_KEY")
        if not cls.DATABASE_URL:
            missing.append("DATABASE_URL")
        if missing:
            raise ValueError(f"Отсутствуют переменные окружения: {missing}")


config = Config()
