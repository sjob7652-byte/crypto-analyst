# -*- coding: utf-8 -*-
"""اختبارات محرك التدفق اللحظي (flow-3) — كلها محلية بلا شبكة."""
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import flow
from flow import (TradeRing, SecRing, parse_trade_msg, parse_book_msg,
                  compute_metrics, classify, ring_cap_for,
                  FLOW_CVD_VETO_USD, FLOW_OBI_VETO, Z_WARN)

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" [{extra}]" if extra and not cond else ""))


NOW = time.time()


def synth_trades(n, price, qty, buy=True, start=None, step=0.5):
    """صفقات اصطناعية متساوية التباعد الزمني."""
    t0 = (start if start is not None else NOW - n * step)
    return [(t0 + i * step, price, qty, 1.0 if buy else -1.0)
            for i in range(n)]


def fill_ring(sym_trades):
    r = TradeRing(10000)
    for t in sym_trades:
        r.append(*t)
    return r


# 1) تحليل aggTrade: إشارة CVD صحيحة
tr = parse_trade_msg({"T": 1700000000000, "p": "1.5", "q": "100", "m": False})
check("parse_trade buy", tr is not None and tr[3] == 1.0 and tr[1] == 1.5)
tr2 = parse_trade_msg({"T": 1700000000000, "p": "1.5", "q": "100", "m": True})
check("parse_trade sell", tr2 is not None and tr2[3] == -1.0)
check("parse_trade bad", parse_trade_msg({"x": 1}) is None)

# بيع صافٍ → CVD سالب بالمقدار الصحيح
trades = synth_trades(60, 2.0, 50.0, buy=False)  # 60 بيع × $100 = -$6000
m = compute_metrics(fill_ring(trades), None, None, NOW)
check("cvd sell negative", m is not None and m["cvd_5m"] == -6000.0,
      f"cvd_5m={m['cvd_5m'] if m else None}")
trades_b = synth_trades(60, 2.0, 50.0, buy=True)
m2 = compute_metrics(fill_ring(trades_b), None, None, NOW)
check("cvd buy positive", m2 is not None and m2["cvd_5m"] == 6000.0)

# 2) bookTicker → OBI
b = parse_book_msg({"b": "1.0", "B": "300", "a": "1.01", "A": "100"})
check("parse_book", b is not None and b[2] == 300.0 and b[4] == 100.0)
m3 = compute_metrics(fill_ring(trades_b), b, None, NOW)
check("obi range+sign", m3 is not None and -1 <= m3["obi"] <= 1
      and abs(m3["obi"] - 0.5) < 1e-9, f"obi={m3['obi'] if m3 else None}")
b2 = parse_book_msg({"b": "1.0", "B": "100", "a": "1.01", "A": "400"})
m4 = compute_metrics(fill_ring(trades_b), b2, None, NOW)
check("obi ask-heavy negative", m4 is not None and m4["obi"] < -0.3,
      f"obi={m4['obi'] if m4 else None}")

# 3) آلة الحالة: DANGER فقط باجتماع الدليلين
check("DANGER on combo",
      classify(-60000.0, -0.5, 1.0) == "DANGER_WHALE_DUMP")
check("WARNING on cvd alone (no veto)",
      classify(-60000.0, 0.1, 1.0) == "WARNING")
check("WARNING on obi alone (no veto)",
      classify(-1000.0, -0.5, 1.0) == "WARNING")
check("WARNING on z burst (no veto)",
      classify(-1000.0, 0.0, 5.5) == "WARNING")
check("SAFE on calm flow",
      classify(5000.0, 0.1, 0.5) == "SAFE")
check("UNKNOWN on no data",
      classify(None, None, None, has_data=False) == "UNKNOWN")
check("UNKNOWN on stale",
      classify(-999999.0, -0.9, 9.9, has_data=False) == "UNKNOWN")
# الفيتو = DANGER فقط
check("veto only DANGER",
      classify(-60000.0, -0.5, 1.0) == "DANGER_WHALE_DUMP"
      and classify(-60000.0, 0.1, 1.0) != "DANGER_WHALE_DUMP")

# 4) التفاف الحلقة: الأقدم→الأحدث صحيح بعد الامتلاء
r = TradeRing(10)
base = NOW - 100
for i in range(25):
    r.append(base + i, 1.0, 1.0, 1.0)
