#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""المشغّل الرئيسي: فحص العملات الجديدة → تحليل → تنبيه → متابعة الصفقات."""
import argparse
import html
import re
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
    PAPER_SELL_FRACTIONS, PAPER_SLIPPAGE, PAPER_MAX_OPEN,
    CIRCUIT_BREAKER_SL_STREAK, CIRCUIT_BREAKER_HALT_H, SL_COOLDOWN_H,
    USE_XBRIDGE, XBRIDGE_MAX_AGE_H,
    MIN_PROBABILITY,
)

# عتبات الانهيار والقائمة السوداء
RUG_ALERT_LOSS = 0.70      # خسارة ≥70% فجأة → رسالة "انهيار" بدل وقف الخسارة
RUG_BLACKLIST_LOSS = 0.80  # خسارة ≥80% → العملة + مطورها إلى القائمة السوداء


def _rug_store(s):
    return s.setdefault("rug_blacklist", {})


def blacklist_rug(s, pos, loss_pct):
    """تسجيل عملة منهارة في القائمة السوداء مع عنوان مطورها (إن عُرف
    عبر RugCheck) — أي عملة جديدة من نفس المطور تُحظر تلقائياً."""
    key = (pos.get("mint") or "").lower() or (pos.get("symbol") or "")
    if not key:
        return False
    bl = _rug_store(s)
    if key in bl:
        return True
    dev = None
    if pos.get("kind") == "dex" and pos.get("chain") == "solana" \
            and pos.get("mint"):
        dev = clients.rugcheck_creator(pos["mint"])
    bl[key] = {"name": pos.get("name"), "dev": dev,
               "time": time.time(), "loss": round(loss_pct * 100, 1)}
    if dev:
        devs = s.setdefault("rug_devs", [])
        if dev not in devs:
            devs.append(dev)
    print(f"  -> ⛔ قائمة سوداء: {pos.get('name')} "
          f"(مطور: {dev or 'غير معروف'})")
    return True


def is_blacklisted(s, mint=None, symbol=None, chain=None):
    """هل العملة أو مطورها في القائمة السوداء؟"""
    bl = s.get("rug_blacklist") or {}
    if mint and mint.lower() in bl:
        return True, "العملة مسجلة في القائمة السوداء (انهيار سابق)"
    if symbol and symbol in bl:
        return True, "العملة مسجلة في القائمة السوداء (انهيار سابق)"
    if chain == "solana" and mint:
        dev = clients.rugcheck_creator(mint)
        if dev and dev in (s.get("rug_devs") or []):
            return True, "مطور العملة في القائمة السوداء (سجل انهيارات)"
    return False, None


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
    # جسر أخبار X عبر قنوات Telegram (Telethon) — تلميحات إضافية بأدنى ثقة
    # يعمل فقط عند وجود الأسرار، وإلا يُتجاهل بصمت تام
    if USE_XBRIDGE:
        try:
            import xbridge
            xb = xbridge.fetch_xbridge_news(max_age_h=XBRIDGE_MAX_AGE_H)
            if xb:
                ctx["news"] = (ctx.get("news") or []) + xb
                print(f"أخبار X⇄TG: {len(xb)} عنصراً من قنوات Telegram")
        except Exception as e:
            print("xbridge error:", e)
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


def _demote_to_watch(s, res, verdict):
    """صفقة تحت عتبة الثقة: تُنقل للمراقبة بدل الدخول —
    لا تنبيه Telegram ولا صفقة وهمية. تُعاد فحصها في الجولات القادمة
    (قد تتحسن مؤشراتها فتتجاوز العتبة)."""
    wl = s.setdefault("waitlist", {})
    wid = res["id"]
    if wid in wl:
        wl[wid]["score"] = res.get("score")  # تحديث النقاط للجولة القادمة
    elif len(wl) < WAITLIST_MAX_SIZE:
        wl[wid] = {
            "chain": res.get("chain"), "pair": res.get("pair"),
            "display": res.get("display"), "score": res.get("score"),
            "added": time.time(), "checks": 0,
        }
    print(f"  -> ⏸ تحت عتبة الثقة ({verdict['prob']}% < {MIN_PROBABILITY}%): "
          f"{res.get('display')} — مراقبة فقط")


