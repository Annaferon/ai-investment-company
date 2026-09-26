"""Оракул: сбор рыночных данных с MOEX, CoinGecko, ЦБ РФ."""
import time
from datetime import datetime
from typing import Any, Optional

import requests

from src.core.logger import get_logger

log = get_logger("oracle")

# --- MOEX ---
MOEX_BASE = "https://iss.moex.com/iss"
MOEX_BULK_URL = f"{MOEX_BASE}/engines/stock/markets/shares/boards/TQBR/securities.json"
MOEX_TICKERS = {"SBER", "GAZP", "LKOH", "GMKN", "ROSN", "NVTK", "TATN", "SNGS", "PLZL", "MTSS"}

# --- CoinGecko ---
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
CRYPTO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
}

MAX_RETRIES = 2
RETRY_DELAY_SEC = 3
TIMEOUT_SEC = 8
FATAL_CODES = {400, 401, 403, 404, 451}


def _is_weekend() -> bool:
    """True если сегодня суббота или воскресенье."""
    return datetime.now().weekday() >= 5


def _retry(func, *args, **kwargs):
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
            log.warning(f"Попытка {attempt}/{MAX_RETRIES} HTTP {code}")
        except requests.Timeout:
            log.warning(f"Таймаут (попытка {attempt}/{MAX_RETRIES})")
            return None
        except Exception as e:
            log.warning(f"Попытка {attempt}/{MAX_RETRIES} ошибка: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SEC)
    return None


# --- MOEX (bulk) ---

def fetch_moex_bulk() -> dict[str, float]:
    """Один запрос — цены всех нужных бумаг MOEX."""
    try:
        r = requests.get(
            MOEX_BULK_URL,
            params={"iss.meta": "off", "iss.only": "marketdata,securities"},
            timeout=TIMEOUT_SEC,
        )
        r.raise_for_status()
        data = r.json()
    except requests.Timeout:
        log.error("MOEX: таймаут")
        return {}
    except Exception as e:
        log.error(f"MOEX ошибка: {e}")
        return {}

    cols = data.get("marketdata", {}).get("columns", [])
    rows = data.get("marketdata", {}).get("data", [])
    if not cols or not rows:
        log.error("MOEX: пустой marketdata")
        return {}

    idx_secid = cols.index("SECID")
    candidate_fields = ["LAST", "LCURRENTPRICE", "PREVPRICE", "WAPRICE", "OPEN"]
    idx_price = None
    field_used = None
    for f in candidate_fields:
        if f in cols:
            idx_price = cols.index(f)
            field_used = f
            break

    if idx_price is None:
        log.error(f"MOEX: не нашли ни одно поле из {candidate_fields}")
        return {}

    prices = {}
    for row in rows:
        ticker = row[idx_secid]
        if ticker not in MOEX_TICKERS:
            continue
        price = row[idx_price]
        if price is not None:
            prices[ticker] = float(price)

    log.info(f"MOEX: получено {len(prices)} цен (поле: {field_used})")
    return prices


# --- CoinGecko (крипта 24/7) ---

def fetch_coingecko_prices() -> dict[str, float]:
    ids = ",".join(CRYPTO_IDS.values())
    url = f"{COINGECKO_BASE}/simple/price"
    try:
        r = requests.get(
            url,
            params={"ids": ids, "vs_currencies": "usd"},
            timeout=TIMEOUT_SEC + 2,
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


# --- ЦБ РФ (металлы + курс) ---

def fetch_cbr_metals() -> dict[str, float]:
    """
    Цены драгметаллов + курс USD/RUB.
    В выходные ЦБ не обновляет — возвращаем последние известные из БД.
    """
    result = {}

    if _is_weekend():
        log.info("Выходной — берём последние цены металлов/курса из БД")
        from src.core.database import db
        rows = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, price
               FROM market_prices
               WHERE ticker IN ('GOLD', 'SILVER', 'USD_RUB')
               ORDER BY ticker, updated_at DESC;"""
        )
        for r in rows:
            result[r["ticker"]] = float(r["price"])
        if result:
            log.info(f"Из БД взято: {list(result.keys())}")
        return result

    # Будний день — пробуем получить курс USD/RUB
    try:
        r = requests.get(
            "https://www.cbr-xml-daily.ru/daily_json.js",
            timeout=TIMEOUT_SEC,
        )
        r.raise_for_status()
        data = r.json()
        usd = data.get("Valute", {}).get("USD", {}).get("Value")
        if usd:
            result["USD_RUB"] = float(usd)
            log.info(f"USD/RUB курс: {usd}")
    except Exception as e:
        log.warning(f"Не получили курс USD/RUB: {e}")

    # Заглушки для металлов (TODO: парсинг XML ЦБ)
    result["GOLD"] = 7500.0
    result["SILVER"] = 95.0

    return result


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
    is_weekend = _is_weekend()

    if is_weekend:
        log.info("Сегодня выходной — акции MOEX пропускаем")

    # Акции — только по будням
    moex_prices = {} if is_weekend else fetch_moex_bulk()

    # Крипта — всегда (24/7)
    crypto_prices = fetch_coingecko_prices()

    # Металлы + курс
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
        "weekend_mode": is_weekend,
    }
