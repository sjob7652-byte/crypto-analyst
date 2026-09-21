# -*- coding: utf-8 -*-
"""إرسال التنبيهات إلى Telegram + تنسيق الرسائل بالعربية."""
import html
import os
import requests
from config import REQUEST_TIMEOUT, TAKE_PROFITS, STOP_LOSS

SIGNAL_EMOJI = {
    "STRONG_BUY": "🟢🟢", "BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴",
}
SIGNAL_AR = {
    "STRONG_BUY": "شراء قوي", "BUY": "شراء",
    "WATCH": "مراقبة فقط", "AVOID": "تجنّب",
}
DISCLAIMER = ("<i>⚠️ عملات الميم عالية المخاطر جداً — لا تخاطر إلا بمبلغ صغير "
              "تتحمّل خسارته كاملة. هذه ليست نصيحة مالية.</i>")


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


def _bullets(items, limit=6):
    return "\n".join("• " + html.escape(str(i)) for i in items[:limit])


def new_signal_msg(res):
    m = res["metrics"]
    e = SIGNAL_EMOJI[res["signal"]]
    label = SIGNAL_AR[res["signal"]]
    lines = [
        f"{e} <b>إشارة {label}: {html.escape(res['display'])}</b>",
        f"النتيجة: <b>{res['score']}/100</b>",
        f"💰 السعر: {fmt_price(m['price'])}",
    ]
    if m.get("liq"):
        lines.append(f"💧 السيولة: {fmt_usd(m['liq'])} | 📊 حجم 24س: {fmt_usd(m['vol24'])}")
        lines.append(f"📈 1س: {m['pc1h']:+.1f}% | 24س: {m['pc24h']:+.1f}%")
    else:
        lines.append(f"📊 حجم 24س: {fmt_usd(m['vol24'])} | التغير 24س: {m['pc24h']:+.1f}%")
    if res["reasons"]:
        lines.append("\n✅ <b>نقاط القوة:</b>\n" + _bullets(res["reasons"]))
    if res["warnings"]:
        lines.append("\n⚠️ <b>انتبه:</b>\n" + _bullets(res["warnings"], 4))
    if res["signal"] in ("BUY", "STRONG_BUY"):
        tps = " / ".join(f"+{int(t * 100)}%" for t in TAKE_PROFITS)
        lines.append(f"\n🎯 <b>أهداف البيع المقترحة:</b> {tps}")
        lines.append(f"🛑 <b>وقف الخسارة:</b> -{int(STOP_LOSS * 100)}%")
        lines.append("سأراقب السعر وأنبّهك عند كل هدف أو عند وقف الخسارة.")
    lines.append(f"\n🔗 <a href=\"{res['pair_url']}\">عرض الرسم البياني</a>")
    lines.append("\n" + DISCLAIMER)
    return "\n".join(lines)


def tp_hit_msg(name, entry, price, level_idx):
    pct = int(TAKE_PROFITS[level_idx] * 100)
    gain = (price / entry - 1) * 100
    return (
        f"🎯 <b>تحقّق الهدف {level_idx + 1} (+{pct}%) — {html.escape(name)}</b>\n"
        f"السعر الآن: {fmt_price(price)} (ربح فعلي: +{gain:.1f}%)\n"
        f"💡 اقتراح: بِع جزءاً (30-50%) لتأمين الربح، وارفع وقف الخسارة إلى سعر دخولك.\n"
        f"سأستمر بمراقبة الأهداف المتبقية."
    )


def all_tp_msg(name, entry, price):
    gain = (price / entry - 1) * 100
    return (
        f"🏆 <b>اكتملت كل الأهداف — {html.escape(name)}</b>\n"
        f"من {fmt_price(entry)} إلى {fmt_price(price)} (ربح: +{gain:.1f}%)\n"
        f"أحسنت! سأتوقف عن متابعة هذه الصفقة. 🎉"
    )


def stop_loss_msg(name, entry, price):
    loss = (1 - price / entry) * 100
    return (
        f"🛑 <b>وقف الخسارة تفعّل — {html.escape(name)}</b>\n"
        f"السعر كسر -{int(STOP_LOSS * 100)}% من دخولك ({fmt_price(entry)} → {fmt_price(price)}، خسارة {loss:.1f}%)\n"
        f"💡 اقتراح: اخرج الآن لحماية رأس مالك. الصفقات الخاسرة جزء من اللعبة — المهم الالتزام بالخطة."
    )


def rug_warn_msg(name, price):
    return (
        f"🚨 <b>تحذير: {html.escape(name)} — إشارات خطر!</b>\n"
        f"السعر: {fmt_price(price)}\n"
        f"السيولة انهارت بشكل حاد أو ظهرت مشكلة في العقد.\n"
        f"💡 اقتراح: فكّر بالخروج الفوري إن كنت داخلاً."
    )


def digest_msg(date_str, positions, new_signals, movers, news):
    lines = [f"📰 <b>ملخص الكريبتو — {date_str}</b>"]
    if positions:
        lines.append("\n📌 <b>صفقات قيد المتابعة:</b>")
        for p in positions:
            pnl = (p["price"] / p["entry"] - 1) * 100
            icon = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"{icon} {html.escape(p['name'])}: {pnl:+.1f}%")
    else:
        lines.append("\n📌 لا صفقات قيد المتابعة حالياً.")
    lines.append(f"\n🔔 إشارات جديدة اليوم: <b>{new_signals}</b>")
    if movers:
        lines.append("\n🔥 <b>أكبر تحركات عملات الميم (Binance):</b>")
        for sym, chg in movers[:3]:
            icon = "📈" if chg >= 0 else "📉"
            lines.append(f"{icon} {sym}: {chg:+.1f}%")
    if news:
        lines.append("\n🗞️ <b>عناوين الأخبار:</b>")
        for title, link in news[:4]:
            lines.append(f"• <a href=\"{link}\">{html.escape(title[:90])}</a>")
    lines.append("\n" + DISCLAIMER)
    return "\n".join(lines)
