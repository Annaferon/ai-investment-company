"""Сбор новостей из RSS-лент."""
from datetime import datetime, timedelta
from typing import Any

import feedparser

from src.core.logger import get_logger

log = get_logger("news_fetcher")


# Источники новостей (RSS)
NEWS_SOURCES = [
    {
        "name": "RBC Economics",
        "url": "https://rssexport.rbc.ru/rbcnews/economics/20/full.rss",
        "category": "macro",
    },
    {
        "name": "RBC Finance",
        "url": "https://rssexport.rbc.ru/rbcnews/finances/20/full.rss",
        "category": "finance",
    },
    {
        "name": "RBC All",
        "url": "https://rssexport.rbc.ru/rbcnews/news/20/full.rss",
        "category": "general",
    },
    {
        "name": "Bank of Russia",
        "url": "https://www.cbr.ru/rss/eventrss",
        "category": "cb",
    },
]


def fetch_recent_news(hours: int = 24, max_per_source: int = 10) -> list[dict[str, Any]]:
    """Собираем новости за последние N часов."""
    cutoff = datetime.now() - timedelta(hours=hours)
    all_news: list[dict[str, Any]] = []

    for source in NEWS_SOURCES:
        try:
            feed = feedparser.parse(source["url"])
            log.info(f"{source['name']}: получено {len(feed.entries)} записей")

            for entry in feed.entries[:max_per_source]:
                published = entry.get("published_parsed")
                if published:
                    pub_dt = datetime(*published[:6])
                    if pub_dt < cutoff:
                        continue
                else:
                    pub_dt = datetime.now()

                all_news.append({
                    "source": source["name"],
                    "category": source["category"],
                    "title": entry.get("title", "").strip(),
                    "summary": entry.get("summary", "")[:500].strip(),
                    "link": entry.get("link", ""),
                    "published": pub_dt.isoformat(),
                })

        except Exception as e:
            log.warning(f"Источник {source['name']} упал: {e}")
            continue

    log.info(f"Всего собрано новостей: {len(all_news)}")
    return all_news


def news_to_text(news: list[dict[str, Any]], max_items: int = 40) -> str:
    """Превращаем список новостей в текст для LLM."""
    if not news:
        return "Новостей за последние 24 часа не найдено."

    lines = []
    for i, n in enumerate(news[:max_items], 1):
        lines.append(f"{i}. [{n['source']}] {n['title']}")
        if n["summary"]:
            lines.append(f"   {n['summary'][:200]}")
    return "\n".join(lines)
