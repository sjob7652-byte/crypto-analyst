# -*- coding: utf-8 -*-
"""عملاء مصادر البيانات المجانية: Dexscreener / honeypot.is / Binance."""
import time
import random
import re
import requests
from config import (
    DEXSCREENER_API, HONEYPOT_API, HONEYPOT_CHAIN_IDS, RUGCHECK_API,
    BINANCE_API, CHAINS, REQUEST_TIMEOUT, USER_AGENT, BROWSER_UA,
    BACKOFF_TRIES, BACKOFF_BASE, GECKOTERMINAL_API, GECKO_NETWORKS,
    GECKO_POOL_LIMIT, REDDIT_SUBS, REDDIT_LIMIT,
    SOURCE_FAILS_TO_COOL, SOURCE_COOLDOWN_BASE, SOURCE_COOLDOWN_MAX,
)

COINGECKO_API = "https://api.coingecko.com/api/v3"
COINPAPRIKA_API = "https://api.coinpaprika.com/v1"
KRAKEN_API = "https://api.kraken.com/0/public"
JUPITER_PRICE_API = "https://lite-api.jup.ag/price/v3"


# ---------- محرك سلاسل المصادر الاحتياطية (failover) ----------
def chain_try(s, key, candidates, need=None):
    """سلسلة مصادر احتياطية: جرّب المرشحين بالترتيب، الأول الذي يعيد
    بيانات صالحة يفوز. إذا فشل مصدر/انتهى حدّه → الانتقال للثاني فوراً.

    s: قاموس الحالة (تُحفظ فيه صحة المصادر: s["sources"][key][name])
    key: اسم السلسلة ("pair"، "trending"...)
    candidates: [(name, fn), ...] — fn بلا وسائط تعيد البيانات أو None
    need: دالة تحقق اختيارية data -> bool (البيانات الفارغة = فشل)
    يعيد (data, source_name) أو (None, None).

    الشفاء الذاتي: المصدر الذي يفشل SOURCE_FAILS_TO_COOL مرات متتالية
    يدخل تبريداً تلقائياً (يُتخطى حتى انتهاء التبريد، والتبريد يتضاعف
    عند التكرار حتى SOURCE_COOLDOWN_MAX) ثم يُعاد اختباره وحده."""
    src = s.setdefault("sources", {}).setdefault(key, {})
    now = time.time()
    for name, fn in candidates:
        h = src.get(name) or {}
        if now < float(h.get("cool_until") or 0):
            continue  # في تبريد — نتخطاه دون إضاعة طلب
        try:
            data = fn()
            ok = bool(data) and (need(data) if need else True)
        except Exception:
            ok, data = False, None
        if ok:
            h.update({"fails": 0, "cool_until": 0, "last_ok": now,
                      "uses": int(h.get("uses") or 0) + 1})
            src[name] = h
            for n2, h2 in src.items():  # مصدر واحد نشط فقط لكل سلسلة
                h2["active"] = (n2 == name)
            return data, name
        fails = int(h.get("fails") or 0) + 1
        if fails >= SOURCE_FAILS_TO_COOL:
            cd = min(SOURCE_COOLDOWN_BASE * (2 ** (fails - SOURCE_FAILS_TO_COOL)),
                     SOURCE_COOLDOWN_MAX)
            h["cool_until"] = now + cd
        h["fails"] = fails
        h["active"] = False
        src[name] = h
    return None, None

_session = requests.Session()
_session.headers.update({"User-Agent": BROWSER_UA,
                         "Accept": "application/json"})


def _get(url, params=None):
    """طلب GET مع حماية من الحظر المؤقت:
    - بصمة متصفح حقيقي (User-Agent) لتفادي حظر عناوين البوتات
    - Exponential Backoff عند 429/5xx: انتظار 3ث ثم 6ث ثم التخلي
    - الأخطاء الدائمة (404 وغيرها) لا تُعاد — لا فائدة"""
    wait = BACKOFF_BASE
    for _ in range(BACKOFF_TRIES):
        try:
            r = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(wait + random.uniform(0, 1))
                wait *= 2
                continue
            return None
        except Exception:
            time.sleep(wait + random.uniform(0, 1))
            wait *= 2
    return None


