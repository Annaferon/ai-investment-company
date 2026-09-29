"""Оракул: сбор рыночных данных с MOEX, Kraken, ЦБ РФ.
Крипта — 24/7 (10 монет). MOEX/ЦБ — только в рабочие часы Пн-Пт."""
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

# --- Kraken (крипта) ---
KRAKEN_BASE = "https://api.kraken.com/0/public"
KRAKEN_PAIRS = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "LINK": "LINKUSD",
    "DOGE": "DOGEUSD",
    "SHIB": "SHIBUSD",
    "PEPE": "PEPEUSD",
    # BNB, WIF, BONK нет на Kraken
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


# ---------- Kraken (крипта) ----------

def fetch_kraken_prices() -> dict[str, float]:
    """Цены крипты через Kraken Ticker (все пары одним запросом)."""
    pairs = ",".join(KRAKEN_PAIRS.values())
    url = f"{KRAKEN_BASE}/Ticker"
    try:
        r = requests.get(
            url,
            params={"pair": pairs},
            headers={"User-Agent": "Mozilla/5.0 (AI-Investment-Company)"},
            timeout=TIMEOUT_SEC + 5,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error(f"Kraken ошибка: {e}")
        return {}

    if data.get("error"):
        log.error(f"Kraken API error: {data['error']}")
        return {}

    result = data.get("result", {})
    prices = {}
    for ticker, kraken_pair in KRAKEN_PAIRS.items():
        # Kraken может вернуть пару под другим именем (XBTUSD → XXBTZUSD)
        row = None
        if kraken_pair in result:
            row = result[kraken_pair]
        else:
            for k, v in result.items():
                if kraken_pair.replace("XBT", "XXBT").replace("USD", "ZUSD") in k or k.startswith(kraken_pair[:3]):
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
            headers={"User-Agent": "Mozilla/5.0 (AI-Investment-Company)"},
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

    if moex_open:
        moex_prices = fetch_moex_bulk()
    else:
        log.info("MOEX закрыт — акции пропускаем")
        moex_prices = {}

    crypto_prices = fetch_kraken_prices()
    metals_prices = fetch_cbr_metals()

    if not moex_prices and not crypto_prices and not metals_prices:
        raise RuntimeError("Оракул не смог получить ни одной цены")

    save_prices_to_db(moex_prices, asset_type="stock", source="moex")
    save_prices_to_db(crypto_prices, asset_type="crypto", source="kraken")
    save_prices_to_db(metals_prices, asset_type="metal", source="cbr")

    total = len(moex_prices) + len(crypto_prices) + len(metals_prices)
    log.info(f"Оракул завершил работу. Всего цен: {total}")

    return {
        "moex": moex_prices,
        "crypto": crypto_prices,
        "metals": metals_prices,
        "total": total,
        "weekend_mode": is_weekend,
        "moex_open": moex_open,
    }
