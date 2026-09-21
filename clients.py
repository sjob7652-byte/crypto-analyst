# -*- coding: utf-8 -*-
"""عملاء مصادر البيانات المجانية: Dexscreener / honeypot.is / Binance."""
import time
import random
import requests
from config import (
    DEXSCREENER_API, HONEYPOT_API, HONEYPOT_CHAIN_IDS, RUGCHECK_API,
    BINANCE_API, CHAINS, REQUEST_TIMEOUT, USER_AGENT, BROWSER_UA,
    BACKOFF_TRIES, BACKOFF_BASE,
)

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
    """نبض السوق مع التحقق المتبادل: يقارن تغير BTC بين CoinGecko وBinance.
    إذا اتفق المصدران → الرقم موثوق. إذا تعارضا كثيراً → غير مؤكد
    (الخبير لا يبني عليه أي تعديل)."""
    cg = coingecko_macro()
    cg_chg = (cg or {}).get("btc_chg")
    bn_chg = None
    try:
        bn_chg = float((binance_ticker("BTCUSDT") or {}).get("priceChangePercent"))
    except (TypeError, ValueError):
        bn_chg = None
    btc_chg, verified = None, False
    if cg_chg is not None and bn_chg is not None:
        if abs(cg_chg - bn_chg) <= MACRO_VERIFY_MAX_DIFF:
            btc_chg = round((cg_chg + bn_chg) / 2, 2)
            verified = True
        # else: تعارض بين المصدرين → لا نثق بالرقم
    elif cg_chg is not None:
        btc_chg = cg_chg
    elif bn_chg is not None:
        btc_chg = bn_chg
    return {
        "btc": (cg or {}).get("btc"),
        "btc_chg": btc_chg,
        "eth_chg": (cg or {}).get("eth_chg"),
        "verified": verified,
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
                raw = urllib.request.urlopen(req, timeout=12).read()
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
