# -*- coding: utf-8 -*-
"""المنفذ الآلي: فتح المراكز من الإشارات + مراقبة TP/SL."""

import time

import config
from scanner import fetch_price
from wallet import PaperWallet


class Executor:
    def __init__(self, wallet=None):
        self.wallet = wallet or PaperWallet()

    # ---------- معالجة إشارات جديدة ----------

    def process_signals(self, signals):
        """فتح مراكز ورقية للإشارات الجديدة. يُرجع قائمة الأحداث."""
        events = []
        for sig in signals:
            pos_id, result = self.wallet.open_position(sig)
            if pos_id:
                events.append({
                    "type": "opened",
                    "pos_id": pos_id,
                    "signal": sig,
                    "position": result,
                })
                print(f"[EXEC] OPENED {sig['symbol']} {sig['direction']} qty={result['qty']}", flush=True)
            else:
                print(f"[EXEC] Skip {sig['symbol']}: {result}", flush=True)
        return events

    # ---------- مراقبة المراكز المفتوحة ----------

    def check_positions(self):
        """فحص كل مركز مفتوح مقابل السعر الحالي (متوازي وسريع) → أحداث TP/SL."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        events = []
        open_pos = self.wallet.open_positions()
        if not open_pos:
            return events

        # جلب الأسعار بشكل متوازي
        def get_price(sym):
            try:
                return sym, fetch_price(sym)
            except Exception:
                return sym, None

        symbols = list(set(p["symbol"] for p in open_pos.values()))
        prices = {}
        with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as ex:
            for sym, price in ex.map(lambda s: (s, fetch_price(s)), symbols):
                prices[sym] = price

        for pid, p in open_pos.items():
            price = prices.get(p["symbol"])
            if price is None:
                continue
            ev = self._check_one(pid, p, price)
            if ev:
                events.append(ev)
        return events

    def _check_one(self, pid, p, price):
        direction = p["direction"]
        is_long = direction == "long"

        # تحديد ما تم لمسه
        if is_long:
            hit_sl = price <= p["sl"]
            hit_tp1 = not p["tp1_hit"] and price >= p["tp1"]
            hit_tp2 = not p["tp2_hit"] and price >= p["tp2"]
            hit_tp3 = price >= p["tp3"]
        else:
            hit_sl = price >= p["sl"]
            hit_tp1 = not p["tp1_hit"] and price <= p["tp1"]
            hit_tp2 = not p["tp2_hit"] and price <= p["tp2"]
            hit_tp3 = price <= p["tp3"]

        # الأولوية: وقف الخسارة أولاً (حماية)
        if hit_sl:
            ev = self.wallet.partial_close(pid, p["remaining_pct"], price, "SL")
            if ev:
                ev["type"] = "sl_hit"
                print(f"[EXEC] SL HIT {p['symbol']} pnl={ev['pnl']}", flush=True)
            return ev

        # الوقف الزمني: إلا TP1 ما تضربش في 72 ساعة (18 شمعة 4H) سكر بسعر السوق
        # (باكتست 2026-10-17: يقطع الصفقات الميتة بخسارة صغيرة بدل انتظار -1R)
        if not p.get("tp1_hit"):
            age_h = (time.time() - p.get("opened_at", 0)) / 3600
            if age_h >= config.KDRX_TIME_STOP_HOURS:
                ev = self.wallet.partial_close(pid, p["remaining_pct"], price, "TSTOP")
                if ev:
                    ev["type"] = "tstop_hit"
                    print(f"[EXEC] TSTOP {p['symbol']} age={age_h:.1f}h pnl={ev['pnl']}", flush=True)
                return ev

        # الأهداف بالترتيب
        if hit_tp3:
            # إغلاق المتبقي كاملاً
            ev = self.wallet.partial_close(pid, p["remaining_pct"], price, "TP3")
            if ev:
                ev["type"] = "tp3_hit"
                p2 = self.wallet.data["positions"][pid]
                p2["tp3_hit"] = True
                self.wallet.save()
                print(f"[EXEC] TP3 HIT {p['symbol']} pnl={ev['pnl']}", flush=True)
            return ev

        if hit_tp2:
            ev = self.wallet.partial_close(pid, config.TP2_CLOSE_PCT, price, "TP2")
            if ev:
                ev["type"] = "tp2_hit"
                p2 = self.wallet.data["positions"][pid]
                p2["tp2_hit"] = True
                self.wallet.save()
                print(f"[EXEC] TP2 HIT {p['symbol']} pnl={ev['pnl']}", flush=True)
            return ev

        if hit_tp1:
            ev = self.wallet.partial_close(pid, config.TP1_CLOSE_PCT, price, "TP1")
            if ev:
                ev["type"] = "tp1_hit"
                p2 = self.wallet.data["positions"][pid]
                p2["tp1_hit"] = True
                self.wallet.save()
                # نقل وقف الخسارة للتعادل (صفقة خالية المخاطر)
                self.wallet.move_sl_to_breakeven(pid)
                print(f"[EXEC] TP1 HIT {p['symbol']} pnl={ev['pnl']} → SL→breakeven", flush=True)
            return ev

        return None


if __name__ == "__main__":
    ex = Executor()
    print("[EXEC] Checking open positions...", flush=True)
    evs = ex.check_positions()
    print(f"[EXEC] {len(evs)} events", flush=True)
    print("[EXEC] Stats:", ex.wallet.stats(), flush=True)
