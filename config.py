# -*- coding: utf-8 -*-
"""إعدادات محلل عملات الميم — عدّل العتبات هنا حسب رغبتك."""

# الشبكات الممسوحة (معرّفات Dexscreener)
CHAINS = ["bsc", "solana", "base", "ethereum"]

# عدد العملات الجديدة المفحوصة في كل دورة
SCAN_LIMIT = 20

# عتبات التصفية الأولية للعملات الجديدة (تنقية البيانات: حدود أعلى = ضجيج أقل)
MIN_LIQUIDITY_USD = 20_000
MIN_VOLUME_24H_USD = 10_000
MIN_TXNS_24H = 50        # حد أدنى لعدد صفقات آخر 24 ساعة
MAX_PAIR_AGE_DAYS = 30

# عتبات الإشارة (من 100)
SCORE_STRONG_BUY = 75
SCORE_BUY = 60
SCORE_WATCH = 40

# إدارة الصفقة (تنبيهات فقط — البوت لا يتداول بأموالك)
TAKE_PROFITS = [0.30, 0.70, 1.50]   # أهداف: +30% / +70% / +150%
STOP_LOSS = 0.25                    # وقف الخسارة: -25%
POSITION_MAX_AGE_DAYS = 7           # إغلاق المتابعة بعد 7 أيام

# عملات الميم المتابَعة على Binance (توقيت الدخول/الخروج)
WATCHLIST = [
    "DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT",
    "FLOKIUSDT", "POPCATUSDT", "PNUTUSDT", "BRETTUSDT", "MOGUSDT",
]

# ساعات الملخص اليومي (بتوقيت UTC — المغرب = UTC+1)
DIGEST_HOURS_UTC = (8, 20)

# عناوين الـ APIs المجانية
DEXSCREENER_API = "https://api.dexscreener.com"
HONEYPOT_API = "https://api.honeypot.is/v2"
RUGCHECK_API = "https://api.rugcheck.xyz"
BINANCE_API = "https://data-api.binance.vision"  # بيانات عمومية بدون مفتاح
NEWS_RSS = "https://cointelegraph.com/rss"  # (قديم — يُستخدم NEWS_FEEDS الآن)

# ---------- مصادر إضافية: عملات + أخبار ----------
USE_COINGECKO = True
COINGECKO_API = "https://api.coingecko.com/api/v3"  # مجاني بدون مفتاح

USE_NEWS = True
# ---------- تنقية البيانات: مصادر موثوقة بطبقات ثقة ----------
# الطبقة 1: إعلام مالي عالمي (الأعلى ثقة) — الطبقة 2: إعلام كريبتو متخصص معروف
# الطبقة 3: إعلام كريبتو عام (يُستخدم للمزاج العام فقط، لا لإشارات العملات)
TIER_WEIGHTS = {1: 3.0, 2: 2.0, 3: 1.0}
NEWS_FEEDS = [
    # الطبقة 1: صحافة مالية عالمية موثوقة ومشهورة
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss", 1),
    ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html", 1),
    # الطبقة 2: إعلام كريبتو متخصص ومعروف
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", 2),
    ("CoinTelegraph", "https://cointelegraph.com/rss", 2),
    ("The Block", "https://www.theblock.co/rss.xml", 2),
    ("Decrypt", "https://decrypt.co/feed", 2),
    ("CryptoSlate", "https://cryptoslate.com/feed/", 2),
    # الطبقة 3: مصادر كريبتو عامة
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/.rss/full/", 3),
    ("CoinJournal", "https://coinjournal.net/rss/", 3),
    ("Investing.com", "https://www.investing.com/rss/news_25.rss", 3),
]
NEWS_LOOKBACK_HOURS = 12   # أخبار آخر 12 ساعة فقط
NEWS_MAX_ITEMS = 40
NEWS_MIN_TITLE_LEN = 25    # تجاهل العناوين القصيرة/الفارغة (ضجيج)
NEWS_DEDUPE_SIM = 0.85     # دمج الأخبار المتشابهة فوق هذه النسبة
NEWS_MIN_TIER_FOR_COIN = 2  # أخبار العملات: فقط الطبقتان 1 و2 (الأكثر ثقة)

# ---------- تنقية البيانات: مؤشر الخوف والطمع + التحقق المتبادل للأسعار ----------
USE_FNG = True
FNG_API = "https://api.alternative.me/fng/"   # مجاني بدون مفتاح
MACRO_VERIFY_MAX_DIFF = 2.5  # أقصى فرق مقبول (نقطة مئوية) بين مصدري BTC

# ---------- إشارات الخبير الجديدة (2026-09-21) ----------
# 1) لائحة الانتظار: عملات "شبه جاهزة" (40-59 نقطة) تُعاد فحصها كل جولة
WAITLIST_MAX_AGE_H = 6    # أقصى مدة للبقاء في لائحة الانتظار
WAITLIST_MAX_SIZE = 30    # أقصى عدد عملات في اللائحة
WAITLIST_ADD_PER_RUN = 5  # إضافات جديدة في كل جولة كحد أقصى

