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
    MIN_TXNS_24H, MAX_PAIR_AGE_DAYS, WATCHLIST, TAKE_PROFITS, STOP_LOSS,
    POSITION_MAX_AGE_DAYS, DIGEST_HOURS_UTC, USE_COINGECKO, USE_NEWS,
    USE_FNG, NEW_ALERT_MAX_AGE_H, WAITLIST_MAX_AGE_H, WAITLIST_MAX_SIZE,
    WAITLIST_ADD_PER_RUN, POS_REEVAL_MIN_SCORE, POS_REEVAL_MIN_AGE_H,
    VOL_SPIKE_MULT, VOL_SPIKE_LOOKBACK, VOL_SPIKE_COOLDOWN_H,
    PAPER_ENABLED, PAPER_START_BALANCE, PAPER_RISK_PER_TRADE,
    PAPER_MAX_POSITIONS, PAPER_SELL_FRACTIONS, PAPER_SLIPPAGE,
)


def build_context(s):
    """سياق الخبير: الأخبار الموثوقة + العملات الرائجة + نبض مُتحقق + ذاكرة النتائج."""
    ctx = {"news": [], "trending": [], "macro": None, "nc": None,
           "fng": None, "news_stats": {}}
    if USE_NEWS:
        try:
            nc = clients.NewsClient()
            ctx["news"] = nc.fetch()
            ctx["nc"] = nc
            ctx["news_stats"] = nc.stats
            print(f"أخبار: {len(ctx['news'])} عنواناً من "
                  f"{nc.stats['sources_ok']} مصادر موثوقة "
                  f"(مكرر مُزال: {nc.stats['dupes_merged']})")
        except Exception as e:
            print("news error:", e)
    if USE_COINGECKO:
        try:
            ctx["trending"] = clients.coingecko_trending()
        except Exception:
            pass
        try:
            ctx["macro"] = clients.verified_macro()
            m = ctx["macro"]
            if m and m.get("btc_chg") is not None:
                v = "✓ مُتحقق من مصدرين" if m.get("verified") else "؟ غير مؤكد"
                print(f"BTC: {m['btc_chg']:+.1f}% (24س) [{v}]")
        except Exception:
            pass
    if USE_FNG:
        try:
            ctx["fng"] = clients.fear_greed()
            if ctx["fng"]:
                print(f"الخوف والطمع: {ctx['fng']['value']}/100 "
                      f"({ctx['fng']['label']})")
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
    trending = [t.get("symbol") for t in (ctx.get("trending") or [])
                if t.get("symbol")]
    return expert.decide(res, coin_news, macro.get("btc_chg"),
                         ctx.get("band_stats"),
                         fng=ctx.get("fng"),
                         macro_verified=macro.get("verified", False),
                         trending=trending)


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
    profiles = clients.latest_profiles()
    boosts = clients.latest_boosts()
    boosted_set = {(c, a.lower()) for c, a in boosts}
    tokens = profiles + boosts
    seen, uniq = set(), []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    print(f"عناوين مرشحة: {len(uniq)} (منها {len(boosted_set)} بترويج مدفوع)")

    pairs = clients.pairs_for_tokens(uniq[:90])
    now_ms = time.time() * 1000
    cands = []
    for p in pick_best_pair(pairs):
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        vol = float((p.get("volume") or {}).get("h24") or 0)
        tx = (p.get("txns") or {}).get("h24") or {}
        ntx = float(tx.get("buys") or 0) + float(tx.get("sells") or 0)
        created = p.get("pairCreatedAt") or 0
        age_d = (now_ms - created) / 86400000 if created else 0
        if liq < MIN_LIQUIDITY_USD or vol < MIN_VOLUME_24H_USD:
            continue
        if ntx < MIN_TXNS_24H:   # تنقية: عملات بلا نشاط حقيقي = ضجيج
            continue
        if created and age_d * 24 > NEW_ALERT_MAX_AGE_H:
            continue  # الفرص الحقيقية في الساعات الأولى فقط
        # فلتر الرمز المشبوه (حروف خفية / تقليد عملات مشهورة)
        sym = ((p.get("baseToken") or {}).get("symbol") or "")
        ok, why = analyzer.check_symbol(sym)
        if not ok:
            print(f"  x رمز مرفوض {sym[:20]!r}: {why}")
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
        boosted = (chain, (addr or "").lower()) in boosted_set
        sec = clients.token_security(chain, addr) if addr else None
        res = analyzer.analyze_pair(p, sec, boosted=boosted)
        res["id"] = f"dex:{chain}:{p.get('pairAddress')}"
        res["kind"] = "dex"
        res["chain"] = chain
        res["pair"] = p.get("pairAddress")
        results.append(res)

    results.sort(key=lambda r: r["score"], reverse=True)
    sent = 0
    wait_added = 0
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
        elif res["signal"] == "WATCH" and wait_added < WAITLIST_ADD_PER_RUN:
            # لائحة الانتظار: "شبه جاهزة" — تُعاد فحصها كل جولة لمدة 6 ساعات
            wl = s.setdefault("waitlist", {})
            if res["id"] not in wl and len(wl) < WAITLIST_MAX_SIZE:
                wl[res["id"]] = {
                    "chain": res["chain"], "pair": res["pair"],
                    "display": res["display"], "score": res["score"],
                    "added": time.time(), "checks": 0,
                }
                wait_added += 1
                print(f"  -> لائحة الانتظار: {res['display']} ({res['score']})")
    return results


