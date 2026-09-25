# -*- coding: utf-8 -*-
"""مخزن السوق المحلي: قاعدة بيانات DuckDB لسجلات الأسعار والأحجام والسيولة.

الهدف: تجميع تاريخ سوقي محلي يُغذي الباكتست (backtest.py) والمعايرة
(calibrate.py) — مجاني بالكامل، بلا مفاتيح API، بلا تسجيل، ويعمل على
معالجات ARM (عجلات DuckDB متوفرة لـ aarch64).

كل الدوال fail-safe: أي استثناء (بما فيه غياب مكتبة duckdb) يُسجَّل
ويُتجاهَل — المخزن مساعد بحثي، ومستحيل أن يُسقط الفحص الرئيسي.

المسار الافتراضي على الـVM: ~/bot/market.duckdb (يُضبط عبر متغير
البيئة MARKET_DB_PATH).
"""
import os
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ts DOUBLE,            -- epoch seconds
    chain TEXT,
    pair TEXT,            -- pair address (dex) أو الرمز (binance)
    symbol TEXT,
    price_usd DOUBLE,
    vol_24h_usd DOUBLE,
    liq_usd DOUBLE,
    mcap_usd DOUBLE,
    buys INTEGER,
    sells INTEGER,
    sentiment DOUBLE,     -- معنويات الأخبار وقت اللقطة (-1..1)
    source TEXT           -- scan | monitor | backfill
)
"""

# عملات الملء التاريخي: رموز Binance العامة (بلا مفتاح) + أزواج DEX معروفة
BACKFILL_BINANCE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT"]


def default_db_path():
    """~/bot/market.duckdb على الـVM — أو متغير البيئة عند ضبطه."""
    env = os.environ.get("MARKET_DB_PATH")
    if env:
        return env
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, "bot", "market.duckdb")


class MarketStore:
    """واجهة fail-safe فوق DuckDB. كل الطرق العامة لا ترفع استثناءً أبداً."""

    def __init__(self, path=None):
        self.path = path or default_db_path()
        self._con = None
        self._ok = None  # None = لم تُحسم بعد

    # ---------- داخلي ----------
    def _connect(self):
        if self._ok is not None:
            return self._con
        try:
            import duckdb
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            self._con = duckdb.connect(self.path)
            self._con.execute(_SCHEMA)
            self._ok = True
        except Exception as e:
            print(f"[store] تعطّل المخزن (سيُتخطى بصمت): {e}")
            self._con = None
            self._ok = False
        return self._con

    # ---------- كتابة ----------
    def record_snapshot(self, chain=None, pair=None, price_usd=None,
                        vol_24h_usd=None, liq_usd=None, mcap_usd=None,
                        buys=None, sells=None, sentiment=None, symbol=None,
                        source="scan"):
        """يسجل لقطة سوقية واحدة. يعيد True عند النجاح، False عند التخطي."""
        try:
            con = self._connect()
            if con is None or price_usd is None:
                return False
            try:
                price_usd = float(price_usd)
            except (TypeError, ValueError):
                return False
            if not (price_usd > 0):
                return False
            con.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [time.time(), chain, pair, symbol, price_usd,
                 _f(vol_24h_usd), _f(liq_usd), _f(mcap_usd),
                 _i(buys), _i(sells), _f(sentiment), source])
            return True
        except Exception as e:
            print(f"[store] record_snapshot skipped: {e}")
            return False

    # ---------- قراءة ----------
    def count(self):
        try:
            con = self._connect()
            if con is None:
                return 0
            return con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        except Exception:
            return 0

    def coins(self):
        """قائمة (chain, pair, symbol, n) لكل عملة مسجلة."""
        try:
            con = self._connect()
            if con is None:
                return []
            rows = con.execute(
                "SELECT chain, pair, symbol, COUNT(*) FROM snapshots "
                "GROUP BY chain, pair, symbol ORDER BY COUNT(*) DESC"
            ).fetchall()
            return [{"chain": r[0], "pair": r[1], "symbol": r[2],
                     "n": r[3]} for r in rows]
        except Exception:
            return []

    def history(self, chain=None, pair=None, since_ts=None, limit=5000):
        """سجل لقطات عملة مرتبة زمنياً — وقود الباكتست."""
        try:
            con = self._connect()
            if con is None:
                return []
            q = ("SELECT ts, chain, pair, price_usd, vol_24h_usd, liq_usd, "
                 "mcap_usd, buys, sells, sentiment, source "
                 "FROM snapshots WHERE 1=1")
            args = []
            if chain:
                q += " AND chain = ?"
                args.append(chain)
            if pair:
                q += " AND pair = ?"
                args.append(pair)
            if since_ts:
                q += " AND ts >= ?"
                args.append(since_ts)
            q += " ORDER BY ts ASC LIMIT %d" % int(limit)
            rows = con.execute(q, args).fetchall()
            keys = ("ts", "chain", "pair", "price", "vol24", "liq", "mcap",
                    "buys", "sells", "sentiment", "source")
            return [dict(zip(keys, r)) for r in rows]
        except Exception as e:
            print(f"[store] history skipped: {e}")
            return []

    def latest_ts(self, chain=None, pair=None):
        """أحدث طابع زمني مسجل لعملة — للملء التكميلي بلا تكرار."""
        try:
            con = self._connect()
            if con is None:
                return 0
            q = "SELECT MAX(ts) FROM snapshots WHERE 1=1"
            args = []
            if chain:
                q += " AND chain = ?"
                args.append(chain)
            if pair:
                q += " AND pair = ?"
                args.append(pair)
            r = con.execute(q, args).fetchone()[0]
            return r or 0
        except Exception:
            return 0

    # ---------- الملء التاريخي (مصادر مجانية فقط، بتهذيب) ----------
    def backfill(self, days=30, per_coin_sleep=1.0):
        """يجلب شموعاً تاريخية بالساعة للعملات الكبرى (Binance العام —
        بلا مفتاح) ويخزنها كمصدر backfill. تكميلي: يبدأ من حيث توقف
        آخر ملء (لا تكرار). مهذب: نوم بين العملات.
        يعيد عدد اللقطات المخزنة."""
        try:
            con = self._connect()
            if con is None:
                return 0
            import requests
        except Exception as e:
            print(f"[store] backfill skipped: {e}")
            return 0
        total = 0
        for sym in BACKFILL_BINANCE:
            try:
                since = self.latest_ts("binance", sym)
                klines = _binance_klines(requests, sym, days, since)
                for k in klines:
                    # k: [open_t, o,h,l,c, vol, close_t, ...] — vol هنا
                    # بالعملة الأساسية؛ نُقدّر vol_usd ≈ vol × close
                    close = float(k[4])
                    vol_usd = float(k[5]) * close
                    self.record_snapshot(
                        chain="binance", pair=sym, symbol=sym,
                        price_usd=close, vol_24h_usd=vol_usd,
                        source="backfill")
                    total += 1
                print(f"[store] backfill {sym}: {len(klines)} شمعة")
            except Exception as e:
                print(f"[store] backfill {sym} skipped: {e}")
            try:
                time.sleep(per_coin_sleep)
            except Exception:
                pass
        return total

    def close(self):
        try:
            if self._con is not None:
                self._con.close()
        except Exception:
            pass
        self._con = None
        self._ok = None


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _binance_klines(requests, symbol, days, since_ts=0):
    """شموع الساعة من Binance العام — بلا مفتاح، حد 1000 شمعة/طلب.
    since_ts: epoch ثواني — يُجلب ما بعده فقط (ملء تكميلي)."""
    need = min(int(days * 24), 1000)
    out = []
    end = None
    params_base = {"symbol": symbol, "interval": "1h", "limit": 1000}
    if since_ts and since_ts > 0:
        # ابدأ بعد آخر شمعة مخزنة بقليل (هامش تداخل شمعة واحدة)
        params_base["startTime"] = int(since_ts * 1000) - 3_600_000
    while len(out) < need:
        params = dict(params_base)
        if end:
            params["endTime"] = end
        r = requests.get("https://api.binance.com/api/v3/klines",
                         params=params, timeout=20)
        if r.status_code != 200:
            break
        batch = r.json()
        if not batch:
            break
        out = batch + out
        end = int(batch[0][0]) - 1
        if len(batch) < 1000:
            break
        time.sleep(0.3)  # تهذيب إضافي داخل الحلقة
    return out[-need:]
