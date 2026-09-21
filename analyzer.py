# -*- coding: utf-8 -*-
"""محرك التقييم: يحوّل بيانات العملة إلى نتيجة من 100 مع أسباب بالعربية."""
import time
from config import (
    SCORE_STRONG_BUY, SCORE_BUY, SCORE_WATCH,
    MIN_LIQUIDITY_USD, MIN_VOLUME_24H_USD, MAX_PAIR_AGE_DAYS,
    WASH_RATIO_LIMIT, HOLDER_TOP10_REJECT, ZERO_WIDTH_CHARS, KNOWN_SYMBOLS,
    FDV_LIQ_RATIO_LIMIT, SECURITY_FAILSAFE_REJECT,
)


def check_symbol(sym):
    """يفحص رمز العملة: يرفض الحروف الخفية/التحكم والرموز المُقلّدة لعملات مشهورة.
    يعيد (مقبول؟, السبب)."""
    s = (sym or "").strip()
    if not s:
        return False, "رمز العملة فارغ"
    if any(c in ZERO_WIDTH_CHARS for c in s):
        return False, "رمز العملة فيه حروف مخفية — علامة نصب"
    if any(ord(c) < 32 or ord(c) == 127 for c in s):
        return False, "رمز العملة فيه حروف غريبة — علامة نصب"
    if s.upper() in KNOWN_SYMBOLS:
        return False, f"تُقلّد رمز عملة مشهورة ({s.upper()}) — غالباً نصب"
    return True, ""


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def signal_of(score):
    if score >= SCORE_STRONG_BUY:
        return "STRONG_BUY"
    if score >= SCORE_BUY:
        return "BUY"
    if score >= SCORE_WATCH:
        return "WATCH"
    return "AVOID"