# ---------- Dexscreener ----------
def latest_profiles():
    """أحدث العملات التي أنشأت ملفاً على Dexscreener (مؤشر على عملات جديدة)."""
    data = _get(f"{DEXSCREENER_API}/token-profiles/latest/v1")
    if not isinstance(data, list):
        return []
    out = []
    for p in data:
        if p.get("chainId") in CHAINS and p.get("tokenAddress"):
            out.append((p["chainId"], p["tokenAddress"]))
    return out


def latest_boosts():
    """أحدث العملات المدفوعة الترويج (غالباً عملات تسوّق لنفسها)."""
    data = _get(f"{DEXSCREENER_API}/token-boosts/latest/v1")
    if not isinstance(data, list):
        return []
    out = []
    for p in data:
        if p.get("chainId") in CHAINS and p.get("tokenAddress"):
            out.append((p["chainId"], p["tokenAddress"]))
    return out


def top_boosts():
    """الأعلى ترويجاً مدفوعاً على Dexscreener (الأكثر سخونة الآن)."""
    data = _get(f"{DEXSCREENER_API}/token-boosts/top/v1")
    if not isinstance(data, list):
        return []
    out = []
    for p in data:
        if p.get("chainId") in CHAINS and p.get("tokenAddress"):
            out.append((p["chainId"], p["tokenAddress"]))
    return out


def geckoterminal_tokens():
    """عملات رائجة وجديدة من GeckoTerminal (مجاني بلا مفتاح، 30 طلب/دقيقة).

    لكل شبكة: trending_pools + new_pools -> عناوين العملات الأساسية.
    الفشل الصامت = المصدر يُتجاهل (لا يؤثر على باقي المصادر)."""
    out = []
    for net, chain in GECKO_NETWORKS.items():
        for kind in ("trending_pools", "new_pools"):
            try:
                data = _get(f"{GECKOTERMINAL_API}/networks/{net}/{kind}")
                items = (data or {}).get("data") or []
                for it in items[:GECKO_POOL_LIMIT]:
                    tid = (((it or {}).get("relationships") or {})
                           .get("base_token", {}).get("data") or {}).get("id")
                    if tid and "_" in tid:
                        addr = tid.split("_", 1)[1]
                        if addr:
                            out.append((chain, addr))
            except Exception:
                continue
    return out


def reddit_mentions():
    """ذكرات العملات ($TICKER) في أحدث منشورات Reddit (JSON عام بلا مفتاح).

    يعيد قاموس {TICKER: عدد الذكرات}. الفشل = قاموس فارغ (تجاهل صامت)."""
    counts = {}
    for sub in REDDIT_SUBS:
        try:
            data = _get(f"https://www.reddit.com/r/{sub}/new/.json",
                        params={"limit": REDDIT_LIMIT})
            posts = ((data or {}).get("data") or {}).get("children") or []
            for ch in posts:
                d = (ch or {}).get("data") or {}
                text = f"{d.get('title', '')} {d.get('selftext', '')}"
                for m in re.findall(r"\$([A-Za-z]{2,10})\b", text):
                    t = m.upper()
                    counts[t] = counts.get(t, 0) + 1
        except Exception:
            continue
    return counts


