# -*- coding: utf-8 -*-
"""حفظ حالة البوت (الصفقات المفتوحة + التنبيهات المرسلة) في ملف JSON.

التخزين الأساسي: GitHub Gist (GH_PAT + GIST_ID في متغيرات البيئة)
— يعمل كقاعدة بيانات NoSQL مجانية، لا يُمسح مثل الـcache.
الاحتياطي: ملف state.json المحلي (يُحفظ أيضاً في cache الـworkflow).

⚠️ تحذير أمني: الـGist يجب أن يكون عاماً (public) لأن الداشبورد يقرأه
مباشرة من المتصفح بلا مصادقة — لذلك يُمنع منعاً باتاً تخزين أي سر
(توكن، كلمة سر، session، مفتاح API) في هذه الحالة. الدالة save()
ترفض الحفظ تلقائياً إذا رصدت مفتاحاً مشبوهاً (حماية fail-closed)."""
import json
import os
import time

import requests

from config import PAPER_START_BALANCE

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
GIST_API = "https://api.github.com/gists"
ALERTED_TTL = 48 * 3600  # تنبيهات أقدم من 48 ساعة تُحذف (منع تضخم الحالة)


def _gist_headers():
    tok = os.environ.get("GH_PAT")
    if not tok:
        return None
    return {"Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json"}


def gist_load():
    """يقرأ state.json من الـGist السري. يعيد None عند غياب الإعداد/الفشل."""
    gid = os.environ.get("GIST_ID")
    headers = _gist_headers()
    if not gid or not headers:
        return None
    try:
        r = requests.get(f"{GIST_API}/{gid}", headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        files = r.json().get("files") or {}
        content = (files.get("state.json") or {}).get("content")
        if not content:
            return None
        # حد أقصى للحجم قبل التحليل: الـstate الحقيقي بضع مئات KB —
        # أي شيء أكبر بكثير = تلف أو عبث، نرفضه بدل التهام الذاكرة
        if len(content) > 5_000_000:
            print("[SECURITY] محتوى state.json ضخم بشكل مريب — تم التجاهل")
            return None
        s = json.loads(content)
        return s if isinstance(s, dict) else None
    except Exception:
        return None


def gist_save(s):
    """يكتب state.json في الـGist السري. يعيد True/False (best effort)."""
    gid = os.environ.get("GIST_ID")
    headers = _gist_headers()
    if not gid or not headers:
        return False
    try:
        r = requests.patch(
            f"{GIST_API}/{gid}", headers=headers, timeout=15,
            json={"files": {"state.json": {
                "content": json.dumps(s, ensure_ascii=False)}}})
        return r.status_code == 200
    except Exception:
        return False


def _defaults(s):
    s.setdefault("positions", {})
    s.setdefault("alerted", {})
    s.setdefault("stats", {})
    s.setdefault("history", [])
    s.setdefault("waitlist", {})
    s.setdefault("paper", _default_paper())
    # أرشيف الصفقات المغلقة — يُملأ تدريجياً منذ هذا التحديث
    s["paper"].setdefault("closed_trades", [])
    return s


def _prune(s):
    """حذف التنبيهات القديمة (>48س) لتفادي تضخم الحالة مع الزمن."""
    now = time.time()
    alerted = s.get("alerted") or {}
    s["alerted"] = {k: v for k, v in alerted.items()
                    if now - v < ALERTED_TTL}
    return s


def _file_load():
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _file_save(s):
    """كتابة ذرية: ملف مؤقت → fsync → استبدال في جزء من الثانية.
    لو تعطل السكربت أثناء الكتابة، الملف الأصلي يبقى سليماً (لا تلف أبداً)."""
    tmp = PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PATH)  # عملية ذرية على نفس القرص
    except Exception as e:
        print("state save error:", e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _default_paper():
    return {"cash": PAPER_START_BALANCE, "start": PAPER_START_BALANCE,
            "positions": {}, "trades": 0, "wins": 0, "losses": 0,
            "closed_trades": []}  # أرشيف: كل صفقة خرج منها البوت مع ربحها/خسارتها


# مفاتيح يُمنع ظهورها في الحالة — الـGist عام والداشبورد يقرأه بلا مصادقة،
# فأي سر يُحفظ هنا يصبح مكشوفاً للعالم. الفحص fail-closed: يرفض الحفظ كاملاً.
_FORBIDDEN_KEY_PARTS = ("token", "secret", "passwd", "password", "session",
                        "api_key", "apikey", "private_key", "chat_id")


def _assert_no_secrets(s):
    """يفحص مفاتيح الحالة بشكل تكراري (كل المستويات) — يعيد False ويطبع
    تحذيراً صارخاً إذا وُجد مفتاح مشبوه (لمنع تسريب الأسرار إلى الـGist
    العام). الفحص على المفاتيح فقط لا القيم: أسماء العملات (قيم) لا
    تُسبب إيجابيات كاذبة مهما احتوت."""
    bad = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if any(part in str(k).lower()
                       for part in _FORBIDDEN_KEY_PARTS):
                    bad.append(str(k))
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    try:
        walk(s or {})
    except Exception:
        return True
    if bad:
        print(f"[SECURITY] رُفض حفظ الحالة: مفاتيح مشبوهة قد تكون أسراراً: {bad}")
        return False
    return True


def load():
    # الأساسي: Gist السري → الاحتياطي: الملف المحلي (cache)
    s = gist_load()
    if s is None:
        s = _file_load()
    if not isinstance(s, dict):
        s = {}
    return _defaults(s)


def save(s):
    if not _assert_no_secrets(s):
        return  # fail-closed: لا نحفظ أسراراً في الـGist العام أبداً
    s = _prune(s)
    _file_save(s)   # محلي (للـcache)
    gist_save(s)    # سحابي (الأساسي — best effort)


# ---------- ذاكرة الخبير: نتائج الإشارات السابقة ----------
# outcome: tp1 / tp2 / tp3 (وصل لهدف) | sl (ضرب وقف الخسارة) |
#          rug (انهيار مفاجئ ≥70% — سحب سيولة محتمل) | expired (انتهت المدة)
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