def check_waitlist(s, dry_run, ctx):
    """إعادة فحص عملات لائحة الانتظار — من تحسّن يُرسل كتنبيه."""
    wl = s.setdefault("waitlist", {})
    if not wl:
        return
    print(f"=== إعادة فحص لائحة الانتظار ({len(wl)}) ===")
    now = time.time()
    for wid in list(wl)[:15]:  # حد أقصى 15 إعادة فحص في الجولة
        e = wl[wid]
        if now - e["added"] > WAITLIST_MAX_AGE_H * 3600:
            wl.pop(wid, None)
            continue
        p = clients.get_pair(e["chain"], e["pair"])
        if not p:
            e["checks"] += 1
            if e["checks"] >= 3:
                wl.pop(wid, None)
            continue
        created = p.get("pairCreatedAt") or 0
        if created and (now * 1000 - created) / 3_600_000 > NEW_ALERT_MAX_AGE_H:
            wl.pop(wid, None)  # تجاوزت نافذة الفرص المبكرة
            continue
        sec = clients.token_security(e["chain"],
                                      (p.get("baseToken") or {}).get("address"))
        res = analyzer.analyze_pair(p, sec)
        res["id"] = wid
        res["kind"] = "dex"
        res["chain"] = e["chain"]
        res["pair"] = e["pair"]
        if res["signal"] in ("BUY", "STRONG_BUY"):
            verdict = make_verdict(res, ctx)
            print(f"  -> 🔄 تحسّنت: {res['display']} ({res['score']}) "
                  f"نجاح~{verdict['prob']}%")
            msg = ("🔄 <b>رجعت بقوة!</b> كانت تحت المراقبة والآن تحسّنت "
                   "مؤشراتها.\n\n" + alerts.new_signal_msg(res, verdict))
            alerts.send(msg, dry_run)
            s["alerted"][f"sig:{wid}"] = now
            open_position(s, res, verdict)
            s["stats"]["signals_today"] = s["stats"].get("signals_today", 0) + 1
            wl.pop(wid, None)
        elif res["signal"] == "AVOID":
            wl.pop(wid, None)  # ساءت — أخرجها من اللائحة
        else:
            e["checks"] += 1
            e["score"] = res["score"]


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
    paper_buy(s, res)


