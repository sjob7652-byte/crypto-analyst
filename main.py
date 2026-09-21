#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""المشغّل الرئيسي: فحص العملات الجديدة → تحليل → تنبيه → متابعة الصفقات."""
import argparse
import time
from datetime import datetime, timezone

import clients
import analyzer
import alerts
import expert
import state as st
from config import (
    CHAINS, SCAN_LIMIT, MIN_LIQUIDITY_USD, MIN_VOLUME_24H_USD,
    MAX_PAIR_AGE_DAYS, WATCHLIST, TAKE_PROFITS, STOP_LOSS,
    POSITION_MAX_AGE_DAYS, DIGEST_HOURS_UTC, USE_COINGECKO, USE_NEWS,
)


def build_context(s):
    """سياق الخبير: الأخبار + العملات الرائجة + نبض السوق + ذاكرة النتائج."""
    ctx = {"news": [], "trending": [], "macro": None, "nc": None}
    if USE_NEWS:
        try:
            nc = clients.NewsClient()
            ctx["news"] = nc.fetch()
            ctx["nc"] = nc
            print(f"أخبار: {len(ctx['news'])} عنواناً من {len(nc.feeds)} مصادر")
        except Exception as e:
            print("news error:", e)
    if USE_COINGECKO:
        try:
            ctx["trending"] = clients.coingecko_trending()
        except Exception:
            pass
        try:
            ctx["macro"] = clients.coingecko_macro()
            if ctx["macro"]:
                print(f"BTC: {ctx['macro']['btc_chg']:+.1f}% (24س)")
        except Exception:
            pass
    ctx["band_stats"] = st.band_stats(s)
    return ctx


def coin_symbol(res):
    """يستخرج رمز العملة من نتيجة التحليل."""
    if res.get("symbol"):
        return res["symbol"].replace("USDT", "")
    return (res.get("display") or "").split("/")[0]


def make_verdict(res, ctx):
    nc = ctx.get("nc")
    sym = coin_symbol(res)
    coin_news = nc.for_coin(sym) if nc else []
    macro = ctx.get("macro") or {}
    return expert.decide(res, coin_news, macro.get("btc_chg"),
                         ctx.get("band_stats"))


def pick_best_pair(pairs):
    """لكل عملة: اختر زوج التداول الأعلى سيولة."""
    best = {}
    for p in pairs:
        try:
            base = p["baseToken"]["address"].lower()
        except Exception:
            continue
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        key = (p.get("chainId"), base)
        if key not in best or liq > best[key][1]:
            best[key] = (p, liq)
    return [p for p, _ in best.values()]


def scan_new_coins(s, dry_run, ctx):
    print("=== فحص العملات الجديدة ===")
    tokens = clients.latest_profiles() + clients.latest_boosts()
    seen, uniq = set(), []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    print(f"عناوين مرشحة: {len(uniq)}")

    pairs = clients.pairs_for_tokens(uniq[:90])
    now_ms = time.time() * 1000
    cands = []
    for p in pick_best_pair(pairs):
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        vol = float((p.get("volume") or {}).get("h24") or 0)
        created = p.get("pairCreatedAt") or 0
        age_d = (now_ms - created) / 86400000 if created else 99999
        if liq < MIN_LIQUIDITY_USD or vol < MIN_VOLUME_24H_USD:
            continue
        if age_d > MAX_PAIR_AGE_DAYS:
            continue
        cands.append(p)
    cands.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
               reverse=True)
    cands = cands[:SCAN_LIMIT]
    print(f"مرشحون بعد التصفية: {len(cands)}")

    results = []
    for p in cands:
        chain = p.get("chainId")
        addr = (p.get("baseToken") or {}).get("address")
        sec = clients.token_security(chain, addr) if addr else None
        res = analyzer.analyze_pair(p, sec)
        res["id"] = f"dex:{chain}:{p.get('pairAddress')}"
        res["kind"] = "dex"
        res["chain"] = chain
        res["pair"] = p.get("pairAddress")
        results.append(res)

    results.sort(key=lambda r: r["score"], reverse=True)
    sent = 0
    for res in results:
        key = f"sig:{res['id']}"
        if time.time() - s["alerted"].get(key, 0) < 24 * 3600:
            continue
        if res["signal"] in ("BUY", "STRONG_BUY") and sent < 5:
            verdict = make_verdict(res, ctx)
            print(f"  -> إشارة {res['signal']}: {res['display']} "
                  f"({res['score']}) نجاح~{verdict['prob']}%")
            alerts.send(alerts.new_signal_msg(res, verdict), dry_run)
            s["alerted"][key] = time.time()
            open_position(s, res, verdict)
            sent += 1
            s["stats"]["signals_today"] = s["stats"].get("signals_today", 0) + 1
        elif res["signal"] == "AVOID" and any("⛔" in w for w in res["warnings"]) \
                and sent < 6:
            print(f"  -> تحذير نصب: {res['display']}")
            alerts.send(alerts.avoid_msg(res), dry_run)
            s["alerted"][key] = time.time()
            sent += 1
    return results


def open_position(s, res, verdict=None):
    m = res["metrics"]
    if not m.get("price"):
        return
    s["positions"][res["id"]] = {
        "kind": res["kind"],
        "name": res["display"],
        "chain": res.get("chain"),
        "pair": res.get("pair"),
        "symbol": res.get("symbol"),
        "entry": m["price"],
        "entry_time": time.time(),
        "tp_hit": [False] * len(TAKE_PROFITS),
        "ref_liq": m.get("liq") or 0,
        "warned": False,
        "score": res.get("score"),
        "band": (verdict or {}).get("band") or expert.band_of(res.get("score")),
        "best_hit": None,
    }


