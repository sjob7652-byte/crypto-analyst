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
