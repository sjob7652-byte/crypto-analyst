# -*- coding: utf-8 -*-
"""عملاء مصادر البيانات المجانية: Dexscreener / honeypot.is / Binance."""
import requests
from config import (
    DEXSCREENER_API, HONEYPOT_API, HONEYPOT_CHAIN_IDS, RUGCHECK_API,
    BINANCE_API, CHAINS, REQUEST_TIMEOUT, USER_AGENT,
)

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


def _get(url, params=None):
    try:
        r = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
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


# ---------- Binance (بيانات عمومية) ----------
def binance_ticker(symbol):
    return _get(f"{BINANCE_API}/api/v3/ticker/24hr", params={"symbol": symbol})


def binance_klines(symbol, interval="1h", limit=24):
    data = _get(f"{BINANCE_API}/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit})
    return data if isinstance(data, list) else []


# ---------- CoinGecko (مجاني بدون مفتاح) ----------
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta
from config import (COINGECKO_API, NEWS_FEEDS, NEWS_LOOKBACK_HOURS,
                    NEWS_MAX_ITEMS, USER_AGENT)


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


# ---------- الأخبار: عدة مصادر RSS مجانية ----------
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
    """يجلب الأخبار من عدة مصادر RSS ويحسب معنوياتها (إيجابي/سلبي)."""

    def __init__(self, feeds=None, hours=NEWS_LOOKBACK_HOURS,
                 max_items=NEWS_MAX_ITEMS):
        self.feeds = feeds if feeds is not None else NEWS_FEEDS
        self.hours = hours
        self.max_items = max_items
        self.items = []

    def fetch(self):
        items = []
        for name, url in self.feeds:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0"})
                raw = urllib.request.urlopen(req, timeout=12).read()
                root = ET.fromstring(raw)
                items += self._parse_rss(root, name)
                items += self._parse_atom(root, name)
            except Exception:
                continue
        # إزالة المكرر + تصفية حسب العمر + الأحدث أولاً
        seen, uniq = set(), []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.hours)
        for it in items:
            if it["link"] in seen or not it["title"]:
                continue
            seen.add(it["link"])
            if it["published"] and it["published"] < cutoff:
                continue
            it["sentiment"] = self.sentiment(it["title"])
            uniq.append(it)
        uniq.sort(key=lambda x: x["published"] or datetime.min.replace(
            tzinfo=timezone.utc), reverse=True)
        self.items = uniq[:self.max_items]
        return self.items

    def _parse_rss(self, root, name):
        out = []
        for item in root.iter("item"):
            out.append({
                "source": name,
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "published": self._parse_date(item.findtext("pubDate")),
            })
        return out

    def _parse_atom(self, root, name):
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

    def for_coin(self, symbol, name=""):
        """أخبار تذكر عملة معينة."""
        sym = (symbol or "").lower().replace("usdt", "")
        nm = (name or "").lower()
        out = []
        for it in self.items:
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
        """متوسط معنويات أخبار السوق العامة."""
        items = self.market_items()
        if not items:
            return 0.0, 0
        return sum(i["sentiment"] for i in items) / len(items), len(items)
