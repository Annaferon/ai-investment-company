"""Работа с базой данных Supabase (PostgreSQL)."""
from contextlib import contextmanager
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from src.core.config import config
from src.core.logger import logger


class Database:
    """Обёртка над PostgreSQL. Открывает соединение на каждый запрос."""
    
    def __init__(self, dsn: Optional[str] = None) -> None:
        self.dsn = dsn or config.DATABASE_URL
        if not self.dsn:
            raise ValueError("DATABASE_URL не задан")
    
    @contextmanager
    def connection(self):
        """Контекстный менеджер: соединение автоматически закрывается."""
        conn = psycopg2.connect(self.dsn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def execute(self, query: str, params: tuple = ()) -> None:
        """Выполнить запрос без возврата данных (INSERT/UPDATE/DELETE)."""
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
        logger.debug(f"SQL executed: {query[:80]}")
    
    def fetch_all(self, query: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Получить все строки как список словарей."""
        with self.connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                return [dict(row) for row in cur.fetchall()]
    
    def fetch_one(self, query: str, params: tuple = ()) -> Optional[dict[str, Any]]:
        """Получить одну строку или None."""
        with self.connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return dict(row) if row else None
    
    def health_check(self) -> bool:
        """Проверка связи с базой."""
        try:
            result = self.fetch_one("SELECT 1 AS ok;")
            return result is not None and result.get("ok") == 1
        except Exception as e:
            logger.error(f"DB health check failed: {e}")
            return False


# Синглтон — один объект на весь проект
db = Database()