def stocktwits_sentiment(symbol):
    """مشاعر متداولي Stocktwits لعملة (API عام مجاني بلا مفتاح).

    يعيد {'bullish': n, 'bearish': n} أو None (غير مدرجة/فشل = تجاهل صامت)."""
    if not symbol:
        return None
    sym = symbol.upper().replace("USDT", "").strip()
    if not sym or len(sym) > 12:
        return None
    try:
        data = _get(f"https://api.stocktwits.com/api/2/streams/"
                    f"symbol/{sym}.X.json")
        msgs = (data or {}).get("messages") or []
        if not msgs:
            return None
        bull = bear = 0
        for m in msgs:
            s = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
            if s == "Bullish":
                bull += 1
            elif s == "Bearish":
                bear += 1
        if bull + bear == 0:
            return None
        return {"bullish": bull, "bearish": bear}
    except Exception:
        return None




def pairs_for_tokens(tokens):
    """يجلب أزواج التداول لعناوين العملات (حتى 30 عنواناً في الطلب الواحد)."""
    by_chain = {}
    for chain, addr in tokens:
        by_chain.setdefault(chain, [])
        if addr not in by_chain[chain]:
            by_chain[chain].append(addr)
    pairs = []
    for chain, addrs in by_chain.items():
        for i in range(0, len(addrs), 30):
            chunk = ",".join(addrs[i:i + 30])
            data = _get(f"{DEXSCREENER_API}/latest/dex/tokens/{chunk}")
            if data and isinstance(data.get("pairs"), list):
                pairs.extend(data["pairs"])
    return pairs


def get_pair(chain, pair_address):
    """يجلب بيانات زوج واحد (لتحديث سعر صفقة مفتوحة)."""
    data = _get(f"{DEXSCREENER_API}/latest/dex/pairs/{chain}/{pair_address}")
    try:
        return data["pairs"][0]
    except Exception:
        return None


def gecko_pool(chain, pool_address):
    """المصدر الاحتياطي الثاني لبيانات الزوج: GeckoTerminal.
    يعيد قاموساً موحّداً فيه priceUsd وliquidity.usd (أو None)."""
    net = GECKO_NETWORKS.get(chain)
    if not net:
        return None
    data = _get(f"{GECKOTERMINAL_API}/networks/{net}/pools/{pool_address}")
    try:
        a = data["data"]["attributes"]
        return {"priceUsd": a.get("base_token_price_usd"),
                "liquidity": {"usd": a.get("reserve_in_usd")},
                "volume": {"m5": a.get("volume_usd", {}).get("m5")},
                "txns": {"m5": {"buys": (a.get("transactions", {})
                                         .get("m5", {}).get("buys")),
                                "sells": (a.get("transactions", {})
                                          .get("m5", {}).get("sells"))},
                         "h1": {"buys": (a.get("transactions", {})
                                         .get("h1", {}).get("buys")),
                                "sells": (a.get("transactions", {})
                                          .get("h1", {}).get("sells"))}},
                "_src": "geckoterminal"}
    except Exception:
        return None


def pair_chain(s, chain, pair_address):
    """سلسلة بيانات الزوج: DexScreener → GeckoTerminal.
    إذا انتهى حدّ الأول أو سقط → الثاني يشتغل تلقائياً."""
    def _has_price(d):
        try:
            return float((d or {}).get("priceUsd") or 0) > 0
        except (TypeError, ValueError):
            return False
    return chain_try(
        s, "pair",
        [("dexscreener", lambda: get_pair(chain, pair_address)),
         ("geckoterminal", lambda: gecko_pool(chain, pair_address))],
        need=_has_price)


# ---------- فحص أمان العقد (مجاني) ----------
def token_security(chain, address):
    """يفحص: هل البيع مستحيل؟ ما الضرائب؟ ما مستوى الخطر؟
    EVM → honeypot.is | Solana → RugCheck"""
    if chain == "solana":
        return _solana_security(address)
    chain_id = HONEYPOT_CHAIN_IDS.get(chain)
    if not chain_id:
        return None
    data = _get(f"{HONEYPOT_API}/IsHoneypot",
                params={"address": address, "chainID": chain_id})
    if not data or "honeypotResult" not in data:
        return None
    hp = data.get("honeypotResult", {}) or {}
    sim = data.get("simulationResult", {}) or {}
    summary = data.get("summary", {}) or {}
    return {
        "is_honeypot": bool(hp.get("isHoneypot")),
        "buy_tax": _num(sim.get("buyTax")),
        "sell_tax": _num(sim.get("sellTax")),
        "risk": str(summary.get("risk", "")),
        "risk_level": _num(summary.get("riskLevel")),
        "holders": _num((data.get("token") or {}).get("totalHolders")),
        "lp_locked": 0,
    }


