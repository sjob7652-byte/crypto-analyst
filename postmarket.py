#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
تقرير ما بعد السوق (Post-Market Analysis) — يعمل مرة واحدة يومياً.

يجمع الصفقات المغلقة خلال آخر 24 ساعة من state.json، يحلل الأنماط
(معدل الفوز، توقيت الدخول، نوع السوق، سياق البيتكوين)، ثم يرسل
تقريراً مسائياً إلى Telegram.

مصدر التحليل العميق:
  1) نموذج محلي عبر Ollama إن وُجد (OLLAMA_HOST / OLLAMA_MODEL)
     — يعمل تلقائياً بلا أي API خارجي ولا حدود استهلاك.
  2) وإلا: محلل حتمي مدمج يستخرج الأنماط الحقيقية من البيانات
     — يعمل دائماً ومجاناً 100%.

ملاحظة صريحة: GitHub Actions خوادم مؤقتة بلا ذاكرة دائمة، لذلك لا يمكن
تشغيل Ollama "محلياً" عليها بشكل دائم. إن أردت التحليل بالنموذج المحلي
فعلاً، شغّل البوت على حاسوبك/VPS واضبط OLLAMA_HOST — نفس السكربت
سيستعمل النموذج تلقائياً.
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone

import requests

import alerts
import clients
import state

WIN_OUTCOMES = ("tp1", "tp2", "tp3", "be")
DAY = 24 * 3600

# عائد تقديري لكل نتيجة (على صفقة وهمية) — بعد خصم الانزلاق تقريباً
OUTCOME_PCT = {"tp1": 0.24, "tp2": 0.62, "tp3": 1.38, "sl": -0.28,
               "rug": -0.90, "expired": 0.0, "be": 0.09}
OUTCOME_AR = {"tp1": "الهدف 1", "tp2": "الهدف 2", "tp3": "الهدف 3",
              "sl": "وقف الخسارة", "rug": "سحب سيولة 🧨",
              "expired": "انتهت المدة", "be": "تعادل مؤمن ⚖️"}


# ---------- جمع البيانات ----------

def day_trades(s, now=None):
    now = now or time.time()
    out = []
    for h in s.get("history") or []:
        try:
            t = float(h.get("time") or 0)
        except Exception:
            continue
        if now - t <= DAY:
            out.append(h)
    return out


def infer_kind(name):
    """تخمين نوع السوق من اسم العملة (الـhistory لا يخزن النوع)."""
    n = (name or "").upper()
    if "/USDT" in n or n.endswith("USDT"):
        return "binance"
    return "dex"


def btc_24h_change():
    try:
        kl = clients.binance_klines("BTCUSDT", interval="1h", limit=25)
        if kl and len(kl) > 1:
            o = float(kl[0][1])
            c = float(kl[-1][4])
            if o > 0:
                return (c - o) / o * 100
    except Exception:
        pass
    return None


# ---------- التحليل الحتمي ----------

def analyze(trades):
    a = {"n": len(trades), "wins": 0, "sl": 0, "rug": 0, "expired": 0,
         "by_outcome": {}, "by_kind": {}, "by_band": {},
         "by_hour": {}, "scores_win": [], "scores_loss": [],
         "pnl_est": 0.0, "names": []}
    for h in trades:
        oc = h.get("outcome") or "؟"
        a["by_outcome"][oc] = a["by_outcome"].get(oc, 0) + 1
        kind = infer_kind(h.get("name"))
        a["by_kind"].setdefault(kind, {"n": 0, "w": 0})
        a["by_kind"][kind]["n"] += 1
        band = h.get("band") or "؟"
        a["by_band"].setdefault(band, {"n": 0, "w": 0})
        a["by_band"][band]["n"] += 1
        try:
            hr = datetime.fromtimestamp(float(h["time"]),
                                       tz=timezone.utc).hour
            a["by_hour"].setdefault(hr, {"n": 0, "w": 0})
            a["by_hour"][hr]["n"] += 1
        except Exception:
            pass
        sc = h.get("score")
        won = oc in WIN_OUTCOMES
        if won:
            a["wins"] += 1
            a["by_kind"][kind]["w"] += 1
            a["by_band"][band]["w"] += 1
            try:
                a["by_hour"][hr]["w"] += 1
            except Exception:
                pass
            if isinstance(sc, (int, float)):
                a["scores_win"].append(sc)
        else:
            if oc == "sl":
                a["sl"] += 1
            elif oc == "rug":
                a["rug"] += 1
            elif oc == "expired":
                a["expired"] += 1
            if isinstance(sc, (int, float)):
                a["scores_loss"].append(sc)
        a["pnl_est"] += 10.0 * OUTCOME_PCT.get(oc, 0.0)
        a["names"].append(f"{h.get('name', '؟')} → "
                         f"{OUTCOME_AR.get(oc, oc)}")
    n = a["n"]
    a["win_rate"] = (100.0 * a["wins"] / n) if n else 0.0
    return a