def below_threshold(verdict):
    """هل نسبة النجاح التقديرية تحت الحد الأدنى للدخول؟"""
    try:
        return int(verdict.get("prob", 0)) < MIN_PROBABILITY
    except (TypeError, ValueError):
        return True  # رقم غير صالح = لا دخول (افتراض آمن)


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
        # فحص السلامة: زوج بلا سعر = بيانات ناقصة — يُتجاهل (لا قرارات على فراغ)
        if not analyzer._f(p.get("priceUsd")):
            continue
        boosted = (chain, (addr or "").lower()) in boosted_set
        sec = clients.token_security(chain, addr) if addr else None
        # الافتراض الآمن: فشل فحص الأمان لعملة بعنوان معروف = مرفوضة
        sec_failed = bool(addr) and sec is None
        # 🐋 فحص تركيز الحيتان (Solana فقط — مجاني بلا مفتاح عبر RugCheck)
        holders = (clients.solana_top10_pct(addr)
                   if (chain == "solana" and addr) else None)
        res = analyzer.analyze_pair(p, sec, boosted=boosted, holders=holders,
                                    security_unknown=sec_failed)
        res["id"] = f"dex:{chain}:{p.get('pairAddress')}"
        res["kind"] = "dex"
        res["chain"] = chain
        res["pair"] = p.get("pairAddress")
        res["mint"] = addr
        results.append(res)

    results.sort(key=lambda r: r["score"], reverse=True)
    sent = 0
    wait_added = 0
    for res in results:
        key = f"sig:{res['id']}"
        if time.time() - s["alerted"].get(key, 0) < 24 * 3600:
            continue
        if res["signal"] in ("BUY", "STRONG_BUY") and sent < 5:
            bad, why = is_blacklisted(s, mint=res.get("mint"),
                                      chain=res.get("chain"))
            if bad:
                print(f"  -> ⛔ محظورة (قائمة سوداء): {res['display']} — {why}")
                continue
            verdict = make_verdict(res, ctx)
            print(f"  -> إشارة {res['signal']}: {res['display']} "
                  f"({res['score']}) نجاح~{verdict['prob']}%")
            # عتبة الثقة: نسبة ضعيفة = مراقبة فقط (لا دخول ولا تنبيه)
            if below_threshold(verdict):
                _demote_to_watch(s, res, verdict)
                continue
            # التنبيه لا يُرسل إلا بعد فتح الصفقة (متابعة + وهمية) بنجاح
            if not open_position(s, res, verdict):
                continue
            alerts.send(alerts.new_signal_msg(res, verdict), dry_run)
            s["alerted"][key] = time.time()
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
        addr = (p.get("baseToken") or {}).get("address")
        # فحص السلامة: زوج بلا سعر = بيانات ناقصة — يُتجاهل
        if not analyzer._f(p.get("priceUsd")):
            wl.pop(wid, None)
            continue
        # الافتراض الآمن: فشل فحص الأمان = مرفوضة
        sec_failed = bool(addr) and sec is None
        holders = (clients.solana_top10_pct(addr)
                   if (e["chain"] == "solana" and addr) else None)
        res = analyzer.analyze_pair(p, sec, holders=holders,
                                    security_unknown=sec_failed)
        res["id"] = wid
        res["kind"] = "dex"
        res["chain"] = e["chain"]
        res["pair"] = e["pair"]
        res["mint"] = addr
        if res["signal"] in ("BUY", "STRONG_BUY"):
            bad, why = is_blacklisted(s, mint=addr, chain=e["chain"])
            if bad:
                print(f"  -> ⛔ محظورة (قائمة سوداء): {res['display']} — {why}")
                wl.pop(wid, None)
                continue
            verdict = make_verdict(res, ctx)
            print(f"  -> 🔄 تحسّنت: {res['display']} ({res['score']}) "
                  f"نجاح~{verdict['prob']}%")
            # عتبة الثقة: تبقى في المراقبة حتى تتجاوز الحد
            if below_threshold(verdict):
                _demote_to_watch(s, res, verdict)
                continue
            # التنبيه لا يُرسل إلا بعد فتح الصفقة (متابعة + وهمية) بنجاح
            if not open_position(s, res, verdict):
                wl.pop(wid, None)
                continue
            msg = ("🔄 <b>رجعت بقوة!</b> كانت تحت المراقبة والآن تحسّنت "
                   "مؤشراتها.\n\n" + alerts.new_signal_msg(res, verdict))
            alerts.send(msg, dry_run)
            s["alerted"][f"sig:{wid}"] = now
            s["stats"]["signals_today"] = s["stats"].get("signals_today", 0) + 1
            wl.pop(wid, None)
        elif res["signal"] == "AVOID":
            wl.pop(wid, None)  # ساءت — أخرجها من اللائحة
        else:
            e["checks"] += 1
            e["score"] = res["score"]


