"""Базовый класс для всех ИИ-агентов компании."""
from abc import ABC, abstractmethod
from typing import Any, Optional

from openai import OpenAI

from src.core.config import config
from src.core.database import db
from src.core.logger import get_logger


class BaseAgent(ABC):
    """Все агенты наследуются от этого класса."""
    
    # По умолчанию — дешёвая модель. Можно переопределить в наследнике.
    DEFAULT_MODEL = "openai/gpt-4o-mini"
    
    def __init__(self, name: str, role: str, model: Optional[str] = None) -> None:
        self.name = name
        self.role = role
        self.model = model or self.DEFAULT_MODEL
        self.log = get_logger(name)
        
        self.client = OpenAI(
            base_url=config.OPENROUTER_BASE_URL,
            api_key=config.OPENROUTER_API_KEY,
        )
        
        self.log.info(f"Агент инициализирован: {name} ({role})")
    
    # ---------- Работа с LLM ----------
    
    def think(self, prompt: str, system: Optional[str] = None) -> str:
        """Отправить запрос в LLM и получить текстовый ответ."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
            )
            text = response.choices[0].message.content or ""
            self.log.debug(f"LLM ответ: {text[:200]}...")
            return text.strip()
        except Exception as e:
            self.log.error(f"Ошибка LLM: {e}")
            raise
    
    # ---------- Запись в БД ----------
    
    def record_decision(
        self,
        ticker: str,
        action: str,
        confidence: float = 0.0,
        reasoning: str = "",
    ) -> None:
        """Записать решение агента в таблицу decisions."""
        db.execute(
            """
            INSERT INTO decisions (agent_name, ticker, action, confidence, reasoning)
            VALUES (%s, %s, %s, %s, %s);
            """,
            (self.name, ticker, action, confidence, reasoning),
        )
        self.log.info(f"Решение записано: {action} {ticker} (уверенность: {confidence})")
    
    # ---------- Абстрактный метод ----------
    
    @abstractmethod
    def run(self) -> Any:
        """Основная логика агента. Реализуется в наследниках."""
        ...
