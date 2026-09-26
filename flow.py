# -*- coding: utf-8 -*-
"""flow.py — محرك تدفق الأوامر اللحظي (Order Flow Firehose) + حارس السيولة.

المعمارية (Master Blueprint):
- اتصال websocket واحد ب‍ Binance (مجاني، مسموح به) يستقبل لحظة بلحظة
  لأعلى ~300 عملة فورية USDT:
    * <sym>@aggTrade  — كل صفقة منفذة (سعر/كمية/اتجاه)
    * <sym>@bookTicker — أفضل عرض/طلب (أخف 10x من عمق السوق الكامل)
- حلقات NumPy لكل عملة في RAM: صفقات (ts/سعر/كمية/اتجاه) + لقطات دفتر
  + عدّاد صفقات/ثانية — بلا قواعد بيانات قرصية بطيئة.
- حلقة حساب كل 100ms على العملات ذات البيانات الجديدة:
    * CVD_5m / CVD_15m بالدولار (بيع الحيتان سالب)
    * OBI = (bidQty-askQty)/(bidQty+askQty)
    * Volatility Z-Score = (صفقات/ث - متوسط 1ساعة) / انحراف 1ساعة
- آلة الحالة لكل عملة:
    * DANGER_WHALE_DUMP — بيع تراكمي قوي + دفتر منحاز للبيع (فيتو!)
    * WARNING          — إشارة واحدة قوية أو z>4 (مراقبة، بلا فيتو)
    * SAFE             — تدفق طبيعي
    * UNKNOWN          — بيانات قديمة/ناقصة (بلا فيتو أبداً)
- جسر IPC = ZeroMQ REP على tcp://127.0.0.1:5559 — البوت يسأل
  {"sym":"BONKUSDT"} فيجيب {"state":..,"cvd_5m":..,"obi":..,"z":..}
  في <5ms. غياب pyzmq = تخطي صامت (بلا فيتو، بلا crash).
- flow_status.json كل 5 ثوانٍ للداشبورد (لا يستطيع ZeroMQ).

عتبات الفيتو (محافظة عمداً):
    FLOW_CVD_VETO_USD = 50000.0   # صافي بيع 5د يتجاوز $50k
    FLOW_OBI_VETO     = -0.3      # دفتر منحاز للبيع
    Z_WARN            = 4.0        # انفجار نشاط

الذاكرة: RAM_BUDGET_GB=12 تُستخدم لتحديد سعة الحلقات عبر معادلة، مع سقف
نافع USEFUL_RING_CAP — بلا حشو مصطنع (يُطبع الـRSS الفعلي عند الإقلاع).

قواعد السلامة:
- لا شبكة إطلاقاً عند الاستيراد.
- خيط القراءة نحيف: json.loads + إلحاق فقط.
- إعادة اتصال بتراجع أسّي (5ث → 5د)؛ العملية لا تموت أبداً.
- التشغيل عبر systemd (quant-engine.service) بأولوية Nice=19.
"""
import json
import os
import threading
import time
import traceback
import urllib.request
from collections import deque

import numpy as np

try:
    import zmq
    _HAS_ZMQ = True
except Exception:
    zmq = None
    _HAS_ZMQ = False

# ---------------- الثوابت ----------------
RAM_BUDGET_GB = 12            # ميزانية الذاكرة القصوى للحلقات
USEFUL_RING_CAP = 100_000     # سقف نافع: يغطي >15د حتى لأكثر عملة نشاطاً
BOOK_RING_CAP = 120           # لقطات دفتر (الأحدث يُستخدم لـOBI)
SEC_RING_CAP = 3600           # عدّاد صفقات/ثانية لساعة كاملة (z-score)
FLOW_CVD_VETO_USD = 50000.0
FLOW_OBI_VETO = -0.3
Z_WARN = 4.0
MIN_TRADES_5M = 50
BIG_TRADE_USD = 10000.0
COMPUTE_TICK_S = 0.1
STATUS_EVERY_S = 5
BATCH_PER_TICK = 150
STALE_AFTER_S = 60
ZMQ_ADDR = "tcp://127.0.0.1:5559"
WS_BASE = "wss://stream.binance.com:9443/stream"
TOP_N_SYMBOLS = 300

FALLBACK_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT",
    "MEMEUSDT", "TURBOUSDT", "NEIROUSDT", "BRETTUSDT", "POPCATUSDT", "MEWUSDT",
    "TRXUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT", "LTCUSDT",
    "ATOMUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT",
    "SUIUSDT", "SEIUSDT", "JUPUSDT", "PYTHUSDT", "ONDOUSDT", "FETUSDT",
    "RENDERUSDT", "TAOUSDT", "FILUSDT", "AAVEUSDT", "UNIUSDT", "MKRUSDT",
    "SNXUSDT", "CRVUSDT", "SANDUSDT", "MANAUSDT", "AXSUSDT", "GALAUSDT",
    "CHZUSDT", "ENJUSDT", "1INCHUSDT", "COMPUSDT", "YFIUSDT", "SUSHIUSDT",
    "BATUSDT", "ZECUSDT", "DASHUSDT", "ETCUSDT", "BCHUSDT", "XLMUSDT",
]