def current_price(pos):
    try:
        if pos["kind"] == "dex":
            p = clients.get_pair(pos["chain"], pos["pair"])
            if not p:
                return None, None
            return (float(p.get("priceUsd") or 0),
                    float((p.get("liquidity") or {}).get("usd") or 0))
        t = clients.binance_ticker(pos["symbol"])
        if not t:
            return None, None
        return float(t.get("lastPrice") or 0), 0
    except Exception:
        return None, None


def update_positions(s, dry_run):
    print("=== متابعة الصفقات المفتوحة ===")
    closed = []
    for pid, pos in list(s["positions"].items()):
        price, liq = current_price(pos)
        if not price:
            continue
        entry = pos["entry"]
        # أهداف البيع
        for i, tp in enumerate(TAKE_PROFITS):
            if not pos["tp_hit"][i] and price >= entry * (1 + tp):
                pos["tp_hit"][i] = True
                pos["best_hit"] = f"tp{i + 1}"
                if all(pos["tp_hit"]):
                    print(f"  -> اكتملت الأهداف: {pos['name']}")
                    alerts.send(alerts.all_tp_msg(pos["name"], entry, price), dry_run)
                    st.record_outcome(s, pos, "tp3")
                    closed.append(pid)
                else:
                    print(f"  -> تحقق الهدف {i + 1}: {pos['name']}")
                    alerts.send(alerts.tp_hit_msg(pos["name"], entry, price, i), dry_run)
                break
        if pid in closed:
            continue
        # وقف الخسارة
        if price <= entry * (1 - STOP_LOSS):
            print(f"  -> وقف الخسارة: {pos['name']}")
            alerts.send(alerts.stop_loss_msg(pos["name"], entry, price), dry_run)
            st.record_outcome(s, pos, "sl")
            closed.append(pid)
            continue
        # تحذير انهيار السيولة
        if pos.get("ref_liq") and liq and liq < pos["ref_liq"] * 0.3 \
                and not pos.get("warned"):
            pos["warned"] = True
            print(f"  -> تحذير سيولة: {pos['name']}")
            alerts.send(alerts.rug_warn_msg(pos["name"], price), dry_run)
        # انتهاء مدة المتابعة
        if time.time() - pos["entry_time"] > POSITION_MAX_AGE_DAYS * 86400:
            st.record_outcome(s, pos, pos.get("best_hit") or "expired")
            closed.append(pid)
    for pid in closed:
        s["positions"].pop(pid, None)
    print(f"صفقات مفتوحة: {len(s['positions'])}")


def scan_watchlist(s, dry_run, ctx):
    print("=== فحص عملات Binance ===")
    movers = []
    for sym in WATCHLIST:
        t = clients.binance_ticker(sym)
        if not t:
            continue
        try:
            chg = float(t.get("priceChangePercent") or 0)
        except (TypeError, ValueError):
            continue
        movers.append((sym.replace("USDT", ""), chg))
        k = clients.binance_klines(sym)
        res = analyzer.analyze_binance(sym, t, k)
        key = f"watch:{sym}"
        if res["signal"] in ("BUY", "STRONG_BUY") \
                and time.time() - s["alerted"].get(key, 0) > 12 * 3600:
            res["id"] = f"binance:{sym}"
            res["kind"] = "binance"
            res["symbol"] = sym
            verdict = make_verdict(res, ctx)
            print(f"  -> إشارة {res['signal']}: {res['display']} "
                  f"({res['score']}) نجاح~{verdict['prob']}%")
            alerts.send(alerts.new_signal_msg(res, verdict), dry_run)
            s["alerted"][key] = time.time()
            open_position(s, res, verdict)
            s["stats"]["signals_today"] = s["stats"].get("signals_today", 0) + 1
    movers.sort(key=lambda x: abs(x[1]), reverse=True)
    return movers


def maybe_digest(s, dry_run, movers, ctx):
    now = datetime.now(timezone.utc)
    if now.hour not in DIGEST_HOURS_UTC:
        return
    if s["stats"].get("digest_sent") == (now.strftime("%Y-%m-%d"), now.hour):
        return
    s["stats"]["digest_sent"] = (now.strftime("%Y-%m-%d"), now.hour)
    positions = []
    for pid, pos in s["positions"].items():
        price, _ = current_price(pos)
        if price:
            positions.append({"name": pos["name"], "entry": pos["entry"],
                              "price": price})
    nc = ctx.get("nc")
    news_top = sorted(ctx.get("news", []),
                      key=lambda x: abs(x.get("sentiment", 0)),
                      reverse=True)[:6]
    dctx = {
        "macro": ctx.get("macro"),
        "trending": ctx.get("trending"),
        "news_top": news_top,
        "track": st.track_summary(s),
    }
    date_str = now.strftime("%Y-%m-%d")
    print("=== إرسال الملخص اليومي ===")
    alerts.send(alerts.digest_msg(date_str, positions,
                                  s["stats"].get("signals_today", 0),
                                  movers, dctx), dry_run)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="طباعة الرسائل بدل إرسالها وعدم حفظ الحالة")
    a = ap.parse_args()

    s = st.load()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if s["stats"].get("day") != today:
        s["stats"] = {"day": today, "signals_today": 0}

    ctx = build_context(s)
    scan_new_coins(s, a.dry_run, ctx)
    update_positions(s, a.dry_run)
    movers = scan_watchlist(s, a.dry_run, ctx)
    maybe_digest(s, a.dry_run, movers, ctx)

    if not a.dry_run:
        st.save(s)
    print("تم.")


if __name__ == "__main__":
    main()