def analyze_pair(pair, security=None, boosted=False, holders=None,
                 security_unknown=False):
    """تقييم عملة جديدة من بيانات Dexscreener + فحص العقد.
    boosted: هل الفريق يروّج لها بترويج مدفوع على Dexscreener؟
    security_unknown: تعذّر فحص الأمان (API فشل) — الافتراض الآمن يرفضها فوراً."""
    reasons, warnings = [], []
    score = 0

    liq = _f((pair.get("liquidity") or {}).get("usd"))
    vol24 = _f((pair.get("volume") or {}).get("h24"))
    tx = (pair.get("txns") or {}).get("h24") or {}
    buys, sells = _f(tx.get("buys")), _f(tx.get("sells"))
    pc = pair.get("priceChange") or {}
    pc1h, pc24h = _f(pc.get("h1")), _f(pc.get("h24"))
    price = _f(pair.get("priceUsd"))
    fdv = _f(pair.get("fdv"))
    created = pair.get("pairCreatedAt") or 0
    age_h = (time.time() * 1000 - created) / 3_600_000 if created else 99999
    info = pair.get("info") or {}
    base = (pair.get("baseToken") or {}).get("symbol", "?")
    quote = (pair.get("quoteToken") or {}).get("symbol", "?")

    # 0) فلتر الرمز المشبوه — رفض فوري
    ok, why = check_symbol(base)
    if not ok:
        warnings.append(f"⛔ {why}")
        return _result(3, pair, base, quote, price, liq, vol24, pc1h, pc24h,
                       buys, sells, age_h, reasons, warnings)

    # 0ب) الافتراض الآمن (Fail-Safe): فشل فحص الأمان = عملة خطيرة ومرفوضة
    # (لا نتجاوز الفحص أبداً — الغياب التام للبيانات أخطر من البيانات السيئة)
    if security_unknown and SECURITY_FAILSAFE_REJECT:
        warnings.append("⛔ تعذّر فحص أمان العقد (API لا يستجيب) — "
                        "الافتراض الآمن: مرفوضة حتى يتوفر الفحص")
        return _result(3, pair, base, quote, price, liq, vol24, pc1h, pc24h,
                       buys, sells, age_h, reasons, warnings)

    # 1) السيولة (20)
    if liq >= 500_000:
        score += 20; reasons.append("سيولة قوية تتجاوز $500K — صعب التلاعب بالسعر")
    elif liq >= 200_000:
        score += 15; reasons.append("سيولة جيدة ($200K+)")
    elif liq >= 100_000:
        score += 10; reasons.append("سيولة مقبولة ($100K+)")
    elif liq >= MIN_LIQUIDITY_USD:
        score += 5
    else:
        warnings.append(f"سيولة ضعيفة (${liq:,.0f}) — خطر الانزلاق السعري")

    # 2) الحجم مقابل السيولة (15) — مع كشف التداول الوهمي
    ratio = vol24 / liq if liq > 0 else 0
    if 0.2 <= ratio <= 5:
        score += 15; reasons.append("حجم تداول صحي مقابل السيولة")
    elif 5 < ratio <= 15:
        score += 8; reasons.append("حجم مرتفع — زخم قوي حول العملة")
    elif ratio > WASH_RATIO_LIMIT:
        # تداول وهمي مؤكد: الحجم أضعاف السيولة بكثير = محافظ المطور تشتري
        # وتبيع لنفسها لخلق حركة مزيفة — رفض فوري بلا نقاط
        warnings.append(f"⛔ تداول وهمي: الحجم يعادل {ratio:.0f}x السيولة — "
                        "حركة مصطنعة بين محافظ المطور، مرفوضة فوراً")
        return _result(3, pair, base, quote, price, liq, vol24, pc1h, pc24h,
                       buys, sells, age_h, reasons, warnings)
    else:
        score += 3; warnings.append("حجم التداول غير متوازن مع السيولة")

    # 2ب) القيمة السوقية مقابل السيولة: FDV ضخم + سيولة صغيرة = خطر إغراق
    if liq > 0 and fdv > 0 and fdv / liq > FDV_LIQ_RATIO_LIMIT:
        score -= 8
        warnings.append("⚠ قيمتها السوقية كبيرة بزاف مقابل سيولتها "
                        "— الفريق يقدر يغرق السوق في أي لحظة")

    # 3) ضغط الشراء/البيع (10)
    total = buys + sells
    if total > 0:
        bp = buys / total
        if bp >= 0.58:
            score += 10; reasons.append(f"ضغط شراء واضح ({bp:.0%} من الصفقات شراء)")
        elif bp >= 0.50:
            score += 6
        else:
            score += 2; warnings.append("ضغط بيع يتفوق — الحذر واجب")

    # 4) الزخم السعري (10)
    if 0 < pc1h < 60:
        score += 10; reasons.append(f"زخم إيجابي (+{pc1h:.0f}% في آخر ساعة)")
    elif -15 <= pc1h <= 0:
        score += 5
    elif pc1h >= 60:
        score += 3; warnings.append(f"ارتفاع صاروخي (+{pc1h:.0f}%) — قد تشتري القمة")
    else:
        warnings.append(f"هبوط حاد ({pc1h:.0f}% في ساعة) — اتجاه سلبي")

    # 5) عمر الزوج (10)
    if 1 <= age_h <= 72:
        score += 10; reasons.append("عملة جديدة بعمر مناسب للفرص المبكرة")
    elif 72 < age_h <= 336:
        score += 6
    elif age_h < 1:
        score += 2; warnings.append("عمرها دقائق فقط — خطر النصب مرتفع جداً")
    else:
        score += 4

    # 6) أمان العقد (20)
    if security:
        if security.get("is_honeypot"):
            warnings.append("⛔ تنبيه خطير: محاكاة البيع فشلت — قد لا تستطيع بيعها أبداً")
            return _result(5, pair, base, quote, price, liq, vol24, pc1h, pc24h,
                           buys, sells, age_h, reasons, warnings)
        if (security.get("risk_level") or 0) >= 5:
            dn = security.get("danger_names") or []
            warnings.append("⛔ مخاطر حرجة في العقد: " + ", ".join(str(x) for x in dn[:3]))
            return _result(5, pair, base, quote, price, liq, vol24, pc1h, pc24h,
                           buys, sells, age_h, reasons, warnings)
        score += 12
        reasons.append("فحص العقد: البيع يعمل بشكل طبيعي")
        bt, stx = security.get("buy_tax", 0), security.get("sell_tax", 0)
        if bt <= 10 and stx <= 10:
            score += 5
            reasons.append(f"ضرائب منخفضة (شراء {bt:.0f}% / بيع {stx:.0f}%)")
        else:
            warnings.append(f"ضرائب مرتفعة (شراء {bt:.0f}% / بيع {stx:.0f}%) — تأكل الربح")
        if (security.get("risk_level") or 0) >= 4:
            warnings.append(f"مستوى خطر مرتفع حسب الفحص ({security.get('risk')})")
        else:
            score += 3
            if (security.get("lp_locked") or 0) >= 80:
                reasons.append("معظم السيولة مقفلة/محروقة — صعب سحبها")
    else:
        warnings.append("تعذّر فحص أمان العقد — اعتبرها مخاطرة إضافية")

    # 7) الحضور الرسمي (5)
    if (info.get("socials") or []) or (info.get("websites") or []):
        score += 5; reasons.append("لها موقع/حسابات رسمية — فريق ظاهر")
    else:
        warnings.append("لا موقع ولا حسابات رسمية موثقة")

    # 8) الترويج المدفوع (+5): الفريق يدفع للترويج = اهتمام متزايد واستعداد لحركة
    if boosted:
        score += 5
        reasons.append("الفريق يروّج لها الآن بترويج مدفوع — اهتمام متزايد حولها")

    # 9) تركيز الحيتان 🐋 (Solana — من تقرير RugCheck الكامل):
    # أكبر 10 محافظ فوق 20% من العرض = رفض فوري (خطر تفريغ جماعي)
    if holders is not None:
        if holders > HOLDER_TOP10_REJECT:
            warnings.append(f"⛔ تركيز خطير: أكبر 10 محافظ تملك {holders:.1f}% "
                            f"من العرض (فوق حد {HOLDER_TOP10_REJECT}%) — "
                            "المطور/الحيتان يقدرون يفرغون في أي لحظة")
            return _result(3, pair, base, quote, price, liq, vol24, pc1h, pc24h,
                           buys, sells, age_h, reasons, warnings,
                           boosted=boosted, fdv=fdv, holders=holders)
        elif holders >= 15:
            score -= 12
            warnings.append(f"تركيز حيتان متوسط: أكبر 10 محافظ تملك {holders:.1f}%")
        else:
            score += 3
            reasons.append(f"توزيع صحي للعرض: أكبر 10 محافظ تملك {holders:.1f}% فقط")

    return _result(max(0, min(100, score)), pair, base, quote, price, liq,
                   vol24, pc1h, pc24h, buys, sells, age_h, reasons, warnings,
                   boosted=boosted, fdv=fdv, holders=holders)