def paper_buy(s, res):
    """شراء وهمي: يخصم من الرصيد الافتراضي ويفتح صفقة وهمية (بلا مخاطرة)."""
    if not PAPER_ENABLED:
        return
    p = s["paper"]
    m = res["metrics"]
    price = m.get("price")
    if not price:
        return
    pid = res["id"]
    if pid in p["positions"]:
        return
    if len(p["positions"]) >= PAPER_MAX_POSITIONS:
        return
    amount = min(PAPER_RISK_PER_TRADE, p["cash"])
    if amount < 5:
        return
    p["cash"] -= amount
    # سعر التنفيذ الواقعي: الشراء بسعر أغلى بسبب الانزلاق السعري
    eff_entry = price * (1 + PAPER_SLIPPAGE)
    p["positions"][pid] = {
        "kind": res["kind"],
        "name": res["display"],
        "chain": res.get("chain"),
        "pair": res.get("pair"),
        "symbol": res.get("symbol"),
        "entry": eff_entry,
        "entry_time": time.time(),
        "qty": amount / eff_entry,
        "invested": amount,
        "realized": 0.0,
        "tp_hit": [False] * len(TAKE_PROFITS),
    }
    p["trades"] += 1
    print(f"  -> محفظة وهمية: شراء {res['display']} بـ ${amount:.2f} "
          f"(تنفيذ: {eff_entry:.6g} بعد الانزلاق)")


def update_paper(s, dry_run):
    """يتابع الصفقات الوهمية: بيع جزئي عند الأهداف، بيع كامل عند وقف الخسارة."""
    p = s["paper"]
    if not p["positions"]:
        return
    print("=== المحفظة الافتراضية ===")
    closed = []
    for pid, pos in list(p["positions"].items()):
        price, _liq = current_price(pos)
        if not price:
            continue
        entry = pos["entry"]
        # سعر التنفيذ الواقعي عند البيع (أرخص بسبب الانزلاق) — التفعيل يبقى
        # على سعر السوق الخام، لكن التنفيذ الفعلي ينزلق
        eff_price = price * (1 - PAPER_SLIPPAGE)
        # أهداف البيع (بيع جزئي)
        for i, tp in enumerate(TAKE_PROFITS):
            if not pos["tp_hit"][i] and price >= entry * (1 + tp):
                pos["tp_hit"][i] = True
                sell_qty = pos["qty"] * PAPER_SELL_FRACTIONS[i]
                proceeds = sell_qty * eff_price
                pos["qty"] -= sell_qty
                pos["realized"] += proceeds - sell_qty * entry
                p["cash"] += proceeds
                print(f"  -> وهمي: بيع {pos['name']} عند TP{i + 1} "
                      f"(${proceeds:.2f} بعد الانزلاق)")
                if all(pos["tp_hit"]):
                    pnl = pos["realized"]
                    if pnl >= 0:
                        p["wins"] += 1
                    else:
                        p["losses"] += 1
                    closed.append(pid)
                    alerts.send(alerts.paper_closed_msg(
                        pos["name"], pnl,
                        pnl / pos["invested"] * 100 if pos["invested"] else 0,
                        "اكتملت الأهداف 🎯", p["cash"]), dry_run)
                break
        if pid in closed:
            continue
        # وقف الخسارة: بيع كل الكمية المتبقية
        if price <= entry * (1 - STOP_LOSS):
            proceeds = pos["qty"] * eff_price
            pnl = proceeds - pos["qty"] * entry + pos["realized"]
            p["cash"] += proceeds
            p["losses"] += 1
            closed.append(pid)
            print(f"  -> وهمي: وقف خسارة {pos['name']} (${pnl:+.2f})")
            alerts.send(alerts.paper_closed_msg(
                pos["name"], pnl,
                pnl / pos["invested"] * 100 if pos["invested"] else 0,
                "وقف الخسارة 🛑", p["cash"]), dry_run)
            continue
        # انتهاء مدة المتابعة: بيع بسعر السوق
        if time.time() - pos["entry_time"] > POSITION_MAX_AGE_DAYS * 86400:
            proceeds = pos["qty"] * eff_price
            pnl = proceeds - pos["qty"] * entry + pos["realized"]
            p["cash"] += proceeds
            if pnl >= 0:
                p["wins"] += 1
            else:
                p["losses"] += 1
            closed.append(pid)
    for pid in closed:
        p["positions"].pop(pid, None)


