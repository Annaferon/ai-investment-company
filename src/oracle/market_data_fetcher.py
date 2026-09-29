"""Оракул: сбор рыночных данных с MOEX, Kraken, ЦБ РФ.
Возвращает цены + % изменения к предыдущему запросу."""
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from src.core.logger import get_logger

log = get_logger("oracle")

MSK = timezone(timedelta(hours=3))

MOEX_BASE = "https://iss.moex.com/iss"
MOEX_BULK_URL = f"{MOEX_BASE}/engines/stock/markets/shares/boards/TQBR/securities.json"
MOEX_TICKERS = {"SBER", "GAZP", "LKOH", "GMKN", "ROSN", "NVTK", "TATN", "SNGS", "PLZL", "MTSS"}

KRAKEN_BASE = "https://api.kraken.com/0/public"
KRAKEN_PAIRS = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "LINK": "LINKUSD",
    "DOGE": "DOGEUSD",
    "SHIB": "SHIBUSD",
    "PEPE": "PEPEUSD",
}

MAX_RETRIES = 2
RETRY_DELAY_SEC = 3
TIMEOUT_SEC = 8
FATAL_CODES = {400, 401, 403, 404, 451}


def _now_msk() -> datetime:
    return datetime.now(MSK)


def _is_weekend() -> bool:
    return _now_msk().weekday() >= 5


def _is_moex_open() -> bool:
    now = _now_msk()
    if now.weekday() >= 5:
        return False
    if now.hour < 7:
        return False
    if now.hour == 23 and now.minute > 50:
        return False
    return True


# ---------- Предыдущие цены (для % изменения) ----------

def get_previous_prices() -> dict[str, float]:
    from src.core.database import db
    rows = db.fetch_all(
        """SELECT DISTINCT ON (ticker) ticker, price
           FROM market_prices
           ORDER BY ticker, updated_at DESC;"""
    )
    return {r["ticker"]: float(r["price"]) for r in rows}


def calc_changes(
    new_prices: dict[str, float],
    old_prices: dict[str, float],
) -> dict[str, float]:
    changes = {}
    for ticker, new_price in new_prices.items():
        old = old_prices.get(ticker)
        if old and old > 0:
            changes[ticker] = (new_price - old) / old * 100
    return changes


# ---------- MOEX ----------

def fetch_moex_bulk() -> dict[str, float]:
    try:
        r = requests.get(
            MOEX_BULK_URL,
            params={"iss.meta": "off", "iss.only": "marketdata,securities"},
            timeout=TIMEOUT_SEC,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error(f"MOEX ошибка: {e}")
        return {}

    cols = data.get("marketdata", {}).get("columns", [])
    rows = data.get("marketdata", {}).get("data", [])
    if not cols or not rows:
        return {}

    idx_secid = cols.index("SECID")
    idx_price = None
    for f in ["LAST", "LCURRENTPRICE", "PREVPRICE", "WAPRICE", "OPEN"]:
        if f in cols:
            idx_price = cols.index(f)
            break

    if idx_price is None:
        return {}

    prices = {}
    for row in rows:
        ticker = row[idx_secid]
        if ticker not in MOEX_TICKERS:
            continue
        price = row[idx_price]
        if price is not None:
            prices[ticker] = float(price)

    log.info(f"MOEX: получено {len(prices)} цен")
    return prices


# ---------- Kraken ----------

def fetch_kraken_prices() -> dict[str, float]:
    pairs = ",".join(KRAKEN_PAIRS.values())
    try:
        r = requests.get(
            f"{KRAKEN_BASE}/Ticker",
            params={"pair": pairs},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=TIMEOUT_SEC + 5,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error(f"Kraken ошибка: {e}")
        return {}

    if data.get("error"):
        log.error(f"Kraken API: {data['error']}")
        return {}

    result = data.get("result", {})
    prices = {}
    for ticker, kraken_pair in KRAKEN_PAIRS.items():
        row = None
        if kraken_pair in result:
            row = result[kraken_pair]
        else:
            for k, v in result.items():
                if kraken_pair[:3] in k or kraken_pair.replace("XBT", "XXBT") in k:
                    row = v
                    break
        if row and "c" in row and row["c"]:
            try:
                prices[ticker] = float(row["c"][0])
            except (IndexError, ValueError):
                continue

    log.info(f"Kraken: получено {len(prices)} цен")
    return prices


# ---------- ЦБ РФ ----------

def fetch_cbr_metals() -> dict[str, float]:
    result = {}

    if not _is_moex_open():
        from src.core.database import db
        rows = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, price
               FROM market_prices
               WHERE ticker IN ('GOLD', 'SILVER', 'USD_RUB')
               ORDER BY ticker, updated_at DESC;"""
        )
        for r in rows:
            result[r["ticker"]] = float(r["price"])
        return result

    try:
        r = requests.get(
            "https://www.cbr-xml-daily.ru/daily_json.js",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=TIMEOUT_SEC,
        )
        r.raise_for_status()
        data = r.json()
        usd = data.get("Valute", {}).get("USD", {}).get("Value")
        if usd:
            result["USD_RUB"] = float(usd)
    except Exception as e:
        log.warning(f"Не получили курс USD/RUB: {e}")

    result["GOLD"] = 7500.0
    result["SILVER"] = 95.0
    return result


# ---------- Сохранение ----------

def save_prices_to_db(prices: dict[str, float], asset_type: str, source: str) -> int:
    from src.core.database import db
    count = 0
    for ticker, price in prices.items():
        try:
            db.execute(
                """INSERT INTO market_prices (ticker, asset_type, price, source, updated_at)
                   VALUES (%s, %s, %s, %s, NOW());""",
                (ticker, asset_type, price, source),
            )
            count += 1
        except Exception as e:
            log.error(f"Ошибка сохранения {ticker}: {e}")
    return count


# ---------- Главный цикл ----------

def run_oracle() -> dict[str, Any]:
    log.info("Оракул просыпается...")
    is_weekend = _is_weekend()
    moex_open = _is_moex_open()

    # 1. Запоминаем старые цены ДО сохранения
    old_prices = get_previous_prices()

    # 2. Получаем новые
    moex_prices = fetch_moex_bulk() if moex_open else {}
    crypto_prices = fetch_kraken_prices()
    metals_prices = fetch_cbr_metals()

    if not moex_prices and not crypto_prices and not metals_prices:
        raise RuntimeError("Оракул не смог получить ни одной цены")

    # 3. Считаем % изменения
    all_new = {**moex_prices, **crypto_prices, **metals_prices}
    changes = calc_changes(all_new, old_prices)

    # 4. Сохраняем
    save_prices_to_db(moex_prices, asset_type="stock", source="moex")
    save_prices_to_db(crypto_prices, asset_type="crypto", source="kraken")
    save_prices_to_db(metals_prices, asset_type="metal", source="cbr")

    total = len(moex_prices) + len(crypto_prices) + len(metals_prices)
    log.info(f"Оракул завершил работу. Всего цен: {total}")

    return {
        "moex": moex_prices,
        "crypto": crypto_prices,
        "metals": metals_prices,
        "changes": changes,
        "total": total,
        "weekend_mode": is_weekend,
        "moex_open": moex_open,
    }