def _result(score, pair, base, quote, price, liq, vol24, pc1h, pc24h,
            buys, sells, age_h, reasons, warnings, boosted=False, fdv=0,
            holders=None):
    return {
        "score": score,
        "signal": signal_of(score),
        "reasons": reasons,
        "warnings": warnings,
        "boosted": boosted,
        "display": f"{base}/{quote}",
        "pair_url": pair.get("url", "https://dexscreener.com"),
        "metrics": {
            "price": price, "liq": liq, "vol24": vol24, "fdv": fdv,
            "pc1h": pc1h, "pc24h": pc24h,
            "buys": buys, "sells": sells, "age_h": age_h,
            "holders_top10": holders,
        },
    }


def analyze_binance(symbol, ticker, klines):
    """تقييم عملة ميم مدرجة على Binance لتوقيت الدخول/الخروج."""
    reasons, warnings = [], []
    score = 0
    chg = _f(ticker.get("priceChangePercent"))
    qvol = _f(ticker.get("quoteVolume"))
    last = _f(ticker.get("lastPrice"))
    name = symbol.replace("USDT", "")

    # 1) الزخم 24س (30)
    if 3 <= chg <= 40:
        score += 30; reasons.append(f"زخم صاعد +{chg:.1f}% خلال 24 ساعة")
    elif 0 <= chg < 3:
        score += 12
    elif chg > 40:
        score += 10; warnings.append(f"ارتفاع حاد +{chg:.0f}% — احذر الشراء في القمة")
    else:
        score += 2; warnings.append(f"هبوط {chg:.1f}% خلال 24 ساعة")

    # 2) حجم التداول (25)
    if qvol >= 50_000_000:
        score += 25; reasons.append("حجم تداول ضخم — اهتمام كبير")
    elif qvol >= 10_000_000:
        score += 18; reasons.append("حجم تداول قوي")
    elif qvol >= 2_000_000:
        score += 10
    else:
        score += 3; warnings.append("حجم تداول ضعيف — سيولة محدودة")

    # 3) الاتجاه على فريم الساعة (30)
    closes = [float(k[4]) for k in klines[-12:] if len(k) > 4]
    if len(closes) >= 6:
        ups = sum(1 for a, b in zip(closes, closes[1:]) if b > a)
        ratio = ups / (len(closes) - 1)
        if ratio >= 0.65:
            score += 30; reasons.append("اتجاه صاعد واضح على فريم الساعة")
        elif ratio >= 0.45:
            score += 18
        else:
            score += 5; warnings.append("اتجاه هابط على فريم الساعة")
        # 4) الهدوء النسبي (15)
        moves = [abs((b - a) / a) for a, b in zip(closes, closes[1:]) if a]
        if moves and max(moves) < 0.25:
            score += 15
        else:
            warnings.append("تقلبات عنيفة — الدخول مخاطرة")

    score = min(100, score)
    return {
        "score": score,
        "signal": signal_of(score),
        "reasons": reasons,
        "warnings": warnings,
        "display": f"{name}/USDT",
        "pair_url": f"https://www.binance.com/en/trade/{name}_USDT",
        "metrics": {"price": last, "liq": 0, "vol24": qvol,
                    "pc1h": 0, "pc24h": chg, "buys": 0, "sells": 0, "age_h": 99999},
    }
