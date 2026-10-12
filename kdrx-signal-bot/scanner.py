# -*- coding: utf-8 -*-
"""محرك الإشارات: جلب بيانات 4h من Binance → مؤشرات → نقاط 0-100 → إشارات."""

import time
import requests
import pandas as pd
import numpy as np

import config


# ================= جلب البيانات =================

def fetch_klines(symbol, interval=None, limit=None):
    """جلب شموع OHLCV من Binance (عمومي، بدون مفتاح)."""
    interval = interval or config.TIMEFRAME
    limit = limit or config.KLINE_LIMIT
    try:
        r = requests.get(
            config.KLINES_ENDPOINT,
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "taker_base", "taker_quote", "ignore",
        ])
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
        return df
    except Exception as e:
        print(f"[SCANNER] fetch failed {symbol}: {e}", flush=True)
        return None


def fetch_price(symbol):
    """السعر الحالي (آخر إغلاق)."""
    df = fetch_klines(symbol, limit=2)
    if df is None or len(df) == 0:
        return None
    return float(df["close"].iloc[-1])


def fetch_market_pulse():
    """جلب أسعار العملات الرئيسية مع تغير 24 ساعة (نبض السوق)."""
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    result = {}
    try:
        r = requests.get(
            config.TICKER_ENDPOINT,
            params={"symbols": str(symbols).replace("'", '"')},
            timeout=15,
        )
        # Binance API يقبل symbols كمصفوفة JSON
        if r.status_code == 400:
            # fallback: طلب واحد واحد
            for sym in symbols:
                try:
                    rr = requests.get(config.TICKER_ENDPOINT, params={"symbol": sym}, timeout=10)
                    if rr.status_code == 200:
                        d = rr.json()
                        result[sym] = {
                            "price": float(d.get("lastPrice", 0)),
                            "change_pct": float(d.get("priceChangePercent", 0)),
                        }
                    time.sleep(0.2)
                except Exception:
                    pass
            return result
        r.raise_for_status()
        for d in r.json():
            sym = d.get("symbol")
            if sym in symbols:
                result[sym] = {
                    "price": float(d.get("lastPrice", 0)),
                    "change_pct": float(d.get("priceChangePercent", 0)),
                }
    except Exception as e:
        print(f"[SCANNER] market pulse failed: {e}", flush=True)
    return result


# ================= المؤشرات (تطبيق يدوي — بدون مكتبات خارجية) =================

def rsi(series, period=None):
    period = period or config.RSI_PERIOD
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series, fast=None, slow=None, signal=None):
    fast = fast or config.MACD_FAST
    slow = slow or config.MACD_SLOW
    signal = signal or config.MACD_SIGNAL
    ema_fast = series.ewm(span=fast, min_periods=slow).mean()
    ema_slow = series.ewm(span=slow, min_periods=slow).mean()
    line = ema_fast - ema_slow
    sig = line.ewm(span=signal, min_periods=signal).mean()
    hist = line - sig
    return line, sig, hist