def _solana_security(mint):
    data = _get(f"{RUGCHECK_API}/v1/tokens/{mint}/report/summary")
    if not data or "risks" not in data:
        return None
    risks = data.get("risks") or []
    dangers = [r for r in risks if r.get("level") == "danger"]
    warns = [r for r in risks if r.get("level") == "warn"]
    names = " ".join((r.get("name") or "") for r in dangers).lower()
    return {
        "is_honeypot": "honeypot" in names,
        "buy_tax": 0,
        "sell_tax": 0,
        "risk": "high" if dangers else ("medium" if warns else "low"),
        "risk_level": 5 if dangers else (3 if warns else 1),
        "holders": 0,
        "lp_locked": _num(data.get("lpLockedPct")),
        "danger_names": [r.get("name") for r in dangers],
    }


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def rugcheck_report(mint):
    """التقرير الكامل من RugCheck (Solana): يتضمن topHolders بالنسب المئوية.
    مجاني وبلا مفتاح — يُستخدم لفحص تركيز الحيتان."""
    data = _get(f"{RUGCHECK_API}/v1/tokens/{mint}/report")
    return data if isinstance(data, dict) else None


def solana_top10_pct(mint):
    """تركيز أكبر 10 حاملين كنسبة من العرض (%) — عملات Solana فقط.
    يعيد None عند تعذّر الجلب (لا يُعاقب العملة على فشل الـAPI)."""
    rep = rugcheck_report(mint)
    try:
        holders = (rep or {}).get("topHolders") or []
        pcts = sorted((float(h.get("pct") or 0) for h in holders),
                      reverse=True)
        if not pcts:
            return None
        return round(sum(pcts[:10]), 2)
    except Exception:
        return None


def rugcheck_creator(mint):
    """عنوان مطور العملة (Solana) من تقرير RugCheck — مجاني وبلا مفتاح.
    يعيد None عند تعذّر الجلب أو عدم توفر المعلومة (لا يُعاقب العملة)."""
    rep = rugcheck_report(mint)
    try:
        c = (rep or {}).get("creator")
        return c if c else None
    except Exception:
        return None


# ---------- Binance (بيانات عمومية) ----------
def binance_ticker(symbol):
    return _get(f"{BINANCE_API}/api/v3/ticker/24hr", params={"symbol": symbol})


def binance_klines(symbol, interval="1h", limit=24):
    data = _get(f"{BINANCE_API}/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit})
    return data if isinstance(data, list) else []


# ---------- CoinGecko (مجاني بدون مفتاح) ----------
import json
import difflib
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta
from config import (COINGECKO_API, NEWS_FEEDS, NEWS_LOOKBACK_HOURS,
                    NEWS_MAX_ITEMS, USER_AGENT, BROWSER_UA, TIER_WEIGHTS,
                    NEWS_MIN_TITLE_LEN, NEWS_DEDUPE_SIM,
                    NEWS_MIN_TIER_FOR_COIN, FNG_API,
                    MACRO_VERIFY_MAX_DIFF)


def coingecko_trending():
    """العملات الرائجة الآن على CoinGecko (اكتشاف مبكر للاهتمام)."""
    data = _get(f"{COINGECKO_API}/search/trending")
    out = []
    try:
        for c in data["coins"]:
            it = c.get("item") or {}
            if it.get("symbol"):
                out.append({"symbol": str(it["symbol"]).upper(),
                            "name": it.get("name", "")})
    except Exception:
        pass
    return out[:10]


def coinpaprika_trending():
    """المصدر الاحتياطي الثاني للعملات الرائجة: CoinPaprika (بلا مفتاح).
    يرتب أكبر 100 عملة حسب تغير 24 ساعة ويعيد أول 10."""
    data = _get(f"{COINPAPRIKA_API}/tickers", params={"limit": 100})
    if not isinstance(data, list):
        return []
    rows = []
    for t in data:
        try:
            chg = float(((t.get("quotes") or {}).get("USD") or {})
                        .get("percent_change_24h") or 0)
            sym = str(t.get("symbol") or "").upper()
            if sym:
                rows.append((chg, {"symbol": sym, "name": t.get("name", "")}))
        except Exception:
            continue
    rows.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in rows[:10]]


