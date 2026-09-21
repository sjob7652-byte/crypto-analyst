# -*- coding: utf-8 -*-
"""الخبير: عقل البوت.

1. يترجم نتيجة التحليل إلى كلام بسيط: سعر الدخول / أسعار الخروج /
   نسبة نجاح تقديرية / سبب واضح لاختيار العملة.
2. ذاكرة تتعلم: يقارن النتائج السابقة حسب فئة النقاط، فيرفع أو يخفض
   نسبة النجاح حسب ما حصل فعلاً في الماضي (hit rate).
3. يأخذ الأخبار ونبض السوق العام بعين الاعتبار.
"""
from config import (TAKE_PROFITS, STOP_LOSS, MIN_SAMPLES_FOR_LEARNING,
                    WASH_RATIO_LIMIT, BUY_PRESSURE_STRONG, BUY_PRESSURE_WEAK,
                    FDV_LIQ_RATIO_LIMIT, TRENDING_PROB_BOOST)


def band_of(score):
    """فئة النقاط المستعملة في الذاكرة."""
    try:
        score = float(score)
    except (TypeError, ValueError):
        return "؟"
    if score >= 75:
        return "75+"
    if score >= 60:
        return "60-74"
    if score >= 40:
        return "40-59"
    return "أقل من 40"


def base_probability(score):
    """نسبة النجاح الأساسية من النقاط (قبل الأخبار والذاكرة)."""
    try:
        score = float(score)
    except (TypeError, ValueError):
        return 35
    if score >= 85:
        return 80
    if score >= 75:
        return 72
    if score >= 65:
        return 64
    if score >= 55:
        return 55
    if score >= 45:
        return 45
    return 35


def verdict_word(prob):
    if prob >= 70:
        return "فرصة قوية", "🟢"
    if prob >= 58:
        return "فرصة جيدة", "🟢"
    if prob >= 45:
        return "فرصة عادية", "🟡"
    return "مخاطرة عالية", "🔴"


def simple_reasons(res, coin_news, fng=None, trending=None):
    """أسباب بسيطة بكلمات سهلة — أقوى 3 إشارات فقط."""
    m = res.get("metrics") or {}
    out = []
    liq = m.get("liq") or 0
    vol = m.get("vol24") or 0
    pc1h = m.get("pc1h")
    pc24 = m.get("pc24h")

    if liq and vol and liq > 0 and vol / liq >= 3 \
            and vol / liq <= WASH_RATIO_LIMIT:
        out.append("تداول قوي عليها الآن (ناس كثيرة تشتري وتبيع)")
    if liq and vol and liq > 0 and vol / liq > WASH_RATIO_LIMIT:
        out.append("حجمها مشبوه مقارنة بسيولتها — كن حذراً")
    # ضغط الشراء مقابل البيع
    buys, sells = m.get("buys") or 0, m.get("sells") or 0
    if buys + sells > 0:
        bp = buys / (buys + sells)
        if bp >= BUY_PRESSURE_STRONG:
            out.append("المشترون أكثر بكثير من البائعين — طلب حقيقي عليها")
    if pc1h is not None and pc1h >= 10:
        out.append("السعر يصعد بسرعة في آخر ساعة")
    elif pc1h is not None and pc1h >= 3:
        out.append("السعر في صعود")
    elif pc24 is not None and pc24 >= 30:
        out.append(f"صاعدة بقوة اليوم (+{pc24:.0f}%)")
    elif pc24 is not None and pc24 >= 10:
        out.append(f"صاعدة اليوم (+{pc24:.0f}%)")
    if liq >= 100_000:
        out.append("سيولة كبيرة (صعب ينهار سعرها فجأة)")
    elif liq >= 30_000:
        out.append("سيولة جيدة")

    warnings = res.get("warnings") or []
    critical = any("⛔" in w for w in warnings)
    if not critical:
        out.append("لا توجد علامات نصب واضحة في العقد")

    if coin_news:
        pos = [n for n in coin_news if n.get("sentiment", 0) > 0.2]
        if pos:
            out.append("أخبار إيجابية عنها اليوم من مصادر موثوقة")

    if res.get("boosted"):
        out.append("فريقها يروّج لها الآن — اهتمام متزايد حولها")

    # رائجة على CoinGecko: ناس حقيقيون يبحثون عنها الآن
    sym = ((res.get("display") or "").split("/")[0] or "").upper()
    tr = {str(t).upper() for t in (trending or [])}
    if sym and sym in tr:
        out.append("رائجة الآن على CoinGecko — ناس كثيرة كتقلب عليها")

    # مؤشر الخوف والطمع: تحذير بسيط عند التطرف
    fng_v = None
    if isinstance(fng, dict):
        fng_v = fng.get("value")
    elif isinstance(fng, (int, float)):
        fng_v = fng
    if fng_v is not None:
        if fng_v <= 25:
            out.append("السوق خائف جداً الآن — كن حذراً أكثر")
        elif fng_v > 75:
            out.append("الطمع شديد في السوق — قد تكون الأسعار في قمة")

    # سبب واحد على الأقل دائماً
    if not out:
        out.append("اجتازت فلاتر الأمان والسيولة الأساسية")
    return "، ".join(out[:3])


