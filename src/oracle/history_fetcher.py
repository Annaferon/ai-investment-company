"""Загрузка истории цен: Kraken (крипта) + MOEX (акции).
Kraken — публичный API, без ключа, ~720 дневных свечей (~2 года)."""
from datetime import datetime, timedelta
from typing import Any

import requests

from src.core.logger import get_logger

log = get_logger("history_fetcher")

# --- Kraken (крипта) ---
KRAKEN_BASE = "https://api.kraken.com/0/public"
KRAKEN_PAIRS = {
    # Stable
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "LINK": "LINKUSD",
    # Meme
    "DOGE": "DOGEUSD",
    "SHIB": "SHIBUSD",
    "PEPE": "PEPEUSD",
    # BNB, WIF, BONK нет на Kraken — пропустим
}

# --- MOEX ---
MOEX_HISTORY_URL = (
    "https://iss.moex.com/iss/history/engines/stock/markets/shares/"
    "boards/TQBR/securities/{ticker}.json"
)
MOEX_TICKERS = ["SBER", "GAZP", "LKOH", "GMKN", "ROSN",
                "NVTK", "TATN", "SNGS", "PLZL", "MTSS"]

CRYPTO_DAYS_FULL = 720    # Kraken даёт ~720 свечей
STOCK_DAYS_FULL = 1095
TIMEOUT_SEC = 30


# ---------- Kraken (крипта) ----------

def fetch_kraken_history(pair: str, days: int) -> list[dict]:
    """Дневная история с Kraken (interval=1440 минут = 1 день)."""
    url = f"{KRAKEN_BASE}/OHLC"
    try:
        r = requests.get(
            url,
            params={"pair": pair, "interval": 1440},
            timeout=TIMEOUT_SEC,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error(f"Kraken {pair}: {e}")
        return []

    if data.get("error"):
        log.error(f"Kraken {pair}: {data['error']}")
        return []

    result_data = data.get("result", {})
    # Ищем ключ с данными (Kraken возвращает пару под своим именем)
    keys = [k for k in result_data.keys() if k != "last"]
    if not keys:
        log.warning(f"Kraken {pair}: пустой ответ")
        return []

    rows = result_data[keys[0]]
    # Формат: [time, open, high, low, close, vwap, volume, count]
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for row in rows:
        try:
            ts = int(row[0])
            dt = datetime.fromtimestamp(ts)
            if dt < cutoff:
                continue
            price = float(row[4])  # close
            if price > 0:
                out.append({"date": dt.date().isoformat(), "price": price})
        except Exception:
            continue

    log.info(f"Kraken {pair}: {len(out)} точек")
    return out


def load_crypto_history(days: int) -> int:
    total = 0
    for ticker, pair in KRAKEN_PAIRS.items():
        records = fetch_kraken_history(pair, min(days, CRYPTO_DAYS_FULL))
        if records:
            saved = save_history(records, ticker, "crypto", "USD", "kraken")
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
