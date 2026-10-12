# -*- coding: utf-8 -*-
"""الناشر: رسائل Telegram بصيغة Kdrx العربية."""

import requests

import config


def _send(text, reply_to=None):
    """إرسال رسالة Telegram. يُرجع message_id عند النجاح (للاقتباس reply)."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("[PUB] Telegram not configured — skipping send", flush=True)
        print(f"[PUB] Would send:\n{text}\n", flush=True)
        return None
    try:
        payload = {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        r = requests.post(
            f"{config.TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=15,
        )
        ok = r.status_code == 200
        if not ok:
            print(f"[PUB] Telegram failed: {r.status_code} {r.text[:200]}", flush=True)
            return None
        try:
            return r.json()["result"]["message_id"]
        except Exception:
            return None
    except Exception as e:
        print(f"[PUB] Telegram error: {e}", flush=True)
        return None


def _fmt_price(x):
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:,.4f}"
    return f"{x:.6f}"


def signal_message(sig, position=None):
    """رسالة إشارة جديدة بصيغة Kdrx الأصلية 100%."""
    direction_ar = "شراء" if sig["direction"] == "long" else "بيع"
    pair = sig["symbol"].replace("USDT", "/USDT")
    emoji = "🟢" if sig["direction"] == "long" else "🔴"
    risk_emoji = sig.get("risk_emoji", "🟡")
    risk_lbl = sig.get("risk_level", "متوسطة")

    # منطقة الدخول (مثل Kdrx: الدخول 68.06، منطقة 67.066–69.054)
    zone = sig.get("entry_zone")
    entry_line = f"الدخول: <code>{_fmt_price(sig['entry'])}</code>"
    if zone:
        entry_line += f" (منطقة {_fmt_price(zone[0])} – {_fmt_price(zone[1])})"

    # الوقف مع مسافته % (مثل Kdrx: 59.609 (−12.42%))
    sl_dist_pct = abs(sig["entry"] - sig["sl"]) / sig["entry"] * 100 if sig["entry"] else 0

    lines = [
        f"{emoji} <b>صفقة {direction_ar} جديدة — {pair}</b>",
        f"سبوت · فريم 4 ساعات · {risk_emoji} مخاطرة {risk_lbl}",
        "",
        f"💪 قوة الإشارة <b>{sig['strength']}/100</b>",
        f"⚖️ المخاطرة/العائد <b>{sig['rr']}</b>",
        f"🛡️ حجم المخاطرة <b>{sig['risk_pct']}%</b>",
        "",
        entry_line,
        f"وقف الخسارة: <code>{_fmt_price(sig['sl'])}</code> (−{sl_dist_pct:.2f}%)",
        f"الهدف 1: <code>{_fmt_price(sig['tp1'])}</code>",
        f"الهدف 2: <code>{_fmt_price(sig['tp2'])}</code>",
        f"الهدف 3: <code>{_fmt_price(sig['tp3'])}</code>",
        "",
        sig.get("analysis", ""),
        "",
        "تحليل تقني آلي — سبوت فقط، بلا رافعة. ليست نصيحة استثمارية.",
    ]
    if position:
        lines += [
            "",
            f"💰 الكمية: <code>{position['qty']}</code>",
            f"💵 القيمة: <code>${position['notional']}</code>",
        ]
    return "\n".join(lines)


def event_message(event):
    """رسالة حدث (TP/SL) بصيغة Kdrx."""
    sym = event["symbol"].replace("USDT", "/USDT")
    pnl = event["pnl"]

    # صيغة Kdrx الخاصة للهدف الأول
    if event["reason"] == "TP1":
        entry = event.get("entry", 0)
        target = event.get("price", 0)
        profit_pct = ((target - entry) / entry * 100) if entry else 0
        if event.get("direction") == "short":
            profit_pct = -profit_pct
        return (
            f"✅ <b>{sym} — تحقّق الهدف الأول</b>\n"
            f"الدخول {_fmt_price(entry)} ← الهدف {_fmt_price(target)}\n"
            f"الربح: <b>{profit_pct:+.1f}%</b>\n"
            f"\n"
            f"بيع 50% وتحريك وقف الخسارة إلى نقطة الدخول —\n"
            f"الصفقة صارت <b>بلا مخاطرة</b> ✅"
        )

    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

    # صيغة Kdrx الخاصة لوقف الخسارة
    if event["reason"] == "SL":
        entry = event.get("entry", 0)
        loss_pct = ((event.get("price", 0) - entry) / entry * 100) if entry else 0
        if event.get("direction") == "short":
            loss_pct = -loss_pct
        return (
            f"🛑 <b>{sym} — ضرب وقف الخسارة</b>\n"
            f"النتيجة: <b>{loss_pct:.2f}%</b> · الدخول كان {_fmt_price(entry)}\n"
            f"\n"
            f"الخسارة جزء من العمل. وقف الخسارة حماك من خسارة أكبر —\n"
            f"وهذا دوره بالضبط. ننشر كل النتائج، الرابحة والخاسرة."
        )

    emoji = "✅" if pnl >= 0 else "🛑"

    # صيغة Kdrx للهدف الثاني/الثالث: 🎯 XRPUSDT بلغ الهدف 2 — الدخول: 1.483، الهدف: 1.554، النتيجة: +4.79%.
    if event["reason"] in ("TP2", "TP3"):
        n = "2" if event["reason"] == "TP2" else "3"
        entry = event.get("entry", 0)
        target = event.get("price", 0)
        res_pct = ((target - entry) / entry * 100) if entry else 0
        if event.get("direction") == "short":
            res_pct = -res_pct
        return (
            f"🎯 <b>{sym} بلغ الهدف {n}</b>\n"
            f"الدخول: {_fmt_price(entry)}، الهدف: {_fmt_price(target)}، "
            f"النتيجة: <b>{res_pct:+.2f}%</b>.\n"
            f"هذا التحليل صدر من التطبيق وتُوبعت نتيجته آلياً — لا مجرد ادّعاء."
        )

    reason_ar = {
        "TP2": "الهدف الثاني 🎯🎯",
        "TP3": "الهدف الثالث 🎯🎯🎯 (إغلاق كامل)",
    }.get(event["reason"], event["reason"])

    pct = int(event["pct"] * 100)
    return (
        f"{emoji} <b>{reason_ar}</b>\n"
        f"الزوج: <b>{sym}</b>\n"
        f"أُغلق: <b>{pct}%</b> @ <code>{_fmt_price(event['price'])}</code>\n"
        f"الربح/الخسارة: <b>{pnl_str}</b>"
    )


def summary_message(stats):
    """الملخص اليومي للمحفظة."""
    emoji = "📈" if stats["return_pct"] >= 0 else "📉"
    return (
        f"{emoji} <b>ملخص المحفظة الورقية</b>\n"
        f"\n"
        f"الرصيد: <b>${stats['cash']:.2f}</b> (البداية: ${stats['start']:.2f})\n"
        f"العائد: <b>{stats['return_pct']:+.2f}%</b>\n"
        f"الصفقات: <b>{stats['total_trades']}</b> "
        f"({stats['wins']}✅ / {stats['losses']}❌)\n"
        f"نسبة الفوز: <b>{stats['win_rate']}%</b>\n"
        f"إجمالي P&L: <b>${stats['total_pnl']:+.2f}</b>\n"
        f"مراكز مفتوحة: <b>{stats['open_count']}</b>"
    )


def publish_signal(sig, position=None):
    """يُرجع message_id لاستخدامه في الاقتباس (reply) عند TP/SL."""
    return _send(signal_message(sig, position))


def publish_event(event, reply_to=None):
    """رسالة حدث تقتبس الإشارة الأصلية (مثل Kdrx)."""
    return _send(event_message(event), reply_to=reply_to)


def publish_summary(stats):
    return _send(summary_message(stats))


def analysis_message(a):
    """رسالة التحليل الفوري (مثل زر Kdrx: 📊 حلّل أصلك الآن)."""
    pair = a["symbol"].replace("USDT", "/USDT")
    if a["direction"] == "long":
        trend_ar, trend_emoji = "صاعد", "📈"
    elif a["direction"] == "short":
        trend_ar, trend_emoji = "هابط", "📉"
    else:
        trend_ar, trend_emoji = "محايد", "➡️"
    lines = [
        f"📊 <b>تحليل {pair}</b>",
        f"سبوت · فريم 4 ساعات · {a['risk_emoji']} مخاطرة {a['risk_level']}",
        "",
        f"💪 قوة الإشارة <b>{a['strength']}</b>/100",
        f"{trend_emoji} الاتجاه: <b>{trend_ar}</b> (RSI {a['rsi']})",
        "",
        a["analysis"],
        "",
        f"🎯 الدخول المقترح: <code>{_fmt_price(a['entry'])}</code>",
        f"🛡️ وقف الخسارة: <code>{_fmt_price(a['sl'])}</code>",
        f"🎯 الأهداف: <code>{_fmt_price(a['tp1'])}</code> · <code>{_fmt_price(a['tp2'])}</code> · <code>{_fmt_price(a['tp3'])}</code>",
        "",
        f"{a['verdict_emoji']} <b>الحكم: {a['verdict']}</b>",
        "",
        "تحليل تقني آلي — سبوت فقط، بلا رافعة. ليست نصيحة استثمارية.",
    ]
    return "\n".join(lines)


def publish_analysis(a, reply_to=None):
    return _send(analysis_message(a), reply_to=reply_to)


def weekly_rollup_message(trades):
    """حصيلة الأسبوع مثل Kdrx: المغلقة · بلغت الهدف · الوقف · نسبة الإصابة · أفضل/أسوأ."""
    total = len(trades)
    if not total:
        return (
            "📊 <b>حصيلة الأسبوع</b>\n\n"
            "لا صفقات مغلقة هذا الأسبوع."
        )
    wins = [t for t in trades if t.get("pnl", 0) > 0]
    losses = [t for t in trades if t.get("pnl", 0) <= 0]
    hit_rate = round(len(wins) / total * 100, 1)
    best = max(trades, key=lambda t: t.get("pnl_pct", 0))
    worst = min(trades, key=lambda t: t.get("pnl_pct", 0))
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    lines = [
        "📊 <b>حصيلة الأسبوع</b>",
        "",
        f"الصفقات المغلقة: <b>{total}</b>",
        f"بلغت الهدف: <b>{len(wins)}</b> · أُغلقت على الوقف: <b>{len(losses)}</b>",
        f"نسبة الإصابة: <b>{hit_rate}%</b>",
        f"أفضل صفقة: <b>{best['symbol'].replace('USDT', '/USDT')}</b> {best.get('pnl_pct', 0):+.2f}%",
        f"أسوأ صفقة: <b>{worst['symbol'].replace('USDT', '/USDT')}</b> {worst.get('pnl_pct', 0):+.2f}%",
        f"صافي الربح: <b>${total_pnl:+.2f}</b>",
        "",
        "ننشر الربح والخسارة معاً — وكل تحليل يُتابع آلياً من لحظة صدوره.",
    ]
    return "\n".join(lines)


def publish_weekly_rollup(trades):
    return _send(weekly_rollup_message(trades))


def publish_startup():
    return _send(
        "🤖 <b>بوت الإشارات بدأ العمل</b>\n"
        "الوضع: <b>paper trading</b> (محفظة وهمية $100)\n"
        "الفريم: <b>4 ساعات</b> | الفحص كل <b>5 دقائق</b> | المراقبة كل <b>دقيقتين</b>"
    )


def market_pulse_message(prices):
    """نبض السوق بصيغة Kdrx: أسعار العملات الرئيسية مع التغير."""
    coins = [
        ("Bitcoin", "BTCUSDT"),
        ("Ethereum", "ETHUSDT"),
        ("Solana", "SOLUSDT"),
        ("XRP", "XRPUSDT"),
    ]
    lines = ["📈 <b>نبض السوق</b>", ""]
    for name, sym in coins:
        data = prices.get(sym, {})
        price = data.get("price", 0)
        change = data.get("change_pct", 0)
        emoji = "🟢" if change >= 0 else "🔴"
        sign = "+" if change >= 0 else ""
        lines.append(f"{emoji} {name}: {_fmt_price(price)} {sign}{change:.2f}%")
    lines += ["", "هل تريد معرفة أيها في منطقة شراء؟ حلّله في ثوانٍ."]
    return "\n".join(lines)


def publish_market_pulse(prices):
    return _send(market_pulse_message(prices))
