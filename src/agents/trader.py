"""Trader-01 — первый агент компании."""
import json
from datetime import datetime, timedelta
from typing import Any, Optional

from src.agents.base import BaseAgent
from src.core.database import db
from src.broker.virtual_broker import broker
from src.risk.risk_manager import risk_manager


SYSTEM_PROMPT = """Ты — профессиональный трейдер виртуальной инвестиционной компании.
Твоя задача: на основе рыночных цен, портфеля и ОТЧЁТОВ АНАЛИТИКОВ предложить ОДНО действие.

⚠️ ЯЗЫК ОТВЕТА: ТОЛЬКО РУССКИЙ. Никаких слов на других языках
(английский, польский, украинский и т.д.). Даже технические термины — по-русски.

ВАЖНО ПРО РЕЖИМ РАБОТЫ:
- Акции РФ (MOEX): Пн–Пт 07:00–23:50 МСК. В выходные закрыты.
- КРИПТА: торгуется КРУГОСУТОЧНО.
- Количество крипты — ДРОБНОЕ. Акций — целое.
- Если не знаешь quantity — ставь quantity: 0 (Risk Manager рассчитает).

ЛИМИТЫ RISK MANAGER (соблюдай!):
- BTC, ETH (Tier 1): максимум 15% капитала на позицию.
- SOL, BNB, LINK и все акции (Tier 2): максимум 5% капитала.
- DOGE, SHIB, PEPE, WIF, BONK (Tier 3): максимум 3% капитала.
- Не более 2 активов из одной группы.
- После продажи тикера — 24 часа не покупать снова.
- Дневной убыток 5% — полная блокировка торговли.

🔴 КРИТИЧНО ПРО СВЕЖЕСТЬ ДАННЫХ:
Каждый отчёт помечен возрастом:
- «свежий» (<6ч) → доверяй полностью
- «устаревший» (6-24ч) → учитывай с осторожностью, снижай confidence на 0.1
- «очень старый» (>24ч) → НЕ учитывай, игнорируй

Если данных нет вообще — принимай решение на основе того, что есть,
и указывай в reasoning, что работал без части аналитики.

ПРИОРИТЕТ ИСТОЧНИКОВ:
1. МАКРО РЕЖИМ — среда (tight/neutral/loose)
2. HISTORICAL — долгосрочный контекст (позиция в 720-дневном диапазоне)
3. CRYPTO/STOCK/METALS — оценки активов
4. MARKET — общий тренд
5. NEWS — новостной фон

ЛОГИКА HISTORICAL:
- Позиция <20% (дно) + bullish → СИЛЬНАЯ покупка
- Позиция 20-40% + bullish → умеренная покупка
- Позиция >80% (пик) + bearish → продажа

ПРАВИЛА SELL:
- BTC и ETH НЕ ПРОДАЁМ в убыток — только при прибыли ≥20% или пик >85%.
- Мемкоины и альткоины — обычный стоп-лосс (10-15%).

ОБЩЕЕ:
- Все цены — в рублях.
- Комиссия брокера: 0.05%.
- Не покупай в bearish-тренде рынка.
- Если хочешь остаться в деньгах — ticker "CASH", action "HOLD".

Отвечай СТРОГО в формате JSON (без пояснений, без markdown):
{
  "ticker": "SBER" | "BTC" | "ETH" | "SOL" | "LINK" | "DOGE" | "SHIB" | "PEPE" | "GAZP" | "LKOH" | "GOLD" | "CASH",
  "action": "BUY" | "SELL" | "HOLD",
  "quantity": 0,
  "confidence": 0.0-1.0,
  "reasoning": "объяснение на русском, 2-4 предложения"
}
"""

MAX_DATA_AGE_HOURS = 6
COOLDOWN_HOURS = 24
REPORT_MAX_AGE_HOURS = 24  # старше — не передаём в LLM

