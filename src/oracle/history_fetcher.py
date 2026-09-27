"""Загрузка истории цен: Yahoo Finance (крипта) + MOEX (акции)."""
from datetime import datetime, timedelta
from typing import Any

import requests
import yfinance as yf

from src.core.logger import get_logger

log = get_logger("history_fetcher")

# --- Yahoo Finance (крипта) ---
CRYPTO_YF = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "BNB": "BNB-USD",
    "LINK": "LINK-USD",
    "DOGE": "DOGE-USD",
    "SHIB": "SHIB-USD",
    "PEPE": "PEPE24478-USD",
    "WIF": "WIF-USD",
    "BONK": "BONK-USD",
}

# --- MOEX ---
MOEX_HISTORY_URL = (
    "https://iss.moex.com/iss/history/engines/stock/markets/shares/"
    "boards/TQBR/securities/{ticker}.json"
)
MOEX_TICKERS = ["SBER", "GAZP", "LKOH", "GMKN", "ROSN",
                "NVTK", "TATN", "SNGS", "PLZL", "MTSS"]

CRYPTO_DAYS_FULL = 365
STOCK_DAYS_FULL = 1095
TIMEOUT_SEC = 30


# ---------- Yahoo Finance (крипта) ----------

def fetch_yf_history(yf_ticker: str, days: int) -> list[dict]:
    """История с Yahoo Finance."""
    try:
        ticker = yf.Ticker(yf_ticker)
        df = ticker.history(period=f"{days}d", interval="1d")
        if df.empty:
            log.warning(f"Yahoo {yf_ticker}: пусто")
            return []

        result = []
        for idx, row in df.iterrows():
            try:
                price = float(row["Close"])
                if price > 0:
                    result.append({
                        "date": idx.date().isoformat(),
                        "price": price,
                    })
            except Exception:
                continue

        log.info(f"Yahoo {yf_ticker}: {len(result)} точек")
        return result
    except Exception as e:
        log.error(f"Yahoo {yf_ticker}: {e}")
        return []


def load_crypto_history(days: int) -> int:
    total = 0
    for ticker, yf_ticker in CRYPTO_YF.items():
        records = fetch_yf_history(yf_ticker, min(days, CRYPTO_DAYS_FULL))
        if records:
            saved = save_history(records, ticker, "crypto", "USD", "yahoo")
            total += saved
    return total


# ---------- MOEX ----------

def fetch_moex_history(ticker: str, days: int) -> list[dict]:
    to_date = datetime.now().date()
    from_date = to_date - timedelta(days=days)

    result: dict[str, float] = {}
    start = 0
    page_size = 100
    max_pages = 30

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


def load_stock_history(days: int) -> int:
    total = 0
    for ticker in MOEX_TICKERS:
        records = fetch_moex_history(ticker, days)
        if records:
            saved = save_history(records, ticker, "stock", "RUB", "moex")
            total += saved
    return total


# ---------- Сохранение ----------

def save_history(
    records: list[dict], ticker: str, asset_type: str,
    currency: str, source: str,
) -> int:
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

def run_history_loader(days: int) -> dict[str, Any]:
    log.info(f"Начинаем загрузку истории за {days} дней...")

    crypto_saved = load_crypto_history(min(days, CRYPTO_DAYS_FULL))
    stock_saved = load_stock_history(min(days, STOCK_DAYS_FULL))

    total = crypto_saved + stock_saved
    log.info(f"Загрузка завершена. Всего точек: {total}")

    return {
        "days": days,
        "crypto_saved": crypto_saved,
        "stock_saved": stock_saved,
        "total": total,
    }
