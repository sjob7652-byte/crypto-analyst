# -*- coding: utf-8 -*-
"""عامل الأبحاث المستمر: مختبر باكتست يعمل 24/7 على أنوية هادئة.

ماذا يفعل (ولماذا هو مفيد، لا حرق عبثي):
- طابور وظائف لا ينتهي يُعالَج بمجموعة 3 عمال (multiprocessing)، كل عامل
  يعمل بأولوية منخفضة os.nice(19) — فاحص الدقيقة والمراقب يفوزان دائماً
  بالمعالج عند الحاجة.
- أنواع الوظائف (بحثية فقط — النتائج لا تُغذّي بارامترات التداول الحية
  تلقائياً أبداً، تُعرض في الداشبورد للمراجعة البشرية):
  أ) مسح شبكي للباكتست: TP1 × TP2 × وقف الخسارة × عتبة النقاط على
     120 يوماً من البيانات الساعية لكل عملة (محرك backtest.py، مع
     توثيق أن الدخول وكيل موثق).
  ب) مونت كارلو: 10k مسار رأسمال محاكى من توزيع عوائد الصفقات المغلقة
     → إحصاءات مخاطر (الوسيط، الشريحة 5%، أقصى تراجع).
  ج) بحث عشوائي لفرط-بارامترات LogisticRegression (C, class_weight)
     على الصفقات المغلقة عند توفر ≥20 صفقة.
- النتائج → جدول research2_results في market.duckdb + ملف حالة
  ~/bot/research2_status.json للداشبورد.
- عند فراغ الطابور يُعاد توليده بأحدث البيانات ويدور للأبد.
- كل وظيفة مغلفة بـtry/except — وظيفة سيئة لا تقتل العامل أبداً.

لا شبكة عند الاستيراد. آمن للاستيراد من main.py والاختبارات.
"""
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# شبكة المسح (بحثية — ليست بارامترات حية)
GRID_TP1 = (0.5, 1.0, 1.5)
GRID_TP2 = (2.0, 3.0, 4.0)
GRID_SL = (0.4, 0.6, 0.8)
GRID_SCORE = (60, 70, 80)
DAYS = 120
MIN_POINTS = 24 * 10
N_WORKERS = 3
INVEST_USD = 5.0
SLIPPAGE = 0.03
MAX_HOLD_H = 336
DEATH_LIQ = 1000


def _home():
    return os.environ.get("HOME") or os.path.expanduser("~")


def default_status_path():
    env = os.environ.get("RESEARCH2_STATUS_PATH")
    if env:
        return env
    return os.path.join(_home(), "bot", "research2_status.json")


def write_status(path, data):
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[research2] write_status failed: {e}")
        return False


