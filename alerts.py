# -*- coding: utf-8 -*-
"""إرسال التنبيهات إلى Telegram — بلغة بسيطة وسهلة الفهم."""
import html
import os
import re
import time
import requests
from config import (REQUEST_TIMEOUT, TAKE_PROFITS, STOP_LOSS,
                    TG_MAX_RETRIES, TG_RETRY_BASE, TG_PENDING_MAX)

DISCLAIMER = ("⚠️ عملات الميم خطيرة جداً — خاطر بمبلغ صغير فقط "
              "تتحمّل خسارته كاملة. هذه ليست نصيحة مالية.")

DASHBOARD_BASE = "https://sjob7652-byte.github.io/crypto-analyst/dashboard/"


def _slug(pid):
    return re.sub(r"[^a-zA-Z0-9]+", "-", str(pid or "")).strip("-")


def wallet_link(res):
    """رابط المحفظة الوهمية — يفتح اللوحة مباشرة على صفقة هذه الإشارة."""
    gist = os.environ.get("GIST_ID", "")
    q = f"?gist={gist}" if gist else ""
    return f"{DASHBOARD_BASE}{q}#trade-{_slug(res.get('id'))}"


# طابور الرسائل الفاشلة: تُحفظ في الذاكرة وتُعاد محاولة إرسالها
# في بداية كل استدعاء لاحق — لا تضيع تنبيهات الانهيار في صمت
_PENDING = []


def _post_message(token, chat, text):
    """محاولة إرسال واحدة. تعيد (نجح؟, يستحق_إعادة؟)."""
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            return True, False
        # 429 (حظر مؤقت) و5xx = يستحق إعادة المحاولة
        if r.status_code == 429 or 500 <= r.status_code < 600:
            wait = TG_RETRY_BASE
            try:
                wait = max(wait, int(r.headers.get("Retry-After", wait)))
            except (TypeError, ValueError):
                pass
            time.sleep(wait)
            return False, True
        print(f"Telegram rejected ({r.status_code}): {r.text[:120]}")
        return False, False
    except Exception as e:
        print("Telegram error:", e)
        return False, True


def _flush_pending(token, chat):
    """يفرغ طابور الرسائل المعلقة قبل إرسال الجديدة."""
    while _PENDING:
        text = _PENDING[0]
        ok, _ = _post_message(token, chat, text)
        if not ok:
            break
        _PENDING.pop(0)
        print(f"  -> أُعيد إرسال رسالة معلقة (متبقٍ: {len(_PENDING)})")


def send(text, dry_run=False):
    """يرسل رسالة Telegram مع إعادة المحاولة والطابور.
    في وضع التجربة يطبع فقط."""
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
    _flush_pending(token, chat)
    wait = TG_RETRY_BASE
    for attempt in range(TG_MAX_RETRIES):
        ok, retryable = _post_message(token, chat, text)
        if ok:
            return True
        if not retryable or attempt == TG_MAX_RETRIES - 1:
            break
        time.sleep(wait)
        wait *= 2
    # فشل كل المحاولات: تُحفظ في الطابور (بحد أقصى) بدل الضياع
    if len(_PENDING) < TG_PENDING_MAX:
        _PENDING.append(text)
    else:
        _PENDING.pop(0)
        _PENDING.append(text)
    print(f"[!] تعذّر إرسال التنبيه بعد {TG_MAX_RETRIES} محاولات — "
          f"حُفظ في الطابور ({len(_PENDING)} معلقة)")
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
        f"💼 <a href=\"{wallet_link(res)}\">تابع هذه الصفقة في المحفظة الوهمية</a>",
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


def rug_pull_msg(name, entry, price, blacklisted=False):
    """رسالة الانهيار المفاجئ — تُستعمل بدل وقف الخسارة العادي عندما
    تتجاوز الخسارة 70% فجأة (يُرجح سحب سيولة)."""
    loss = (1 - price / entry) * 100
    bl = ("⛔ تمت إضافة العملة ومطورها إلى القائمة السوداء — "
          "لن تصلك إشارات منه مجدداً.\n" if blacklisted else "")
    return (
        f"🚨 <b>انهيار مفاجئ / سحب سيولة — {html.escape(name)}</b>\n"
        f"العملة انهارت فجأة (خسارة: -{loss:.1f}%). هذا ليس وقف خسارة "
        f"فنياً — المؤشرات توحي بسحب سيولة (Rug Pull).\n"
        f"دخلت بـ: {fmt_price(entry)} → الآن: {fmt_price(price)}\n"
        f"{bl}"
        f"💡 <b>درس:</b> العملات الجديدة جداً بسيولة ضعيفة هي الأخطر — "
        f"التزم دائماً بمبلغ صغير تتحمل خسارته."
    )


def paper_closed_msg(name, pnl_usd, pnl_pct, reason, cash):
    """نتيجة إغلاق صفقة وهمية — تُرسل مرة واحدة عند الإغلاق فقط."""
    icon = "🟢" if pnl_usd >= 0 else "🔴"
    return (
        f"💼 <b>المحفظة الافتراضية: أُغلقت صفقة {html.escape(name)}</b>\n"
        f"السبب: {reason}\n"
        f"{icon} النتيجة: {pnl_usd:+.2f}$ ({pnl_pct:+.1f}%)\n"
        f"💰 الرصيد النقدي الآن: ${cash:.2f}\n"
        f"<i>تجربة وهمية — ليست أموالاً حقيقية.</i>"
    )


