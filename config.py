# -*- coding: utf-8 -*-
"""إعدادات محلل عملات الميم — عدّل العتبات هنا حسب رغبتك."""

# الشبكات الممسوحة (معرّفات Dexscreener)
CHAINS = ["bsc", "solana", "base", "ethereum"]

# عدد العملات الجديدة المفحوصة في كل دورة
SCAN_LIMIT = 20

# عتبات التصفية الأولية للعملات الجديدة
MIN_LIQUIDITY_USD = 10_000
MIN_VOLUME_24H_USD = 5_000
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
NEWS_RSS = "https://cointelegraph.com/rss"

# معرّف الشبكة لدى honeypot.is
HONEYPOT_CHAIN_IDS = {
    "ethereum": 1, "bsc": 56, "polygon": 137,
    "base": 8453, "arbitrum": 42161, "optimism": 10,
}

REQUEST_TIMEOUT = 20
USER_AGENT = "memecoin-analyst/1.0 (free-tier)"
