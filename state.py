# -*- coding: utf-8 -*-
"""حفظ حالة البوت (الصفقات المفتوحة + التنبيهات المرسلة) في ملف JSON."""
import json
import os
import time

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def load():
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            s = json.load(f)
        s.setdefault("positions", {})
        s.setdefault("alerted", {})
        s.setdefault("stats", {})
        s.setdefault("history", [])
        return s
    except Exception:
        return {"positions": {}, "alerted": {}, "stats": {}, "history": []}


def save(s):
    try:
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("state save error:", e)


# ---------- ذاكرة الخبير: نتائج الإشارات السابقة ----------
# outcome: tp1 / tp2 / tp3 (وصل لهدف) | sl (ضرب وقف الخسارة) | expired (انتهت المدة)
def record_outcome(s, pos, outcome):
    h = s.setdefault("history", [])
    h.append({
        "name": pos.get("name"),
        "band": pos.get("band") or "؟",
        "score": pos.get("score"),
        "outcome": outcome,
        "time": time.time(),
    })
    s["history"] = h[-200:]


def band_stats(s):
    """لكل فئة نقاط: عدد الإشارات ونسبة التي وصلت لهدف على الأقل."""
    stats = {}
    for h in s.get("history", []):
        b = h.get("band") or "؟"
        d = stats.setdefault(b, {"n": 0, "wins": 0})
        d["n"] += 1
        if h.get("outcome") in ("tp1", "tp2", "tp3"):
            d["wins"] += 1
    for d in stats.values():
        d["hit_rate"] = (d["wins"] / d["n"]) if d["n"] else 0.0
    return stats


def track_summary(s, n=10):
    """ملخص آخر n إشارة: كم منها رابحة."""
    h = s.get("history", [])[-n:]
    wins = sum(1 for x in h if x.get("outcome") in ("tp1", "tp2", "tp3"))
    return {"n": len(h), "wins": wins}