FALLBACK_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-31b-it:free",
    "inclusionai/ling-3.0-flash-fin:free",
    "openai/gpt-oss-20b:free",
    "cohere/north-mini-code:free",
    "openrouter/free",
]


def _age_hours(timestamp) -> Optional[float]:
    """Возраст данных в часах."""
    if timestamp is None:
        return None
    try:
        delta = datetime.now() - timestamp
        return delta.total_seconds() / 3600
    except Exception:
        return None


def _freshness_label(age: Optional[float]) -> str:
    """Метка свежести по возрасту."""
    if age is None:
        return "возраст неизвестен"
    if age < 6:
        return f"свежий, {age:.0f}ч"
    if age < 24:
        return f"устаревший, {age:.0f}ч"
    return f"очень старый, {age:.0f}ч"


class Trader(BaseAgent):
    DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

    def __init__(self, name: str = "Trader-01") -> None:
        super().__init__(name=name, role="trader")

    # ---------- Данные ----------

    def get_cooldown_tickers(self) -> set[str]:
        cutoff = datetime.now() - timedelta(hours=COOLDOWN_HOURS)
        rows = db.fetch_all(
            """SELECT DISTINCT ticker FROM trades
               WHERE action = 'SELL' AND created_at >= %s;""",
            (cutoff,),
        )
        return {r["ticker"] for r in rows}

    def get_market_prices(self) -> dict[str, float]:
        cutoff = datetime.now() - timedelta(hours=MAX_DATA_AGE_HOURS)
        rows = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, price, asset_type
               FROM market_prices
               WHERE updated_at >= %s
               ORDER BY ticker, updated_at DESC;""",
            (cutoff,),
        )
        if not rows:
            return {}

        raw = {}
        usd_rub = 90.0
        for r in rows:
            ticker = r["ticker"]
            price = float(r["price"])
            atype = r["asset_type"]
            if ticker == "USD_RUB":
                usd_rub = price
            else:
                raw[ticker] = (price, atype)

        prices = {}
        for ticker, (price, atype) in raw.items():
            prices[ticker] = price * usd_rub if atype == "crypto" else price

        self.log.info(f"Цен: {len(prices)} | USD/RUB: {usd_rub:.2f}")
        return prices

    def get_portfolio(self) -> list[dict[str, Any]]:
        return db.fetch_all("SELECT ticker, quantity, avg_price FROM portfolio;")

    def get_cash(self) -> float:
        row = db.fetch_one("SELECT cash FROM account WHERE id = 1;")
        return float(row["cash"]) if row else 0.0

    def get_analysts_block(self) -> str:
        blocks = []

        # --- MACRO ---
        macro = db.fetch_one(
            """SELECT regime, key_rate, inflation, usd_rub, brent,
                      summary, implications, created_at
               FROM macro_reports
               WHERE created_at >= NOW() - INTERVAL '48 hours'
               ORDER BY created_at DESC LIMIT 1;"""
        )
        if macro:
            age = _age_hours(macro["created_at"])
            if age is not None and age <= REPORT_MAX_AGE_HOURS:
                label = _freshness_label(age)
                lines = [f"=== МАКРО РЕЖИМ ({label}): {macro['regime'].upper()} ==="]
                lines.append(f"Ставка ЦБ: {macro['key_rate']}% | Инфляция: {macro['inflation']}%")
                lines.append(f"USD/RUB: {macro['usd_rub']:.2f} | Brent: ${macro['brent']}")
                lines.append(f"Вывод: {macro['summary']}")
                try:
                    impls = json.loads(macro["implications"]) if macro["implications"] else []
                    if impls:
                        lines.append("Влияние на сектора:")
                        for i in impls:
                            if isinstance(i, dict):
                                lines.append(f"  • {i.get('sector')}: {i.get('outlook')}")
                except Exception:
                    pass
                blocks.append("\n".join(lines))

        # --- HISTORICAL ---
        historical = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, trend, sentiment, score,
                      range_position, created_at
               FROM historical_reports
               WHERE created_at >= NOW() - INTERVAL '48 hours'
               ORDER BY ticker, created_at DESC;"""
        )
        if historical:
            lines = ["=== HISTORICAL (720 дней) ==="]
            for h in historical:
                age = _age_hours(h["created_at"])
                if age is not None and age <= REPORT_MAX_AGE_HOURS:
                    label = _freshness_label(age)
                    lines.append(
                        f"  • {h['ticker']}: {h['sentiment']} (score {h['score']}) "
                        f"| позиция {h['range_position']}% | {label}"
                    )
            if len(lines) > 1:
                blocks.append("\n".join(lines))

        # --- STOCK ---
        stocks = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, sentiment, score, created_at
               FROM stock_reports
               WHERE created_at >= NOW() - INTERVAL '48 hours'
               ORDER BY ticker, created_at DESC;"""
        )
        if stocks:
            lines = ["=== АКЦИИ РФ ==="]
            for s in stocks:
                age = _age_hours(s["created_at"])
                if age is not None and age <= REPORT_MAX_AGE_HOURS:
                    label = _freshness_label(age)
                    lines.append(f"  • {s['ticker']}: {s['sentiment']} ({s['score']}) | {label}")
            if len(lines) > 1:
                blocks.append("\n".join(lines))

        # --- CRYPTO ---
        cryptos = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, sentiment, score, created_at
               FROM crypto_reports
               WHERE created_at >= NOW() - INTERVAL '48 hours'
               ORDER BY ticker, created_at DESC;"""
        )
        if cryptos:
            lines = ["=== КРИПТА ==="]
            for c in cryptos:
                age = _age_hours(c["created_at"])
                if age is not None and age <= REPORT_MAX_AGE_HOURS:
                    label = _freshness_label(age)
                    lines.append(f"  • {c['ticker']}: {c['sentiment']} ({c['score']}) | {label}")
            if len(lines) > 1:
                blocks.append("\n".join(lines))

        # --- METALS ---
        metals = db.fetch_all(
            """SELECT DISTINCT ON (ticker) ticker, sentiment, score, created_at
               FROM metals_reports
               WHERE created_at >= NOW() - INTERVAL '48 hours'
               ORDER BY ticker, created_at DESC;"""
        )
        if metals:
            lines = ["=== МЕТАЛЛЫ ==="]
            for m in metals:
                age = _age_hours(m["created_at"])
                if age is not None and age <= REPORT_MAX_AGE_HOURS:
                    label = _freshness_label(age)
                    lines.append(f"  • {m['ticker']}: {m['sentiment']} ({m['score']}) | {label}")
            if len(lines) > 1:
                blocks.append("\n".join(lines))

        # --- NEWS ---
        news = db.fetch_one(
            """SELECT summary, sentiment, confidence, created_at
               FROM news_reports
               WHERE created_at >= NOW() - INTERVAL '48 hours'
               ORDER BY created_at DESC LIMIT 1;"""
        )
        if news:
            age = _age_hours(news["created_at"])
            if age is not None and age <= REPORT_MAX_AGE_HOURS:
                label = _freshness_label(age)
                blocks.append(
                    f"=== НОВОСТИ ({label}) ===\n"
                    f"Sentiment: {news['sentiment']}\n{news['summary'][:500]}"
                )

        # --- MARKET ---
        market = db.fetch_one(
            """SELECT summary, overall_trend, volatility_level, created_at
               FROM market_reports
               WHERE created_at >= NOW() - INTERVAL '48 hours'
               ORDER BY created_at DESC LIMIT 1;"""
        )
        if market:
            age = _age_hours(market["created_at"])
            if age is not None and age <= REPORT_MAX_AGE_HOURS:
                label = _freshness_label(age)
                blocks.append(
                    f"=== РЫНОК ({label}) ===\n"
                    f"Тренд: {market['overall_trend']}\n"
                    f"Волатильность: {market['volatility_level']}\n"
                    f"{market['summary'][:500]}"
                )

        if not blocks:
            return "ОТЧЁТЫ АНАЛИТИКОВ: нет свежих данных (все старше 24ч)."

        return "ОТЧЁТЫ АНАЛИТИКОВ:\n\n" + "\n\n".join(blocks)

    # ---------- Решение ----------

    def decide(self) -> dict[str, Any]:
        prices = self.get_market_prices()
        if not prices:
            raise RuntimeError("Нет свежих рыночных данных")

        cooldown = self.get_cooldown_tickers()
        if cooldown:
            self.log.info(f"В cooldown: {cooldown} — исключаем из BUY")
            prices = {t: p for t, p in prices.items() if t not in cooldown}

        portfolio = self.get_portfolio()
        cash = self.get_cash()
        analysts_block = self.get_analysts_block()

        weekend_hint = ""
        if datetime.now().weekday() >= 5:
            weekend_hint = "\nВАЖНО: Выходной. Доступна только крипта."

        cooldown_hint = ""
        if cooldown:
            cooldown_hint = (
                f"\nВАЖНО: Тикеры {', '.join(cooldown)} в cooldown после недавней продажи. "
                f"Их НЕ покупать. В списке цен их уже нет."
            )

        prompt = f"""Цены (₽):
{json.dumps(prices, ensure_ascii=False, indent=2, default=float)}

Портфель:
{json.dumps(portfolio, ensure_ascii=False, indent=2, default=float) if portfolio else "пусто"}

Свободные деньги: {cash:.2f} ₽

{analysts_block}
{weekend_hint}{cooldown_hint}

Что делаем? Отвечай JSON без пояснений. ТОЛЬКО НА РУССКОМ."""

        last_error = None
        for model in FALLBACK_MODELS:
            try:
                self.log.info(f"Пробую модель: {model}")
                self.model = model
                raw = self.think(prompt=prompt, system=SYSTEM_PROMPT)

                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.strip("`").replace("json", "", 1).strip()

                first_brace = cleaned.find("{")
                last_brace = cleaned.rfind("}")
                if first_brace != -1 and last_brace > first_brace:
                    cleaned = cleaned[first_brace:last_brace + 1]

                decision = json.loads(cleaned)
                self.log.info(f"✓ Модель ответила: {model}")
                return decision

            except json.JSONDecodeError as e:
                self.log.warning(f"Модель {model} — не JSON: {str(e)[:100]}")
                last_error = e
                continue
            except Exception as e:
                self.log.warning(f"Модель {model} недоступна: {str(e)[:150]}")
                last_error = e
                continue

        raise RuntimeError(f"Все модели недоступны. Последняя ошибка: {last_error}")

    def run(self) -> dict[str, Any]:
        self.log.info("Trader просыпается...")

        try:
            decision = self.decide()
        except Exception as e:
            self.log.error(f"Не смог принять решение: {e}")
            return {"error": str(e)}

        ticker = decision.get("ticker", "CASH").upper()
        action = decision.get("action", "HOLD").upper()

        self.record_decision(
            ticker=ticker, action=action,
            confidence=float(decision.get("confidence", 0.0)),
            reasoning=decision.get("reasoning", ""),
        )

        self.log.info(f"Решение: {action} {ticker} ({decision.get('confidence')})")

        if ticker == "CASH" or action == "HOLD":
            return {**decision, "execution": {"executed": False, "reason": "cash_hold"}}

        prices = self.get_market_prices()
        decision["price"] = prices.get(ticker, 0)

        if decision["price"] <= 0:
            self.log.warning(f"Нет цены для {ticker}")
            return {**decision, "execution": {"executed": False, "reason": "no_price"}}

        checked = risk_manager.check(decision)
        if checked.get("risk_check", {}).get("status") == "rejected":
            reason = checked["risk_check"]["reason"]
            return {**checked, "execution": {"executed": False, "reason": "risk_rejected", "detail": reason}}

        execution = broker.execute(checked)
        self.log.info(f"Брокер: {execution}")

        return {**checked, "execution": execution}