def open_position(s, res, verdict=None):
    """يفتح صفقة متابعة + صفقة وهمية — يرجع True فقط إذا نجح الاثنان.
    الترتيب مقصود: لا تُفتح صفقة متابعة دون صفقة وهمية مطابقة، ولا يُرسل
    تنبيه Telegram إلا بعد نجاح الفتح (ضمان ذري)."""
    m = res["metrics"]
    # السعر من المقاييس، وإلا من سعر الدخول المعلن في التنبيه
    price = m.get("price") or (verdict or {}).get("entry")
    if not price:
        return False
    if not paper_buy(s, res, verdict):
        print(f"  -> تعذر فتح الصفقة الوهمية لـ {res['display']} "
              f"(رصيد غير كافٍ) — لن يُرسل تنبيه")
        return False
    s["positions"][res["id"]] = {
        "kind": res["kind"],
        "name": res["display"],
        "chain": res.get("chain"),
        "pair": res.get("pair"),
        "symbol": res.get("symbol"),
        "mint": res.get("mint"),
        "entry": price,
        "entry_time": time.time(),
        "tp_hit": [False] * len(TAKE_PROFITS),
        "ref_liq": m.get("liq") or 0,
        "warned": False,
        "score": res.get("score"),
        "band": (verdict or {}).get("band") or expert.band_of(res.get("score")),
        "best_hit": None,
    }
    return True


