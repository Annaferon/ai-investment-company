"""Загрузка истории цен: CoinPaprika (крипта) + MOEX (акции).
CoinPaprika — бесплатно, без ключа, 1 год истории за запрос."""
from datetime import datetime, timedelta
from typing import Any

import requests

from src.core.logger import get_logger

log = get_logger("history_fetcher")

# --- CoinPaprika (крипта) ---
COINPAPRIKA_BASE = "https://api.coinpaprika.com/v1"
CRYPTO_IDS = {
    # Stable
    "BTC": "btc-bitcoin",
    "ETH": "eth-ethereum",
    "SOL": "sol-solana",
    "BNB": "bnb-binance-coin",
    "LINK": "link-chainlink",
    # Meme
    "DOGE": "doge-dogecoin",
    "SHIB": "shib-shiba-inu",
    "PEPE": "pepe-pepe",
    "WIF": "wif-dogwifcoin",
    "BONK": "bonk-bonk",
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


# ---------- CoinPaprika ----------

def fetch_coinpaprika_history(coin_id: str, days: int) -> list[dict]:
    """История цен монеты с CoinPaprika (в USD)."""
    end = datetime.now()
    start = end - timedelta(days=min(days, 365))

    url = f"{COINPAPRIKA_BASE}/tickers/{coin_id}/historical"
    try:
        r = requests.get(
            url,
            params={"start": start.strftime("%Y-%m-%d"), "interval": "1d"},
            timeout=TIMEOUT_SEC,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error(f"CoinPaprika {coin_id}: {e}")
        return []

    if not isinstance(data, list):
        log.warning(f"CoinPaprika {coin_id}: неожиданный формат")
        return []

    result = []
    for p in data:
        try:
            ts = p.get("timestamp", "")
            price = p.get("price")
            if ts and price:
                date_str = ts[:10]
                result.append({"date": date_str, "price": float(price)})
        except Exception:
            continue

    log.info(f"CoinPaprika {coin_id}: {len(result)} точек")
    return result


def load_crypto_history(days: int) -> int:
    total = 0
    for ticker, cp_id in CRYPTO_IDS.items():
        records = fetch_coinpaprika_history(cp_id, days)
        if records:
            saved = save_history(records, ticker, "crypto", "USD", "coinpaprika")
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
