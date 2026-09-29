"""Базовый класс для всех ИИ-агентов компании."""
from abc import ABC, abstractmethod
from typing import Any, Optional

from openai import OpenAI

from src.core.config import config
from src.core.database import db
from src.core.logger import get_logger


class BaseAgent(ABC):
    """Все агенты наследуются от этого класса."""

    DEFAULT_MODEL = "deepseek/deepseek-chat-v3.1:free"

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

    # ---------- LLM ----------

    def think(self, prompt: str, system: Optional[str] = None) -> str:
        """Отправить запрос в LLM. Всегда возвращает непустую строку или бросает исключение."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.3,
        )

        # Защита от None
        if not response or not response.choices:
            raise RuntimeError("LLM вернула пустой response")

        choice = response.choices[0]
        if not choice or not choice.message:
            raise RuntimeError("LLM вернула пустой choice")

        content = choice.message.content
        if content is None:
            raise RuntimeError("LLM вернула None в content")

        text = content.strip()
        if not text:
            raise RuntimeError("LLM вернула пустой текст")

        return text

    # ---------- БД ----------

    def record_decision(
        self,
        ticker: str,
        action: str,
        confidence: float = 0.0,
        reasoning: str = "",
    ) -> None:
        db.execute(
            """INSERT INTO decisions (agent_name, ticker, action, confidence, reasoning)
               VALUES (%s, %s, %s, %s, %s);""",
            (self.name, ticker, action, confidence, reasoning),
        )
        self.log.info(f"Решение записано: {action} {ticker} (уверенность: {confidence})")

    @abstractmethod
    def run(self) -> Any:
        ...
