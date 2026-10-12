# -*- coding: utf-8 -*-
"""اختبار خلفي بسيط: تطبيق منطق الإشارات على بيانات تاريخية."""

import sys
import time

import pandas as pd

import config
from scanner import fetch_klines, compute_indicators, score_row, build_signal


def backtest_symbol(symbol, lookback_days=180):
    """اختبار رمز واحد على بيانات تاريخية."""
    # جلب بيانات كافية (4h → 6 شموع/يوم)
    needed = lookback_days * 6 + 100
    df = fetch_klines(symbol, limit=1000)
    if df is None or len(df) < 100:
        return None

    df = compute_indicators(df.copy())
    trades = []
    in_position = None

    # محاكاة شمعة بشمعة (نستخدم المؤشرات المحسوبة مسبقاً — تقريب مقبول للـ v1)
    for i in range(60, len(df) - 1):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        if in_position:
            # فحص الخروج
            price_high, price_low = df["high"].iloc[i], df["low"].iloc[i]
            p = in_position
            is_long = p["direction"] == "long"
            exit_price, reason = None, None

            if is_long:
                if price_low <= p["sl"]:
                    exit_price, reason = p["sl"], "SL"
                elif price_high >= p["tp3"]:
                    exit_price, reason = p["tp3"], "TP3"
                elif price_high >= p["tp2"]:
                    exit_price, reason = p["tp2"], "TP2"
                elif price_high >= p["tp1"]:
                    exit_price, reason = p["tp1"], "TP1"
            else:
                if price_high >= p["sl"]:
                    exit_price, reason = p["sl"], "SL"
                elif price_low <= p["tp3"]:
                    exit_price, reason = p["tp3"], "TP3"
                elif price_low <= p["tp2"]:
                    exit_price, reason = p["tp2"], "TP2"
                elif price_low <= p["tp1"]:
                    exit_price, reason = p["tp1"], "TP1"

            if exit_price:
                # P&L بـ R-multiples (مبسط: نفترض إغلاق كامل عند أول مستوى)
                sl_dist = abs(p["entry"] - p["sl"])
                if is_long:
                    r = (exit_price - p["entry"]) / sl_dist
                else:
                    r = (p["entry"] - exit_price) / sl_dist
                trades.append({"symbol": symbol, "r": round(r, 2), "reason": reason})
                in_position = None
            continue

        # فحص الدخول
        if pd.isna(row["adx"]) or pd.isna(row["atr"]):
            continue
        strength, direction, details, _ = score_row(row, df)
        if direction == "neutral" or strength < config.MIN_SCORE:
            continue

        entry = float(row["close"])
        atr_v = float(row["atr"])
        sl_d = max(atr_v * config.ATR_SL_MULT, entry * config.SL_MIN_PCT)
        if direction == "long":
            sl = entry - sl_d
            tps = [entry + sl_d * m for m in config.TP_MULTIPLES]
        else:
            sl = entry + sl_d
            tps = [entry - sl_d * m for m in config.TP_MULTIPLES]
        in_position = {
            "direction": direction, "entry": entry, "sl": sl,
            "tp1": tps[0], "tp2": tps[1], "tp3": tps[2],
        }

    return trades


def run_backtest(symbols=None, days=180):
    symbols = symbols or config.WATCHLIST[:10]  # أول 10 للسرعة
    all_trades = []
    for sym in symbols:
        print(f"[BACKTEST] {sym}...", flush=True)
        t = backtest_symbol(sym, days)
        if t:
            all_trades.extend(t)
            print(f"  → {len(t)} trades", flush=True)
        time.sleep(0.3)

    if not all_trades:
        print("[BACKTEST] No trades found!", flush=True)
        return

    df = pd.DataFrame(all_trades)
    wins = len(df[df["r"] > 0])
    total = len(df)
    win_rate = wins / total * 100
    avg_r = df["r"].mean()
    total_r = df["r"].sum()

    # Max drawdown (بـ R)
    cum = df["r"].cumsum()
    peak = cum.cummax()
    dd = (cum - peak).min()

    print("\n" + "=" * 50)
    print("BACKTEST RESULTS")
    print("=" * 50)
    print(f"Symbols tested : {len(symbols)}")
    print(f"Total trades   : {total}")
    print(f"Wins / Losses  : {wins} / {total - wins}")
    print(f"Win rate       : {win_rate:.1f}%")
    print(f"Avg R per trade: {avg_r:.2f}R")
    print(f"Total R        : {total_r:.2f}R")
    print(f"Max drawdown   : {dd:.2f}R")
    print(f"Expectancy     : {'POSITIVE ✅' if avg_r > 0 else 'NEGATIVE ❌'}")
    print("=" * 50)

    # توزيع أسباب الخروج
    print("\nExit reasons:")
    print(df["reason"].value_counts().to_string())

    return {
        "total": total, "wins": wins, "win_rate": win_rate,
        "avg_r": avg_r, "total_r": total_r, "max_dd": dd,
    }


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    run_backtest(config.WATCHLIST[:n], days)
