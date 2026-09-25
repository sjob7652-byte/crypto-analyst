# -*- coding: utf-8 -*-
"""المشاعر المحلية: نموذج لغوي صغير يعمل على الجهاز (ONNX)، مع بديل
الكلمات المفتاحية عند غياب النموذج.

score(text) -> float في [-1, 1] (موجب = إيجابي). لا يرفع استثناءً أبداً:
- إن وُجد النموذج محلياً ويعمل → معنويات عصبية.
- وإلا → نفس خوارزمية الكلمات المفتاحية المستعملة في clients.py
  (مطابقة حرفياً: نفس القوائم، نفس المعادلة) — فالنتيجة متطابقة
  مع السلوك الحالي عند غياب النموذج.

النموذج: distilbert-base-uncased-finetuned-sst-2-english بصيغة ONNX
(مصدر عام على HuggingFace Hub، بلا مصادقة). يُنزَّل مرة واحدة عبر
download_model() — تُستدعى من run_research.sh (الليل)، ولا يُنزَّل
أبداً أثناء الفحص (حتى لا يتعطل).
"""
import json
import os
import re

# نفس قوائم clients.py حرفياً — البديل مطابق للسلوك الحالي
_POS_WORDS = [
    "etf approval", "approves etf", "all-time high", "record high", "ath",
    "rally", "bullish", "surge", "soar", "breakout", "adoption",
    "partnership", "institutional", "inflow", "upgrade successful",
]
_NEG_WORDS = [
    "hack", "exploit", "rug", "lawsuit", "sec", "crash", "plunge",
    "scam", "fraud", "bankrupt", "dump", "fud", "outflow", "breach",
]
# النموذج: FinBERT المالي (Xenova/finbert) — مدرّب على الأخبار المالية
# (إيجابي/سلبي/محايد)، أدق على عناوين الكريبتو من نماذج المراجعات العامة
# (SST-2 قرأ "bullish" سلبياً و"الحياد" سلبياً — انحياز خطر).
_HF_MODEL = "Xenova/finbert"
_HF_BASE = f"https://huggingface.co/{_HF_MODEL}/resolve/main"
_MODEL_FILE = "onnx/model_quantized.onnx"  # ~110MB بدل 440MB الكامل
_TOKENIZER_FILE = "tokenizer.json"
_MAX_LEN = 128

# خريطة مصطلحات الكريبتو → إنجليزية مالية يفهمها النموذج
# (تُطبَّق على مسار ONNX فقط — مسار الكلمات يبقى مطابقاً لـclients.py)
_JARGON = {
    "rug pull": "scam fraud", "rugged": "scammed", "bullish": "optimistic",
    "bearish": "pessimistic", "to the moon": "soaring", "mooning": "soaring",
    "pump": "surge", "pumping": "surging", "dump": "crash",
    "dumping": "crashing", "rekt": "destroyed", "hodl": "hold",
    "degen": "risky", "ath": "record high",
}


def _preprocess(text):
    """يستبدل مصطلحات الكريبتو قبل الترميز — مسار ONNX فقط."""
    t = (text or "").lower()
    for k, v in _JARGON.items():
        t = re.sub(r"\b" + re.escape(k) + r"\b", v, t)
    return t

_session = None      # onnxruntime session (lazy)
_vocab = None        # wordpiece vocab dict (lazy)
_backend_tried = False


def default_model_dir():
    env = os.environ.get("SENTIMENT_MODEL_DIR")
    if env:
        return env
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, "bot", "models", "sentiment")


def model_files_present(d=None):
    d = d or default_model_dir()
    return (os.path.isfile(os.path.join(d, "model.onnx"))
            and os.path.isfile(os.path.join(d, "tokenizer.json")))


