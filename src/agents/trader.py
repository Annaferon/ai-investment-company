def get_analysts_block(self) -> str:
    blocks = []

    # --- MACRO (в самом верху — влияет на всё) ---
    macro = db.fetch_one(
        """SELECT regime, key_rate, inflation, usd_rub, brent,
                  summary, implications
           FROM macro_reports
           WHERE created_at >= NOW() - INTERVAL '24 hours'
           ORDER BY created_at DESC LIMIT 1;"""
    )
    if macro:
        lines = [f"=== МАКРО РЕЖИМ: {macro['regime'].upper()} ==="]
        lines.append(f"Ключевая ставка ЦБ: {macro['key_rate']}%")
        lines.append(f"Инфляция: {macro['inflation']}%")
        lines.append(f"USD/RUB: {macro['usd_rub']:.2f}")
        lines.append(f"Brent: ${macro['brent']}")
        lines.append(f"Вывод: {macro['summary']}")
        try:
            impls = json.loads(macro["implications"]) if macro["implications"] else []
            if impls:
                lines.append("Влияние на сектора:")
                for i in impls:
                    if isinstance(i, dict):
                        lines.append(
                            f"  • {i.get('sector')}: {i.get('outlook')} — "
                            f"{i.get('reasoning', '')[:120]}"
                        )
        except Exception:
            pass
        blocks.append("\n".join(lines))

    # --- HISTORICAL ---
    historical = db.fetch_all(
        """SELECT DISTINCT ON (ticker) ticker, trend, sentiment, score,
                  range_position, reasoning
           FROM historical_reports
           WHERE created_at >= NOW() - INTERVAL '24 hours'
           ORDER BY ticker, created_at DESC;"""
    )
    if historical:
        lines = ["=== HISTORICAL (720 дней) ==="]
        for h in historical:
            lines.append(
                f"  • {h['ticker']}: {h['sentiment']} (score {h['score']}) "
                f"| позиция {h['range_position']}% | тренд {h['trend']}"
            )
        blocks.append("\n".join(lines))

    # --- STOCK ---
    stocks = db.fetch_all(
        """SELECT DISTINCT ON (ticker) ticker, sentiment, score, reasoning
           FROM stock_reports
           WHERE created_at >= NOW() - INTERVAL '24 hours'
           ORDER BY ticker, created_at DESC;"""
    )
    if stocks:
        lines = ["=== АКЦИИ РФ ==="]
        for s in stocks:
            lines.append(f"  • {s['ticker']}: {s['sentiment']} ({s['score']})")
        blocks.append("\n".join(lines))

    # --- CRYPTO ---
    cryptos = db.fetch_all(
        """SELECT DISTINCT ON (ticker) ticker, sentiment, score, reasoning
           FROM crypto_reports
           WHERE created_at >= NOW() - INTERVAL '24 hours'
           ORDER BY ticker, created_at DESC;"""
    )
    if cryptos:
        lines = ["=== КРИПТА ==="]
        for c in cryptos:
            lines.append(f"  • {c['ticker']}: {c['sentiment']} ({c['score']})")
        blocks.append("\n".join(lines))

    # --- METALS ---
    metals = db.fetch_all(
        """SELECT DISTINCT ON (ticker) ticker, sentiment, score, reasoning
           FROM metals_reports
           WHERE created_at >= NOW() - INTERVAL '48 hours'
           ORDER BY ticker, created_at DESC;"""
    )
    if metals:
        lines = ["=== МЕТАЛЛЫ ==="]
        for m in metals:
            lines.append(f"  • {m['ticker']}: {m['sentiment']} ({m['score']})")
        blocks.append("\n".join(lines))

    if not blocks:
        return "ОТЧЁТЫ АНАЛИТИКОВ: нет данных."

    return "ОТЧЁТЫ АНАЛИТИКОВ:\n\n" + "\n\n".join(blocks)
