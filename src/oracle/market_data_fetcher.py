"""Оракул: сбор рыночных данных с MOEX, Binance, ЦБ РФ."""
import requests
from typing import Any, Optional

from src.core.logger import get_logger

log = get_logger("oracle")

# --- MOEX ISS API ---
MOEX_BASE = "https://iss.moex.com/iss"

# Список акций РФ для отслеживания (можно расширить)
MOEX_TICKERS = ["SBER", "GAZP", "LKOH", "GMKN", "ROSN", "NVTK", "TATN", "SNGS", "PLZL", "MTSS"]


def fetch_moex_price(ticker: str) -> Optional[float]:
    """Получить последнюю цену акции с MOEX ISS."""
    url = f"{MOEX_BASE}/engines/stock/markets/shares/boards/TQBR/securities/{ticker}.json"
    try:
        r = requests.get(url, params={"iss.meta": "off"}, timeout=10)
        r.raise_for_status()
        data = r.json()
        # Ищем в блоке marketdata последнюю цену
        cols = data["marketdata"]["columns"]
        rows = data["marketdata"]["data"]
        if not rows:
            return None
        idx = cols.index("LAST") if "LAST" in cols else cols.index("LCURRENTPRICE")
        for row in rows:
            if row[idx] is not None:
                return float(row[idx])
        return None
    except Exception as e:
        log.warning(f"MOEX {ticker} ошибка: {e}")
        return None


def fetch_all_moex() -> dict[str, float]:
    """Получить цены всех отслеживаемых акций MOEX."""
    prices = {}
    for ticker in MOEX_TICKERS:
        price = fetch_moex_price(ticker)
        if price:
            prices[ticker] = price
        else:
            log.warning(f"MOEX {ticker}: цена не получена")
    return prices


# --- Binance API ---
BINANCE_BASE = "https://api.binance.com/api/v3"
BINANCE_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "BNB": "BNBUSDT"}


def fetch_binance_price(symbol: str) -> Optional[float]:
    """Получить цену крипты с Binance (в USDT)."""
    url = f"{BINANCE_BASE}/ticker/price"
    try:
        r = requests.get(url, params={"symbol": symbol}, timeout=10)
        r.raise_for_status()
        data = r.json()
        return float(data["price"])
    except Exception as e:
        log.warning(f"Binance {symbol} ошибка: {e}")
        return None


def fetch_all_crypto() -> dict[str, float]:
    """Цены крипты в USDT."""
    prices = {}
    for ticker, symbol in BINANCE_SYMBOLS.items():
        price = fetch_binance_price(symbol)
        if price:
            prices[ticker] = price
    return prices


# --- ЦБ РФ API (металлы) ---
CBR_METALS_URL = "https://cbr.ru/scripts/xml_metall.asp"


def fetch_cbr_metals() -> dict[str, float]:
    """Получить учётные цены драгметаллов с ЦБ РФ (за грамм, в рублях)."""
    # Упрощённо: берём золото и серебро через XML
    # Полный парсинг XML здесь опущен для краткости — можно добавить позже
    # Пока возвращаем заглушку с реальными примерными ценами
    return {
        "GOLD": 7500.0,   # золото, руб/грамм
        "SILVER": 95.0,   # серебро, руб/грамм
    }


def save_prices_to_db(prices: dict[str, float], asset_type: str, source: str) -> int:
    """Сохранить цены в таблицу market_prices."""
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
    """Один цикл работы Оракула: собрать все цены и сохранить."""
    log.info("Оракул просыпается...")

    # MOEX
    moex_prices = fetch_all_moex()
    save_prices_to_db(moex_prices, asset_type="stock", source="moex")

    # Binance
    crypto_prices = fetch_all_crypto()
    save_prices_to_db(crypto_prices, asset_type="crypto", source="binance")

    # ЦБ РФ
    metals_prices = fetch_cbr_metals()
    save_prices_to_db(metals_prices, asset_type="metal", source="cbr")

    total = len(moex_prices) + len(crypto_prices) + len(metals_prices)
    log.info(f"Оракул завершил работу. Всего цен: {total}")

    return {
        "moex": len(moex_prices),
        "crypto": len(crypto_prices),
        "metals": len(metals_prices),
        "total": total,
  }