def paper_summary(s):
    """ملخص المحفظة الافتراضية للملخص اليومي."""
    p = s["paper"]
    invested = 0.0
    for pos in p["positions"].values():
        price, _liq = current_price(pos)
        invested += (pos["qty"] * (price or pos["entry"]))
    total = p["cash"] + invested
    pnl = total - p["start"]
    pct = pnl / p["start"] * 100 if p["start"] else 0
    n_closed = p["wins"] + p["losses"]
    winrate = p["wins"] / n_closed * 100 if n_closed else 0
    return {
        "total": total, "cash": p["cash"], "pnl": pnl, "pct": pct,
        "open": len(p["positions"]), "closed": n_closed,
        "wins": p["wins"], "winrate": winrate,
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
        # إعادة تقييم الصفقة: هل المؤشرات ساءت من بعد الدخول؟
        if (pos["kind"] == "dex" and not pos.get("deteriorated_warned")
                and time.time() - pos["entry_time"] > POS_REEVAL_MIN_AGE_H * 3600):
            pp = clients.get_pair(pos["chain"], pos["pair"])
            if pp:
                rr = analyzer.analyze_pair(pp, None)
                if rr["score"] < POS_REEVAL_MIN_SCORE:
                    pos["deteriorated_warned"] = True
                    print(f"  -> تحذير تدهور: {pos['name']} "
                          f"({pos.get('score')} → {rr['score']})")
                    alerts.send(alerts.pos_deteriorated_msg(
                        pos["name"], entry, price,
                        pos.get("score"), rr["score"]), dry_run)
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
        # كشف الضخ المفاجئ: حجم آخر ساعة مقابل متوسط الساعات السابقة
        try:
            vols = [float(x[5]) for x in k[-(VOL_SPIKE_LOOKBACK + 1):-1]
                    if len(x) > 5]
            last_v = float(k[-1][5]) if k and len(k[-1]) > 5 else 0
            if vols and last_v > 0:
                avg_v = sum(vols) / len(vols)
                if avg_v > 0 and last_v >= VOL_SPIKE_MULT * avg_v:
                    vkey = f"volspike:{sym}"
                    wkey = f"watch:{sym}"
                    now_t = time.time()
                    if now_t - s["alerted"].get(vkey, 0) > VOL_SPIKE_COOLDOWN_H * 3600 \
                            and now_t - s["alerted"].get(wkey, 0) > VOL_SPIKE_COOLDOWN_H * 3600:
                        mult = last_v / avg_v
                        print(f"  -> حركة غير عادية: {sym} (الحجم ×{mult:.1f})")
                        alerts.send(alerts.unusual_volume_msg(
                            sym.replace("USDT", ""), chg, mult), dry_run)
                        s["alerted"][vkey] = now_t
        except Exception:
            pass
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
        "fng": ctx.get("fng"),
        "news_stats": ctx.get("news_stats") or {},
        "paper": paper_summary(s) if PAPER_ENABLED else None,
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
    check_waitlist(s, a.dry_run, ctx)
    update_positions(s, a.dry_run)
    movers = scan_watchlist(s, a.dry_run, ctx)
    update_paper(s, a.dry_run)
    maybe_digest(s, a.dry_run, movers, ctx)

    if not a.dry_run:
        st.save(s)
    print("تم.")


if __name__ == "__main__":
    main()