def adx(high, low, close, period=None):
    period = period or config.ADX_PERIOD
    up = high.diff()
    dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(alpha=1 / period, min_periods=period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(alpha=1 / period, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, min_periods=period).mean(), plus_di, minus_di


def atr(high, low, close, period=None):
    period = period or config.ATR_PERIOD
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def bollinger(series, period=None, std=None):
    period = period or config.BB_PERIOD
    std = std or config.BB_STD
    ma = series.rolling(period).mean()
    sd = series.rolling(period).std()
    return ma + std * sd, ma, ma - std * sd


def stoch_rsi(series, period=None):
    period = period or config.STOCH_RSI_PERIOD
    r = rsi(series, period)
    min_r = r.rolling(period).min()
    max_r = r.rolling(period).max()
    return (r - min_r) / (max_r - min_r).replace(0, np.nan) * 100


def compute_indicators(df):
    """حساب كل المؤشرات وإضافتها للـ DataFrame."""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    df["rsi"] = rsi(c)
    m_line, m_sig, m_hist = macd(c)
    df["macd_line"], df["macd_sig"], df["macd_hist"] = m_line, m_sig, m_hist
    df["adx"], df["plus_di"], df["minus_di"] = adx(h, l, c)
    df["atr"] = atr(h, l, c)
    df["ema9"] = c.ewm(span=9, min_periods=9).mean()
    df["ema21"] = c.ewm(span=21, min_periods=21).mean()
    df["ema50"] = c.ewm(span=50, min_periods=50).mean()
    df["ema200"] = c.ewm(span=config.KDRX_EMA_TREND, min_periods=config.KDRX_EMA_TREND).mean()
    bb_u, bb_m, bb_l = bollinger(c)
    df["bb_u"], df["bb_m"], df["bb_l"] = bb_u, bb_m, bb_l
    df["bb_pct"] = (c - bb_l) / (bb_u - bb_l).replace(0, np.nan)
    df["stoch_rsi"] = stoch_rsi(c)
    df["vol_ratio"] = v / v.rolling(config.VOLUME_MA_PERIOD).mean()
    return df


# ================= الدعم والمقاومة (طريقة Kdrx: الدخول قرب الدعم) =================

def find_support_resistance(df, lookback=None):
    """إيجاد أقرب دعم ومقاومة من القيعان/القمم المحلية."""
    lookback = lookback or config.SUPPORT_LOOKBACK
    recent = df.tail(lookback)
    price = float(df["close"].iloc[-2])  # آخر سعر مغلق

    # القيعان المحلية (دعم): low أقل من الجيران
    lows = recent["low"]
    supports = []
    for i in range(2, len(lows) - 2):
        if lows.iloc[i] < lows.iloc[i-1] and lows.iloc[i] < lows.iloc[i+1] \
           and lows.iloc[i] < lows.iloc[i-2] and lows.iloc[i] < lows.iloc[i+2]:
            supports.append(float(lows.iloc[i]))

    # القمم المحلية (مقاومة)
    highs = recent["high"]
    resistances = []
    for i in range(2, len(highs) - 2):
        if highs.iloc[i] > highs.iloc[i-1] and highs.iloc[i] > highs.iloc[i+1] \
           and highs.iloc[i] > highs.iloc[i-2] and highs.iloc[i] > highs.iloc[i+2]:
            resistances.append(float(highs.iloc[i]))

    # أقرب دعم تحت السعر، أقرب مقاومة فوق السعر
    support = max([s for s in supports if s < price], default=None)
    resistance = min([r for r in resistances if r > price], default=None)
    return support, resistance


def is_near_support(price, support):
    """هل السعر قريب من الدعم (ضمن النسبة المحددة)؟"""
    if support is None or support <= 0:
        return False
    return abs(price - support) / support <= config.SUPPORT_PROXIMITY_PCT


def is_near_resistance(price, resistance):
    """هل السعر قريب من المقاومة؟"""
    if resistance is None or resistance <= 0:
        return False
    return abs(price - resistance) / resistance <= config.SUPPORT_PROXIMITY_PCT


def risk_level(strength):
    """مستوى المخاطرة مثل Kdrx: قوة عالية = مخاطرة منخفضة."""
    if strength >= 80:
        return "منخفضة", "🟢"
    elif strength >= 65:
        return "متوسطة", "🟡"
    else:
        return "مرتفعة", "🔴"


def build_analysis_text(direction, rsi_val, support, resistance, vol_ratio, entry, dip_pct=None):
    """نص التحليل مثل Kdrx: الاتجاه + مؤشر القوة + الدعم + التراجع (+ الحجم معلومة)."""
    trend = "صاعد" if direction == "long" else "هابط"
    parts = [f"الاتجاه {trend} ومؤشر القوة {rsi_val:.0f}"]
    if direction == "long" and support:
        parts.append(f"والسعر قريب من دعم عند {_fmt(support)}")
    elif direction == "short" and resistance:
        parts.append(f"والسعر قريب من مقاومة عند {_fmt(resistance)}")
    if dip_pct is not None and dip_pct > 0:
        parts.append(f"بعد تراجع {dip_pct * 100:.1f}% من القمة")
    parts.append(f"حجم التداول ×{vol_ratio:.2f} المعدل.")
    return "📌 " + "، ".join(parts)


def _fmt(x):
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:.4f}"
    return f"{x:.6f}"