w = r.window(base)
check("ring wraparound ordered", w is not None and len(w[0]) == 10
      and w[0][0] == base + 15 and w[0][-1] == base + 24,
      f"first={w[0][0] if w else None}")
w2 = r.window(base + 20)
check("ring cutoff", w2 is not None and len(w2[0]) == 5)

# 5) z-score: انفجار نشاط → z مرتفع
sec = SecRing()
t0 = int(NOW) - 4000
for i in range(3000):
    for _ in range(2):
        sec.add(t0 + i)          # 2/ث لمدة ~50 دقيقة
burst_sec = int(NOW) - 10
for i in range(10):
    for _ in range(60):
        sec.add(burst_sec + i)   # 60/ث لآخر 10 ثوانٍ
z = sec.zscore()
check("zscore burst high", z is not None and z > Z_WARN, f"z={z}")
sec2 = SecRing()
for i in range(2000):
    sec2.add(t0 + i)
    sec2.add(t0 + i)
z2 = sec2.zscore()
check("zscore calm low", z2 is not None and abs(z2) < 2.0, f"z={z2}")
sec3 = SecRing()
check("zscore no history", sec3.zscore() is None)

# 6) سعة الحلقة من الميزانية + السقف النافع
cap = ring_cap_for(300)
check("ring cap useful", cap == 100000, f"cap={cap}")
cap_small = ring_cap_for(300, budget_gb=0.001)
check("ring cap budget-bound", cap_small < 100000 and cap_small >= 1000,
      f"cap={cap_small}")

# 7) ZeroMQ: دورة REQ/REP حقيقية ضد المحرك
import zmq as _zmq_check  # noqa — متوفر في بيئة الاختبار
from flow import FlowEngine
eng = FlowEngine(["BONKUSDT", "DOGEUSDT"])
eng.latest["BONKUSDT"] = {"sym": "BONKUSDT", "state": "DANGER_WHALE_DUMP",
                          "cvd_5m": -75000.0, "obi": -0.45, "z": 5.1,
                          "computed_ts": time.time()}
eng.latest["DOGEUSDT"] = {"sym": "DOGEUSDT", "state": "SAFE",
                          "cvd_5m": 12000.0, "obi": 0.2, "z": 0.4,
                          "computed_ts": time.time()}
TEST_ADDR = "tcp://127.0.0.1:5567"
t = threading.Thread(target=eng._rep_loop, kwargs={"addr": TEST_ADDR},
                     daemon=True)
t.start()
time.sleep(0.4)
ctx = _zmq_check.Context.instance()
s = ctx.socket(_zmq_check.REQ)
s.setsockopt(_zmq_check.SNDTIMEO, 1000)
s.setsockopt(_zmq_check.RCVTIMEO, 1000)
s.setsockopt(_zmq_check.LINGER, 0)
s.connect(TEST_ADDR)
s.send_json({"sym": "BONKUSDT"})
rep = s.recv_json()
check("zmq DANGER round-trip <1s",
      rep["state"] == "DANGER_WHALE_DUMP" and rep["cvd_5m"] == -75000.0
      and rep["obi"] == -0.45 and rep["z"] == 5.1, f"{rep}")
s.send_json({"sym": "DOGEUSDT"})
rep2 = s.recv_json()
check("zmq SAFE round-trip", rep2["state"] == "SAFE")
s.send_json({"sym": "NOPEUSDT"})
rep3 = s.recv_json()
check("zmq UNKNOWN for missing", rep3["state"] == "UNKNOWN"
      and rep3["cvd_5m"] is None)
# بيانات قديمة → UNKNOWN
eng.latest["DOGEUSDT"]["computed_ts"] = time.time() - 120
s.send_json({"sym": "DOGEUSDT"})
rep4 = s.recv_json()
check("zmq stale → UNKNOWN", rep4["state"] == "UNKNOWN")
s.close()
eng._stop.set()

# 8) غياب pyzmq → مسار الفاحص يتخطى بصمت
import main as botmain
saved = sys.modules.get("zmq")
sys.modules["zmq"] = None
try:
    v, info = botmain._flow_veto({"symbol": "BONKUSDT"})
    check("pyzmq-missing fallback", v is False and info is None)
finally:
    if saved is not None:
        sys.modules["zmq"] = saved
    else:
        del sys.modules["zmq"]