def trending_chain(s):
    """سلسلة «الرائجة الآن»: CoinGecko → CoinPaprika."""
    return chain_try(
        s, "trending",
        [("coingecko", coingecko_trending),
         ("coinpaprika", coinpaprika_trending)],
        need=lambda d: len(d or []) > 0)


def coingecko_macro():
    """نبض السوق العام: سعر BTC وتغير 24س لـ BTC/ETH."""
    data = _get(f"{COINGECKO_API}/simple/price",
                params={"ids": "bitcoin,ethereum", "vs_currencies": "usd",
                        "include_24hr_change": "true"})
    if not isinstance(data, dict):
        return None
    try:
        return {
            "btc": float(data["bitcoin"]["usd"]),
            "btc_chg": float(data["bitcoin"].get("usd_24h_change") or 0),
            "eth_chg": float(data["ethereum"].get("usd_24h_change") or 0),
        }
    except Exception:
        return None


def kraken_btc():
    """المصدر الاحتياطي الثالث لنبض BTC: Kraken العام (بلا مفتاح).
    يعيد {"btc": السعر، "btc_chg": تغير 24س محسوب من متوسط السعر}."""
    data = _get(f"{KRAKEN_API}/Ticker", params={"pair": "XBTUSD"})
    try:
        t = data["result"]["XXBTZUSD"]
        last = float(t["c"][0])
        vwap24 = float(t["p"][0])
        chg = (last - vwap24) / vwap24 * 100 if vwap24 else 0
        return {"btc": last, "btc_chg": round(chg, 2)}
    except Exception:
        return None


def jupiter_price(mint):
    """سعر عملة Solana الاحتياطي: Jupiter Price API (بلا مفتاح).
    يُستخدم عندما يفشل جلب الزوج من المصدرين الأساسيين."""
    if not mint:
        return None
    data = _get(JUPITER_PRICE_API, params={"ids": mint})
    try:
        return float(data[mint]["usdPrice"])
    except Exception:
        return None


def fear_greed():
    """مؤشر الخوف والطمع للكريبتو (0-100) — مجاني بدون مفتاح."""
    try:
        req = urllib.request.Request(
            FNG_API + "?limit=1", headers={"User-Agent": BROWSER_UA})
        data = json.loads(urllib.request.urlopen(req, timeout=12).read().decode())
        d = data["data"][0]
        return {"value": int(d["value"]),
                "label": str(d.get("value_classification", ""))}
    except Exception:
        return None