# ================= التقييم (0-100) — منهجية KDRX التجريبية =================
# مستخرج من 1398 صفقة KDRX مختومة (2026-10-12):
#  ترند EMA200 (30) + منطقة RSI ‏(25‏) + شراء التراجع (20) + قرب الدعم (15) + زخم ADX ‏(10‏)
#  شراء فقط (long-only). لا فلتر حجم، لا بوابة ADX صلبة.

def _dip_stats(row, df):
    """إحصائيات التراجع: (dip_pct من قمة 20 شمعة، المسافة لقاع 20 شمعة بوحدة ATR)."""
    try:
        n = config.KDRX_DIP_LOOKBACK
        hist = df.iloc[-2 - n:-2]  # آخر 20 شمعة مغلقة قبل شمعة الإشارة (بلا lookahead)
        if len(hist) < n:
            return None, None
        close = float(row["close"])
        atr_v = float(row["atr"])
        high20 = float(hist["high"].max())
        low20 = float(hist["low"].min())
        dip_pct = (high20 - close) / close if close > 0 else 0.0
        dist_low_atr = (close - low20) / atr_v if atr_v > 0 else 99.0
        return dip_pct, dist_low_atr
    except Exception:
        return None, None


def _kdrx_score(row, df):
    """تقييم KDRX ‏(0-100‏) لصفقة شراء. يُرجع (النقاط، التفاصيل)."""
    score, details = 0.0, {}
    close = float(row["close"])

    # 1) الترند: فوق EMA200 (91.7% من صفقات KDRX) — 30 نقطة
    ema200 = row.get("ema200")
    ema50 = row.get("ema50")
    if ema200 is not None and pd.notna(ema200) and close > float(ema200):
        score += 20
        details["trend200"] = "above EMA200"
    if ema50 is not None and pd.notna(ema50) and close > float(ema50):
        score += 10
        details["trend50"] = "above EMA50"

    # 2) RSI في المنطقة المحايدة 45-60 (65% من الصفقات) — 25 نقطة
    r = row.get("rsi")
    if r is not None and pd.notna(r):
        r = float(r)
        if config.KDRX_RSI_LO <= r <= config.KDRX_RSI_HI:
            score += 25
            details["rsi"] = f"neutral {r:.0f}"
        elif config.KDRX_RSI_HARD_LO <= r <= config.KDRX_RSI_HARD_HI:
            score += 12
            details["rsi"] = f"edge {r:.0f}"

    # 3) شراء التراجع: نزول 3-8% من قمة 20 شمعة (الوسيط 4.5%) — 20 نقطة
    dip_pct, dist_low_atr = _dip_stats(row, df)
    if dip_pct is not None:
        if 0.03 <= dip_pct <= 0.08:
            score += 20
            details["dip"] = f"-{dip_pct * 100:.1f}%"
        elif 0.02 <= dip_pct < 0.03 or 0.08 < dip_pct <= 0.13:
            score += 10
            details["dip"] = f"-{dip_pct * 100:.1f}%"

    # 4) قرب الدعم: الوقف ≈ 0.1 ATR تحت قاع 20 شمعة — 15 نقطة
    if dist_low_atr is not None:
        if dist_low_atr <= 1.0:
            score += 15
            details["support"] = f"{dist_low_atr:.1f} ATR"
        elif dist_low_atr <= 2.0:
            score += 8
            details["support"] = f"{dist_low_atr:.1f} ATR"

    # 5) زخم ADX (خفيف — لا بوابة صلبة: 37% من الصفقات بـ ADX<20) — 10 نقاط
    a = row.get("adx")
    pdi, mdi = row.get("plus_di"), row.get("minus_di")
    if a is not None and pd.notna(a) and pdi is not None and mdi is not None:
        a = float(a)
        if a > 25 and float(pdi) > float(mdi):
            score += 10
            details["adx"] = f"momentum {a:.0f}"
        elif a >= 20:
            score += 5

    return round(score), details