def paper_tp_msg(name, level_idx, sold_pct, proceeds, realized, remaining_pct, cash):
    """تنبيه الجني الجزئي الحقيقي — يُرسل عند تنفيذ بيع جزئي فعلي في المحفظة الوهمية."""
    pct = int(TAKE_PROFITS[level_idx] * 100)
    return (
        f"🎯 <b>جني جزئي حقيقي — {html.escape(name)}</b>\n"
        f"وصل الهدف {level_idx + 1} (+{pct}%) — تم بيع <b>{sold_pct:.0f}%</b> من الصفقة فعلياً\n"
        f"💵 عائد البيع: ${proceeds:.2f}\n"
        f"🔒 ربح مُحقق لحد الآن: ${realized:+.2f}\n"
        f"📌 المتبقي في الصفقة: {remaining_pct:.0f}% (يكمل للأهداف الباقية)\n"
        f"💰 الرصيد النقدي الآن: ${cash:.2f}\n"
        f"<i>تجربة وهمية — ليست أموالاً حقيقية.</i>"
    )


def rug_warn_msg(name, price):
    return (
        f"🚨 <b>خطر! {html.escape(name)}</b>\n"
        f"السعر: {fmt_price(price)}\n"
        f"السيولة تنهار أو ظهرت مشكلة في العقد.\n"
        f"💡 <b>نصيحة:</b> اخرج فوراً إذا كنت داخلاً."
    )


def pos_deteriorated_msg(name, entry, price, old_score, new_score):
    """تحذير: مؤشرات صفقة مفتوحة ساءت بعد الدخول — فكر في الخروج المبكر."""
    try:
        pnl = (price / entry - 1) * 100
    except (TypeError, ZeroDivisionError):
        pnl = 0
    state = f"ربح +{pnl:.1f}%" if pnl >= 0 else f"خسارة {pnl:.1f}%"
    return (
        f"⚠️ <b>انتبه — {html.escape(name)}</b>\n"
        f"المؤشرات ساءت من بعد ما دخلت (النقاط: {old_score} ← {new_score}).\n"
        f"السعر الآن: {fmt_price(price)} ({state})\n"
        f"💡 <b>نصيحة:</b> فكّر تخرج بجزء قبل ما توصل لوقف الخسارة. "
        f"القرار لك."
    )


def unusual_volume_msg(name, chg, mult):
    """تنبيه حركة غير عادية: حجم مفاجئ على عملة Binance."""
    direction = "📈" if chg >= 0 else "📉"
    return (
        f"👀 <b>حركة غير عادية: {html.escape(name)}</b> {direction}\n"
        f"حجم التداول في آخر ساعة تضاعف <b>×{mult:.1f}</b> عن المتوسط — "
        f"شي حاجة كتوجد.\n"
        f"السعر: {chg:+.1f}% في 24 ساعة.\n"
        f"💡 <b>نصيحة:</b> راقبها عن قرب، ولا تدخل إلا بإشارة شراء واضحة."
    )


def digest_msg(date_str, positions, new_signals, movers, ctx):
    lines = [f"📰 <b>ملخص اليوم — {date_str}</b>"]

    macro = (ctx or {}).get("macro")
    if macro and macro.get("btc_chg") is not None:
        icon = "📈" if macro["btc_chg"] >= 0 else "📉"
        check = " ✓" if macro.get("verified") else ""
        btc_price = fmt_usd(macro["btc"]) if macro.get("btc") else "—"
        lines.append(f"\n{icon} <b>البيتكوين:</b> {btc_price} ({macro['btc_chg']:+.1f}% في 24س){check}")

    fng = (ctx or {}).get("fng")
    if fng and fng.get("value") is not None:
        v = fng["value"]
        icon = "😨" if v <= 25 else ("😡" if v > 75 else "😐")
        lines.append(f"{icon} <b>الخوف والطمع:</b> {v}/100 ({html.escape(str(fng.get('label', '')))})")

    ns = (ctx or {}).get("news_stats") or {}
    if ns.get("sources_ok"):
        q = f"📊 <b>جودة البيانات:</b> {ns['sources_ok']} مصادر موثوقة"
        if ns.get("dupes_merged"):
            q += f" • {ns['dupes_merged']} خبر مكرر مُزال"
        if macro and macro.get("verified"):
            q += " • BTC مُتحقق من مصدرين"
        lines.append("\n" + q)

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

    paper = (ctx or {}).get("paper")
    if paper:
        icon = "🟢" if paper["pnl"] >= 0 else "🔴"
        lines.append(
            f"\n💼 <b>المحفظة الافتراضية (تجربة):</b>\n"
            f"بدأنا بـ $100 ← القيمة الآن: <b>${paper['total']:.2f}</b> "
            f"(نقد: ${paper['cash']:.2f})\n"
            f"{icon} الربح/الخسارة: {paper['pnl']:+.2f}$ ({paper['pct']:+.1f}%)\n"
            f"🏆 الصفقات المغلقة: {paper['closed']} "
            f"(رابحة: {paper['wins']} • نسبة الفوز: {paper['winrate']:.0f}%)\n"
            f"📌 صفقات وهمية مفتوحة: {paper['open']}"
        )

    news = (ctx or {}).get("news_top") or []
    if news:
        lines.append("\n🗞️ <b>أهم الأخبار:</b>")
        for it in news[:4]:
            s = it.get("sentiment", 0)
            icon = "🟢" if s > 0.2 else ("🔴" if s < -0.2 else "⚪")
            lines.append(f"{icon} <a href=\"{it['link']}\">{html.escape(it['title'][:85])}</a>")

    lines.append("\n" + DISCLAIMER)
    return "\n".join(lines)