def read_status(path=None):
    try:
        with open(path or default_status_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------- بيانات العمال (تُحمَّل مرة واحدة لكل عامل) ----------
_W = {"hist": {}, "trades": []}


def _load_closed_trades():
    """صفقات الأرشيف من state — قراءة فقط، بلا قفل كتابة."""
    try:
        from statelock import state_locked
        import state as st
        with state_locked():
            s = st.load()
        tr = s.get("closed_trades") or []
        return [t for t in tr if not t.get("partial")]
    except Exception as e:
        print(f"[research2] closed trades skipped: {e}")
        return []


def _worker_init(db_path, days):
    """تهيئة العامل: أولوية منخفضة + تحميل البيانات مرة واحدة."""
    try:
        os.nice(19)
    except Exception:
        pass
    try:
        from store import MarketStore
        ms = MarketStore(db_path)
        try:
            since = time.time() - days * 86400
            hist = {}
            for c in ms.coins():
                h = ms.history(c["chain"], c["pair"], since_ts=since,
                               limit=100000)
                if len(h) >= MIN_POINTS:
                    hist[f"{c['chain']}:{c['pair']}"] = h
            _W["hist"] = hist
        finally:
            ms.close()
    except Exception as e:
        print(f"[research2] worker data load failed: {e}")
        _W["hist"] = {}
    _W["trades"] = _load_closed_trades()
    print(f"[research2] worker ready: {len(_W['hist'])} coins, "
          f"{len(_W['trades'])} closed trades")


# ---------- محرك الباكتست المُعامَل (نسخة بحثية من backtest.py) ----------
def _proxy_entries_param(hist, vol_mult_min, mom_min):
    """إشارة الدخول الوكيلة — عتبات مُعامَلة لتمثيل عتبة النقاط الممسوحة.
    score 60 → (1.5, 3%) | 70 → (2.0, 5%) | 80 → (3.0, 8%).
    موثق: يقيس كفاءة الخروج لا ألفا الدخول المثبتة."""
    sigs = []
    closes = [h["price"] for h in hist]
    vols = [h.get("vol24") or 0 for h in hist]
    n = len(hist)
    for i in range(24 * 8, n):
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
        if (vol_mult >= vol_mult_min and mom > mom_min
                and (liq == 0 or liq >= 10_000)):
            sigs.append(i)
    return sigs


def _replay_one_param(hist, ei, tp1, tp2, sl):
    entry_raw = hist[ei]["price"]
    if not entry_raw or entry_raw <= 0:
        return None
    entry = entry_raw * (1 + SLIPPAGE)
    t0 = hist[ei]["ts"]
    qty = INVEST_USD / entry
    be = False
    stop = entry * (1 - sl)
    realized = 0.0
    for h in hist[ei + 1:]:
        if h["ts"] - t0 > MAX_HOLD_H * 3600:
            px = h["price"] * (1 - SLIPPAGE)
            return qty * px - INVEST_USD + realized
        px = h["price"]
        if not px or px <= 0:
            continue
        liq = h.get("liq") or 0
        if 0 < liq < DEATH_LIQ:
            return qty * px * (1 - SLIPPAGE) - INVEST_USD + realized
        eff = px * (1 - SLIPPAGE)
        if not be and px >= entry * (1 + tp1):
            sell_q = qty * 0.5
            realized += sell_q * eff - sell_q * entry
            qty -= sell_q
            be = True
            stop = entry
        if px >= entry * (1 + tp2):
            return qty * eff - qty * entry + realized
        if px <= stop:
            return qty * eff - qty * entry + realized
    last = hist[-1]["price"] * (1 - SLIPPAGE)
    return qty * last - qty * entry + realized


_SCORE_MAP = {60: (1.5, 0.03), 70: (2.0, 0.05), 80: (3.0, 0.08)}


def grid_job(job):
    """وظيفة مسح واحدة: (عملة، tp1، tp2، sl، score) → صف نتيجة.
    خالصة وقابلة للاختبار على بيانات صناعية."""
    hist = _W["hist"].get(job["coin"], [])
    tp1, tp2, sl = job["tp1"], job["tp2"], job["sl"]
    vm, mm = _SCORE_MAP.get(job["score"], _SCORE_MAP[70])
    pnls = []
    for ei in _proxy_entries_param(hist, vm, mm):
        try:
            p = _replay_one_param(hist, ei, tp1, tp2, sl)
            if p is not None:
                pnls.append(p)
        except Exception:
            continue
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    mean = sum(pnls) / n if n else 0.0
    var = sum((p - mean) ** 2 for p in pnls) / n if n else 0.0
    std = var ** 0.5
    return {
        "kind": "grid",
        "coin": job["coin"],
        "params": {"tp1": tp1, "tp2": tp2, "sl": sl,
                   "score": job["score"]},
        "trades": n,
        "winrate_pct": round(wins / n * 100, 1) if n else 0.0,
        "expectancy_usd": round(mean, 3),
        "total_pnl_usd": round(sum(pnls), 2),
        "metric": round(mean / std, 3) if std > 0 else 0.0,
    }


def montecarlo_job(job):
    """10k مسار رأسمال من توزيع عوائد الصفقات المغلقة — إحصاءات مخاطر."""
    import numpy as np
    trades = _W["trades"]
    pnls = [t.get("pnl") for t in trades if t.get("pnl") is not None]
    if len(pnls) < 5:
        return {"kind": "montecarlo", "error": "need>=5 trades",
                "metric": 0.0}
    paths = int(job.get("paths", 10000))
    steps = int(job.get("steps", 100))
    rng = np.random.default_rng(42)  # بذرة ثابتة — قابلية إعادة الإنتاج
    draws = rng.choice(pnls, size=(paths, steps))
    equity = 100.0 + np.cumsum(draws, axis=1)
    final = equity[:, -1]
    # أقصى تراجع لكل مسار
    peak = np.maximum.accumulate(equity, axis=1)
    mdd = np.max(peak - equity, axis=1)
    return {
        "kind": "montecarlo",
        "params": {"paths": paths, "steps": steps,
                   "n_trades_src": len(pnls)},
        "trades": len(pnls),
        "median_final": round(float(np.median(final)), 2),
        "p5_final": round(float(np.percentile(final, 5)), 2),
        "p95_final": round(float(np.percentile(final, 95)), 2),
        "median_maxdd": round(float(np.median(mdd)), 2),
        "metric": round(float(np.median(final)) - 100.0, 2),
    }


def mlsearch_job(job):
    """بحث عشوائي لفرط-بارامترات LogisticRegression على الصفقات المغلقة."""
    import numpy as np
    trades = _W["trades"]
    rows = [t for t in trades if t.get("pnl") is not None]
    if len(rows) < 20:
        return {"kind": "mlsearch", "error": "need>=20 trades",
                "metric": 0.0}
    try:
        import calibrate
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_score
    except Exception as e:
        return {"kind": "mlsearch", "error": f"import: {e}",
                "metric": 0.0}
    X = np.array([calibrate._feat_vec(t) for t in rows])
    y = np.array([1 if (t.get("pnl") or 0) > 0 else 0 for t in rows])
    if y.sum() == 0 or y.sum() == len(y):
        return {"kind": "mlsearch", "error": "single-class",
                "metric": 0.0}
    rng = np.random.default_rng(7)
    trials = int(job.get("trials", 30))
    best = {"metric": -1.0}
    for _ in range(trials):
        C = float(10 ** rng.uniform(-3, 2))
        cw = None if rng.random() < 0.5 else "balanced"
        try:
            m = LogisticRegression(C=C, class_weight=cw, max_iter=500)
            scores = cross_val_score(m, X, y, cv=3,
                                     scoring="roc_auc")
            auc = float(np.mean(scores))
        except Exception:
            continue
        if auc > best["metric"]:
            best = {"metric": round(auc, 4), "C": round(C, 4),
                    "class_weight": cw}
    if best["metric"] < 0:
        return {"kind": "mlsearch", "error": "all-trials-failed",
                "metric": 0.0}
    return {"kind": "mlsearch", "params": best, "trades": len(rows),
            "metric": best["metric"]}


def run_job(job):
    """موزع الوظائف — كل وظيفة مغلفة، السيئة لا تقتل العامل."""
    try:
        kind = job.get("kind")
        if kind == "grid":
            return grid_job(job)
        if kind == "montecarlo":
            return montecarlo_job(job)
        if kind == "mlsearch":
            return mlsearch_job(job)
        return {"kind": kind, "error": "unknown-kind", "metric": 0.0}
    except Exception as e:
        return {"kind": job.get("kind"), "error": str(e)[:200],
                "metric": 0.0}


# ---------- المنسق ----------
def build_queue():
    """يولد طابور وظائف كامل من أحدث البيانات."""
    jobs = []
    coins = sorted(_W["hist"].keys())
    for cid in coins:
        for tp1 in GRID_TP1:
            for tp2 in GRID_TP2:
                for sl in GRID_SL:
                    for score in GRID_SCORE:
                        jobs.append({"kind": "grid", "coin": cid,
                                     "tp1": tp1, "tp2": tp2,
                                     "sl": sl, "score": score})
    jobs.append({"kind": "montecarlo", "paths": 10000, "steps": 100})
    jobs.append({"kind": "mlsearch", "trials": 30})
    return jobs


def _results_table(db_path):
    try:
        import duckdb
        con = duckdb.connect(db_path)
        con.execute("""
        CREATE TABLE IF NOT EXISTS research2_results (
            ts DOUBLE, kind TEXT, coin TEXT, params TEXT, trades INTEGER,
            winrate_pct DOUBLE, expectancy_usd DOUBLE, total_pnl_usd DOUBLE,
            metric DOUBLE, detail TEXT
        )""")
        return con
    except Exception as e:
        print(f"[research2] results table failed: {e}")
        return None


def _record(con, res):
    if con is None:
        return
    try:
        con.execute(
            "INSERT INTO research2_results VALUES (?,?,?,?,?,?,?,?,?,?)",
            [time.time(), res.get("kind"), res.get("coin"),
             json.dumps(res.get("params", {}), ensure_ascii=False),
             res.get("trades", 0), res.get("winrate_pct"),
             res.get("expectancy_usd"), res.get("total_pnl_usd"),
             res.get("metric", 0.0),
             json.dumps({k: v for k, v in res.items()
                         if k not in ("kind", "coin", "params", "trades",
                                      "winrate_pct", "expectancy_usd",
                                      "total_pnl_usd", "metric")},
                        ensure_ascii=False)[:2000]])
    except Exception as e:
        print(f"[research2] record failed: {e}")


def _load_hist_main(db_path, days):
    """نسخة المنسق من تحميل البيانات (لبناء الطابور ومعرفة العملات)."""
    try:
        from store import MarketStore
        ms = MarketStore(db_path)
        try:
            since = time.time() - days * 86400
            for c in ms.coins():
                h = ms.history(c["chain"], c["pair"], since_ts=since,
                               limit=100000)
                if len(h) >= MIN_POINTS:
                    _W["hist"][f"{c['chain']}:{c['pair']}"] = h
        finally:
            ms.close()
    except Exception as e:
        print(f"[research2] main data load failed: {e}")


def run(db_path=None, status_path=None):
    """الحلقة الأبدية: طابور → عمال → نتائج → طابور جديد."""
    from store import default_db_path
    db_path = db_path or os.environ.get("MARKET_DB_PATH") or default_db_path()
    status_path = status_path or default_status_path()
    print(f"[research2] starting: {N_WORKERS} workers (nice 19), db={db_path}")
    cycle = 0
    while True:
        cycle += 1
        try:
            _W["hist"] = {}
            _load_hist_main(db_path, DAYS)
            jobs = build_queue()
            total = len(jobs)
            print(f"[research2] cycle {cycle}: {total} jobs, "
                  f"{len(_W['hist'])} coins")
            if not jobs:
                print("[research2] no jobs — sleeping 10 min")
                time.sleep(600)
                continue
            from concurrent.futures import ProcessPoolExecutor
            con = _results_table(db_path)
            done = 0
            best = None
            t0 = time.time()
            try:
                with ProcessPoolExecutor(
                        max_workers=N_WORKERS,
                        initializer=_worker_init,
                        initargs=(db_path, DAYS)) as ex:
                    for res in ex.map(run_job, jobs, chunksize=1):
                        done += 1
                        _record(con, res)
                        if (res.get("kind") == "grid" and not res.get("error")
                                and (best is None
                                     or res["metric"] > best["metric"])):
                            best = res
                        if done % 25 == 0 or done == total:
                            write_status(status_path, {
                                "jobs_done": done, "jobs_total": total,
                                "cycle": cycle,
                                "best_params": (best or {}).get("params"),
                                "best_metric": (best or {}).get("metric"),
                                "best_coin": (best or {}).get("coin"),
                                "elapsed_s": round(time.time() - t0, 1),
                                "last_job_ts": time.time(),
                            })
                            print(f"[research2] {done}/{total} "
                                  f"({time.time() - t0:.0f}s)")
            finally:
                try:
                    if con is not None:
                        con.close()
                except Exception:
                    pass
            write_status(status_path, {
                "jobs_done": done, "jobs_total": total, "cycle": cycle,
                "best_params": (best or {}).get("params"),
                "best_metric": (best or {}).get("metric"),
                "best_coin": (best or {}).get("coin"),
                "elapsed_s": round(time.time() - t0, 1),
                "last_job_ts": time.time(),
            })
            print(f"[research2] cycle {cycle} done in "
                  f"{time.time() - t0:.0f}s — regenerating queue")
        except KeyboardInterrupt:
            print("[research2] stopped by user")
            break
        except Exception:
            print("[research2] cycle failed (will retry in 60s):")
            traceback.print_exc()
            time.sleep(60)


def main():
    run()


if __name__ == "__main__":
    main()