def _avg(xs):
    return sum(xs) / len(xs) if xs else None


def deterministic_insights(a, btc_chg):
    """استنتاجات حتمية من البيانات — تُذكر فقط إن دعمتها الأرقام."""
    ins = []
    n = a["n"]
    if n == 0:
        return ["لا توجد صفقات مغلقة خلال آخر 24 ساعة — لا أنماط لتحليلها اليوم."]
    wr = a["win_rate"]
    if wr >= 60:
        ins.append(f"يوم إيجابي: معدل الفوز {wr:.0f}% — الالتزام بإشارات "
                   f"النقاط العالية يؤتي ثماره.")
    elif wr < 40:
        ins.append(f"يوم صعب: معدل الفوز {wr:.0f}% فقط — راجع معايير "
                   f"الدخول قبل الغد.")
    # DEX مقابل Binance
    for kind, d in a["by_kind"].items():
        if d["n"] >= 2:
            w = 100.0 * d["w"] / d["n"]
            label = "المنصات (Binance)" if kind == "binance" else "العملات الجديدة (DEX)"
            ins.append(f"{label}: {d['w']}/{d['n']} ناجحة ({w:.0f}%).")
    # توقيت الدخول (بتوقيت المغرب)
    bad_hours = [h for h, d in a["by_hour"].items()
                 if d["n"] >= 2 and d["w"] == 0]
    if bad_hours:
        local = sorted((h + 1) % 24 for h in bad_hours)
        ins.append("كل الصفقات المفتوحة حوالي الساعة " +
                   "، ".join(f"{h:02d}:00" for h in local) +
                   " (بتوقيت المغرب) خسرت اليوم — انتبه لهذا التوقيت.")
    # النقاط: الفائزون مقابل الخاسرين
    aw, al = _avg(a["scores_win"]), _avg(a["scores_loss"])
    if aw is not None and al is not None and (a["scores_win"] and
                                              a["scores_loss"]):
        if aw - al >= 8:
            ins.append(f"متوسط نقاط الصفقات الرابحة ({aw:.0f}) أعلى بوضوح "
                       f"من الخاسرة ({al:.0f}) — فلتر النقاط يعمل.")
        elif al > aw:
            ins.append(f"تنبيه: متوسط نقاط الخاسرة ({al:.0f}) أعلى من "
                       f"الرابحة ({aw:.0f}) — النقاط وحدها لم تحمِ اليوم.")
    # سياق البيتكوين
    if btc_chg is not None:
        if btc_chg <= -3 and a["sl"] >= 2:
            ins.append(f"تزامنت معظم ضربات وقف الخسارة مع هبوط البيتكوين "
                       f"({btc_chg:+.1f}% خلال 24h) — السوق العام كان ضاغطاً.")
        elif btc_chg >= 3 and wr < 40:
            ins.append(f"البيتكوين صعد ({btc_chg:+.1f}%) لكن الإشارات فشلت — "
                       f"المشكلة في اختيار العملات لا في السوق العام.")
    if a["rug"] >= 1:
        ins.append(f"🧨 {a['rug']} حالة سحب سيولة اليوم — تمت إضافة العملات "
                   f"ومطوريها للقائمة السوداء تلقائياً ولن تصلك إشارات منهم.")
    if n < 6:
        ins.append("ملاحظة: العينة صغيرة اليوم — لا تبنِ قواعد صارمة "
                   "على بضع صفقات.")
    return ins


# ---------- النموذج المحلي (Ollama) ----------

