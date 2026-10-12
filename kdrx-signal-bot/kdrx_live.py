# -*- coding: utf-8 -*-
"""نسخ KDRX الحي: أيوب يحوّل إشارة KDRX للبوت → تُفتح صفقة ورقية فورية
تُراقب (TP1: بيع 50% + وقف للتعادل، TP2: بيع 30%، TP3: الباقي) — محفظة
ورقية $100 منفصلة تماماً عن محفظة بوتنا وعن محاكاة السجل المختوم."""

import re
import time

import config
from wallet import PaperWallet
from executor import Executor

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_NUM_RE = r"\d[\d,\.]*"


def _norm(text):
    return text.translate(_AR_DIGITS)


def _num(s):
    try:
        return float(s.replace(",", "").replace("٬", "").strip())
    except Exception:
        return None


def looks_like_kdrx(text):
    """هل تبدو الرسالة إشارة KDRX (وليس أمراً عادياً)؟"""
    t = text
    tl = t.lower()
    has_signal = ("صفقة" in t) or ("توصية" in t) or ("kdrx" in tl)
    has_levels = (
        ("وقف" in t) or ("ستوب" in t) or ("هدف" in t)
        or re.search(r"\bSL\b", t) is not None
        or re.search(r"\bTP\d?\b", t) is not None
    )
    return bool(has_signal and has_levels)


def _after(text, pattern, window=90):
    """أول رقم بعد كلمة مفتاحية."""
    for m in re.finditer(pattern, text):
        seg = text[m.end():m.end() + window]
        nm = re.search(_NUM_RE, seg)
        if nm:
            v = _num(nm.group(0))
            if v:
                return v
    return None


def parse_kdrx_signal(text):
    """تحليل نص إشارة KDRX المُحوّلة. يُرجع (parsed, None) أو (None, سبب)."""
    t = _norm(text)
    head = t[:250]  # الترويسة فقط لتحديد الاتجاه (تجنّب "بيع 50%" في المتن)

    # ---- الرمز ----
    m = re.search(r"([A-Za-z]{2,12})\s*/\s*USDT\b", t)
    if not m:
        m = re.search(r"\b([A-Za-z]{2,12})USDT\b", t, re.IGNORECASE)
    if not m:
        return None, "لم أجد رمز العملة (مثال: ETH/USDT)"
    symbol = m.group(1).upper() + "USDT"

    # ---- الاتجاه ----
    direction = "long"
    if re.search(r"صفقة بيع|\bبيع\b|🔴|\bSHORT\b|\bSELL\b", head, re.IGNORECASE):
        direction = "short"

    # ---- الدخول ----
    entry = _after(t, r"الدخول|سعر الدخول|[Ee]ntry")
    if entry is None:
        zm = re.search(
            r"(?:منطقة|نطاق)[^\d]{0,25}(" + _NUM_RE + r")\s*[–\-—ـ]\s*(" + _NUM_RE + r")", t)
        if zm:
            a, b = _num(zm.group(1)), _num(zm.group(2))
            if a and b:
                entry = (a + b) / 2
    if entry is None:
        return None, "لم أجد سعر الدخول"

    # ---- وقف الخسارة ----
    sl = _after(t, r"وقف الخسارة|وقف|ستوب|\bSL\b|[Ss]top")
    if sl is None:
        return None, "لم أجد وقف الخسارة"

    # ---- الأهداف ----
    tps = []
    for kw in (r"الهدف الأول|هدف ?1|\bTP1\b",
               r"الهدف الثاني|هدف ?2|\bTP2\b",
               r"الهدف الثالث|هدف ?3|\bTP3\b"):
        v = _after(t, kw)
        if v:
            tps.append(v)
    if len(tps) < 3:
        mg = re.search(r"(?:الأهداف?|[Tt]argets?)[^\d\n]{0,12}((?:" + _NUM_RE + r"\s*[/|،,]\s*){1,}" + _NUM_RE + r")", t)
        if mg:
            nums = [_num(x) for x in re.findall(_NUM_RE, mg.group(1))]
            nums = [x for x in nums if x]
            if len(nums) >= 3:
                tps = nums[:3]
    if len(tps) < 3:
        return None, "لم أجد الأهداف الثلاثة"

    # ---- قوة الإشارة (اختياري) ----
    strength = 70
    ms = re.search(r"قوة[^\d]{0,10}(\d{1,3})\s*/\s*100", t)
    if ms:
        try:
            strength = max(1, min(100, int(ms.group(1))))
        except Exception:
            pass

    # ---- نسبة المخاطرة من الإشارة نفسها (إن ذُكرت) ----
    risk_frac = config.KDRX_LIVE_RISK
    mr = re.search(r"مخاطرة[^\d%]{0,30}(\d+\.?\d*)\s*%", t)
    if mr:
        try:
            rv = float(mr.group(1))
            if 1.0 <= rv <= 15.0:
                risk_frac = rv / 100.0
        except Exception:
            pass

    # ---- تحقق من ترتيب المستويات ----
    tp1, tp2, tp3 = tps[0], tps[1], tps[2]
    ok = (sl < entry < tp1 <= tp2 <= tp3) if direction == "long" \
        else (sl > entry > tp1 >= tp2 >= tp3)
    if not ok or min(entry, sl, tp1, tp2, tp3) <= 0:
        return None, "ترتيب المستويات غير منطقي (تحقق من الأرقام)"

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "strength": strength,
        "risk_frac": risk_frac,
        "created_at": int(time.time()),
        "source": "kdrx_forward",
    }, None


# ---------- المحفظة ----------

def get_wallet():
    w = PaperWallet(path=config.KDRX_LIVE_WALLET_FILE)
    w.data.setdefault("seen_signals", {})
    # حجز مبلغ الصفقة من النقد عند الفتح (طلب أيوب 2026-10-01) — خاص بهذه المحفظة فقط
    w.data.setdefault("reserve_cash", True)
    return w


def _fingerprint(p):
    return f"{p['symbol']}|{p['entry']}|{p['sl']}|{p['tp1']}"


def open_kdrx_trade(parsed):
    """فتح الصفقة الورقية. يُرجع (pos_id, position) أو (None, سبب)."""
    w = get_wallet()
    seen = w.data["seen_signals"]
    fp = _fingerprint(parsed)
    if fp in seen and time.time() - seen[fp] < 24 * 3600:
        return None, "duplicate"
    pos_id, res = w.open_position(parsed)
    if pos_id:
        seen[fp] = int(time.time())
        w.save()
        return pos_id, w.data["positions"][pos_id]
    return None, res


def check_positions():
    """فحص المراكز المفتوحة مقابل السعر الحي → أحداث TP/SL."""
    w = get_wallet()
    return Executor(w).check_positions()


def gist_state():
    """حالة النسخ الحي للداشبورد."""
    w = get_wallet()
    s = w.stats()
    return {
        "stats": s,
        "positions": w.open_positions(),
        "closed_trades": list(reversed(w.data.get("closed_trades", [])))[:30],
        "updated_at": int(time.time()),
    }


def describe_open_error(reason):
    return {
        "duplicate": "هذه الإشارة فُتحت من قبل (مكررة).",
        "max_positions": "وصلنا للحد الأقصى (5 مراكز مفتوحة).",
        "already_open": "يوجد مركز مفتوح لنفس الزوج.",
        "bad_sl": "وقف الخسارة غير صالح.",
        "insufficient_cash": "الرصيد لا يكفي لفتح الصفقة.",
    }.get(reason, f"سبب غير معروف: {reason}")
