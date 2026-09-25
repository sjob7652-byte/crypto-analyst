# -*- coding: utf-8 -*-
"""الباكتست: إعادة تشغيل قواعد المستثمر الحية حرفياً على بيانات المخزن.

قواعد الخروج مطابقة للكود الحي (config.py + main.py):
- هدف1 +100%: بيع 50%، وقف الباقي → الدخول (تعادل)
- هدف2 +300%: خروج كامل للباقي
- وقف الخسارة: -60% (أو الدخول بعد الجني)
- حد زمني: 336 ساعة (14 يوماً) → إغلاق بسعر السوق
- إشارات الموت: سيولة <$1K (تقريب للبيانات الساعية — يُوثَّق)

قواعد الدخول: النقاط/الاحتمال التاريخية غير مخزنة، لذا تُستعمل إشارة
وكيلة موثقة: زخم سعري + انفجار حجم (تقريب محافظ لـ score≥70/prob≥60).
الباكتست يقيس أساساً كفاءة *الخروج* وإدارة الصفقات.

متعدد العمليات (ProcessPoolExecutor) عبر العملات — نقي بلا حالة مشتركة.
fail-safe: أي عملة فاشلة تُتخطى، والنتيجة الكلية تُكتب دائماً.
"""
import argparse
import concurrent.futures
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# مرآة config.py — أي تغيير هنا يجب أن يطابق الكود الحي حرفياً
TP1, TP2 = 1.00, 3.00
FRAC1 = 0.5
STOP_LOSS = 0.60
MAX_HOLD_H = 336
DEATH_LIQ = 1000
INVEST_USD = 5.0
SLIPPAGE = 0.03


