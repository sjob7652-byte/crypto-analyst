# -*- coding: utf-8 -*-
"""إرسال التنبيهات إلى Telegram — بلغة بسيطة وسهلة الفهم."""
import html
import os
import requests
from config import REQUEST_TIMEOUT, TAKE_PROFITS, STOP_LOSS

DISCLAIMER = ("⚠️ عملات الميم خطيرة جداً — خاطر بمبلغ صغير فقط "
              "تتحمّل خسارته كاملة. هذه ليست نصيحة مالية.")


def send(text, dry_run=False):
    """يرسل رسالة Telegram. في وضع التجربة يطبع فقط."""
    if dry_run:
        print(text)
        print("—" * 45)
        return True
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[!] لا توجد بيانات Telegram في المتغيرات — طباعة الرسالة بدل الإرسال:")
        print(text)
        print("—" * 45)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=REQUEST_TIMEOUT,
        )
        return r.status_code == 200
    except Exception as e:
        print("Telegram error:", e)
        return False


def fmt_usd(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    if x >= 1_000_000:
        return f"${x / 1_000_000:,.2f}M"
    if x >= 1_000:
        return f"${x / 1_000:,.1f}K"
    if x >= 0.01:
        return f"${x:,.2f}"
    return f"${x:.8g}"


def fmt_price(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"${x:,.2f}" if x >= 0.01 else f"${x:.8g}"


def new_signal_msg(res, verdict):
    """إشارة شراء بكلام بسيط: سعر الدخول/الخروج + نسبة النجاح + السبب."""
    name = html.escape(res["display"])
    v = verdict
    lines = [
        f"{v['emoji']} <b>{v['word']}: {name}</b>",
        "",
        f"💰 <b>ادخل الآن بسعر:</b> {fmt_price(v['entry'])}",
        f"🎯 <b>بِع عند:</b> {fmt_price(v['tps'][0])} (الهدف الأول +30%)",
        f"📈 الهدف الثاني: {fmt_price(v['tps'][1])} (+70%)",
        f"🚀 الهدف الثالث: {fmt_price(v['tps'][2])} (+150%)",
        f"🛑 <b>وقف الخسارة:</b> {fmt_price(v['sl'])} ← إذا وصل السعر هنا اخرج فوراً",
        "",
        f"✅ <b>نسبة النجاح التقديرية:</b> {v['prob']}%",
    ]
    if v.get("learned") is not None:
        lines.append(f"🧠 <i>بناءً على نتائج {v['band']} السابقة: {v['learned']}% منها رابحة</i>")
    lines.append(f"💡 <b>لماذا هذه العملة؟</b> {html.escape(v['reason'])}")
    if v.get("news"):
        lines.append(f"📰 <b>خبر عنها:</b> {html.escape(v['news'][:110])}")
    if v.get("warn"):
        lines.append(f"⚠️ <b>انتبه:</b> {html.escape(v['warn'])}")
    lines += [
        "",
        f"🔗 <a href=\"{res['pair_url']}\">الرسم البياني</a>",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines)


def avoid_msg(res):
    """تحذير بسيط: لا تدخل."""
    name = html.escape(res["display"])
    reason = ""
    for w in (res.get("warnings") or []):
        if "⛔" in w:
            reason = w.replace("⛔", "").strip()
            break
    if not reason and res.get("warnings"):
        reason = str(res["warnings"][0])
    return (
        f"🔴 <b>لا تدخل: {name}</b>\n"
        f"⛔ <b>السبب:</b> {html.escape(reason or 'علامات خطر في العقد أو السيولة')}\n"
        f"تجاهل هذه العملة وركّز على الفرص القادمة."
    )


def tp_hit_msg(name, entry, price, level_idx):
    pct = int(TAKE_PROFITS[level_idx] * 100)
    gain = (price / entry - 1) * 100
    return (
        f"🎯 <b>مبروك! وصل الهدف {level_idx + 1} — {html.escape(name)}</b>\n"
        f"دخلت بسعر: {fmt_price(entry)}\n"
        f"السعر الآن: {fmt_price(price)} (ربحك: +{gain:.1f}%)\n"
        f"💡 <b>نصيحة:</b> بِع نصف الكمية لتأمين الربح، واترك النصف للأهداف الباقية."
    )


def all_tp_msg(name, entry, price):
    gain = (price / entry - 1) * 100
    return (
        f"🏆 <b>اكتملت كل الأهداف — {html.escape(name)}</b> 🎉\n"
        f"من {fmt_price(entry)} إلى {fmt_price(price)} (ربح: +{gain:.1f}%)\n"
        f"أحسنت! توقفت عن متابعة هذه الصفقة."
    )


def stop_loss_msg(name, entry, price):
    loss = (1 - price / entry) * 100
    return (
        f"🛑 <b>اخرج الآن — {html.escape(name)}</b>\n"
        f"السعر نزل تحت وقف الخسارة.\n"
        f"دخلت بـ: {fmt_price(entry)} → الآن: {fmt_price(price)} (خسارة: -{loss:.1f}%)\n"
        f"💡 <b>نصيحة:</b> اخرج فوراً لحماية ما تبقى. الالتزام بالخطة أهم من صفقة واحدة."
    )


def rug_warn_msg(name, price):
    return (
        f"🚨 <b>خطر! {html.escape(name)}</b>\n"
        f"السعر: {fmt_price(price)}\n"
        f"السيولة تنهار أو ظهرت مشكلة في العقد.\n"
        f"💡 <b>نصيحة:</b> اخرج فوراً إذا كنت داخلاً."
    )


def digest_msg(date_str, positions, new_signals, movers, ctx):
    lines = [f"📰 <b>ملخص اليوم — {date_str}</b>"]

    macro = (ctx or {}).get("macro")
    if macro:
        icon = "📈" if macro["btc_chg"] >= 0 else "📉"
        lines.append(f"\n{icon} <b>البيتكوين:</b> {fmt_usd(macro['btc'])} ({macro['btc_chg']:+.1f}% في 24س)")

    if positions:
        lines.append("\n📌 <b>صفقاتك المفتوحة:</b>")
        for p in positions:
            pnl = (p["price"] / p["entry"] - 1) * 100
            icon = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"{icon} {html.escape(p['name'])}: {pnl:+.1f}%")
    else:
        lines.append("\n📌 لا صفقات مفتوحة حالياً.")
    lines.append(f"\n🔔 إشارات جديدة اليوم: <b>{new_signals}</b>")

    track = (ctx or {}).get("track")
    if track and track["n"]:
        lines.append(f"🧠 <b>سجل الإشارات:</b> {track['wins']} رابحة من آخر {track['n']}")

    if movers:
        lines.append("\n🔥 <b>أكبر تحركات عملات الميم:</b>")
        for sym, chg in movers[:3]:
            icon = "📈" if chg >= 0 else "📉"
            lines.append(f"{icon} {sym}: {chg:+.1f}%")

    trending = (ctx or {}).get("trending") or []
    if trending:
        names = ", ".join(t["symbol"] for t in trending[:5])
        lines.append(f"\n👀 <b>رائج الآن:</b> {html.escape(names)}")

    news = (ctx or {}).get("news_top") or []
    if news:
        lines.append("\n🗞️ <b>أهم الأخبار:</b>")
        for it in news[:4]:
            s = it.get("sentiment", 0)
            icon = "🟢" if s > 0.2 else ("🔴" if s < -0.2 else "⚪")
            lines.append(f"{icon} <a href=\"{it['link']}\">{html.escape(it['title'][:85])}</a>")

    lines.append("\n" + DISCLAIMER)
    return "\n".join(lines)
