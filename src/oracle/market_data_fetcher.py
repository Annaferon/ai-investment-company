"""Оракул: сбор рыночных данных с MOEX, CoinGecko, ЦБ РФ."""
import time
from typing import Any, Optional

import requests

from src.core.logger import get_logger

log = get_logger("oracle")

# --- MOEX ---
MOEX_BASE = "https://iss.moex.com/iss"
MOEX_TICKERS = ["SBER", "GAZP", "LKOH", "GMKN", "ROSN", "NVTK", "TATN", "SNGS", "PLZL", "MTSS"]

# --- CoinGecko (крипта) ---
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
CRYPTO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
}

MAX_RETRIES = 3
RETRY_DELAY_SEC = 5
# Не повторяем при этих кодах (это не временные ошибки)
FATAL_CODES = {400, 401, 403, 404, 451}


def _retry(func, *args, **kwargs):
    """Retry только для временных ошибок."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = func(*args, **kwargs)
            if result is not None and result != {}:
                return result
            log.warning(f"Попытка {attempt}/{MAX_RETRIES}: пустой результат")
        except requests.HTTPError as e:
            code = e.response.status_code if e.response else 0
            if code in FATAL_CODES:
                log.error(f"Fatal {code} — не повторяем")
                return None
            log.warning(f"Попытка {attempt}/{MAX_RETRIES} ошибка: {e}")
        except Exception as e:
            log.warning(f"Попытка {attempt}/{MAX_RETRIES} ошибка: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SEC)
    return None


# --- MOEX ---

def fetch_moex_price(ticker: str) -> Optional[float]:
    url = f"{MOEX_BASE}/engines/stock/markets/shares/boards/TQBR/securities/{ticker}.json"
    r = requests.get(url, params={"iss.meta": "off"}, timeout=10)
    r.raise_for_status()
    data = r.json()
    cols = data["marketdata"]["columns"]
    rows = data["marketdata"]["data"]
    if not rows:
        return None
    idx = cols.index("LAST") if "LAST" in cols else cols.index("LCURRENTPRICE")
    for row in rows:
        if row[idx] is not None:
            return float(row[idx])
    return None


def fetch_all_moex() -> dict[str, float]:
    prices = {}
    for ticker in MOEX_TICKERS:
        price = _retry(fetch_moex_price, ticker)
        if price:
            prices[ticker] = price
        else:
            log.error(f"MOEX {ticker}: не получили цену")
    return prices


# --- CoinGecko (крипта в USD) ---

def fetch_coingecko_prices() -> dict[str, float]:
    """Получить цены крипты одним запросом (CoinGecko)."""
    ids = ",".join(CRYPTO_IDS.values())
    url = f"{COINGECKO_BASE}/simple/price"
    try:
        r = requests.get(
            url,
            params={"ids": ids, "vs_currencies": "usd"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error(f"CoinGecko ошибка: {e}")
        return {}

    prices = {}
    for ticker, cg_id in CRYPTO_IDS.items():
        if cg_id in data and "usd" in data[cg_id]:
            prices[ticker] = float(data[cg_id]["usd"])
    log.info(f"CoinGecko: получено {len(prices)} цен")
    return prices


# --- ЦБ РФ (металлы) ---

def fetch_cbr_metals() -> dict[str, float]:
    """Цены драгметаллов с ЦБ РФ."""
    # TODO: подключить реальный XML-парсинг cbr.ru
    return {"GOLD": 7500.0, "SILVER": 95.0}


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
    log.info(f"Сохранено {count} цен ({asset_type}, {source})")
    return count


def run_oracle() -> dict[str, Any]:
    log.info("Оракул просыпается...")

    moex_prices = fetch_all_moex()
    crypto_prices = fetch_coingecko_prices()
    metals_prices = fetch_cbr_metals()

    if not moex_prices and not crypto_prices and not metals_prices:
        raise RuntimeError("Оракул не смог получить ни одной цены")

    save_prices_to_db(moex_prices, asset_type="stock", source="moex")
    save_prices_to_db(crypto_prices, asset_type="crypto", source="coingecko")
    save_prices_to_db(metals_prices, asset_type="metal", source="cbr")

    total = len(moex_prices) + len(crypto_prices) + len(metals_prices)
    log.info(f"Оракул завершил работу. Всего цен: {total}")

    return {
        "moex": len(moex_prices),
        "crypto": len(crypto_prices),
        "metals": len(metals_prices),
        "total": total,
    }