# 9) تطبيع الرموز
c = botmain._flow_candidates({"symbol": "BONK", "pair": None})
check("candidate BONK→BONKUSDT", c == ["BONKUSDT"], f"{c}")
c2 = botmain._flow_candidates({"symbol": "bonk/usdt", "pair": None})
check("candidate bonk/usdt", c2 == ["BONKUSDT"], f"{c2}")
c3 = botmain._flow_candidates({"symbol": None, "pair": None})
check("candidate empty", c3 == [])

# 10) _read_daemon_status يقرأ flow_status.json
tmpd = tempfile.mkdtemp()
os.makedirs(os.path.join(tmpd, "bot"), exist_ok=True)
with open(os.path.join(tmpd, "bot", "flow_status.json"), "w") as f:
    json.dump({"ts": time.time(), "symbols": 300, "msg_per_sec": 123.4,
               "rss_gb": 1.1, "vetoes": ["XUSDT"]}, f)
old_home = os.environ.get("HOME")
os.environ["HOME"] = tmpd
try:
    st = {}
    botmain._read_daemon_status(st)
    check("daemon status reads flow",
          st.get("flow", {}).get("symbols") == 300
          and st["flow"]["vetoes"] == ["XUSDT"])
finally:
    if old_home is not None:
        os.environ["HOME"] = old_home

# 11) ثوابت المستثمر والموت لم تتغير
import config
check("investor constants",
      config.TAKE_PROFITS == [1.00, 3.00]
      and config.PAPER_SELL_FRACTIONS == (0.5, 1.0)
      and config.STOP_LOSS == 0.60
      and config.POSITION_MAX_AGE_H == 336
      and config.SCORE_BUY == 70
      and config.SCORE_STRONG_BUY == 80
      and config.MIN_PROBABILITY == 60
      and config.PAPER_MAX_OPEN == 10)
check("death constants",
      config.DEATH_LIQ_USD == 1000
      and config.DEATH_VOL_M5_USD == 50
      and config.DEATH_NOBUY_MIN_SELLS == 3
      and config.DEATH_CONFIRM_MIN == 15
      and config.DEATH_MIN_AGE_H == 1)

# 12) paper_buy يحمل خطاف الفيتو وحقول التدفق
import inspect
src = inspect.getsource(botmain.paper_buy)
check("paper_buy veto hook", "_flow_veto(res)" in src and "flow_vetoes" in src)
check("paper_buy flow tags", '"flow_cvd"' in src and '"flow_state"' in src)
check("no config write", "config.py" not in src)

# 13) توقيع on_message: websocket-client يستدعي callback(ws, raw) دائماً.
# انحدار قاتل صامت (2026-09-26): تمرير on_ws_message(self, raw) مباشرة كان
# يرمي TypeError لكل رسالة، والمكتبة تبتلعها عبر NullHandler — صفر رسائل
# بلا أي أثر في السجل. المحوّل _ws_on_message يمنع ذلك.
eng2 = FlowEngine(["BTCUSDT"])
raw_trade = json.dumps({
    "stream": "btcusdt@aggTrade",
    "data": {"s": "BTCUSDT", "T": 1700000000000,
             "p": "100.0", "q": "1.0", "m": False}})
fake_ws = object()  # يحاكي WebSocketApp في استدعاء ‏_callback‏: callback(self, *args)
try:
    eng2._ws_on_message(fake_ws, raw_trade)
    cb_ok = True
except TypeError:
    cb_ok = False
check("ws callback signature (ws, raw)", cb_ok and eng2.msg_count == 1,
      f"msg_count={eng2.msg_count}")
raw_book = json.dumps({
    "stream": "btcusdt@bookTicker",
    "data": {"s": "BTCUSDT", "b": "99.9", "B": "5", "a": "100.1", "A": "3"}})
eng2._ws_on_message(fake_ws, raw_book)
check("ws callback bookTicker counted", eng2.msg_count == 2,
      f"msg_count={eng2.msg_count}")
src_conn = inspect.getsource(flow.FlowEngine._connect_loop)
check("connect_loop wires _ws_on_message",
      "on_message=self._ws_on_message" in src_conn)
check("connect_loop wires on_error/on_close",
      "on_error=self._ws_on_error" in src_conn
      and "on_close=self._ws_on_close" in src_conn)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