def _kdrx_hard_gates(row, df):
    """البوابات الصلبة للدخول (منهجية KDRX). تُرجع (مقبول؟، [الأسباب])."""
    reasons = []
    close = float(row["close"])

    ema200 = row.get("ema200")
    if ema200 is None or pd.isna(ema200) or close <= float(ema200):
        reasons.append("under EMA200")

    r = row.get("rsi")
    if r is None or pd.isna(r) or not (config.KDRX_RSI_HARD_LO <= float(r) <= config.KDRX_RSI_HARD_HI):
        reasons.append("RSI out of range")

    dip_pct, _ = _dip_stats(row, df)
    if dip_pct is None or dip_pct < config.KDRX_MIN_DIP_PCT:
        reasons.append("no dip")

    return (len(reasons) == 0), reasons


def score_row(row, df):
    """تقييم شمعة واحدة بمنهجية KDRX ‏(شراء فقط‏) → (القوة، الاتجاه، التفاصيل، الاتجاه)."""
    score, details = _kdrx_score(row, df)
    ok, _ = _kdrx_hard_gates(row, df)
    direction = "long" if (ok and score >= config.MIN_SCORE) else "neutral"
    return score, direction, details, direction


def buyer_dominance(df, lookback=None):
    """سيطرة المشترين من أحجام taker (تقريب نظام الحيتان عند Kdrx).

    Kdrx يمنع الدخول إذا سيطرة البائعين > 51.6% (مرصود من قناة المشغّل).
    """
    lookback = lookback or config.DOMINANCE_LOOKBACK
    try:
        recent = df.tail(lookback)
        taker_buy = recent["taker_base"].astype(float).sum()
        total = recent["volume"].astype(float).sum()
        if total <= 0:
            return 0.5
        return float(taker_buy / total)
    except Exception:
        return 0.5


def risk_fraction(strength):
    """حجم المخاطرة يتدرج مع قوة الإشارة: 3.09% → 6.67% (مرصود من إشارات Kdrx)."""
    span = 100 - config.MIN_SCORE
    f = (strength - config.MIN_SCORE) / span if span > 0 else 0
    f = max(0.0, min(1.0, f))
    return config.RISK_MIN + f * (config.RISK_MAX - config.RISK_MIN)


# ================= بناء الإشارة =================