def decide(res, coin_news, btc_chg, band_stats, fng=None, macro_verified=True,
           trending=None):
    """يبني القرار النهائي: أسعار + نسبة نجاح + سبب + خبر.

    res: نتيجة analyzer | coin_news: أخبار تذكر العملة (من مصادر موثوقة فقط)
    btc_chg: تغير BTC في 24س (نبض السوق) | band_stats: إحصائيات الذاكرة
    fng: مؤشر الخوف والطمع | macro_verified: هل رقم BTC مُتحقق من مصدرين؟
    trending: رموز العملات الرائجة على CoinGecko
    """
    m = res.get("metrics") or {}
    entry = float(m.get("price") or 0)
    tps = [entry * (1 + t) for t in TAKE_PROFITS]
    sl = entry * (1 - STOP_LOSS)
    score = res.get("score", 0)

    prob = base_probability(score)

    # 1) أخبار العملة نفسها (من مصادر موثوقة فقط)
    news_sent = 0.0
    if coin_news:
        news_sent = sum(n.get("sentiment", 0) for n in coin_news) / len(coin_news)
    if news_sent > 0.2:
        prob += 7
    elif news_sent < -0.2:
        prob -= 12

    # 2) نبض السوق العام — فقط إذا كان رقم BTC مُتحققاً من مصدرين
    if macro_verified and btc_chg is not None and btc_chg <= -5:
        prob -= 8

    # 2ب) مؤشر الخوف والطمع: التطرف في أي اتجاه = مخاطرة أعلى
    fng_v = None
    if isinstance(fng, dict):
        fng_v = fng.get("value")
    elif isinstance(fng, (int, float)):
        fng_v = fng
    if fng_v is not None:
        if fng_v <= 25:
            prob -= 6   # خوف شديد: بيع مذعور
        elif fng_v < 45:
            prob -= 3
        elif fng_v > 75:
            prob -= 6   # طمع شديد: السوق قد تكون في قمة
        elif fng_v >= 55:
            prob -= 2

    # 2ج) ضغط الشراء مقابل البيع: طلب حقيقي يرفع الاحتمال، هروب البائعين يخفضه
    buys, sells = m.get("buys") or 0, m.get("sells") or 0
    if buys + sells > 0:
        bp = buys / (buys + sells)
        if bp >= BUY_PRESSURE_STRONG:
            prob += 4
        elif bp <= BUY_PRESSURE_WEAK:
            prob -= 6

    # 2د) التداول الوهمي: حجم أضعاف السيولة = أرقام مضللة
    liq_v, vol_v = m.get("liq") or 0, m.get("vol24") or 0
    if liq_v > 0 and vol_v / liq_v > WASH_RATIO_LIMIT:
        prob -= 8

    # 2هـ) الترويج المدفوع: الفريق يستثمر في التسويق = استعداد لحركة سعرية
    if res.get("boosted"):
        prob += 3

    # 2و) القيمة السوقية مقابل السيولة: FDV ضخم = خطر إغراق
    fdv_v = m.get("fdv") or 0
    if liq_v > 0 and fdv_v / liq_v > FDV_LIQ_RATIO_LIMIT:
        prob -= 8

    # 2ز) رائجة على CoinGecko: اهتمام حقيقي من الناس
    sym_u = ((res.get("display") or "").split("/")[0] or "").upper()
    if sym_u and sym_u in {str(t).upper() for t in (trending or [])}:
        prob += TRENDING_PROB_BOOST

    # 3) ذاكرة الخبير: ماذا حصل فعلاً مع إشارات بنفس الفئة؟
    band = band_of(score)
    bs = (band_stats or {}).get(band)
    learned = None
    if bs and bs.get("n", 0) >= MIN_SAMPLES_FOR_LEARNING:
        learned = round(bs["hit_rate"] * 100)
        prob = round(prob * 0.65 + learned * 0.35)

    prob = max(5, min(95, int(prob)))
    word, emoji = verdict_word(prob)

    top_news = coin_news[0]["title"] if coin_news else None
    warn = None
    for w in (res.get("warnings") or []):
        if "⛔" in w:
            warn = w.replace("⛔", "").strip()
            break

    return {
        "entry": entry,
        "tps": tps,
        "sl": sl,
        "prob": prob,
        "word": word,
        "emoji": emoji,
        "band": band,
        "learned": learned,
        "reason": simple_reasons(res, coin_news, fng, trending),
        "news": top_news,
        "warn": warn,
        "fng": fng_v,
        "macro_verified": macro_verified,
    }
