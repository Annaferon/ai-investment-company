"""Auditor — оценивает эффективность агентов и аналитиков.
Каждый день собирает сигналы и оценивает их по цене через 3 дня."""
from datetime import date, datetime, timedelta
from typing import Any

from src.core.database import db
from src.core.logger import get_logger

log = get_logger("auditor")

# Таблицы с сигналами аналитиков
REPORT_TABLES = {
    "stock_reports": "stock",
    "crypto_reports": "crypto",
    "metals_reports": "metal",
}

LOOKBACK_DAYS = 14          # искать сигналы за последние N дней
EVAL_WINDOW_DAYS = 3        # оценивать через N дней
PRICE_MOVE_THRESHOLD = 0.5  # % — минимальное движение для оценки


class Auditor:
    def __init__(self, name: str = "Auditor-01") -> None:
        self.name = name

    # ---------- Цены ----------

    def _get_price_at(self, ticker: str, target_date) -> float | None:
        """Ближайшая цена до указанной даты."""
        row = db.fetch_one(
            """SELECT price FROM price_history
               WHERE ticker = %s AND price_date <= %s
               ORDER BY price_date DESC LIMIT 1;""",
            (ticker, target_date),
        )
        return float(row["price"]) if row else None

    # ---------- Сигналы аналитиков ----------

    def _insert_new_signals(self) -> int:
        inserted = 0
        cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)

        for table in REPORT_TABLES.keys():
            try:
                rows = db.fetch_all(
                    f"""SELECT agent_name, ticker, sentiment, report_date
                        FROM {table}
                        WHERE report_date >= %s
                          AND sentiment IN ('bullish', 'bearish')
                        ORDER BY report_date ASC;""",
                    (cutoff,),
                )
            except Exception as e:
                log.error(f"Ошибка чтения {table}: {e}")
                continue

            for r in rows:
                ticker = r["ticker"]
                signal_date = r["report_date"]
                price = self._get_price_at(ticker, signal_date)
                if price is None or price <= 0:
                    continue
                try:
                    db.execute(
                        """INSERT INTO signal_outcomes
                           (agent_name, ticker, source_table, signal_type,
                            signal_date, price_at_signal)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT DO NOTHING;""",
                        (r["agent_name"], ticker, table, r["sentiment"],
                         signal_date, price),
                    )
                    inserted += 1
                except Exception as e:
                    log.error(f"Ошибка вставки {ticker}: {e}")
        return inserted

    def _evaluate_pending_signals(self) -> int:
        eval_date = date.today() - timedelta(days=EVAL_WINDOW_DAYS)
        rows = db.fetch_all(
            """SELECT id, ticker, signal_type, signal_date, price_at_signal
               FROM signal_outcomes
               WHERE is_correct IS NULL AND signal_date <= %s;""",
            (eval_date,),
        )
        evaluated = 0
        for r in rows:
            after_date = r["signal_date"] + timedelta(days=EVAL_WINDOW_DAYS)
            price_after = self._get_price_at(r["ticker"], after_date)
            if price_after is None:
                continue
            price_before = float(r["price_at_signal"])
            if price_before <= 0:
                continue
            change_pct = (price_after - price_before) / price_before * 100

            is_correct = None
            if r["signal_type"] == "bullish":
                if change_pct > PRICE_MOVE_THRESHOLD:
                    is_correct = True
                elif change_pct < -PRICE_MOVE_THRESHOLD:
                    is_correct = False
            elif r["signal_type"] == "bearish":
                if change_pct < -PRICE_MOVE_THRESHOLD:
                    is_correct = True
                elif change_pct > PRICE_MOVE_THRESHOLD:
                    is_correct = False

            if is_correct is None:
                db.execute(
                    "UPDATE signal_outcomes SET evaluated_at = NOW() WHERE id = %s;",
                    (r["id"],),
                )
                continue

            db.execute(
                """UPDATE signal_outcomes
                   SET price_after = %s, is_correct = %s, evaluated_at = NOW()
                   WHERE id = %s;""",
                (price_after, is_correct, r["id"]),
            )
            evaluated += 1
        return evaluated

    # ---------- Решения Trader ----------

    def _insert_new_trades(self) -> int:
        cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
        rows = db.fetch_all(
            """SELECT ticker, action, created_at
               FROM decisions
               WHERE created_at >= %s AND action IN ('BUY', 'SELL')
               ORDER BY created_at ASC;""",
            (cutoff,),
        )
        inserted = 0
        for r in rows:
            ticker = r["ticker"]
            action = r["action"]
            created = r["created_at"]
            decision_date = created.date() if hasattr(created, "date") else created
            price = self._get_price_at(ticker, decision_date)
            if price is None or price <= 0:
                continue
            try:
                db.execute(
                    """INSERT INTO trade_outcomes
                       (ticker, action, decision_date, price_at_action)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT DO NOTHING;""",
                    (ticker, action, decision_date, price),
                )
                inserted += 1
            except Exception as e:
                log.error(f"Ошибка вставки {ticker}: {e}")
        return inserted

    def _evaluate_pending_trades(self) -> int:
        eval_date = date.today() - timedelta(days=EVAL_WINDOW_DAYS)
        rows = db.fetch_all(
            """SELECT id, ticker, action, decision_date, price_at_action
               FROM trade_outcomes
               WHERE is_correct IS NULL AND decision_date <= %s;""",
            (eval_date,),
        )
        evaluated = 0
        for r in rows:
            after_date = r["decision_date"] + timedelta(days=EVAL_WINDOW_DAYS)
            price_after = self._get_price_at(r["ticker"], after_date)
            if price_after is None:
                continue
            price_before = float(r["price_at_action"])
            if price_before <= 0:
                continue
            change_pct = (price_after - price_before) / price_before * 100

            is_correct = None
            if r["action"] == "BUY":
                if change_pct > PRICE_MOVE_THRESHOLD:
                    is_correct = True
                elif change_pct < -PRICE_MOVE_THRESHOLD:
                    is_correct = False
            elif r["action"] == "SELL":
                if change_pct < -PRICE_MOVE_THRESHOLD:
                    is_correct = True
                elif change_pct > PRICE_MOVE_THRESHOLD:
                    is_correct = False

            if is_correct is None:
                db.execute(
                    "UPDATE trade_outcomes SET evaluated_at = NOW() WHERE id = %s;",
                    (r["id"],),
                )
                continue

            db.execute(
                """UPDATE trade_outcomes
                   SET price_after = %s, is_correct = %s, evaluated_at = NOW()
                   WHERE id = %s;""",
                (price_after, is_correct, r["id"]),
            )
            evaluated += 1
        return evaluated

    # ---------- Статистика ----------

    def get_agent_stats(self) -> list[dict]:
        week_ago = date.today() - timedelta(days=7)
        agents_rows = db.fetch_all(
            "SELECT DISTINCT agent_name FROM signal_outcomes;"
        )
        result = []
        for a in agents_rows:
            name = a["agent_name"]

            all_row = db.fetch_one(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) as correct
                   FROM signal_outcomes
                   WHERE agent_name = %s AND is_correct IS NOT NULL;""",
                (name,),
            )
            total_all = int(all_row["total"]) if all_row else 0
            correct_all = int(all_row["correct"] or 0) if all_row else 0

            week_row = db.fetch_one(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) as correct
                   FROM signal_outcomes
                   WHERE agent_name = %s AND is_correct IS NOT NULL
                     AND signal_date >= %s;""",
                (name, week_ago),
            )
            total_week = int(week_row["total"]) if week_row else 0
            correct_week = int(week_row["correct"] or 0) if week_row else 0

            result.append({
                "agent_name": name,
                "total_all": total_all,
                "correct_all": correct_all,
                "accuracy_all": correct_all / total_all * 100 if total_all else 0,
                "total_week": total_week,
                "correct_week": correct_week,
                "accuracy_week": correct_week / total_week * 100 if total_week else 0,
            })
        return result

    def get_trader_stats(self) -> dict:
        week_ago = date.today() - timedelta(days=7)
        all_row = db.fetch_one(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) as correct
               FROM trade_outcomes WHERE is_correct IS NOT NULL;"""
        )
        total_all = int(all_row["total"]) if all_row else 0
        correct_all = int(all_row["correct"] or 0) if all_row else 0

        week_row = db.fetch_one(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) as correct
               FROM trade_outcomes
               WHERE is_correct IS NOT NULL AND decision_date >= %s;""",
            (week_ago,),
        )
        total_week = int(week_row["total"]) if week_row else 0
        correct_week = int(week_row["correct"] or 0) if week_row else 0

        return {
            "total_all": total_all,
            "correct_all": correct_all,
            "accuracy_all": correct_all / total_all * 100 if total_all else 0,
            "total_week": total_week,
            "correct_week": correct_week,
            "accuracy_week": correct_week / total_week * 100 if total_week else 0,
        }

    def get_account_stats(self) -> dict:
        acc = db.fetch_one("SELECT cash, initial_capital FROM account WHERE id = 1;")
        if not acc:
            return {"cash": 0, "assets": 0, "total": 0, "initial": 0, "pnl": 0, "pnl_pct": 0}

        cash = float(acc["cash"])
        initial = float(acc["initial_capital"])

        positions = db.fetch_all("SELECT ticker, quantity, avg_price FROM portfolio;")
        assets_value = 0.0
        for p in positions:
            price_row = db.fetch_one(
                """SELECT price FROM market_prices
                   WHERE ticker = %s ORDER BY updated_at DESC LIMIT 1;""",
                (p["ticker"],),
            )
            if price_row:
                assets_value += float(p["quantity"]) * float(price_row["price"])
            else:
                assets_value += float(p["quantity"]) * float(p["avg_price"])

        total = cash + assets_value
        pnl = total - initial
        return {
            "cash": cash,
            "assets": assets_value,
            "total": total,
            "initial": initial,
            "pnl": pnl,
            "pnl_pct": pnl / initial * 100 if initial else 0,
        }

    # ---------- Отчёт ----------

    def build_report(self) -> str:
        lines = ["📊 *АУДИТ — эффективность агентов*", ""]

        acc = self.get_account_stats()
        pnl_emoji = "🟢" if acc["pnl"] >= 0 else "🔴"
        lines.append("💰 *КАПИТАЛ*")
        lines.append(f"• Свободные: {acc['cash']:,.2f} ₽")
        lines.append(f"• В активах: {acc['assets']:,.2f} ₽")
        lines.append(f"• *Итого: {acc['total']:,.2f} ₽*")
        lines.append(f"{pnl_emoji} P/L: {acc['pnl']:+,.2f} ₽ ({acc['pnl_pct']:+.2f}%)")
        lines.append("")

        lines.append("👥 *ЭФФЕКТИВНОСТЬ АГЕНТОВ*")
        agents = self.get_agent_stats()
        if not agents:
            lines.append("(пока нет оценённых сигналов)")
        for a in agents:
            acc_all = a["accuracy_all"]
            emoji = "🟢" if acc_all >= 60 else "🟡" if acc_all >= 45 else "🔴"
            lines.append(f"\n{emoji} *{a['agent_name']}*")
            lines.append(f"  Неделя: {a['accuracy_week']:.0f}% ({a['correct_week']}/{a['total_week']})")
            lines.append(f"  Всё время: {acc_all:.0f}% ({a['correct_all']}/{a['total_all']})")
        lines.append("")

        t = self.get_trader_stats()
        if t["total_all"] > 0:
            lines.append("👨‍💼 *TRADER-01*")
            lines.append(f"  Неделя: {t['accuracy_week']:.0f}% ({t['correct_week']}/{t['total_week']})")
            lines.append(f"  Всё время: {t['accuracy_all']:.0f}% ({t['correct_all']}/{t['total_all']})")
            lines.append("")

        lines.append("🎯 *РЕКОМЕНДАЦИИ*")
        has_recos = False
        for a in agents:
            if a["total_all"] < 5:
                lines.append(f"⚪ {a['agent_name']} — мало данных, наблюдаем")
            elif a["accuracy_all"] >= 65:
                lines.append(f"🟢 {a['agent_name']} — усиливаем ({a['accuracy_all']:.0f}%)")
            elif a["accuracy_all"] < 40:
                lines.append(f"🔴 {a['agent_name']} — кандидат на увольнение ({a['accuracy_all']:.0f}%)")
            else:
                lines.append(f"⚪ {a['agent_name']} — наблюдаем ({a['accuracy_all']:.0f}%)")
            has_recos = True
        if not has_recos:
            lines.append("(нет данных)")

        return "\n".join(lines)

    # ---------- Запуск ----------

    def run_daily(self) -> dict[str, Any]:
        log.info("Auditor daily запускается...")
        inserted_signals = self._insert_new_signals()
        evaluated_signals = self._evaluate_pending_signals()
        inserted_trades = self._insert_new_trades()
        evaluated_trades = self._evaluate_pending_trades()
        log.info(f"Сигналы: +{inserted_signals} новых, оценено {evaluated_signals}")
        log.info(f"Сделки: +{inserted_trades} новых, оценено {evaluated_trades}")
        return {
            "signals_inserted": inserted_signals,
            "signals_evaluated": evaluated_signals,
            "trades_inserted": inserted_trades,
            "trades_evaluated": evaluated_trades,
        }

    def run_weekly(self) -> dict[str, Any]:
        log.info("Auditor weekly — формируем отчёт")
        self.run_daily()
        report = self.build_report()
        return {"report": report}