# 6) الفرص الحقيقية في الساعات الأولى — عملة أقدم من هذا لا تُرسل كتنبيه جديد
NEW_ALERT_MAX_AGE_H = 48

# 3) كشف التداول الوهمي: حجم/سيولة فوق هذا الحد = مشبوه
WASH_RATIO_LIMIT = 20

# 2) ضغط الشراء/البيع
BUY_PRESSURE_STRONG = 0.65  # مشترون ≥ 65% = طلب حقيقي
BUY_PRESSURE_WEAK = 0.40    # مشترون ≤ 40% = الناس تهرب

# 4) فلتر الرموز المشبوهة: حروف خفية مرفوضة + رموز العملات الكبيرة
#    أي عملة جديدة تحمل رمز عملة مشهورة = مُقلّدة وغالباً نصب
ZERO_WIDTH_CHARS = set("​‌‍⁠﻿­⁣ㅤ")
KNOWN_SYMBOLS = {
    "BTC", "ETH", "USDT", "USDC", "SOL", "BNB", "XRP", "DOGE", "ADA",
    "TRX", "LINK", "AVAX", "XLM", "SUI", "HBAR", "LTC", "DOT", "BCH",
    "SHIB", "UNI", "PEPE", "NEAR", "APT", "ARB", "OP", "INJ", "ATOM",
    "FIL", "TAO", "RENDER", "FET", "WIF", "BONK", "FLOKI", "DAI",
    "WBTC", "WETH", "STETH", "TON", "ICP", "KAS", "CRO", "POL",
    "AAVE", "MKR", "SNX", "CRV", "LDO", "GRT", "SAND", "MANA",
}

# ---------- المحفظة الافتراضية (Paper Trading) — تجربة بلا مخاطرة ----------
PAPER_ENABLED = True
PAPER_START_BALANCE = 100.0   # رصيد البداية بالدولار (وهمي 100%)
PAPER_RISK_PER_TRADE = 10.0   # مبلغ كل صفقة وهمية
PAPER_MAX_POSITIONS = 5       # أقصى صفقات وهمية مفتوحة
# توزيع البيع الجزئي: TP1 → نصف الكمية، TP2 → نصف الباقي، TP3 → الباقي كله
PAPER_SELL_FRACTIONS = (0.5, 0.5, 1.0)
# الانزلاق السعري الواقعي: الميم كوينز فيها انزلاق كبير، نحسبو 3% عند كل تنفيذ
# (الشراء بسعر أغلى، والبيع بسعر أرخص) باش النتائج الوهمية تكون قريبة من الواقع
PAPER_SLIPPAGE = 0.03

# ---------- إشارات الخبير: الدفعة الثانية (2026-09-21) ----------
# 1) فحص القيمة السوقية: FDV/سيولة فوق هذا = خطر إغراق من الفريق
FDV_LIQ_RATIO_LIMIT = 50
# 2) عملة جديدة رائجة على CoinGecko = اهتمام حقيقي (+نقاط احتمال)
TRENDING_PROB_BOOST = 5
# 3) إعادة تقييم الصفقات: تحذير إذا نزلت النقاط تحت هذا بعد الدخول
POS_REEVAL_MIN_SCORE = 40
POS_REEVAL_MIN_AGE_H = 2   # لا إعادة تقييم قبل ساعتين من الدخول
# 4) كشف الضخ المفاجئ على Binance: حجم آخر ساعة × هذا مقابل المتوسط
VOL_SPIKE_MULT = 3
VOL_SPIKE_LOOKBACK = 12    # متوسط آخر 12 ساعة
VOL_SPIKE_COOLDOWN_H = 12

# ---------- الخبير: ذاكرة تتعلم من النتائج ----------
MIN_SAMPLES_FOR_LEARNING = 5  # أقل عدد نتائج سابقة ليعتمد عليها التعلم

# معرّف الشبكة لدى honeypot.is
HONEYPOT_CHAIN_IDS = {
    "ethereum": 1, "bsc": 56, "polygon": 137,
    "base": 8453, "arbitrum": 42161, "optimism": 10,
}

REQUEST_TIMEOUT = 20
USER_AGENT = "memecoin-analyst/1.0 (free-tier)"
# بصمة متصفح حقيقي: بعض الـAPIs تحظر عناوين البوتات المعروفة (429)
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# إستراتيجية إعادة المحاولة عند الحظر المؤقت (429/5xx): 3محاولات بانتظار 3ث ثم 6ث
BACKOFF_TRIES = 3
BACKOFF_BASE = 3  # ثواني — يتضاعف بعد كل فشل
