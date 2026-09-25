# -*- coding: utf-8 -*-
"""معايرة الاحتمال: أحكام الخبراء أولاً، ثم التعلم من البيانات الحية.

الفلسفة (بحث 2026-09-25 — لا نبدأ من الصفر): أحكام الخبراء المستخلصة
من دراسة Therani الكمية (25,912 صفقة على Solana) وأصوات الممارسين
(DamiDefi، INSIGHTFUL، Maurits، u/arianaram) مُشفَّرة هنا كقواعد تعديل
محافظة (±نقاط مئوية، بمجموع محدود). مع تراكم الصفقات المغلقة الحقيقية،
يتولى نموذج sklearn تدريجياً عبر مزج مرجّح بعدد العينات.

adjust(probability, features) -> float — لا يغيّر العتبات نفسها أبداً
(SCORE_BUY وMIN_PROBABILITY تبقيان كما هما في config.py)، بل يُحسّن
الرقم المُدخَل إلى بوابة الدخول.

كل الدوال fail-safe: أي استثناء → الاحتمال الأصلي دون تعديل.
"""
import math
import os
import pickle

# عدد الصفقات الذي يبدأ عنده النموذج بالمشاركة، والسقف الأقصى لوزنه
MODEL_START_N = 20
MODEL_MAX_W = 0.6
PRIOR_CAP = 15  # أقصى مجموع تعديلات الأحكام (±نقطة مئوية)


def default_model_path():
    env = os.environ.get("CALIBRATOR_MODEL_PATH")
    if env:
        return env
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, "bot", "models", "calibrator.pkl")


