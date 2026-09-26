# -*- coding: utf-8 -*-
"""عامل البث المباشر: دفق Binance websocket للعملات الميمية لحظة بلحظة.

ماذا يفعل (ولماذا هو مفيد، لا حرق عبثي):
- يتصل بدفق Binance العام المجاني (websocket — مسموح به، بلا حدود
  عدوانية عكس الـREST) ويستقبل كل ثانية:
    * !miniTicker@1000ms — سعر/حجم كل العملات الميمية الـ12
    * <sym>@aggTrade — كل صفقة منفذة (سعر/كمية/اتجاه شراء-بيع)
- يخزن الصفقات في قاعدة منفصلة ~/bot/ticks.duckdb (لا يمس market.duckdb —
  قاعدة كاتب واحد)، ويحسب إحصاءات متدحرجة 1د/5د (تغير السعر، عدد
  الصفقات، ضغط الشراء) — وقود استخباراتي للباكتست والمعايرة لاحقاً.
- يكتب حالة حية في ~/bot/stream_status.json يقرؤها المراقب والداشبورد.

قواعد السلامة:
- لا شبكة عند الاستيراد إطلاقاً (كل شيء في main()/run()).
- إعادة اتصال بتراجع أسّي عند أي انقطاع؛ العملية لا تموت أبداً.
- كتابة DuckDB دُفعات كل 5 ثوانٍ + تقليم بيانات أقدم من 6 ساعات.
"""
import json
import os
import sys
import threading
import time
import traceback
from collections import deque

# العملات الميمية المراقبة (رموز Binance الفورية)
SYMBOLS = ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT",
           "FLOKIUSDT", "MEMEUSDT", "TURBOUSDT", "NEIROUSDT", "BRETTUSDT",
           "POPCATUSDT", "MEWUSDT"]