def paper_buy(s, res, verdict=None):
    """شراء وهمي: يخصم من الرصيد الافتراضي ويفتح صفقة وهمية (بلا مخاطرة).
    يرجع True عند نجاح الشراء، False عند تعذره (بلا رصيد/بلا سعر/موجودة)."""
    if not PAPER_ENABLED:
        return True
    p = s["paper"]
    m = res["metrics"]
    price = m.get("price") or (verdict.get("entry") if verdict else None)
    if not price:
        return False
    pid = res["id"]
    if pid in p["positions"]:
        return True  # الصفقة الوهمية موجودة أصلاً — الضمان محقق
    # ---------- حزمة الحماية (2026-09-24) ----------
    # 1) سقف الصفقات المفتوحة — لا شراء جديد والمحفظة ممتلئة
    if len(p["positions"]) >= PAPER_MAX_OPEN:
        print(f"  -> سقف الصفقات المفتوحة ({PAPER_MAX_OPEN}) ممتلئ — لا شراء")
        return False
    # 2) circuit breaker: إيقاف الشراء بعد سلسلة إغلاقات خاسرة
    if time.time() < p.get("halt_until", 0):
        print("  -> إيقاف مؤقت للشراء (circuit breaker) — لا شراء")
        return False
    # 3) تهدئة: منع إعادة الدخول في نفس العملة بعد إغلاق خاسر
    cd = p.get("sl_cooldown") or {}
    # تنظيف المدخلات المنتهية أولاً
    now = time.time()
    cd = {k: v for k, v in cd.items() if now - v < SL_COOLDOWN_H * 3600}
    p["sl_cooldown"] = cd
    if pid in cd:
        print(f"  -> تهدئة بعد إغلاق خاسر لـ {res['display']} — لا شراء")
        return False
    # الرصيد النقدي هو المحدد الطبيعي الثاني بعد سقف الصفقات
    amount = min(PAPER_RISK_PER_TRADE, p["cash"])
    if amount < 5:
        return False
    p["cash"] -= amount
    # سعر التنفيذ الواقعي: الشراء بسعر أغلى بسبب الانزلاق السعري
    eff_entry = price * (1 + PAPER_SLIPPAGE)
    p["positions"][pid] = {
        "kind": res["kind"],
        "name": res["display"],
        "chain": res.get("chain"),
        "pair": res.get("pair"),
        "symbol": res.get("symbol"),
        "mint": res.get("mint"),
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
    return True


def _archive_closed(p, pos, exit_price, pnl, reason, invested_override=None,
                   partial=False):
    """ينقل الصفقة المغلقة إلى الأرشيف مع كل تفاصيلها — لا تُحذف أبداً.
    reason: TP1 (جني جزئي 50%) | BE (تعادل: خروج عند الدخول بعد الجني) |
            TP (اكتمال الأهداف) | SL (وقف الخسارة) | RUG (انهيار) |
            EXPIRED (انتهاء المدة).
    invested_override: للجني الجزئي — تُحسب النسبة على الجزء المُباع فقط.
    partial=True: حدث جزئي (جني TP1) — الداشبورد يعرضه لكنه يستثنيه من
    مجاميع الربح ونسبة النجاح، لأن الإغلاق النهائي يحسب الربح الكلي
    (متضمناً المحقق) في سجل واحد. بدون هذا يُحتسب ربح TP1 مرتين."""
    inv = invested_override if invested_override else (pos.get("invested") or 0)
    rec = {
        "name": pos.get("name"),
        "symbol": pos.get("symbol"),
        "kind": pos.get("kind"),
        "chain": pos.get("chain"),
        "entry": pos.get("entry"),
        "exit": exit_price,
        "invested": round(inv, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / inv * 100, 2) if inv else 0.0,
        "reason": reason,
        "partial": bool(partial),
        "entry_time": pos.get("entry_time"),
        "close_time": time.time(),
    }
    arch = p.setdefault("closed_trades", [])
    arch.append(rec)
    # حد أقصى: أحدث 2000 صفقة — لمنع تضخم الـGist مع الزمن
    if len(arch) > 2000:
        del arch[:len(arch) - 2000]


def _paper_close(p, pid, pos, eff_price, arch, s, dry_run):
    """إغلاق كامل موحّد لصفقة وهمية — نقطة الخروج الوحيدة في الكود.
    الترتيب ثابت دائماً: ردّ الكاش ← عدّاد الفوز/الخسارة ←
    سلسلة الخسائر + تهدئة العملة + circuit-breaker ← الأرشفة (إجبارية).
    هذا يمنع فجوة الأرشيف: مستحيل إغلاق صفقة دون أرشفتها، لأن كل
    مسارات الإغلاق (SL/RUG/BE/EXPIRED) تمرّ من هنا إجبارياً.
    يرجع (pnl, proceeds)."""
    qty = pos.get("qty") or 0
    entry = pos.get("entry") or 0
    proceeds = qty * eff_price
    pnl = proceeds - qty * entry + pos.get("realized", 0)
    p["cash"] += proceeds
    if pnl >= 0:
        p["wins"] += 1
        p["consec_sl"] = 0
    else:
        p["losses"] += 1
        # سلسلة الخسائر المتتالية + تهدئة: منع إعادة الدخول في نفس العملة
        p["consec_sl"] = p.get("consec_sl", 0) + 1
        p.setdefault("sl_cooldown", {})[pid] = time.time()
        # circuit breaker: إيقاف الشراء بعد N إغلاقات خاسرة متتالية
        # (لا تمديد تلقائي أثناء الإيقاف — يُعاد التفعيل فقط بعد انتهائه)
        if (p["consec_sl"] >= CIRCUIT_BREAKER_SL_STREAK
                and time.time() >= p.get("halt_until", 0)):
            p["halt_until"] = time.time() + CIRCUIT_BREAKER_HALT_H * 3600
            print(f"  -> 🛑 circuit breaker: {p['consec_sl']} إغلاقات خاسرة "
                  f"متتالية — إيقاف الشراء {CIRCUIT_BREAKER_HALT_H}h")
            try:
                alerts.send(alerts.circuit_breaker_msg(
                    p["consec_sl"], CIRCUIT_BREAKER_HALT_H, p["cash"]), dry_run)
            except Exception as e:
                print(f"  -> تعذر إرسال تنبيه circuit breaker: {e}")
    # الأرشفة إجبارية — لا إغلاق دون سجل
    _archive_closed(p, pos, eff_price, pnl, arch)
    return pnl, proceeds


def _update_one_paper_position(s, p, pid, pos, closed, partials, dry_run):
    """متابعة صفقة وهمية واحدة — تُستدعى داخل try/except لكل صفقة."""
    price, _liq = current_price(pos)
    if not price:
        return
    entry = pos["entry"]
    # ترحيل: صفقات قديمة قبل تبسيط الأهداف (3 → 1)
    th = pos.get("tp_hit") or []
    pos["tp_hit"] = (th + [False] * len(TAKE_PROFITS))[:len(TAKE_PROFITS)]
    if pos["tp_hit"][0] and not pos.get("be"):
        pos["be"] = True  # وصلت الهدف سابقاً → وقفها الآن عند الدخول
    # سعر التنفيذ الواقعي عند البيع (أرخص بسبب الانزلاق) — التفعيل يبقى
    # على سعر السوق الخام، لكن التنفيذ الفعلي ينزلق
    eff_price = price * (1 - PAPER_SLIPPAGE)
    # الهدف الوحيد (+30%): بيع 50% فوراً + نقل وقف الخسارة لسعر الدخول
    for i, tp in enumerate(TAKE_PROFITS):
        if not pos["tp_hit"][i] and price >= entry * (1 + tp):
            pos["tp_hit"][i] = True
            sell_qty = pos["qty"] * PAPER_SELL_FRACTIONS[i]
            proceeds = sell_qty * eff_price
            part_pnl = proceeds - sell_qty * entry
            pos["qty"] -= sell_qty
            pos["realized"] += part_pnl
            pos["be"] = True  # 🛡️ النصف المتبقي أصبح خالي المخاطر
            p["cash"] += proceeds
            print(f"  -> وهمي: جني جزئي 50% {pos['name']} "
                  f"(+${part_pnl:.2f} محقق) — وقف الخسارة → الدخول")
            # أرشفة الجني الجزئي كحدث مستقل (partial: يُستثنى من مجاميع
            # الربح/النجاح في الداشبورد — الإغلاق النهائي يحسب الكل)
            _archive_closed(p, pos, eff_price, part_pnl, "TP1",
                            invested_override=sell_qty * entry,
                            partial=True)
            partials.append(pid)
            # جني جزئي حقيقي: نُعلن البيع الفعلي لا مجرد نصيحة
            orig_qty = pos["invested"] / entry if entry else 0
            remaining_pct = (pos["qty"] / orig_qty * 100
                             if orig_qty else 0)
            alerts.send(alerts.paper_tp_msg(
                pos["name"], i, PAPER_SELL_FRACTIONS[i] * 100,
                proceeds, pos["realized"], remaining_pct,
                p["cash"]), dry_run)
            break
    if pid in closed:
        return
    # وقف الخسارة: بعد الجني ينتقل لسعر الدخول (تعادل) — قبل الجني -15%
    # (خسارة ≥70% فجأة → "انهيار" Rug Pull بدل وقف الخسارة العادي)
    stop = entry if pos.get("be") else entry * (1 - STOP_LOSS)
    if price <= stop:
        loss = 1 - price / entry
        rug = loss >= RUG_ALERT_LOSS
        if rug and loss >= RUG_BLACKLIST_LOSS:
            blacklist_rug(s, pos, loss)
        if rug:
            reason, arch = "🚨 انهيار مفاجئ (Rug Pull)", "RUG"
        elif pos.get("be"):
            reason, arch = "⚖️ تعادل: خروج عند سعر الدخول", "BE"
        else:
            reason, arch = "وقف الخسارة 🛑", "SL"
        # الإغلاق المركزي: كاش + عدّادات + حماية + أرشفة (إجبارية)
        pnl, _proceeds = _paper_close(p, pid, pos, eff_price, arch,
                                      s, dry_run)
        closed.append(pid)
        print(f"  -> وهمي: {arch} {pos['name']} (${pnl:+.2f})")
        alerts.send(alerts.paper_closed_msg(
            pos["name"], pnl,
            pnl / pos["invested"] * 100 if pos["invested"] else 0,
            reason, p["cash"]), dry_run)
        return
    # انتهاء مدة المتابعة: بيع بسعر السوق
    if time.time() - pos["entry_time"] > POSITION_MAX_AGE_DAYS * 86400:
        pnl, _proceeds = _paper_close(p, pid, pos, eff_price, "EXPIRED",
                                      s, dry_run)
        closed.append(pid)
        print(f"  -> وهمي: EXPIRED {pos['name']} (${pnl:+.2f})")


def update_paper(s, dry_run):
    """يتابع الصفقات الوهمية: بيع جزئي عند الأهداف، بيع كامل عند وقف الخسارة."""
    p = s["paper"]
    if not p["positions"]:
        return
    print("=== المحفظة الافتراضية ===")
    closed = []
    partials = []
    arch_before = len(p.get("closed_trades", []))
    for pid, pos in list(p["positions"].items()):
        # صفقة واحدة فاسدة يجب ألا تُسقط متابعة البقية
        try:
            _update_one_paper_position(s, p, pid, pos, closed, partials,
                                       dry_run)
        except Exception as e:
            print(f"  -> خطأ في متابعة {pos.get('name')}: {e} — تُترك مفتوحة")
            continue
    for pid in closed:
        p["positions"].pop(pid, None)
    # حارس الأرشيف: كل إغلاق في هذا التشغيل يجب أن يقابله سجل —
    # أي فجوة تُعلن فوراً بدل أن تُكتشف بعد أسابيع
    arch_after = len(p.get("closed_trades", []))
    expected = arch_before + len(closed) + len(partials)
    if arch_after != expected:
        msg = (f"⚠️ فجوة أرشيف في المحفظة الوهمية: أُغلقت {len(closed)} صفقة "
               f"(+{len(partials)} جني جزئي) لكن الأرشيف زاد "
               f"{arch_after - arch_before} فقط — راجع السجلات")
        print("  -> " + msg)
        alerts.send(msg, dry_run)


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
        # ترحيل: صفقات قديمة قبل تبسيط الأهداف (3 → 1)
        th = pos.get("tp_hit") or []
        pos["tp_hit"] = (th + [False] * len(TAKE_PROFITS))[:len(TAKE_PROFITS)]
        if pos["tp_hit"][0] and not pos.get("be"):
            pos["be"] = True
        # الهدف الوحيد (+30%): تنبيه + نقل وقف الخسارة لسعر الدخول
        for i, tp in enumerate(TAKE_PROFITS):
            if not pos["tp_hit"][i] and price >= entry * (1 + tp):
                pos["tp_hit"][i] = True
                pos["best_hit"] = f"tp{i + 1}"
                pos["be"] = True  # 🛡️ النصف المتبقي أصبح خالي المخاطر
                print(f"  -> تحقق الهدف: {pos['name']} — وقف الخسارة → الدخول")
                alerts.send(alerts.tp_hit_msg(pos["name"], entry, price, i), dry_run)
                break
        if pid in closed:
            continue
        # وقف الخسارة — أو "انهيار مفاجئ" إن تجاوزت الخسارة 70% فجأة
        # (يُرجح سحب سيولة، فيُسجل كنوع مستقل "rug" بدل "sl")
        # بعد الجني: الوقف عند سعر الدخول (تعادل) بدل -15%
        stop = entry if pos.get("be") else entry * (1 - STOP_LOSS)
        if price <= stop:
            loss = 1 - price / entry
            if loss >= RUG_ALERT_LOSS:
                blacklisted = (blacklist_rug(s, pos, loss)
                               if loss >= RUG_BLACKLIST_LOSS else False)
                print(f"  -> 🚨 انهيار: {pos['name']} (-{loss * 100:.1f}%)")
                alerts.send(alerts.rug_pull_msg(pos["name"], entry, price,
                                                blacklisted), dry_run)
                st.record_outcome(s, pos, "rug")
            elif pos.get("be"):
                print(f"  -> ⚖️ تعادل: {pos['name']} (خروج عند الدخول)")
                alerts.send(alerts.be_stop_msg(pos["name"], entry), dry_run)
                st.record_outcome(s, pos, "tp1")  # الهدف تحقق والباقي خرج متعادلاً
            else:
                print(f"  -> وقف الخسارة: {pos['name']}")
                alerts.send(alerts.stop_loss_msg(pos["name"], entry, price),
                            dry_run)
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
            bad, why = is_blacklisted(s, symbol=sym)
            if bad:
                print(f"  -> ⛔ محظورة (قائمة سوداء): {res['display']} — {why}")
                continue
            verdict = make_verdict(res, ctx)
            print(f"  -> إشارة {res['signal']}: {res['display']} "
                  f"({res['score']}) نجاح~{verdict['prob']}%")
            # بوابة الثقة (نفسها في كل المسارات): احتمال < 50% = مراقبة فقط
            if below_threshold(verdict):
                print(f"  -> ⏸ تحت عتبة الثقة ({verdict['prob']}% < {MIN_PROBABILITY}%): "
                      f"{res.get('display')} — مراقبة فقط")
                continue
            # التنبيه لا يُرسل إلا بعد فتح الصفقة (متابعة + وهمية) بنجاح
            if not open_position(s, res, verdict):
                continue
            alerts.send(alerts.new_signal_msg(res, verdict), dry_run)
            s["alerted"][key] = time.time()
            s["stats"]["signals_today"] = s["stats"].get("signals_today", 0) + 1
    movers.sort(key=lambda x: abs(x[1]), reverse=True)
    return movers


def maybe_digest(s, dry_run, movers, ctx):
    now = datetime.now(timezone.utc)
    if now.hour not in DIGEST_HOURS_UTC:
        return
    # مفتاح الإرسال كقائمة: JSON يحفظ القوائم كما هي عبر الـGist، أما الـtuple
    # فكان يتحول إلى list بعد الحفظ فيضيع التطابق ويُعاد إرسال الملخص كل
    # 5 دقائق طوال ساعة الإرسال (تضخم Telegram وسجل التنبيهات بالتكرار)
    sent_key = [now.strftime("%Y-%m-%d"), now.hour]
    if s["stats"].get("digest_sent") == sent_key:
        return
    s["stats"]["digest_sent"] = sent_key
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
    # الملخص الدوري: Telegram فقط — لا يُسجل في سجل تنبيهات الداشبورد
    # (قرار المستخدم 2026-09-23: التقرير يصل Telegram فقط، بمحتواه
    # الكامل كما كان — ربح/خسارة المحفظة الوهمية — والأرقام من نفس
    # الـstate الذي يقرأه الداشبورد فتبقى متطابقة)
    alerts.send(alerts.digest_msg(date_str, positions,
                                  s["stats"].get("signals_today", 0),
                                  movers, dctx), dry_run, log_alert=False)


def _sos_alert(error):
    """تنبيه الطوارئ: قبل الانهيار، نخبر المستخدم أن النظام توقف —
    حتى لا يعيش في وهم أن 'السوق هادئ' بينما البوت معطل."""
    try:
        alerts.send(
            "🚨 <b>توقف النظام عن العمل بسبب خطأ تقني</b>\n"
            f"السبب: {html.escape(str(error)[:300])}\n"
            "يرجى مراجعة سجلات GitHub Actions فوراً.", dry_run=False)
    except Exception as e:
        print("SOS failed:", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="طباعة الرسائل بدل إرسالها وعدم حفظ الحالة")
    a = ap.parse_args()

    try:
        _run(a)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[!] انهيار غير متوقع: {e}")
        if not a.dry_run:
            _sos_alert(e)
        raise  # يفشل الـworkflow بعلامة حمراء — وضوح كامل


def _plain(text):
    """نص التنبيه بصيغة بسيطة للداشبورد (إزالة وسوم HTML الخاصة بـTelegram)."""
    t = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(" ".join(t.split()))


def _log_alert(s, kind, text):
    """سجل التنبيهات في الحالة — الداشبورد يعرضه عبر الـGist.
    هكذا 'ينصت' الداشبورد لكل تنبيه يرسله البوت دون قراءة Telegram."""
    log = s.setdefault("alert_log", [])
    log.append({"t": time.time(), "kind": kind or "info",
                "text": _plain(text)[:300]})
    # حد أقصى: أحدث 40 تنبيهاً — لمنع تضخم الـGist
    if len(log) > 40:
        del log[:len(log) - 40]


def _run(a):
    s = st.load()
    # الداشبورد ينصت: كل alerts.send يُسجل في الحالة → يُعرض في الداشبورد
    alerts.LOG_HOOK = lambda kind, text: _log_alert(s, kind, text)
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