def ollama_commentary(summary_text):
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "llama3.1")
    prompt = (
        "أنت مدير محفظة عملات ميم خبير. حلل هذا السجل اليومي للصفقات المغلقة "
        "مع سياق السوق:\n\n" + summary_text +
        "\n\nالمطلوب بالعربية: ما الأنماط المشتركة للصفقات التي ضربت وقف "
        "الخسارة اليوم مقارنة بحركة السوق؟ أعط 3-5 نقاط مركزة وعملية "
        "لتحسين الغد، بلا مقدمات طويلة."
    )
    try:
        r = requests.post(
            host + "/api/generate",
            json={"model": model, "prompt": prompt, "stream": False,
                  "options": {"num_predict": 500, "temperature": 0.3}},
            timeout=240,
        )
        if r.status_code == 200:
            txt = (r.json().get("response") or "").strip()
            return txt or None
    except Exception:
        pass
    return None


# ---------- بناء التقرير ----------

def build_report(a, btc_chg, ai_text, day_label):
    n, wr = a["n"], a["win_rate"]
    pnl = a["pnl_est"]
    pnl_s = f"{pnl:+.2f}$"
    lines = [
        f"🌙 <b>تقرير ما بعد السوق — {day_label}</b>",
        "━━━━━━━━━━━━",
        f"📊 صفقات مغلقة (24h): <b>{n}</b>",
        f"✅ رابحة: {a['wins']} ({wr:.0f}%)  |  🛑 وقف خسارة: {a['sl']}  |  "
        f"🧨 سحب سيولة: {a['rug']}  |  ⌛ انتهت: {a['expired']}",
        f"💰 الربح/الخسارة التقديري (محفظة وهمية $10/صفقة): <b>{pnl_s}</b>",
    ]
    if btc_chg is not None:
        lines.append(f"₿ البيتكوين (24h): <b>{btc_chg:+.1f}%</b>")
    lines += ["", "🔍 <b>أنماط مرصودة:</b>"]
    for ins in deterministic_insights(a, btc_chg):
        lines.append(f"• {ins}")
    if ai_text:
        lines += ["", "🤖 <b>تحليل النموذج المحلي (Ollama):</b>", ai_text]
    else:
        lines += ["",
                  "ℹ️ النموذج المحلي غير متاح على خوادم GitHub المؤقتة — "
                  "التحليل أعلاه إحصائي من بياناتك الفعلية."]
    lines += ["",
              "⚠️ الأنماط وصفية من سجل اليوم وليست ضماناً للغد — "
              "عملات الميم تبقى عالية المخاطر."]
    return "\n".join(lines)


# ---------- التشغيل ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="طباعة التقرير فقط بلا إرسال")
    ap.add_argument("--force", action="store_true",
                    help="إرسال حتى لو أُرسل تقرير اليوم")
    ap.add_argument("--file",
                    help="ملف state.json للتجربة محلياً (بدل Gist)")
    args = ap.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            s = json.load(f)
        save_state = False
    else:
        s = state.load()
        save_state = True

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not args.force and s.get("postmarket_last") == today and not args.dry_run:
        print("تقرير اليوم أُرسل مسبقاً — تخطٍ.")
        return

    trades = day_trades(s)
    a = analyze(trades)
    btc_chg = btc_24h_change()
    day_label = datetime.now(timezone.utc).strftime("%d/%m/%Y")

    parts = [
        f"صفقات مغلقة: {a['n']}، فوز: {a['wins']} ({a['win_rate']:.0f}%)، "
        f"وقف خسارة: {a['sl']}، سحب سيولة: {a['rug']}",
        "حسب النوع: " + (", ".join(
            f"{k}: {d['w']}/{d['n']}" for k, d in a["by_kind"].items())
            or "لا يوجد"),
        (f"البيتكوين 24h: {btc_chg:+.1f}%"
         if btc_chg is not None else "البيتكوين: غير متاح"),
        "الصفقات:\n" + "\n".join(" - " + x for x in a["names"][:30]),
    ]
    summary = "\n".join(parts)
    ai_text = None if args.dry_run else ollama_commentary(summary)

    msg = build_report(a, btc_chg, ai_text, day_label)
    ok = alerts.send(msg, dry_run=args.dry_run)

    if ok and save_state and not args.dry_run:
        s["postmarket_last"] = today
        try:
            state.save(s)
        except Exception as e:
            print("تعذر حفظ الحالة:", e)


if __name__ == "__main__":
    main()
