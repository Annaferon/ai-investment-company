"""Оракул: сбор рыночных данных с MOEX, Binance, ЦБ РФ."""
import time
from typing import Any, Optional

import requests

from src.core.logger import get_logger

log = get_logger("oracle")

MOEX_BASE = "https://iss.moex.com/iss"
MOEX_TICKERS = ["SBER", "GAZP", "LKOH", "GMKN", "ROSN", "NVTK", "TATN", "SNGS", "PLZL", "MTSS"]

BINANCE_BASE = "https://api.binance.com/api/v3"
BINANCE_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "BNB": "BNBUSDT"}

# Сколько раз пробуем получить данные
MAX_RETRIES = 5
RETRY_DELAY_SEC = 10


def _retry(func, *args, **kwargs):
    """Универсальная retry-обёртка."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = func(*args, **kwargs)
            if result is not None and result != {}:
                return result
            log.warning(f"Попытка {attempt}/{MAX_RETRIES}: пустой результат")
        except Exception as e:
            log.warning(f"Попытка {attempt}/{MAX_RETRIES} ошибка: {e}")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SEC)
    log.error(f"Не удалось получить данные после {MAX_RETRIES} попыток")
    return None


def fetch_moex_price(ticker: str) -> Optional[float]:
    """Цена акции с MOEX ISS."""
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
    """Все цены акций MOEX с retry."""
    prices = {}
    for ticker in MOEX_TICKERS:
        price = _retry(fetch_moex_price, ticker)
        if price:
            prices[ticker] = price
        else:
            log.error(f"MOEX {ticker}: не удалось получить цену")
    return prices


def fetch_binance_price(symbol: str) -> Optional[float]:
    """Цена крипты с Binance (USDT)."""
    url = f"{BINANCE_BASE}/ticker/price"
    r = requests.get(url, params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    data = r.json()
    return float(data["price"])


def fetch_all_crypto() -> dict[str, float]:
    """Все цены крипты с retry."""
    prices = {}
    for ticker, symbol in BINANCE_SYMBOLS.items():
        price = _retry(fetch_binance_price, symbol)
        if price:
            prices[ticker] = price
        else:
            log.error(f"Binance {ticker}: не удалось получить цену")
    return prices


def fetch_cbr_metals() -> dict[str, float]:
    """Цены драгметаллов с ЦБ РФ."""
    # TODO: подключить реальный XML-парсинг
    return {"GOLD": 7500.0, "SILVER": 95.0}


def save_prices_to_db(prices: dict[str, float], asset_type: str, source: str) -> int:
    """Сохранить цены в market_prices."""
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
    """Один цикл работы Оракула."""
    log.info("Оракул просыпается...")

    moex_prices = fetch_all_moex()
    crypto_prices = fetch_all_crypto()
    metals_prices = fetch_cbr_metals()

    # Главное правило: если НИ ОДНОЙ цены не получили — падаем с ошибкой
    if not moex_prices and not crypto_prices and not metals_prices:
        raise RuntimeError("Оракул не смог получить ни одной цены")

    save_prices_to_db(moex_prices, asset_type="stock", source="moex")
    save_prices_to_db(crypto_prices, asset_type="crypto", source="binance")
    save_prices_to_db(metals_prices, asset_type="metal", source="cbr")

    total = len(moex_prices) + len(crypto_prices) + len(metals_prices)
    log.info(f"Оракул завершил работу. Всего цен: {total}")

    return {
        "moex": len(moex_prices),
        "crypto": len(crypto_prices),
        "metals": len(metals_prices),
        "total": total,
    }
