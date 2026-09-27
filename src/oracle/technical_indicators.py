"""Технические индикаторы для анализа графиков.
Считает RSI, MA, ATR, волатильность, позицию в диапазоне."""
from typing import Optional

from src.core.database import db
from src.core.logger import get_logger

log = get_logger("indicators")


def get_price_series(ticker: str, days: int = 365) -> list[dict]:
    """Получить историю цен тикера из price_history."""
    rows = db.fetch_all(
        """SELECT price_date, price FROM price_history
           WHERE ticker = %s
           ORDER BY price_date ASC
           LIMIT %s;""",
        (ticker, days),
    )
    return [{"date": r["price_date"], "price": float(r["price"])} for r in rows]


# ---------- Скользящие средние ----------

def calc_ma(prices: list[float], period: int) -> Optional[float]:
    """Простое скользящее среднее за последние N точек."""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calc_ema(prices: list[float], period: int) -> Optional[float]:
    """Экспоненциальное скользящее среднее."""
    if len(prices) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


# ---------- RSI ----------

def calc_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """RSI — индекс относительной силы.
    >70 — перекуплен, <30 — перепродан."""
    if len(prices) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-diff)

    # Берём последние `period` значений
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ---------- ATR (волатильность) ----------

def calc_atr(prices: list[float], period: int = 14) -> Optional[float]:
    """Approximate ATR — средний диапазон цены (упрощённо)."""
    if len(prices) < period + 1:
        return None
    ranges = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    return sum(ranges[-period:]) / period


# ---------- Позиция в диапазоне ----------

def calc_range_position(prices: list[float]) -> Optional[float]:
    """Где сейчас цена в диапазоне min-max за период (0-100%)."""
    if not prices:
        return None
    lo, hi = min(prices), max(prices)
    if hi == lo:
        return 50.0
    return ((prices[-1] - lo) / (hi - lo)) * 100


# ---------- Главная функция ----------

def analyze_ticker(ticker: str, days: int = 365) -> dict:
    """Полный технический анализ тикера."""
    series = get_price_series(ticker, days)
    if len(series) < 30:
        return {"ticker": ticker, "error": "not_enough_data", "points": len(series)}

    prices = [p["price"] for p in series]
    current = prices[-1]

    # Считаем индикаторы
    ma20 = calc_ma(prices, 20)
    ma50 = calc_ma(prices, 50)
    ma200 = calc_ma(prices, 200) if len(prices) >= 200 else None
    rsi = calc_rsi(prices, 14)
    atr = calc_atr(prices, 14)
    range_pos = calc_range_position(prices)

    # Изменения за периоды
    def pct_change(n: int) -> Optional[float]:
        if len(prices) <= n:
            return None
        return (prices[-1] - prices[-n]) / prices[-n] * 100

    change_24h = pct_change(1)
    change_7d = pct_change(7)
    change_30d = pct_change(30)
    change_365d = pct_change(365)

    # Сигналы
    signals = []
    if rsi is not None:
        if rsi > 70:
            signals.append("RSI перекуплен (>70)")
        elif rsi < 30:
            signals.append("RSI перепродан (<30)")

    if ma20 and ma50:
        if ma20 > ma50:
            signals.append("MA20 > MA50 (uptrend)")
        else:
            signals.append("MA20 < MA50 (downtrend)")

    if ma200 and current > ma200:
        signals.append("Цена выше MA200 (long-term bull)")
    elif ma200 and current < ma200:
        signals.append("Цена ниже MA200 (long-term bear)")

    return {
        "ticker": ticker,
        "points": len(prices),
        "current": current,
        "min_year": min(prices),
        "max_year": max(prices),
        "range_position": round(range_pos, 1) if range_pos is not None else None,
        "rsi": round(rsi, 1) if rsi else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma50": round(ma50, 2) if ma50 else None,
        "ma200": round(ma200, 2) if ma200 else None,
        "atr": round(atr, 4) if atr else None,
        "atr_pct": round(atr / current * 100, 2) if atr and current else None,
        "change_24h": round(change_24h, 2) if change_24h is not None else None,
        "change_7d": round(change_7d, 2) if change_7d is not None else None,
        "change_30d": round(change_30d, 2) if change_30d is not None else None,
        "change_365d": round(change_365d, 2) if change_365d is not None else None,
        "signals": signals,
    }


def analyze_all(tickers: list[str]) -> dict[str, dict]:
    """Анализ всех тикеров."""
    result = {}
    for ticker in tickers:
        try:
            result[ticker] = analyze_ticker(ticker)
        except Exception as e:
            log.error(f"Ошибка анализа {ticker}: {e}")
            result[ticker] = {"ticker": ticker, "error": str(e)}
    return result


def format_for_llm(analysis: dict[str, dict]) -> str:
    """Красиво форматируем результаты для LLM."""
    lines = []
    for ticker, data in analysis.items():
        if "error" in data:
            lines.append(f"{ticker}: нет данных ({data.get('error')})")
            continue

        lines.append(f"\n=== {ticker} ===")
        lines.append(f"Цена: ${data['current']:,.2f}")
        lines.append(f"Диапазон за год: ${data['min_year']:,.2f} — ${data['max_year']:,.2f}")
        lines.append(f"Позиция в диапазоне: {data['range_position']}% (0=дно, 100=пик)")
        lines.append(f"RSI: {data['rsi']} {'(перекуплен)' if data['rsi'] and data['rsi']>70 else '(перепродан)' if data['rsi'] and data['rsi']<30 else ''}")
        lines.append(f"MA20: ${data['ma20']:,.2f} | MA50: ${data['ma50']:,.2f}" + (f" | MA200: ${data['ma200']:,.2f}" if data['ma200'] else ""))
        lines.append(f"ATR: {data['atr_pct']}% (волатильность)")
        lines.append(f"Изменения: 24ч {data['change_24h']:+.1f}% | 7д {data['change_7d']:+.1f}% | 30д {data['change_30d']:+.1f}%" + (f" | 365д {data['change_365d']:+.1f}%" if data['change_365d'] else ""))
        if data['signals']:
            lines.append(f"Сигналы: {'; '.join(data['signals'])}")
    return "\n".join(lines)