def _home():
    return os.environ.get("HOME") or os.path.expanduser("~")


def _bot_path(name):
    env = os.environ.get("FLOW_" + name.upper().replace(".", "_"))
    if env:
        return env
    return os.path.join(_home(), "bot", name)


def _log(msg):
    try:
        with open(_bot_path("flow.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")
    except Exception:
        pass


def _rss_gb():
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        return round(pages * 4096 / 1e9, 3)
    except Exception:
        return None


def ring_cap_for(n_symbols, budget_gb=RAM_BUDGET_GB,
                 useful_cap=USEFUL_RING_CAP):
    """سعة حلقة الصفقات من ميزانية الذاكرة — مع سقف نافع يمنع الحشو."""
    bytes_per_trade = 4 * 8  # ts/price/qty/side كـ float64
    budget_cap = int(budget_gb * 1e9 / max(n_symbols, 1) / bytes_per_trade)
    return max(1000, min(budget_cap, useful_cap))


def fetch_top_symbols(limit=TOP_N_SYMBOLS):
    """استدعاء REST الوحيد: ترتيب العملات الفورية حسب حجم 24ساعة."""
    req = urllib.request.Request(
        "https://api.binance.com/api/v3/ticker/24hr",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    ranked = []
    for t in data:
        s = str(t.get("symbol") or "")
        if not s.endswith("USDT"):
            continue
        if s[:-4].endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue  # توكنات مدعومة
        try:
            qv = float(t.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            qv = 0.0
        ranked.append((qv, s))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in ranked[:limit]]


# ---------------- منطق نقي (قابل للاختبار بلا شبكة) ----------------

def parse_trade_msg(d):
    """aggTrade خام → (ts, price, qty, side) حيث side=+1 شراء / -1 بيع."""
    try:
        return (float(d["T"]) / 1000.0, float(d["p"]), float(d["q"]),
                1.0 if not d["m"] else -1.0)
    except (KeyError, TypeError, ValueError):
        return None


def parse_book_msg(d):
    """bookTicker خام → (ts, bid_p, bid_q, ask_p, ask_q) أو None."""
    try:
        return (time.time(), float(d["b"]), float(d["B"]),
                float(d["a"]), float(d["A"]))
    except (KeyError, TypeError, ValueError):
        return None


class TradeRing:
    """حلقة NumPy للصفقات: ts/price/qty/side كـ float64."""

    def __init__(self, cap):
        self.cap = cap
        self.ts = np.zeros(cap, dtype=np.float64)
        self.price = np.zeros(cap, dtype=np.float64)
        self.qty = np.zeros(cap, dtype=np.float64)
        self.side = np.zeros(cap, dtype=np.float64)
        self.head = 0
        self.count = 0

    def append(self, ts, price, qty, side):
        i = self.head
        self.ts[i] = ts
        self.price[i] = price
        self.qty[i] = qty
        self.side[i] = side
        self.head = (i + 1) % self.cap
        if self.count < self.cap:
            self.count += 1

    def window(self, cutoff):
        """مصفوفات مرتبة (الأقدم→الأحدث) حيث ts >= cutoff، أو None."""
        n = self.count
        if n == 0:
            return None
        if n < self.cap:
            ts, px, q, sd = (self.ts[:n], self.price[:n],
                             self.qty[:n], self.side[:n])
        else:
            idx = (np.arange(n, dtype=np.int64) + self.head) % self.cap
            ts, px, q, sd = (self.ts[idx], self.price[idx],
                             self.qty[idx], self.side[idx])
        m = ts >= cutoff
        if not np.any(m):
            return None
        return ts[m], px[m], q[m], sd[m]


class SecRing:
    """عدّاد صفقات/ثانية لساعة — أساس الـz-score."""

    def __init__(self, cap=SEC_RING_CAP):
        self.cap = cap
        self.buf = np.zeros(cap, dtype=np.float64)
        self.head = 0
        self.count = 0
        self.cur_sec = None
        self.cur_n = 0

    def _push(self, v):
        self.buf[self.head] = v
        self.head = (self.head + 1) % self.cap
        if self.count < self.cap:
            self.count += 1

    def add(self, ts):
        sec = int(ts)
        if self.cur_sec is None:
            self.cur_sec = sec
        if sec != self.cur_sec:
            self._push(float(self.cur_n))
            gap = sec - self.cur_sec - 1
            for _ in range(min(max(gap, 0), self.cap)):
                self._push(0.0)
            self.cur_sec = sec
            self.cur_n = 0
        self.cur_n += 1

    def zscore(self, short_s=5):
        """(معدل آخر ثوانٍ - متوسط الساعة) / انحراف الساعة."""
        n = self.count
        if n < 120:
            return None  # تاريخ ناقص
        if n < self.cap:
            a = self.buf[:n]
        else:
            a = np.concatenate((self.buf[self.head:], self.buf[:self.head]))
        short = float(a[-short_s:].mean()) if n >= short_s else float(a.mean())
        mean = float(a.mean())
        std = float(a.std())
        if std < 1e-9:
            return 0.0
        return (short - mean) / std


def compute_metrics(ring, book_latest, sec_ring, now):
    """مقاييس عملة من الحلقات — vectorized. ترجع dict أو None."""
    w = ring.window(now - 900.0)
    if w is None:
        return None
    ts, px, q, sd = w
    notion = px * q
    signed = notion * sd
    cvd15 = float(signed.sum())
    m5 = ts >= now - 300.0
    n5 = int(m5.sum())
    if n5 < MIN_TRADES_5M:
        return None
    cvd5 = float(signed[m5].sum())
    buy5 = float(notion[m5 & (sd > 0)].sum())
    tot5 = float(notion[m5].sum())
    big5 = float(notion[m5 & (notion >= BIG_TRADE_USD)].sum())
    obi = None
    if book_latest is not None:
        _, _, bq, _, aq = book_latest
        denom = bq + aq
        if denom > 0:
            obi = float((bq - aq) / denom)
    z = sec_ring.zscore() if sec_ring is not None else None
    return {
        "cvd_5m": round(cvd5, 2),
        "cvd_15m": round(cvd15, 2),
        "obi": round(obi, 4) if obi is not None else None,
        "buy_pressure": round(buy5 / tot5, 4) if tot5 > 0 else None,
        "big_trade_ratio": round(big5 / tot5, 4) if tot5 > 0 else None,
        "z": round(z, 3) if z is not None else None,
        "n_trades": n5,
        "vol_5m": round(tot5, 2),
        "computed_ts": now,
    }


def classify(cvd_5m, obi, z, has_data=True):
    """آلة الحالة: DANGER فقط باجتماع الدليلين — WARNING لا تمنع أبداً."""
    if not has_data or cvd_5m is None:
        return "UNKNOWN"
    if (cvd_5m < -FLOW_CVD_VETO_USD and obi is not None
            and obi < FLOW_OBI_VETO):
        return "DANGER_WHALE_DUMP"
    if ((z is not None and z > Z_WARN)
            or cvd_5m < -FLOW_CVD_VETO_USD
            or (obi is not None and obi < FLOW_OBI_VETO)):
        return "WARNING"
    return "SAFE"


# ---------------- المحرك الحي ----------------

class FlowEngine:
    def __init__(self, symbols):
        self.symbols = symbols
        self.cap = ring_cap_for(len(symbols))
        self.rings = {s: TradeRing(self.cap) for s in symbols}
        self.books = {s: deque(maxlen=BOOK_RING_CAP) for s in symbols}
        self.secs = {s: SecRing() for s in symbols}
        self.dirty = {s: False for s in symbols}
        self.latest = {}  # sym -> metrics (تُستبدل ذرياً — آمن للقراءة)
        self.msg_count = 0
        self.reconnects = 0
        self._stop = threading.Event()

    # --- المسار الساخن: نحيف عمداً (بلا I/O، بلا أقفال ثقيلة) ---
    def on_ws_message(self, raw):
        try:
            msg = json.loads(raw)
        except Exception:
            return
        d = msg.get("data")
        if not isinstance(d, dict):
            return
        sym = d.get("s")
        if sym not in self.rings:
            return
        stream = msg.get("stream") or ""
        if stream.endswith("@aggTrade"):
            t = parse_trade_msg(d)
            if t:
                ts, price, qty, side = t
                self.rings[sym].append(ts, price, qty, side)
                self.secs[sym].add(ts)
                self.dirty[sym] = True
                self.msg_count += 1
        elif stream.endswith("@bookTicker"):
            b = parse_book_msg(d)
            if b:
                self.books[sym].append(b)
                self.msg_count += 1

    def _compute_tick(self):
        now = time.time()
        batch = [s for s, f in self.dirty.items() if f][:BATCH_PER_TICK]
        for sym in batch:
            self.dirty[sym] = False
        for sym in batch:
            try:
                book_latest = self.books[sym][-1] if self.books[sym] else None
                m = compute_metrics(self.rings[sym], book_latest,
                                    self.secs[sym], now)
                if not m:
                    continue
                m["state"] = classify(m["cvd_5m"], m["obi"], m["z"])
                m["sym"] = sym
                self.latest[sym] = m  # استبدال ذري
            except Exception as e:
                _log(f"compute {sym}: {e}")

    def _compute_loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._compute_tick()
            except Exception as e:
                _log(f"compute loop: {e}\n{traceback.format_exc(limit=3)}")
            dt = time.time() - t0
            time.sleep(max(0.0, COMPUTE_TICK_S - dt))

    def _answer(self, sym, now=None):
        now = now if now is not None else time.time()
        m = self.latest.get(sym)
        if not m or now - m.get("computed_ts", 0) > STALE_AFTER_S:
            return {"state": "UNKNOWN", "cvd_5m": None,
                    "obi": None, "z": None}
        return {"state": m["state"], "cvd_5m": m["cvd_5m"],
                "obi": m["obi"], "z": m.get("z")}

    def _rep_loop(self, addr=None):
        """خيط ZeroMQ REP — يُتجاوز بصمت إذا غاب pyzmq."""
        if not _HAS_ZMQ:
            _log("pyzmq missing — ZeroMQ bridge disabled (no veto, no crash)")
            return
        ctx = zmq.Context()
        sock = ctx.socket(zmq.REP)
        sock.setsockopt(zmq.RCVTIMEO, 200)
        sock.setsockopt(zmq.LINGER, 0)
        sock.bind(addr or ZMQ_ADDR)
        _log(f"ZeroMQ REP on {addr or ZMQ_ADDR}")
        while not self._stop.is_set():
            try:
                req = sock.recv_json()
            except zmq.Again:
                continue
            except Exception:
                continue
            try:
                sym = str((req or {}).get("sym") or "").upper()
                sock.send_json(self._answer(sym))
            except Exception:
                try:
                    sock.send_json({"state": "UNKNOWN", "cvd_5m": None,
                                    "obi": None, "z": None})
                except Exception:
                    pass
        sock.close()

    def _status_loop(self):
        last_c, last_t = 0, time.time()
        while not self._stop.is_set():
            time.sleep(STATUS_EVERY_S)
            try:
                now = time.time()
                dt = max(now - last_t, 0.001)
                mps = (self.msg_count - last_c) / dt
                last_c, last_t = self.msg_count, now
                vetoes = sorted(
                    s for s, m in self.latest.items()
                    if m.get("state") == "DANGER_WHALE_DUMP"
                    and now - m.get("computed_ts", 0) <= STALE_AFTER_S)
                state = {"ts": now, "symbols": len(self.symbols),
                         "msg_per_sec": round(mps, 1),
                         "rss_gb": _rss_gb(), "vetoes": vetoes}
                p = _bot_path("flow_status.json")
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(state, f)
                os.replace(tmp, p)
            except Exception as e:
                _log(f"status loop: {e}")

    def _connect_loop(self):
        import websocket  # متأخر — لا شبكة عند import flow
        streams = "/".join(
            f"{s.lower()}@aggTrade/{s.lower()}@bookTicker"
            for s in self.symbols)
        url = f"{WS_BASE}/stream?streams={streams}"
        backoff = 5
        while not self._stop.is_set():
            try:
                _log(f"connecting ({len(self.symbols)} symbols, "
                     f"{len(self.symbols) * 2} streams, retry #{self.reconnects})")
                ws = websocket.WebSocketApp(url,
                                            on_message=self.on_ws_message)
                ws.run_forever(ping_interval=30, ping_timeout=10)
                self.reconnects += 1
                _log("disconnected — retrying")
            except Exception as e:
                self.reconnects += 1
                _log(f"ws error: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)

    def run(self):
        est_gb = (len(self.symbols) * self.cap * 32
                  + len(self.symbols) * SEC_RING_CAP * 8) / 1e9
        _log(f"rings: {len(self.symbols)} symbols x cap={self.cap} "
             f"(~{est_gb:.2f} GB budgeted of {RAM_BUDGET_GB} GB cap), "
             f"RSS at startup: {_rss_gb()} GB, zmq={'on' if _HAS_ZMQ else 'off'}")
        for target, name in ((self._compute_loop, "compute"),
                             (self._status_loop, "status"),
                             (self._rep_loop, "zmq-rep")):
            t = threading.Thread(target=target, daemon=True, name=f"flow-{name}")
            t.start()
        self._connect_loop()


def main():
    try:
        symbols = fetch_top_symbols()
        _log(f"ranked {len(symbols)} symbols via 24hr ticker")
    except Exception as e:
        _log(f"ranking failed ({e}) — fallback list")
        symbols = list(FALLBACK_SYMBOLS)
    if not symbols:
        symbols = list(FALLBACK_SYMBOLS)
    FlowEngine(symbols).run()


if __name__ == "__main__":
    main()