def verified_macro():
    """نبض السوق مع التحقق المتبادل بين 3 مصادر: CoinGecko وBinance وKraken.
    إذا اتفق مصدران على الأقل (ضمن MACRO_VERIFY_MAX_DIFF) → الرقم موثوق.
    مصدر واحد فقط → يُستخدم دون توثيق. إذا سقط مصدر → الآخران يغطيانه."""
    cg = coingecko_macro()
    cg_chg = (cg or {}).get("btc_chg")
    bn_chg = None
    try:
        bn_chg = float((binance_ticker("BTCUSDT") or {}).get("priceChangePercent"))
    except (TypeError, ValueError):
        bn_chg = None
    kb = kraken_btc()
    kb_chg = (kb or {}).get("btc_chg")
    votes = [c for c in (cg_chg, bn_chg, kb_chg) if c is not None]
    btc_chg, verified = None, False
    if len(votes) >= 2:
        # أغلبية متفقة: ابحث عن زوج متفق ضمن الحد
        best = None
        for i in range(len(votes)):
            for j in range(i + 1, len(votes)):
                if abs(votes[i] - votes[j]) <= MACRO_VERIFY_MAX_DIFF:
                    avg = (votes[i] + votes[j]) / 2
                    if best is None or abs(votes[i] - votes[j]) < best[1]:
                        best = (avg, abs(votes[i] - votes[j]))
        if best is not None:
            btc_chg, verified = round(best[0], 2), True
        else:
            btc_chg = round(sum(votes) / len(votes), 2)  # تعارض → متوسط حذر
    elif votes:
        btc_chg = votes[0]
    return {
        "btc": (cg or {}).get("btc") or (kb or {}).get("btc"),
        "btc_chg": btc_chg,
        "eth_chg": (cg or {}).get("eth_chg"),
        "verified": verified,
        "macro_sources": sum(1 for c in (cg_chg, bn_chg, kb_chg)
                             if c is not None),
    }


# ---------- الأخبار: مصادر موثوقة بطبقات ثقة + تنقية ----------
POS_WORDS = [
    "etf approval", "approves etf", "all-time high", "record high", "ath",
    "rally", "bullish", "surge", "soar", "breakout", "adoption",
    "partnership", "institutional", "inflow", "upgrade successful",
]
NEG_WORDS = [
    "hack", "exploit", "rug", "lawsuit", "sec", "crash", "plunge",
    "scam", "fraud", "bankrupt", "dump", "fud", "outflow", "breach",
]
MARKET_WORDS = [
    "bitcoin", "btc", "ethereum", "eth", "crypto market", "cryptocurrency market",
    "etf", "fed", "interest rate", "inflation",
]


