"""Загрузка истории цен: CoinGecko (крипта) + MOEX (акции + металлы)."""
from datetime import datetime, timedelta
from typing import Any

import requests

from src.core.logger import get_logger

log = get_logger("history_fetcher")

# --- CoinGecko ---
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
CRYPTO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
}

# --- MOEX ---
MOEX_HISTORY_URL = (
    "https://iss.moex.com/iss/history/engines/stock/markets/shares/"
    "boards/TQBR/securities/{ticker}.json"
)
MOEX_TICKERS = ["SBER", "GAZP", "LKOH", "GMKN", "ROSN",
                "NVTK", "TATN", "SNGS", "PLZL", "MTSS"]

CRYPTO_DAYS = 1460   # 4 года
STOCK_DAYS = 1095    # 3 года

TIMEOUT_SEC = 30


# ---------- CoinGecko ----------

def fetch_coingecko_history(coin_id: str, days: int = CRYPTO_DAYS) -> list[dict]:
    """История цен монеты с CoinGecko (в USD)."""
    url = f"{COINGECKO_BASE}/coins/{coin_id}/market_chart"
    try:
        r = requests.get(
            url,
            params={"vs_currency": "usd", "days": days},
            timeout=TIMEOUT_SEC,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error(f"CoinGecko {coin_id}: {e}")
        return []

    prices = data.get("prices", [])
    if not prices:
        log.warning(f"CoinGecko {coin_id}: пустой ответ")
        return []

    by_date: dict[str, float] = {}
    for ts_ms, price in prices:
        dt = datetime.fromtimestamp(ts_ms / 1000).date().isoformat()
        by_date[dt] = float(price)

    result = [{"date": d, "price": p} for d, p in sorted(by_date.items())]
    log.info(f"CoinGecko {coin_id}: {len(result)} точек за {days} дней")
    return result


def load_crypto_history() -> int:
    total = 0
    for ticker, cg_id in CRYPTO_IDS.items():
        records = fetch_coingecko_history(cg_id, CRYPTO_DAYS)
        if records:
            saved = save_history(records, ticker, "crypto", "USD", "coingecko")
            total += saved
    return total


# ---------- MOEX ----------

def fetch_moex_history(ticker: str, days: int = STOCK_DAYS) -> list[dict]:
    """История акций с MOEX ISS (в рублях) с пагинацией."""
    to_date = datetime.now().date()
    from_date = to_date - timedelta(days=days)

    result: dict[str, float] = {}
    start = 0
    page_size = 100
    max_pages = 20

    for _ in range(max_pages):
        try:
            r = requests.get(
                MOEX_HISTORY_URL.format(ticker=ticker),
                params={
                    "iss.meta": "off",
                    "from": from_date.isoformat(),
                    "till": to_date.isoformat(),
                    "start": start,
                    "limit": page_size,
                },
                timeout=TIMEOUT_SEC,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.error(f"MOEX {ticker} стр.{start}: {e}")
            break

        cols = data.get("history", {}).get("columns", [])
        rows = data.get("history", {}).get("data", [])

        if not rows:
            break

        if "TRADEDATE" not in cols or "CLOSE" not in cols:
            log.error(f"MOEX {ticker}: нет нужных колонок")
            break

        idx_date = cols.index("TRADEDATE")
        idx_close = cols.index("CLOSE")
        idx_legal = cols.index("LEGALCLOSEPRICE") if "LEGALCLOSEPRICE" in cols else None

        for row in rows:
            date_str = row[idx_date]
            price = row[idx_legal] if idx_legal is not None and row[idx_legal] else row[idx_close]
            if date_str and price:
                result[date_str] = float(price)

        if len(rows) < page_size:
            break
        start += page_size

    out = [{"date": d, "price": p} for d, p in sorted(result.items())]
    log.info(f"MOEX {ticker}: {len(out)} точек")
    return out


def load_stock_history() -> int:
    total = 0
    for ticker in MOEX_TICKERS:
        records = fetch_moex_history(ticker, STOCK_DAYS)
        if records:
            saved = save_history(records, ticker, "stock", "RUB", "moex")
            total += saved
    return total


# ---------- Сохранение ----------

def save_history(
    records: list[dict],
    ticker: str,
    asset_type: str,
    currency: str,
    source: str,
) -> int:
    """Сохраняем историю в БД. Дубликаты обновляются."""
    from src.core.database import db
    count = 0
    for r in records:
        try:
            db.execute(
                """INSERT INTO price_history
                   (ticker, asset_type, price, currency, price_date, source)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (ticker, price_date, source)
                   DO UPDATE SET price = EXCLUDED.price;""",
                (ticker, asset_type, float(r["price"]), currency, r["date"], source),
            )
            count += 1
        except Exception as e:
            log.error(f"Ошибка сохранения {ticker} {r['date']}: {e}")
    return count


# ---------- Главный цикл ----------

def run_history_loader() -> dict[str, Any]:
    log.info("Начинаем загрузку истории цен...")

    crypto_saved = load_crypto_history()
    stock_saved = load_stock_history()

    total = crypto_saved + stock_saved
    log.info(f"Загрузка завершена. Всего точек: {total}")

    return {
        "crypto_saved": crypto_saved,
        "stock_saved": stock_saved,
        "total": total,
    }