def build_signal(symbol, df):
    """بناء إشارة KDRX من آخر شمعة مغلقة — بالمنهجية المستخرجة من السجل المختوم."""
    if df is None or len(df) < config.KDRX_EMA_TREND + 30:
        return None
    df = compute_indicators(df.copy())
    # آخر شمعة مغلقة = قبل الأخيرة (الأخيرة قد تكون جارية)
    row = df.iloc[-2]

    # تحقق من صحة المؤشرات الأساسية
    if pd.isna(row["atr"]) or pd.isna(row["rsi"]) or pd.isna(row["ema200"]):
        return None

    strength, direction, details, _ = score_row(row, df)

    # Kdrx: شراء فقط (long-only spot) + البوابات الصلبة التجريبية
    if direction != "long":
        return None
    if strength < config.MIN_SCORE:
        return None

    # بوابة السيطرة (نظام الحيتان — مرصود من أدوات Kdrx): لا دخول إذا البائعون مسيطرون
    dominance = buyer_dominance(df)
    if dominance < config.MIN_BUYER_DOMINANCE:
        return None

    entry = float(row["close"])
    atr_val = float(row["atr"])

    # الوقف: 2× ATR تحت الدخول (≈ تحت قاع 20 شمعة)، بحد أدنى 2.5% — تجريبي
    sl_dist = max(atr_val * config.ATR_SL_MULT, entry * config.SL_MIN_PCT)
    sl = entry - sl_dist

    # سلم KDRX الثابت: 1R / 2R / 3R
    tp1 = entry + sl_dist * config.TP_MULTIPLES[0]
    tp2 = entry + sl_dist * config.TP_MULTIPLES[1]
    tp3 = entry + sl_dist * config.TP_MULTIPLES[2]

    # منطقة الدخول ±1.5% (مثل Kdrx: الدخول 68.06، منطقة 67.066–69.054)
    zp = config.ENTRY_ZONE_PCT
    entry_lo, entry_hi = entry * (1 - zp), entry * (1 + zp)

    rr = round(config.TP_MULTIPLES[0], 2)  # سلم 1R/2R/3R
    rfrac = risk_fraction(strength)
    risk_pct = round(rfrac * 100, 2)

    # حقول Kdrx الإضافية
    risk_lbl, risk_emoji = risk_level(strength)
    rsi_val = float(row["rsi"]) if pd.notna(row["rsi"]) else 50
    vol_r = float(row["vol_ratio"]) if pd.notna(row["vol_ratio"]) else 1.0
    dip_pct, _ = _dip_stats(row, df)
    support = float(df.iloc[-2 - config.KDRX_DIP_LOOKBACK:-2]["low"].min())
    analysis = build_analysis_text("long", rsi_val, support, None, vol_r, entry, dip_pct)

    return {
        "symbol": symbol,
        "direction": "long",  # شراء فقط
        "timeframe": config.TIMEFRAME,
        "type": "spot",
        "strength": strength,
        "entry": round(entry, 6),
        "entry_zone": (round(entry_lo, 6), round(entry_hi, 6)),
        "sl": round(sl, 6),
        "tp1": round(tp1, 6),
        "tp2": round(tp2, 6),
        "tp3": round(tp3, 6),
        "rr": f"{rr:.0f} : 1",
        "risk_pct": risk_pct,
        "risk_frac": round(rfrac, 4),
        "adx": round(float(row["adx"]), 1),
        "atr": round(atr_val, 6),
        "dominance": round(dominance * 100, 1),
        "details": details,
        "candle_time": int(df["open_time"].iloc[-2]),
        "created_at": int(time.time()),
        # حقول Kdrx
        "risk_level": risk_lbl,
        "risk_emoji": risk_emoji,
        "rsi_value": round(rsi_val, 0),
        "support": round(support, 6) if support else None,
        "resistance": None,
        "vol_ratio": round(vol_r, 2),
        "analysis": analysis,
    }