class NewsClient:
    """يجلب الأخبار من مصادر موثوقة مُصنّفة بطبقات ثقة، ويُنقّيها:
    - دمج الأخبار المتشابهة (نفس الخبر من عدة مصادر يُحسب مرة واحدة)
    - تجاهل العناوين القصيرة/الفارغة (ضجيج)
    - وزن المعنويات حسب ثقة المصدر (Bloomberg/CNBC أثقل من المدونات)
    """

    def __init__(self, feeds=None, hours=NEWS_LOOKBACK_HOURS,
                 max_items=NEWS_MAX_ITEMS):
        self.feeds = feeds if feeds is not None else NEWS_FEEDS
        self.hours = hours
        self.max_items = max_items
        self.items = []
        self.stats = {"sources_ok": 0, "sources_fail": 0,
                      "dupes_merged": 0, "dropped_short": 0}

    def fetch(self):
        items = []
        for feed in self.feeds:
            name, url = feed[0], feed[1]
            tier = feed[2] if len(feed) > 2 else 3
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": BROWSER_UA})
                # حد أقصى 2MB قبل التحليل: خلاصات RSS الحقيقية أصغر بكثير —
                # أي شيء أكبر = تضخيم كيانات XML خبيث (billion laughs) أو تلف
                raw = urllib.request.urlopen(req, timeout=12).read(2_000_000)
                root = ET.fromstring(raw)
                got = (self._parse_rss(root, name, tier)
                       + self._parse_atom(root, name, tier))
                if got:
                    self.stats["sources_ok"] += 1
                else:
                    self.stats["sources_fail"] += 1
                items += got
            except Exception:
                self.stats["sources_fail"] += 1
                continue
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.hours)
        filtered = []
        for it in items:
            if len(it["title"]) < NEWS_MIN_TITLE_LEN:
                self.stats["dropped_short"] += 1
                continue
            if it["published"] and it["published"] < cutoff:
                continue
            it["sentiment"] = self.sentiment(it["title"])
            filtered.append(it)
        self.items = self._dedupe(filtered)[:self.max_items]
        return self.items

    @staticmethod
    def _norm(title):
        """تطبيع العنوان للمقارنة: حروف/أرقام فقط بلا تشكيل زائد."""
        t = "".join(ch for ch in (title or "").lower()
                    if ch.isalnum() or ch.isspace())
        return " ".join(t.split())

    def _dedupe(self, items):
        """دمج الأخبار المتشابهة: نُبقي الأعلى ثقة (الأقدم عند التساوي)."""
        items.sort(key=lambda x: x["published"] or datetime.min.replace(
            tzinfo=timezone.utc), reverse=True)
        kept, norms = [], []
        for it in items:
            n = self._norm(it["title"])
            dup = -1
            for i, kn in enumerate(norms):
                if difflib.SequenceMatcher(None, n, kn).ratio() >= NEWS_DEDUPE_SIM:
                    dup = i
                    break
            if dup >= 0:
                self.stats["dupes_merged"] += 1
                if it["tier"] < kept[dup]["tier"]:
                    kept[dup], norms[dup] = it, n
            else:
                kept.append(it)
                norms.append(n)
        return kept

    def _parse_rss(self, root, name, tier):
        out = []
        for item in root.iter("item"):
            out.append({
                "source": name,
                "tier": tier,
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "published": self._parse_date(item.findtext("pubDate")),
            })
        return out

    def _parse_atom(self, root, name, tier):
        out = []
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.iter(ns + "entry"):
            link = ""
            for l in entry.iter(ns + "link"):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", "")
                    break
            out.append({
                "source": name,
                "tier": tier,
                "title": ((entry.findtext(ns + "title") or "").strip()),
                "link": link.strip(),
                "published": self._parse_date(
                    entry.findtext(ns + "published") or
                    entry.findtext(ns + "updated")),
            })
        return out

    @staticmethod
    def _parse_date(s):
        if not s:
            return None
        try:
            dt = parsedate_to_datetime(s.strip())
            if dt and not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    @staticmethod
    def sentiment(text):
        """معنويات العنوان: من -1 (سلبي جداً) إلى +1 (إيجابي جداً)."""
        t = (text or "").lower()
        pos = sum(1 for w in POS_WORDS if w in t)
        neg = sum(1 for w in NEG_WORDS if w in t)
        if pos + neg == 0:
            return 0.0
        return (pos - neg) / (pos + neg)

    def for_coin(self, symbol, name="", min_tier=NEWS_MIN_TIER_FOR_COIN):
        """أخبار تذكر عملة معينة — فقط من المصادر الموثوقة (الطبقات 1 و2)."""
        sym = (symbol or "").lower().replace("usdt", "")
        nm = (name or "").lower()
        out = []
        for it in self.items:
            if it.get("tier", 3) > min_tier:
                continue
            t = it["title"].lower()
            if (sym and len(sym) >= 2 and sym in t) or (nm and nm in t):
                out.append(it)
        return out

    def market_items(self):
        """أخبار السوق العامة (تؤثر على كل العملات)."""
        out = []
        for it in self.items:
            t = it["title"].lower()
            if any(w in t for w in MARKET_WORDS):
                out.append(it)
        return out

    def market_mood(self):
        """متوسط معنويات أخبار السوق العامة — مرجّح بثقة المصدر."""
        items = self.market_items()
        if not items:
            return 0.0, 0
        wsum = sum(TIER_WEIGHTS.get(i.get("tier", 3), 1.0) for i in items)
        mood = sum(i["sentiment"] * TIER_WEIGHTS.get(i.get("tier", 3), 1.0)
                   for i in items) / wsum
        return mood, len(items)
