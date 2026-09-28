"""Оракул: сбор рыночных данных с MOEX, CoinGecko, ЦБ РФ.
Возвращает цены + % изменения к предыдущему запросу."""
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from src.core.logger import get_logger

log = get_logger("oracle")

MSK = timezone(timedelta(hours=3))

# --- MOEX ---
MOEX_BASE = "https://iss.moex.com/iss"
MOEX_BULK_URL = f"{MOEX_BASE}/engines/stock/markets/shares/boards/TQBR/securities.json"
MOEX_TICKERS = {"SBER", "GAZP", "LKOH", "GMKN", "ROSN", "NVTK", "TATN", "SNGS", "PLZL", "MTSS"}

# --- CoinGecko (крипта) ---
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
CRYPTO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "LINK": "chainlink",
    "DOGE": "dogecoin",
    "SHIB": "shiba-inu",
    "PEPE": "pepe",
    "WIF": "dogwifcoin",
    "BONK": "bonk",
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


# ---------- Предыдущие цены ----------

def get_previous_prices() -> dict[str, float]:
    """Предыдущие цены из БД (последнее значение для каждого тикера)."""
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
    """% изменения новой цены к предыдущей."""
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
        log.error("MOEX: не нашли поле цены")
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


# ---------- CoinGecko ----------

def fetch_crypto_prices() -> dict[str, float]:
    ids = ",".join(CRYPTO_IDS.values())
    url = f"{COINGECKO_BASE}/simple/price"
    try:
        r = requests.get(
            url,
            params={"ids": ids, "vs_currencies": "usd"},
            timeout=TIMEOUT_SEC + 5,
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


# ---------- ЦБ РФ ----------

def fetch_cbr_metals() -> dict[str, float]:
    result = {}

    if not _is_moex_open():
        log.info("MOEX закрыт — металлы/курс берём из БД")
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


def run_oracle() -> dict[str, Any]:
    log.info("Оракул просыпается...")
    is_weekend = _is_weekend()
    moex_open = _is_moex_open()

    # 1. Запоминаем предыдущие цены ДО сохранения новых
    old_prices = get_previous_prices()

    # 2. Получаем новые цены
    if moex_open:
        moex_prices = fetch_moex_bulk()
    else:
        log.info("MOEX закрыт — акции пропускаем")
        moex_prices = {}

    crypto_prices = fetch_crypto_prices()
    metals_prices = fetch_cbr_metals()

    if not moex_prices and not crypto_prices and not metals_prices:
        raise RuntimeError("Оракул не смог получить ни одной цены")

    # 3. Считаем % изменения
    all_new = {**moex_prices, **crypto_prices, **metals_prices}
    changes = calc_changes(all_new, old_prices)

    # 4. Сохраняем в БД
    save_prices_to_db(moex_prices, asset_type="stock", source="moex")
    save_prices_to_db(crypto_prices, asset_type="crypto", source="coingecko")
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