def scan_all():
    """فحص كل العملات بشكل متوازي (سريع) → قائمة الإشارات."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    signals = []

    def scan_one(symbol):
        try:
            df = fetch_klines(symbol)
            if df is None:
                return None
            sig = build_signal(symbol, df)
            if sig:
                print(f"[SCANNER] SIGNAL {symbol} {sig['direction']} strength={sig['strength']}", flush=True)
            return sig
        except Exception as e:
            print(f"[SCANNER] error {symbol}: {e}", flush=True)
            return None

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=config.SCAN_WORKERS) as ex:
        futures = {ex.submit(scan_one, s): s for s in config.WATCHLIST}
        for f in as_completed(futures):
            sig = f.result()
            if sig:
                signals.append(sig)
    dt = time.time() - t0
    print(f"[SCANNER] Scanned {len(config.WATCHLIST)} pairs in {dt:.1f}s → {len(signals)} signals", flush=True)
    return signals


def scan_all_sequential():
    """فحص تسلسلي (احتياطي)."""
    signals = []
    for symbol in config.WATCHLIST:
        df = fetch_klines(symbol)
        if df is None:
            continue
        sig = build_signal(symbol, df)
        if sig:
            signals.append(sig)
            print(f"[SCANNER] SIGNAL {symbol} {sig['direction']} strength={sig['strength']}", flush=True)
        time.sleep(0.2)
    return signals


def analyze_asset(symbol):
    """تحليل فوري لأي عملة (مثل زر Kdrx: 📊 حلّل أصلك الآن).

    بمنهجية KDRX التجريبية (شراء فقط) — يُرجع الصورة الكاملة + حكم (منطقة شراء/انتظر/تجنّب).
    """
    df = fetch_klines(symbol)
    if df is None or len(df) < config.KDRX_EMA_TREND + 30:
        return None
    df = compute_indicators(df.copy())
    row = df.iloc[-2]  # آخر شمعة مغلقة
    if pd.isna(row["atr"]) or pd.isna(row["rsi"]) or pd.isna(row["ema200"]):
        return None

    strength, direction, details = _kdrx_score(row, df)
    gates_ok, gate_reasons = _kdrx_hard_gates(row, df)
    entry = float(row["close"])
    atr_val = float(row["atr"])
    sl_dist = max(atr_val * config.ATR_SL_MULT, entry * config.SL_MIN_PCT)
    dip_pct, _ = _dip_stats(row, df)
    support = float(df.iloc[-2 - config.KDRX_DIP_LOOKBACK:-2]["low"].min())
    rsi_val = float(row["rsi"]) if pd.notna(row["rsi"]) else 50.0
    vol_r = float(row["vol_ratio"]) if pd.notna(row["vol_ratio"]) else 1.0

    # المستويات (شراء فقط — سلم 1R/2R/3R)
    sl = entry - sl_dist
    tp1 = entry + sl_dist * config.TP_MULTIPLES[0]
    tp2 = entry + sl_dist * config.TP_MULTIPLES[1]
    tp3 = entry + sl_dist * config.TP_MULTIPLES[2]

    # الحكم — منطق Kdrx التجريبي
    if gates_ok and strength >= config.MIN_SCORE:
        verdict_emoji = "🟢"
        verdict = "منطقة شراء"
    elif strength >= 45:
        verdict_emoji = "🟡"
        verdict = "إشارات مختلطة — انتظر تأكيداً أوضح"
    else:
        verdict_emoji = "🔴"
        verdict = "لا توجد فرصة واضحة الآن — تجنّب"

    risk_lbl, risk_emoji = risk_level(strength)
    analysis = build_analysis_text("long", rsi_val, support, None, vol_r, entry, dip_pct)

    return {
        "symbol": symbol,
        "price": round(entry, 6),
        "direction": direction,
        "strength": strength,
        "details": details,
        "gate_reasons": gate_reasons,
        "rsi": round(rsi_val, 1),
        "vol_ratio": round(vol_r, 2),
        "support": round(support, 6),
        "resistance": None,
        "entry": round(entry, 6),
        "sl": round(sl, 6),
        "tp1": round(tp1, 6),
        "tp2": round(tp2, 6),
        "tp3": round(tp3, 6),
        "verdict_emoji": verdict_emoji,
        "verdict": verdict,
        "risk_level": risk_lbl,
        "risk_emoji": risk_emoji,
        "analysis": analysis,
        "at": int(time.time()),
    }


if __name__ == "__main__":
    print(f"[SCANNER] Scanning {len(config.WATCHLIST)} pairs on {config.TIMEFRAME}...", flush=True)
    sigs = scan_all()
    print(f"[SCANNER] Done. {len(sigs)} signals found.", flush=True)
    for s in sigs:
        print(f"  {s['symbol']} {s['direction']} score={s['strength']} entry={s['entry']} sl={s['sl']}", flush=True)