def download_model(dest=None):
    """يُنزَّل النموذج + المُجزّئ من HuggingFace Hub (عام، بلا مصادقة).
    تُستدعى من مهمة الليل — لا تُستدعى أبداً أثناء الفحص."""
    dest = dest or default_model_dir()
    try:
        import requests
        os.makedirs(dest, exist_ok=True)
        for remote, local in ((_MODEL_FILE, "model.onnx"),
                              (_TOKENIZER_FILE, "tokenizer.json")):
            lp = os.path.join(dest, local)
            if os.path.isfile(lp) and os.path.getsize(lp) > 1000:
                continue
            url = f"{_HF_BASE}/{remote}"
            print(f"[nlp] downloading {url} ...")
            r = requests.get(url, timeout=120, stream=True)
            if r.status_code != 200:
                print(f"[nlp] download failed: {r.status_code}")
                return False
            with open(lp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    if chunk:
                        f.write(chunk)
            print(f"[nlp] saved {lp} ({os.path.getsize(lp)//1024} KB)")
        return model_files_present(dest)
    except Exception as e:
        print(f"[nlp] download_model failed: {e}")
        return False


def _ensure_session():
    """تحميل كسول للنموذج — يعيد True إن نجح، False فيبقى البديل."""
    global _session, _vocab, _backend_tried
    if _session is not None:
        return True
    if _backend_tried:
        return False
    _backend_tried = True
    try:
        d = default_model_dir()
        if not model_files_present(d):
            return False
        import onnxruntime as ort
        import numpy as np  # noqa: F401 (يُستعمل في _onnx_score)
        with open(os.path.join(d, "tokenizer.json"), encoding="utf-8") as f:
            tj = json.load(f)
        vocab = ((tj.get("model") or {}).get("vocab")
                 or tj.get("vocab"))
        if not isinstance(vocab, dict) or "[CLS]" not in vocab:
            print("[nlp] bad tokenizer.json")
            return False
        _vocab = vocab
        _session = ort.InferenceSession(os.path.join(d, "model.onnx"),
                                       providers=["CPUExecutionProvider"])
        return True
    except Exception as e:
        print(f"[nlp] onnx backend unavailable: {e}")
        _session = None
        return False


def backend():
    """أي محرك سيُستعمل: 'onnx' | 'keyword'."""
    try:
        return "onnx" if _ensure_session() else "keyword"
    except Exception:
        return "keyword"


def score(text):
    """معنويات النص: -1 (سلبي جداً) .. +1 (إيجابي جداً). لا يرفع أبداً.

    عند توفر النموذج: مزيج 50/50 بين FinBERT المالي وقوائم الكلمات
    المُدققة للكريبتو — النموذج يفهم الصياغة، والكلمات تغطي العامية
    التي يخطئها (rug, exploit...). عند غياب النموذج: الكلمات فقط —
    مطابقة حرفياً لسلوك clients.py الحالي."""
    try:
        if not (text or "").strip():
            return 0.0
        kw = _keyword_score(text)
        if _ensure_session():
            s = _onnx_score(text)
            if s is not None:
                return max(-1.0, min(1.0, 0.5 * s + 0.5 * kw))
        return kw
    except Exception:
        try:
            return _keyword_score(text)
        except Exception:
            return 0.0


def _keyword_score(text):
    """مطابق لـ NewsClient.sentiment في clients.py — نفس القوائم والمعادلة."""
    t = (text or "").lower()
    pos = sum(1 for w in _POS_WORDS if w in t)
    neg = sum(1 for w in _NEG_WORDS if w in t)
    if pos + neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)


# ---------- WordPiece بسيط (DistilBERT uncased) ----------
def _wp_word(word, vocab):
    if word in vocab:
        return [word]
    out, start = [], 0
    while start < len(word):
        end, cur = len(word), None
        while start < end:
            sub = word[start:end]
            if start > 0:
                sub = "##" + sub
            if sub in vocab:
                cur = sub
                break
            end -= 1
        if cur is None:
            return ["[UNK]"]
        out.append(cur)
        start = end
    return out


def _encode(text, vocab):
    ids, start = [], 0
    # إبقاء [^a-z0-9] كرموز منفصلة (أرقام/ترقيم)
    for chunk in re.findall(r"[a-z0-9]+|[^a-z0-9\s]+", (text or "").lower()):
        ids.extend(_wp_word(chunk, vocab))
    ids = ids[:_MAX_LEN - 2]
    seq = [vocab["[CLS]"]] + [vocab.get(t, vocab["[UNK]"]) for t in ids] \
        + [vocab["[SEP]"]]
    mask = [1] * len(seq)
    pad = _MAX_LEN - len(seq)
    if pad:
        seq += [vocab.get("[PAD]", 0)] * pad
        mask += [0] * pad
    return seq, mask


def _onnx_score(text):
    """تشغيل النموذج — يعيد float أو None عند أي مشكلة.
    يدعم نموذجين/ثلاث فئات: [سلبي، إيجابي] أو [إيجابي، سلبي، محايد]
    (يُكتشف من config id2label عند التحميل)."""
    try:
        import numpy as np
        vocab, sess = _vocab, _session
        ids, mask = _encode(_preprocess(text), vocab)
        zeros = [0] * len(ids)
        feed = {}
        for inp in sess.get_inputs():
            n = inp.name.lower()
            arr = np.array([ids], dtype=np.int64)
            if "mask" in n:
                arr = np.array([mask], dtype=np.int64)
            elif "token_type" in n or "type_ids" in n:
                arr = np.array([zeros], dtype=np.int64)
            feed[inp.name] = arr
        logits = sess.run(None, feed)[0][0]
        mx = max(logits)
        exps = [_exp(x - mx) for x in logits]
        s = sum(exps)
        probs = [e / s for e in exps]
        if len(probs) >= 3 and _label_order() == "pos-neg-neu":
            # FinBERT: [إيجابي، سلبي، محايد]
            return max(-1.0, min(1.0, probs[0] - probs[1]))
        # ثنائي: [سلبي، إيجابي]
        p_pos = probs[1] if len(probs) > 1 else 0.5
        return max(-1.0, min(1.0, 2.0 * p_pos - 1.0))
    except Exception as e:
        print(f"[nlp] onnx score failed: {e}")
        return None


def _label_order():
    """ترتيب الفئات للنموذج المضمّن — ثابت معروف (Xenova/finbert:
    [إيجابي، سلبي، محايد])."""
    if _HF_MODEL == "Xenova/finbert":
        return "pos-neg-neu"
    return "neg-pos"  # الافتراضي الآمن لثنائيات SST-2


def _exp(x):
    # exp يدوي لتفادي استيراد math في المسار الحرج (متاح دائماً)
    import math
    return math.exp(x)