# ---------- أحكام الخبراء (قواعد التعديل) ----------
# كل حكم: (الاسم، دالة الشرط(features)->bool، التعديل بالنقاط، المصدر)
def _fnum(features, *names):
    for n in names:
        v = features.get(n)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _rules():
    liq = lambda f: _fnum(f, "liq_usd", "liquidity_usd")
    mcap = lambda f: _fnum(f, "mcap_usd", "fdv_usd")
    bp = lambda f: _fnum(f, "buy_pressure", "m_buy_pressure")
    vm = lambda f: _fnum(f, "vol_mult", "m_vol_mult")
    atr = lambda f: _fnum(f, "atr_pct", "m_atr_pct")
    sent = lambda f: _fnum(f, "sentiment", "news_sentiment")
    age = lambda f: _fnum(f, "age_h")
    return [
        # 1) طبقة السيولة — أقوى إشارة منفردة (Therani):
        # $100K+ = 84% moon / 0.25% dump؛ $50–100K = 59% / 0.19%؛ <$1K = 59% dump
        ("LIQ>=100K", lambda f: (liq(f) or 0) >= 100_000, +6,
         "Therani liquidity tiers"),
        ("LIQ 50-100K", lambda f: 50_000 <= (liq(f) or 0) < 100_000, +4,
         "Therani liquidity tiers"),
        ("LIQ<10K", lambda f: (liq(f) or 0) < 10_000, -6,
         "Therani liquidity tiers"),
        ("LIQ<1K", lambda f: 0 < (liq(f) or 0) < 1_000, -10,
         "Therani death-signal zone"),
        # 2) منطقة القيمة السوقية (Therani): $100K–$1M ≈ 75–78% moon؛
        # <$1K mcap ≈ 91% dump — حد أدنى صارم
        ("MCAP 100K-1M", lambda f: _in(mcap(f), 100_000, 1_000_000), +5,
         "Therani mcap bands"),
        ("MCAP 1-10M", lambda f: _in(mcap(f), 1_000_000, 10_000_000), +2,
         "Therani mcap bands"),
        ("MCAP<100K", lambda f: (mcap(f) or 0) < 100_000, -4,
         "Therani mcap bands"),
        ("MCAP<10K", lambda f: 0 < (mcap(f) or 0) < 10_000, -10,
         "Therani: 91% dump under $1K mcap"),
        ("MCAP>200M", lambda f: (mcap(f) or 0) > 200_000_000, -2,
         "already discovered — limited moonshot"),
        # 3) ضغط الشراء (taker buys): طلب حقيقي مقابل توزيع
        ("BP>=0.65", lambda f: (bp(f) or 0) >= 0.65, +4,
         "buy-pressure momentum"),
        ("BP>=0.55", lambda f: 0.55 <= (bp(f) or 0) < 0.65, +2,
         "buy-pressure momentum"),
        ("BP<=0.35", lambda f: (bp(f) or 0) <= 0.35, -4,
         "distribution — sellers in control"),
        ("ZERO-BUYS", lambda f: (f.get("buys") or 0) == 0
         and (f.get("sells") or 0) >= 3, -8,
         "Therani NOBUY death signal"),
        # 4) مضاعف الحجم: اهتمام مستدام (DamiDefi: حجم مستمر لأيام)
        # مقابل اهتمام ميت
        ("VOLx>=3", lambda f: (vm(f) or 0) >= 3.0, +3,
         "DamiDefi sustained volume"),
        ("VOLx<0.5", lambda f: (vm(f) or 0) < 0.5, -3,
         "dying interest"),
        # 5) نظام التقلب: المعتدل = زخم صحي؛ المفرط = فوضى ما قبل الانهيار
        ("ATR 3-10", lambda f: _in(atr(f), 3.0, 10.0), +2,
         "healthy momentum regime"),
        ("ATR>15", lambda f: (atr(f) or 0) > 15.0, -3,
         "untradeable chop / pre-dump"),
        # 6) المعنويات: ذيل السرد (Maurits: ادخل قبل انتشار الوعي)
        ("SENT>0.3", lambda f: (sent(f) or 0) > 0.3, +3,
         "narrative tailwind"),
        ("SENT<-0.3", lambda f: (sent(f) or 0) < -0.3, -4,
         "negative catalyst"),
        # 6ب) حارس التنسيق: ضجة بلا حجم = سيولة خروج (Therani: مجموعات
        # Telegram ترفع معدل الـdump بعد التخرج) — يُلغي مكسب المعنويات
        ("HYPE-NO-VOL", lambda f: (sent(f) or 0) > 0.3
         and (vm(f) or 1.0) < 1.0, -3,
         "Therani coordination/exit-liquidity risk"),
        # 7) العمر: فوضى الإطلاق مقابل نافذة الفرص
        ("AGE<1h", lambda f: (age(f) or 999) < 1.0, -5,
         "launch chaos — death system also skips first hour"),
        ("AGE 6h-7d", lambda f: _in(age(f), 6.0, 168.0), +2,
         "Therani opportunity window"),
        ("AGE>14d", lambda f: (age(f) or 0) > 336.0, -2,
         "stale at entry"),
        # 8) الكومبو القاتل: تكديس الفلاتر (بحث: 76.77% pump عند التكديس)
        ("KILLER-COMBO", lambda f: (liq(f) or 0) >= 50_000
         and _in(mcap(f), 100_000, 1_000_000)
         and (bp(f) or 0) >= 0.55, +4,
         "compound filter stack 76.77% pump"),
        # 9) حارس شائعة الإدراج: الشائعة بلا تأكيد حجم = تلفيق محتمل
        # (دراسات CEX: العب الشائعة لا الإدراج؛ الإدراج = سيولة خروج)
        ("LISTING-RUMOR", lambda f: bool(f.get("listing_rumor"))
         and (vm(f) or 1.0) < 1.5, -5,
         "CEX studies: rumor needs volume confirmation"),
        # 10) السلسلة: Solana أعمق سيولة/بيانات للميمكوينز (تحيز خفيف)
        ("CHAIN-SOL", lambda f: (f.get("chain") or "") == "solana", +1,
         "deepest memecoin liquidity"),
    ]


def _in(v, lo, hi):
    return v is not None and lo <= v <= hi


def _drop_rule(applied, total, name):
    """يُبطل حكماً أضعف سبق تطبيقه عندما يصل حكم أشد (منع التضاعف)."""
    for a in applied:
        if a[0] == name:
            return ([x for x in applied if x[0] != name], total - a[1])
    return applied, total


def prior_adjust(prob, features, _return_rules=False):
    """يطبق أحكام الخبراء على الاحتمال. يعيد (المعدّل، [الأحكام المطبقة])."""
    try:
        p = float(prob)
    except (TypeError, ValueError):
        p = 50.0
    features = features or {}
    total, applied = 0.0, []
    for name, cond, delta, src in _rules():
        try:
            if cond(features):
                # منع التضاعف: الأشد (لاحقاً في القائمة) يُبطل الأضعف
                # الذي سبق تطبيقه — LIQ<1K يغني عن LIQ<10K مثلاً
                if name == "LIQ<1K":
                    applied, total = _drop_rule(applied, total, "LIQ<10K")
                if name == "MCAP<10K":
                    applied, total = _drop_rule(applied, total, "MCAP<100K")
                total += delta
                applied.append((name, delta, src))
        except Exception:
            continue
    total = max(-PRIOR_CAP, min(PRIOR_CAP, total))
    out = max(1.0, min(99.0, p + total))
    if _return_rules:
        return out, applied
    return out


