# -*- coding: utf-8 -*-
"""المحفظة الورقية: $100 افتراضية، تتبع المراكز والصفقات المغلقة."""

import json
import os
import time

import config


class PaperWallet:
    def __init__(self, path=None):
        self.path = path or config.WALLET_FILE
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "cash": config.STARTING_CASH,
            "start": config.STARTING_CASH,
            "positions": {},       # signal_id → position
            "closed_trades": [],
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
        }

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    # ---------- فتح مركز ----------

    def open_position(self, signal):
        """فتح مركز ورقي من إشارة. يُرجع (position_id, position) أو (None, reason)."""
        pos = self.data["positions"]
        if len(pos) >= config.MAX_POSITIONS:
            return None, "max_positions"

        symbol = signal["symbol"]
        # لا مركزين مفتوحين لنفس الزوج
        for p in pos.values():
            if p["symbol"] == symbol and p["status"] == "open":
                return None, "already_open"

        entry = signal["entry"]
        sl = signal["sl"]
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return None, "bad_sl"

        # $50 CHALLENGE: position-size targeting — 20% of equity per trade
        # (5 positions x 20% = 100% max allocation, ~0.8% risk/trade, diversified)
        equity = self.equity()
        position_pct = getattr(config, 'POSITION_PCT', 0.20)
        notional = equity * position_pct
        qty = notional / entry

        # لا نسمح برافعة: القيمة الاسمية ≤ الرصيد المتاح
        if notional > self.data["cash"]:
            # تقليل الكمية لتناسب النقد المتاح
            qty = self.data["cash"] / entry
            notional = qty * entry
            if qty * sl_dist < 0.01:  # مخاطرة ضئيلة جداً
                return None, "insufficient_cash"

        pos_id = f"{symbol}-{signal['created_at']}"
        position = {
            "id": pos_id,
            "symbol": symbol,
            "direction": signal["direction"],
            "entry": entry,
            "qty": round(qty, 6),
            "orig_qty": round(qty, 6),  # الكمية الأصلية: الإغلاقات الجزئية نِسَب منها
            "notional": round(notional, 2),
            "sl": sl,
            "sl_original": sl,
            "tp1": signal["tp1"],
            "tp2": signal["tp2"],
            "tp3": signal["tp3"],
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "remaining_pct": 1.0,
            "realized_pnl": 0.0,
            "status": "open",
            "opened_at": int(time.time()),
            "signal_strength": signal["strength"],
            "signal_msg_id": None,  # message_id لرسالة الإشارة (للاقتباس reply عند TP/SL)
        }
        pos[pos_id] = position
        if self.data.get("reserve_cash"):
            # محفظة نسخ KDRX فقط: حجز قيمة الصفقة من النقد (يُسترد تدريجياً مع الإغلاقات)
            self.data["cash"] = round(self.data["cash"] - notional, 4)
        self.save()
        return pos_id, position

    def set_signal_msg_id(self, pos_id, msg_id):
        """حفظ message_id لرسالة الإشارة الأصلية."""
        pos = self.data["positions"].get(pos_id)
        if pos and msg_id:
            pos["signal_msg_id"] = msg_id
            self.save()

    # ---------- إغلاق جزئي / كامل ----------

    def partial_close(self, pos_id, pct, price, reason):
        """إغلاق نسبة من المركز بسعر معين (النسبة من الكمية الأصلية)."""
        pos = self.data["positions"].get(pos_id)
        if not pos or pos["status"] != "open":
            return None
        # pct دائماً نسبة من الكمية الأصلية (50/30/20 مثلاً)، لا من المتبقي الحالي
        orig_qty = pos.get("orig_qty")
        if not orig_qty:
            rp = pos.get("remaining_pct") or 0
            orig_qty = pos["qty"] / rp if rp > 0 else pos["qty"]
        close_qty = min(orig_qty * pct, pos["qty"])
        if pos["direction"] == "long":
            pnl = (price - pos["entry"]) * close_qty
        else:
            pnl = (pos["entry"] - price) * close_qty
        pos["qty"] = round(pos["qty"] - close_qty, 6)
        pos["remaining_pct"] = round(pos["remaining_pct"] - pct, 4)
        pos["realized_pnl"] = round(pos["realized_pnl"] + pnl, 4)
        self.data["cash"] = round(self.data["cash"] + pnl, 4)
        if self.data.get("reserve_cash"):
            # محفظة نسخ KDRX فقط: استرداد أصل المبلغ المحجوز لهذا الجزء المغلق
            self.data["cash"] = round(self.data["cash"] + close_qty * pos["entry"], 4)
        self.data["total_pnl"] = round(self.data["total_pnl"] + pnl, 4)
        event = {
            "pos_id": pos_id, "symbol": pos["symbol"],
            "pct": pct, "price": price, "pnl": round(pnl, 4),
            "reason": reason, "at": int(time.time()),
            "entry": pos["entry"], "direction": pos["direction"],
        }
        if pos["remaining_pct"] <= 0.001:
            pos["status"] = "closed"
            pos["closed_at"] = int(time.time())
            pos["close_reason"] = reason
            self._record_closed(pos)
        self.save()
        return event

    def move_sl_to_breakeven(self, pos_id):
        pos = self.data["positions"].get(pos_id)
        if pos and pos["status"] == "open":
            pos["sl"] = pos["entry"]
            self.save()

    def _record_closed(self, pos):
        self.data["total_trades"] += 1
        if pos["realized_pnl"] > 0:
            self.data["wins"] += 1
        else:
            self.data["losses"] += 1
        self.data["closed_trades"].append({
            "id": pos["id"], "symbol": pos["symbol"],
            "direction": pos["direction"], "entry": pos["entry"],
            "pnl": pos["realized_pnl"],
            "pnl_pct": round(pos["realized_pnl"] / pos["notional"] * 100, 2) if pos["notional"] else 0,
            "reason": pos.get("close_reason", "?"),
            "opened_at": pos["opened_at"],
            "closed_at": pos.get("closed_at", int(time.time())),
        })

    # ---------- إحصائيات ----------

    def equity(self):
        """الرصيد الكلي = النقد + الربح المحقق (غير المحقق يُحسب عند الإغلاق)."""
        cash = self.data["cash"]
        if self.data.get("reserve_cash"):
            # محفظة نسخ KDRX: النقد المتاح + الأصل المحجوز في المراكز المفتوحة
            reserved = sum(p["qty"] * p["entry"] for p in self.open_positions().values())
            return round(cash + reserved, 2)
        return round(cash, 2)

    def open_positions(self):
        return {k: v for k, v in self.data["positions"].items() if v["status"] == "open"}

    def stats(self):
        d = self.data
        total = d["total_trades"]
        win_rate = round(d["wins"] / total * 100, 1) if total else 0
        reserved = 0.0
        if d.get("reserve_cash"):
            # محفظة نسخ KDRX: الأصل المحجوز في المراكز المفتوحة (يُحتسب في العائد)
            reserved = round(sum(p["qty"] * p["entry"] for p in self.open_positions().values()), 2)
        base = d["cash"] + reserved
        ret = round((base - d["start"]) / d["start"] * 100, 2)
        out = {
            "cash": round(d["cash"], 2),
            "start": d["start"],
            "return_pct": ret,
            "total_trades": total,
            "wins": d["wins"],
            "losses": d["losses"],
            "win_rate": win_rate,
            "total_pnl": round(d["total_pnl"], 2),
            "open_count": len(self.open_positions()),
        }
        if d.get("reserve_cash"):
            out["reserved"] = reserved
        return out

    def to_dict(self):
        """الحالة الكاملة للـ dashboard."""
        d = self.data
        return {
            "cash": round(d["cash"], 2),
            "start": d["start"],
            "positions": self.open_positions(),
            "closed_trades": d.get("closed_trades", []),
            "total_trades": d["total_trades"],
            "wins": d["wins"],
            "losses": d["losses"],
            "total_pnl": round(d["total_pnl"], 2),
        }