WS_BASE = "wss://stream.binance.com:9443/stream"
FLUSH_EVERY_S = 5          # دفعة كتابة DuckDB
STATUS_EVERY_S = 10        # كتابة ملف الحالة
TICK_RETENTION_S = 6 * 3600   # الاحتفاظ بصفقات 6 ساعات فقط
MINI_RETENTION_S = 24 * 3600  # الاحتفاظ بملخصات 24 ساعة

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    ts DOUBLE, symbol TEXT, price DOUBLE, qty DOUBLE, is_buy INTEGER
);
CREATE TABLE IF NOT EXISTS mini (
    ts DOUBLE, symbol TEXT, close DOUBLE, open24 DOUBLE,
    high24 DOUBLE, low24 DOUBLE, vol24 DOUBLE
);
CREATE TABLE IF NOT EXISTS stats (
    ts DOUBLE, symbol TEXT, window_s INTEGER, price_chg_pct DOUBLE,
    n_trades INTEGER, buy_vol_usd DOUBLE, sell_vol_usd DOUBLE
);
"""


def _home():
    return os.environ.get("HOME") or os.path.expanduser("~")


def default_ticks_path():
    env = os.environ.get("TICKS_DB_PATH")
    if env:
        return env
    return os.path.join(_home(), "bot", "ticks.duckdb")


def default_status_path():
    env = os.environ.get("STREAM_STATUS_PATH")
    if env:
        return env
    return os.path.join(_home(), "bot", "stream_status.json")


def stream_url():
    """رابط الدفق المدمج: miniTicker كل ثانية + aggTrade لكل عملة."""
    streams = ["!miniTicker@1000ms"]
    streams += [s.lower() + "@aggTrade" for s in SYMBOLS]
    return WS_BASE + "?streams=" + "/".join(streams)


def parse_message(raw):
    """يحلل رسالة Binance مدمجة → (kind, dict) أو (None, None).
    kind ∈ {"tick", "mini"}. خالص بلا شبكة — قابل للاختبار."""
    try:
        msg = json.loads(raw) if isinstance(raw, str) else raw
        data = msg.get("data") or {}
        ev = data.get("e")
        if ev == "aggTrade":
            price = float(data["p"])
            qty = float(data["q"])
            if not (price > 0) or not (qty > 0):
                return None, None
            # m=True تعني المشتري صانع السوق → الصفقة بيع من المنفِّذ
            return "tick", {
                "ts": (data.get("T") or data.get("E") or 0) / 1000.0,
                "symbol": data.get("s"),
                "price": price,
                "qty": qty,
                "is_buy": 0 if data.get("m") else 1,
            }
        if ev == "24hrMiniTicker":
            close = float(data["c"])
            if not (close > 0):
                return None, None
            return "mini", {
                "ts": (data.get("E") or 0) / 1000.0,
                "symbol": data.get("s"),
                "close": close,
                "open24": _f(data.get("o")),
                "high24": _f(data.get("h")),
                "low24": _f(data.get("l")),
                "vol24": _f(data.get("q")),
            }
        return None, None
    except Exception:
        return None, None


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def write_status(path, data):
    """كتابة ذرية لملف الحالة (tmp + replace)."""
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[stream] write_status failed: {e}")
        return False


def read_status(path=None):
    """قراءة ملف الحالة — يستعملها main.py والاختبارات."""
    try:
        with open(path or default_status_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class RollingStats:
    """إحصاءات متدحرجة 1د/5د من صفقات خام — حساب خالص قابل للاختبار."""

    WINDOWS = (60, 300)

    def __init__(self):
        # symbol → deque[(ts, price, qty_usd, is_buy)]
        self._buf = {}

    def add_tick(self, tick):
        sym = tick.get("symbol")
        if not sym:
            return
        dq = self._buf.setdefault(sym, deque())
        dq.append((tick["ts"], tick["price"],
                   tick["price"] * tick["qty"], tick.get("is_buy", 1)))
        # تقليم أقدم من أكبر نافذة
        cutoff = tick["ts"] - max(self.WINDOWS)
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def compute(self, now=None):
        """→ قائمة صفوف stats جاهزة للكتابة."""
        now = now or time.time()
        rows = []
        for sym, dq in self._buf.items():
            for w in self.WINDOWS:
                cutoff = now - w
                pts = [p for p in dq if p[0] >= cutoff]
                if not pts:
                    continue
                first_px, last_px = pts[0][1], pts[-1][1]
                chg = (last_px / first_px - 1) * 100 if first_px > 0 else 0
                buy_v = sum(p[2] for p in pts if p[3])
                sell_v = sum(p[2] for p in pts if not p[3])
                rows.append({
                    "ts": now, "symbol": sym, "window_s": w,
                    "price_chg_pct": round(chg, 4),
                    "n_trades": len(pts),
                    "buy_vol_usd": round(buy_v, 2),
                    "sell_vol_usd": round(sell_v, 2),
                })
        return rows


class TickStore:
    """كتابة DuckDB دفعات — الكاتب الوحيد لملف ticks.duckdb."""

    def __init__(self, path=None):
        self.path = path or default_ticks_path()
        self._con = None

    def connect(self):
        try:
            import duckdb
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            self._con = duckdb.connect(self.path)
            for stmt in _SCHEMA.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    self._con.execute(stmt)
            return True
        except Exception as e:
            print(f"[stream] duckdb unavailable: {e}")
            self._con = None
            return False

    def flush(self, ticks, minis, stats_rows):
        """دفعة واحدة: صفقات + ملخصات + إحصاءات + تقليم قديم."""
        if self._con is None:
            return 0
        n = 0
        try:
            if ticks:
                self._con.executemany(
                    "INSERT INTO ticks VALUES (?,?,?,?,?)",
                    [(t["ts"], t["symbol"], t["price"], t["qty"],
                      t.get("is_buy", 1)) for t in ticks])
                n += len(ticks)
            if minis:
                self._con.executemany(
                    "INSERT INTO mini VALUES (?,?,?,?,?,?,?)",
                    [(m["ts"], m["symbol"], m["close"], m["open24"],
                      m["high24"], m["low24"], m["vol24"]) for m in minis])
                n += len(minis)
            if stats_rows:
                self._con.executemany(
                    "INSERT INTO stats VALUES (?,?,?,?,?,?,?)",
                    [(r["ts"], r["symbol"], r["window_s"],
                      r["price_chg_pct"], r["n_trades"],
                      r["buy_vol_usd"], r["sell_vol_usd"])
                     for r in stats_rows])
                n += len(stats_rows)
            now = time.time()
            self._con.execute("DELETE FROM ticks WHERE ts < ?",
                              [now - TICK_RETENTION_S])
            self._con.execute("DELETE FROM mini WHERE ts < ?",
                              [now - MINI_RETENTION_S])
            return n
        except Exception as e:
            print(f"[stream] flush failed: {e}")
            return 0

    def close(self):
        try:
            if self._con is not None:
                self._con.close()
        except Exception:
            pass
        self._con = None


def _run_once(state):
    """اتصال واحد — يعود عند الانقطاع (أو الخطأ) لإعادة المحاولة."""
    import websocket  # websocket-client — مستورد هنا فقط (لا شبكة عند الاستيراد)
    ticks_buf = deque()
    mini_buf = deque()
    stats = RollingStats()
    lock = threading.Lock()
    stop = threading.Event()
    msg_times = deque()  # طوابع الرسائل لقياس msg/sec

    def on_message(ws, raw):
        kind, row = parse_message(raw)
        if kind is None:
            return
        with lock:
            msg_times.append(time.time())
            if kind == "tick":
                ticks_buf.append(row)
                stats.add_tick(row)
            else:
                mini_buf.append(row)

    def on_error(ws, err):
        print(f"[stream] ws error: {err}")

    def on_close(ws, code, msg):
        print(f"[stream] ws closed ({code})")
        stop.set()

    def flusher():
        last_status = 0
        while not stop.wait(1):
            now = time.time()
            with lock:
                ticks = list(ticks_buf)
                ticks_buf.clear()
                minis = list(mini_buf)
                mini_buf.clear()
                # msg/sec على آخر 10 ثوانٍ
                cutoff = now - 10
                while msg_times and msg_times[0] < cutoff:
                    msg_times.popleft()
                mps = round(len(msg_times) / 10.0, 1)
                last_tick = ticks[-1]["ts"] if ticks else state.get(
                    "last_tick_ts", 0)
            srows = stats.compute(now)
            n = state["store"].flush(ticks, minis, srows)
            state["msg_count"] = state.get("msg_count", 0) + len(ticks) + len(minis)
            if ticks:
                state["last_tick_ts"] = last_tick
            if now - last_status >= STATUS_EVERY_S:
                last_status = now
                write_status(state["status_path"], {
                    "symbols": len(SYMBOLS),
                    "symbols_list": SYMBOLS,
                    "msg_per_sec": mps,
                    "last_tick_ts": state.get("last_tick_ts", 0),
                    "reconnects": state.get("reconnects", 0),
                    "rows_written": state.get("msg_count", 0),
                    "ts": now,
                })
            if n:
                print(f"[stream] flushed {n} rows ({mps} msg/s)")

    app = websocket.WebSocketApp(
        stream_url(), on_message=on_message,
        on_error=on_error, on_close=on_close)
    th = threading.Thread(target=flusher, daemon=True)
    th.start()
    try:
        app.run_forever(ping_interval=30, ping_timeout=10)
    finally:
        stop.set()
        th.join(timeout=5)


def run(status_path=None, ticks_path=None):
    """الحلقة الأبدية: اتصال → انقطاع → تراجع أسّي → إعادة. لا تموت أبداً."""
    state = {
        "store": TickStore(ticks_path),
        "status_path": status_path or default_status_path(),
        "reconnects": 0,
    }
    if not state["store"].connect():
        print("[stream] no duckdb — running status-only mode")
    backoff = 1
    print(f"[stream] starting: {len(SYMBOLS)} symbols → {state['store'].path}")
    while True:
        try:
            _run_once(state)
        except KeyboardInterrupt:
            print("[stream] stopped by user")
            break
        except Exception:
            print("[stream] fatal in _run_once (will retry):")
            traceback.print_exc()
        state["reconnects"] += 1
        print(f"[stream] reconnect #{state['reconnects']} in {backoff}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)
    state["store"].close()


def main():
    run()


if __name__ == "__main__":
    main()