# ---------- نموذج sklearn (يتولى تدريجياً) ----------
ML_FEATURES = ["entry_score", "entry_prob", "m_atr_pct", "m_vol_mult",
               "m_buy_pressure", "sentiment", "liq_usd", "mcap_usd", "age_h"]


def _feat_vec(tr):
    """يحول سجل أرشيف إلى متجه — log للسيولة/القيمة (فروق أسّية)."""
    v = []
    for name in ML_FEATURES:
        x = tr.get(name)
        try:
            x = float(x) if x is not None else 0.0
        except (TypeError, ValueError):
            x = 0.0
        if name in ("liq_usd", "mcap_usd"):
            x = math.log1p(max(0.0, x)) / 20.0  # تطبيع تقريبي
        elif name in ("entry_score", "entry_prob"):
            x = x / 100.0
        elif name == "age_h":
            x = min(x, 336.0) / 336.0
        v.append(x)
    return v


def train(trades):
    """يدرب LogisticRegression على الصفقات المغلقة (غير الجزئية).
    يعيد (model, n) أو (None, n) عند نقص البيانات/الأصناف."""
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        rows = [t for t in (trades or [])
                if not t.get("partial") and t.get("pnl") is not None]
        if len(rows) < MODEL_START_N:
            return None, len(rows)
        X = np.array([_feat_vec(t) for t in rows])
        y = np.array([1 if (t.get("pnl") or 0) > 0 else 0 for t in rows])
        if y.sum() == 0 or y.sum() == len(y):
            return None, len(rows)  # صنف واحد فقط — لا تدريب ذا معنى
        model = LogisticRegression(max_iter=500)
        model.fit(X, y)
        return model, len(rows)
    except Exception as e:
        print(f"[calibrate] train skipped: {e}")
        return None, 0


def model_predict_proba(model, features):
    """احتمال الفوز % من النموذج — None عند الفشل."""
    try:
        import numpy as np
        X = np.array([_feat_vec(features or {})])
        return float(model.predict_proba(X)[0][1]) * 100.0
    except Exception:
        return None


def save_model(model, path=None):
    try:
        path = path or default_model_path()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(model, f)
        return True
    except Exception as e:
        print(f"[calibrate] save_model failed: {e}")
        return False


def load_model(path=None):
    try:
        path = path or default_model_path()
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def adjust(prob, features=None, model=None, n_trades=0):
    """الواجهة الرئيسية: أحكام الخبراء ثم مزج النموذج (إن وُجد ونضج).

    - n_trades < 20 → الأحكام وحدها (لا بداية باردة).
    - n_trades ≥ 20 → وزن النموذج = min(0.6, (n-20)/80).
    لا يرفع استثناءً أبداً — عند الشك يعيد الاحتمال الأصلي."""
    try:
        p0 = prior_adjust(prob, features)
    except Exception:
        try:
            p0 = max(1.0, min(99.0, float(prob)))
        except (TypeError, ValueError):
            p0 = 50.0
    try:
        if model is None or (n_trades or 0) < MODEL_START_N:
            return p0
        w = min(MODEL_MAX_W, (n_trades - MODEL_START_N) / 80.0)
        if w <= 0:
            return p0
        pm = model_predict_proba(model, features)
        if pm is None:
            return p0
        out = (1.0 - w) * p0 + w * pm
        return max(1.0, min(99.0, out))
    except Exception:
        return p0


def retrain_from_archive(archive, path=None):
    """يعيد التدريب من أرشيف الصفقات المغلقة ويحفظ النموذج.
    يعيد قاموس حالة للداشبورد."""
    status = {"n_trades": 0, "trained": False, "model_weight": 0.0,
              "priors": len(_rules())}
    try:
        model, n = train(archive)
        status["n_trades"] = n
        if model is not None:
            save_model(model, path)
            status["trained"] = True
            status["model_weight"] = round(
                min(MODEL_MAX_W, (n - MODEL_START_N) / 80.0), 3)
        else:
            status["note"] = ("priors-only" if n < MODEL_START_N
                              else "single-class — priors-only")
    except Exception as e:
        status["note"] = f"error: {e}"
    return status