def _proxy_entries(hist):
    """إشارات دخول وكيلة من الشموع الساعية: انفجار حجم + زخم.
    hist: قائمة dicts مرتبة زمنياً (ts, price, vol24)."""
    sigs = []
    closes = [h["price"] for h in hist]
    vols = [h.get("vol24") or 0 for h in hist]
    n = len(hist)
    for i in range(24 * 8, n):  # نحتاج 8 أيام تاريخ
        # متوسط حجم الساعة في آخر 24س مقابل وسيط الأيام الـ7 السابقة
        recent = vols[i - 24:i]
        base = sorted(vols[i - 24 * 8:i - 24])
        if not base or not recent:
            continue
        med = base[len(base) // 2]
        if med <= 0:
            continue
        vol_mult = (sum(recent) / 24) / med
        mom = closes[i] / closes[i - 24] - 1 if closes[i - 24] else 0
        liq = hist[i].get("liq") or 0
        if vol_mult >= 2.0 and mom > 0.05 and (liq == 0 or liq >= 10_000):
            sigs.append(i)
    return sigs


def replay_coin(job):
    """يعيد تشغيل عملة واحدة — دالة نقية (آمنة للـmultiprocessing).
    job = (coin_id, hist). يعيد قاموس النتائج."""
    coin_id, hist = job
    try:
        trades = []
        for ei in _proxy_entries(hist):
            t = _replay_one(hist, ei)
            if t:
                trades.append(t)
        return {"coin": coin_id, "trades": trades, "error": None}
    except Exception as e:
        return {"coin": coin_id, "trades": [], "error": str(e)[:200]}


def _replay_one(hist, ei):
    entry_raw = hist[ei]["price"]
    if not entry_raw or entry_raw <= 0:
        return None
    entry = entry_raw * (1 + SLIPPAGE)
    t0 = hist[ei]["ts"]
    qty = INVEST_USD / entry
    be = False
    stop = entry * (1 - STOP_LOSS)
    realized = 0.0
    for h in hist[ei + 1:]:
        if h["ts"] - t0 > MAX_HOLD_H * 3600:
            px = h["price"] * (1 - SLIPPAGE)
            pnl = qty * px - INVEST_USD + realized
            return _rec("EXPIRED", pnl, h["ts"] - t0)
        px = h["price"]
        if not px or px <= 0:
            continue
        liq = h.get("liq") or 0
        if 0 < liq < DEATH_LIQ:
            ex = px * (1 - SLIPPAGE)
            pnl = qty * ex - INVEST_USD + realized
            return _rec("DEAD", pnl, h["ts"] - t0)
        eff = px * (1 - SLIPPAGE)
        if not be and px >= entry * (1 + TP1):
            sell_q = qty * FRAC1
            realized += sell_q * eff - sell_q * entry
            qty -= sell_q
            be = True
            stop = entry
        if px >= entry * (1 + TP2):
            pnl = qty * eff - qty * entry + realized
            return _rec("TP2", pnl, h["ts"] - t0)
        if px <= stop:
            pnl = qty * eff - qty * entry + realized
            return _rec("BE" if be else "SL", pnl, h["ts"] - t0)
    # انتهت البيانات والصفقة مفتوحة → إغلاق بسعر الأخير (محافظ)
    last = hist[-1]["price"] * (1 - SLIPPAGE)
    pnl = qty * last - qty * entry + realized
    return _rec("OPEN-END", pnl, hist[-1]["ts"] - t0)


def _rec(reason, pnl, hold_h):
    return {"reason": reason, "pnl": round(pnl, 4),
            "hold_h": round(hold_h / 3600, 1)}


def aggregate(results):
    trades = [t for r in results for t in r["trades"]]
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    avg_w = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_l = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    wr = len(wins) / n if n else 0
    expectancy = wr * avg_w - (1 - wr) * abs(avg_l)
    total = sum(t["pnl"] for t in trades)
    # أقصى تراجع من منحنى الربح التراكمي
    eq, peak, mdd = 0.0, 0.0, 0.0
    for t in trades:
        eq += t["pnl"]
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    by_reason = {}
    for t in trades:
        d = by_reason.setdefault(t["reason"], {"n": 0, "pnl": 0.0})
        d["n"] += 1
        d["pnl"] = round(d["pnl"] + t["pnl"], 2)
    return {
        "time": time.time(),
        "coins": len(results),
        "coins_with_trades": sum(1 for r in results if r["trades"]),
        "trades": n,
        "wins": len(wins),
        "winrate_pct": round(wr * 100, 1),
        "avg_win_usd": round(avg_w, 2),
        "avg_loss_usd": round(avg_l, 2),
        "expectancy_usd": round(expectancy, 3),
        "total_pnl_usd": round(total, 2),
        "max_drawdown_usd": round(mdd, 2),
        "by_reason": by_reason,
        "entry_proxy": "vol_mult>=2 & 24h_momentum>5% (documented proxy)",
        "rules": "TP1 +100% x50% / TP2 +300% x100% / SL -60% / 336h / DEAD liq<1K",
    }


def run(db_path=None, days=90, workers=4, min_points=24 * 10):
    from store import MarketStore
    ms = MarketStore(db_path)
    try:
        since = time.time() - days * 86400
        jobs = []
        for c in ms.coins():
            h = ms.history(c["chain"], c["pair"], since_ts=since,
                           limit=100000)
            if len(h) >= min_points:
                cid = f"{c['chain']}:{c['pair']}"
                jobs.append((cid, h))
        print(f"[backtest] {len(jobs)} عملة × ≥{min_points} نقطة")
        results = []
        if jobs:
            with concurrent.futures.ProcessPoolExecutor(
                    max_workers=workers) as ex:
                for r in ex.map(replay_coin, jobs, chunksize=1):
                    results.append(r)
                    if r["error"]:
                        print(f"[backtest] {r['coin']} error: {r['error']}")
        return aggregate(results)
    finally:
        ms.close()


def write_state(summary):
    """يكتب النتيجة في state['research']['backtest'] — بنفس قفل الحالة."""
    try:
        from statelock import state_locked
        import state as st
        with state_locked():
            s = st.load()
            r = s.setdefault("research", {})
            r["backtest"] = summary
            st.save(s)
        print("[backtest] state['research']['backtest'] updated")
        return True
    except Exception as e:
        print(f"[backtest] write_state failed: {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--write-state", action="store_true")
    a = ap.parse_args()
    summary = run(db_path=a.db, days=a.days, workers=a.workers)
    print("[backtest] trades=%(trades)d winrate=%(winrate_pct)s%% "
          "EV=$%(expectancy_usd)s total=$%(total_pnl_usd)s" % summary)
    if a.write_state:
        write_state(summary)
    return summary


if __name__ == "__main__":
    main()
