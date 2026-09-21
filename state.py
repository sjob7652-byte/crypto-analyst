# -*- coding: utf-8 -*-
"""حفظ حالة البوت (الصفقات المفتوحة + التنبيهات المرسلة) في ملف JSON."""
import json
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def load():
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            s = json.load(f)
        s.setdefault("positions", {})
        s.setdefault("alerted", {})
        s.setdefault("stats", {})
        return s
    except Exception:
        return {"positions": {}, "alerted": {}, "stats": {}}


def save(s):
    try:
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("state save error:", e)
