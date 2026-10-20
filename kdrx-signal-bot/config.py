# -*- coding: utf-8 -*-
"""إعدادات بوت إشارات Kdrx-style — كل القيم هنا قابلة للتعديل."""

import os

def _load_dotenv(path="/home/ubuntu/bot/.env"):
    """تحميل متغيرات البيئة من ملف .env — لأن cron لا يملكها في بيئته."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except FileNotFoundError:
        pass


_load_dotenv()

# أسماء بديلة للتوافق: GIST_ID/GH_PAT في .env ←→ KDRX_GIST_ID/GITHUB_TOKEN في الكود
if not os.environ.get("KDRX_GIST_ID") and os.environ.get("GIST_ID"):
    os.environ["KDRX_GIST_ID"] = os.environ["GIST_ID"]
if not os.environ.get("GITHUB_TOKEN") and os.environ.get("GH_PAT"):
    os.environ["GITHUB_TOKEN"] = os.environ["GH_PAT"]

# ---------- Binance (بيانات عمومية مجانية — بدون مفتاح) ----------
BINANCE_API = "https://data-api.binance.vision"
KLINES_ENDPOINT = f"{BINANCE_API}/api/v3/klines"
TICKER_ENDPOINT = f"{BINANCE_API}/api/v3/ticker/24hr"

# ---------- قائمة العملات (Top 50 USDT spot — سيولة عالية) ----------
WATCHLIST = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "MATICUSDT", "LTCUSDT", "ATOMUSDT", "NEARUSDT", "UNIUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT",
    "FILUSDT", "AAVEUSDT", "GRTUSDT", "SANDUSDT", "MANAUSDT",
    "AXSUSDT", "THETAUSDT", "VETUSDT", "ALGOUSDT", "FTMUSDT",
    "TRXUSDT", "ETCUSDT", "XLMUSDT", "HBARUSDT", "BONKUSDT",
    "SHIBUSDT", "PEPEUSDT", "WLDUSDT", "SEIUSDT", "TIAUSDT",
    "JUPUSDT", "ONDOUSDT", "FETUSDT", "RENDERUSDT", "ARUSDT",
    "DASHUSDT", "ZECUSDT", "EGLDUSDT", "TONUSDT", "NEIROUSDT",
]

TIMEFRAME = "4h"
KLINE_LIMIT = 350  # عدد الشموع لكل طلب (350 لاستقرار EMA200)

# ---------- السرعة (استغلال كامل للـVM — مجاني 100%) ----------
SCAN_WORKERS = 8          # عدد الخيوط المتوازية للفحص
SCAN_INTERVAL_MINUTES = 5  # فحص الإشارات كل 5 دقائق
MONITOR_INTERVAL_MINUTES = 2  # مراقبة المراكز (TP/SL) كل دقيقتين

# ---------- بوابة الإشارة ----------
MIN_SCORE = 55        # الحد الأدنى لقوة الإشارة (من 100)
# ملاحظة KDRX التجريبية (1398 صفقة مختومة): 37% من صفقات KDRX تُفتح بـ ADX<20 —
# لا بوابة صلبة على ADX في الإنتاج. يُبقى الثابت لمختبر الـbacktest فقط.
MIN_ADX = 20
SIGNAL_COOLDOWN_HOURS = 12  # لا إشارة جديدة لنفس الزوج قبل 12 ساعة

# ---------- المؤشرات ----------
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
ADX_PERIOD = 14
ATR_PERIOD = 14
BB_PERIOD, BB_STD = 20, 2.0
STOCH_RSI_PERIOD = 14
VOLUME_MA_PERIOD = 20

# ---------- المستويات — طريقة Kdrx المستخرجة من السجل المختوم (1398 صفقة) ----------
ATR_SL_MULT = 2.0     # وقف الخسارة = 2 × ATR (الوسيط التجريبي 1.86×)
TP_MULTIPLES = (1.0, 2.0, 3.0)  # سلم KDRX الثابت: 1R/2R/3R (الوسيط 0.88R/2R/3R)
SL_MIN_PCT = 0.025    # حد أدنى صلب للوقف: 2.5% من الدخول (مرصود: p10=p25=2.50%)
MIN_RR = 1.0          # مخاطرة/عائد الهدف الأول ≈ 1:1 (سلم 1R/2R/3R)

# ---------- المحفظة الورقية ----------
STARTING_CASH = 50.0   # $50 CHALLENGE
RISK_PER_TRADE = 0.05   # $50 CHALLENGE: 5% fixed (Kelly-optimal for max growth, backtest +0.142R)
MAX_POSITIONS = 5
# توزيع الإغلاق الجزئي (طريقة Kdrx: بيع 50% عند الهدف الأول + نقل الوقف للدخول)
TP1_CLOSE_PCT = 0.50    # عند الهدف 1: إغلاق 50% + نقل الوقف للدخول (صفقة بلا مخاطرة)
TP2_CLOSE_PCT = 0.30    # عند الهدف 2: إغلاق 30%
TP3_CLOSE_PCT = 0.20    # عند الهدف 3: إغلاق 20% المتبقية

# ---------- فلاتر Kdrx ----------
SUPPORT_LOOKBACK = 50       # عدد الشموع للبحث عن الدعم/المقاومة
SUPPORT_PROXIMITY_PCT = 0.03  # السعر قريب من الدعم: ضمن 3%

# ---------- نسخة Kdrx الكاملة (من البحث المباشر في قناته الرسمية) ----------
LONG_ONLY = True            # Kdrx: شراء فقط (سبوت — لا بيع)
ENTRY_ZONE_PCT = 0.015      # منطقة الدخول ±1.5% (مرصود: DASH 68.06 → 67.066–69.054)
RISK_MIN = 0.05              # $50 CHALLENGE: fixed 5% (no escalation, pure Kelly)
RISK_MAX = 0.05              # $50 CHALLENGE: fixed 5% (no escalation, pure Kelly)
MIN_BUYER_DOMINANCE = 0.484 # بوابة السيطرة: لا دخول إذا سيطرة البائعين > 51.6% (نظام الحيتان)
DOMINANCE_LOOKBACK = 20     # شموع لحساب سيطرة المشترين/البائعين

# ---------- منهجية KDRX التجريبية — مستخرجة من السجل المختوم (1398 صفقة، 2026-10-12) ----------
KDRX_RSI_LO = 45            # منطقة الدخول: RSI 45–60 (65% من صفقات KDRX — ليست تشبعاً بيعياً)
KDRX_RSI_HI = 60
KDRX_RSI_HARD_LO = 40       # بوابة صلبة أوسع للدخول
KDRX_RSI_HARD_HI = 68
KDRX_DIP_LOOKBACK = 20      # نافذة القمة/القاع (20 شمعة ≈ 80 ساعة على 4H)
KDRX_MIN_DIP_PCT = 0.03     # أدنى تراجع: 3% (منطقة KDRX المثلى 3-8% — باكتست 2026-10-17)
KDRX_MAX_DIP_PCT = 0.08     # أقصى تراجع: 8% (فوقها = زخم مكسور، تُرفض)
BTC_MOMENTUM_RSI_MIN = 50   # فلتر زخم السوق: لا شراء إلا إذا RSI(4H) ديال BTC فوق 50 (باكتست 2026-10-17: +0.053R)
KDRX_TIME_STOP_HOURS = 72   # وقف زمني: إلا TP1 ما تضربش في 18 شمعة (3 أيام) سكر الصفقة (باكتست RadoxAI 2026-10-17: +0.142R)
KDRX_EMA_TREND = 200        # فلتر الترند الصلب: الإغلاق فوق EMA200 (91.7% من الصفقات)

# ---------- Telegram ----------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = "https://api.telegram.org"

# ---------- التشغيل ----------
STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
WALLET_FILE = os.path.join(STATE_DIR, "wallet.json")
SIGNALS_FILE = os.path.join(STATE_DIR, "signals.json")
LOG_FILE = os.path.join(STATE_DIR, "bot.log")

# ---------- نسخ KDRX الحي (إشارات مُحوّلة من أيوب) ----------
KDRX_LIVE_WALLET_FILE = os.path.join(STATE_DIR, "kdrx_live.json")
KDRX_LIVE_RISK = 0.05          # مخاطرة افتراضية 5% إذا لم تُذكر في الإشارة
KDRX_LIVE_MAX_POSITIONS = 5

# ---------- الكون الديناميكي (أعلى سيولة — طلب أيوب 2026-10-12) ----------
UNIVERSE_SIZE = 200             # أعلى 200 زوج سبوت USDT سيولة (بدل القائمة الثابتة)
UNIVERSE_REFRESH_HOURS = 0      # تحديث الكون كل فحص (0 = دائماً طازج — طلب أيوب 2026-10-12)
UNIVERSE_FILE = os.path.join(STATE_DIR, "universe.json")
# عملات مستقرة ورموز رافعة تُستبعد دائماً من الكون
UNIVERSE_EXCLUDE = {
    "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT",
    "EURUSDT", "GBPUSDT", "BRLUSDT", "ARSUSDT", "MXNUSDT",
}

# ---------- فلتر الحيتان / التلاعب (طلب أيوب 2026-10-12) ----------
WHALE_VOL_MULT = 4.0            # رفض إذا حجم آخر شمعة مغلقة > 4× متوسط 20 شمعة (ضخ مشبوه)
WHALE_WICK_MULT = 2.0           # رفض إذا الفتيل العلوي > 2× جسم الشمعة (تلاعب/تصريف)
WHALE_RANGE_ATR_MULT = 2.5      # ويكون مدى الشمعة > 2.5× ATR (حركة غير طبيعية)

# ---------- خريطة RSI الحرارية المحلية (طلب أيوب 2026-10-12) ----------
HEATMAP_DAILY_RSI_MIN = 30      # رفض إذا RSI اليومي < 30 (سكين ساقط على الفريم الكبير)
HEATMAP_REFRESH_HOURS = 1       # تحديث الخريطة كل ساعة
HEATMAP_FILE = os.path.join(STATE_DIR, "heatmap.json")
HEATMAP_DAILY_LIMIT = 60        # شموع يومية لحساب RSI14

# $50 CHALLENGE (2026-10-20): fresh start, max growth mode
CHALLENGE_NAME = '$50 Challenge'
CHALLENGE_START_BALANCE = 50.0
CHALLENGE_START_DATE = '2026-10-20'
POSITION_PCT = 0.20  # $50 CHALLENGE: 20% of equity per position (5 max = 100%)
