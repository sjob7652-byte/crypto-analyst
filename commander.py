#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
مستمع أوامر Telegram — Commander.

حلقة long-poll دائمة (getUpdates) تنفذ أوامر المالك على المحفظة الوهمية:
  /status  /sell <X|all>  /halt [hours]  /resume  /help

قواعد صارمة:
- لا يعالج أي رسالة ليست من TELEGRAM_CHAT_ID (تجاهل تام + تسجيل).
- لا يخزن أي سر في الحالة (الـGist عام — state.py يرفض الحفظ عند الشك).
- أي تعديل للحالة يتم تحت قفل state.lock (statelock) المشترك مع السكانر،
  لمنع سباق last-write-wins على الـGist.
- ردود الأوامر Telegram فقط (log_alert=False) — بلا أثر في الداشبورد.
- يحدّث نفسه تلقائياً: عند تغير origin/main يعيد الضبط ويعيد التشغيل.

الإقلاع: يُستدعى من main.py (ensure_commander) — لا يحتاج cron خاص.
"""

import html
import os
import subprocess
import sys
import time
import traceback

import requests

# يستورد دوال البوت الأصلية (main محمي بـ if __name__ == "__main__" — الاستيراد آمن)
import state as st
import main as M
import alerts
from config import PAPER_SLIPPAGE
from statelock import state_locked

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(REPO_DIR)
PID_FILE = os.path.join(BOT_DIR, "commander.pid")
OFFSET_FILE = os.path.join(BOT_DIR, "commander.offset")

SELF_UPDATE_EVERY = 300   # فحص تحديث الكود كل 5 دقائق
POLL_TIMEOUT = 25         # long-poll getUpdates
NET_TIMEOUT = 30

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or ""


# ---------- أدوات Telegram ----------
def tg(method, **params):
    r = requests.post(
        "https://api.telegram.org/bot%s/%s" % (TOKEN, method),
        json=params, timeout=NET_TIMEOUT)
    return r.json()


def reply(text):
    # ردود الأوامر Telegram فقط — لا تُسجل في الداشبورد
    try:
        alerts.send(text, dry_run=False, log_alert=False)
    except Exception as e:
        print("reply failed:", e)


def esc(x):
    return html.escape(str(x), quote=False)


# ---------- الـoffset ----------
def load_offset():
    try:
        with open(OFFSET_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None


def save_offset(off):
    tmp = OFFSET_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(off))
    os.replace(tmp, OFFSET_FILE)


def drain_first_run():
    """أول تشغيل بلا offset: نتجاوز كل التحديثات القديمة ولا ننفذ شيئاً —
    أمر قديم يُعاد تنفيذه قد يبيع صفقة لم تعد موجودة أو أسوأ."""
    try:
        data = tg("getUpdates", timeout=5)
        results = data.get("result", []) if data.get("ok") else []
        off = max((u["update_id"] for u in results), default=-1) + 1
    except Exception as e:
        print("drain failed:", e)
        off = 0
    save_offset(off)
    print("first run: drained, offset=%d (old commands ignored)" % off)
    return off


# ---------- مطابقة الصفقات ----------
def all_positions(s):
    """اتحاد القاموسين (paper.positions هو المرجع، s.positions مرآة قديمة)."""
    merged = {}
    for pid, pos in (s.get("positions") or {}).items():
        merged[pid] = pos
    for pid, pos in (s.get("paper", {}).get("positions") or {}).items():
        merged[pid] = pos
    return merged


def match_positions(positions, query):
    q = (query or "").strip()
    qu = q.upper()
    if not qu:
        return []
    # مطابقة تامة أولاً — تفوز فوراً
    for pid in positions:
        if pid == q or pid.upper() == qu:
            return [pid]
    hits = []
    for pid, pos in positions.items():
        sym = (pos.get("symbol") or "").upper()
        name = (pos.get("name") or "").upper()
        if (pid.upper().endswith(":" + qu)
                or (sym and sym == qu)
                or (name and name.startswith(qu))):
            hits.append(pid)
    return hits


def pos_label(pid, pos):
    return esc(pos.get("name") or pos.get("symbol") or pid)


# ---------- الأوامر ----------
HELP_TEXT = (
    "🤖 <b>أوامر البوت</b>\n"
    "/status — ملخص المحفظة الوهمية\n"
    "/sell &lt;رمز|all&gt; — إغلاق فوري لصفقة (مثال: <code>/sell DOGE</code>)\n"
    "/halt [ساعات] — إيقاف الشراء مؤقتاً (الافتراضي 24)\n"
    "/resume — استئناف الشراء\n"
    "/help — هذه القائمة"
)


def cmd_status():
    with state_locked():
        s = st.load()
        summ = M.paper_summary(s)
        positions = all_positions(s)
    lines = [
        "💼 <b>المحفظة الوهمية</b>",
        "السيولة: $%.2f" % summ["cash"],
        "القيمة الإجمالية: $%.2f (%+.1f%%)" % (summ["total"], summ["pct"]),
        "المفتوحة: %d | المغلقة: %d | نسبة الفوز: %.0f%%"
        % (summ["open"], summ["closed"], summ["winrate"]),
    ]
    if positions:
        names = ", ".join(pos_label(pid, p)
                          for pid, p in list(positions.items())[:8])
        lines.append("الصفقات: " + names)
    halt = (s.get("paper") or {}).get("halt_until", 0)
    if halt and time.time() < halt:
        lines.append("⏸️ الشراء متوقف حتى %s UTC"
                     % time.strftime("%H:%M", time.gmtime(halt)))
    reply("\n".join(lines))


def cmd_sell(arg):
    if not arg:
        reply("الاستعمال: <code>/sell &lt;رمز|all&gt;</code> — مثال: <code>/sell DOGE</code>")
        return
    with state_locked():
        s = st.load()
        p = s["paper"]
        positions = all_positions(s)
        if not positions:
            reply("لا توجد صفقات مفتوحة.")
            return
        if arg.strip().lower() == "all":
            targets = list(positions.keys())
            multi_ok = True  # "الكل" مقصود صراحة — ليس غموضاً
        else:
            targets = match_positions(positions, arg)
            multi_ok = False
        if not targets:
            open_list = ", ".join(pos_label(pid, positions[pid])
                                  for pid in list(positions)[:10])
            reply("❌ لا مطابقة لـ <code>%s</code>.\nالمفتوحة: %s"
                  % (esc(arg), open_list))
            return
        if len(targets) > 1 and not multi_ok:
            cands = "\n".join("• <code>%s</code> — %s"
                              % (esc(t), pos_label(t, positions[t]))
                              for t in targets[:10])
            reply("⚠️ عدة مطابقات — حدد بدقة:\n%s" % cands)
            return
        results = []
        for pid in targets:
            pos = positions[pid]
            # الصفقة الحقيقية (في المحفظة) تُغلق مع رد الكاش؛
            # مدخل المرآة العلوية فقط يُحذف بلا حركة كاش (منع تضخيم وهمي)
            in_wallet = pid in p["positions"]
            if in_wallet:
                price, _liq = M.current_price(pos)
                if not price:
                    results.append("❌ %s: تعذر جلب السعر — تُركت مفتوحة"
                                   % pos_label(pid, pos))
                    continue
                eff_price = price * (1 - PAPER_SLIPPAGE)
                pnl, _proceeds = M._paper_close(p, pid, pos, eff_price, "CMD",
                                                s, False)
                p["positions"].pop(pid, None)
                invested = (pos.get("invested")
                            or (pos.get("qty", 0) * pos.get("entry", 0)) or 1)
                pct = pnl / invested * 100
                results.append("✅ <b>%s</b>: $%+.2f (%+.1f%%)"
                               % (pos_label(pid, pos), pnl, pct))
            else:
                results.append("🧹 <b>%s</b>: حُذفت من المرآة (ليست في المحفظة)"
                               % pos_label(pid, pos))
            s.get("positions", {}).pop(pid, None)
            st.record_outcome(s, pos, "manual")
        st.save(s)
    reply("الإغلاق اليدوي (%d):\n%s" % (len(results), "\n".join(results)))


def cmd_halt(arg):
    try:
        hours = float((arg or "24").strip())
    except ValueError:
        reply("عدد الساعات غير صالح — مثال: <code>/halt 12</code>")
        return
    if not 0 < hours <= 168:
        reply("المدة يجب أن تكون بين 1 و 168 ساعة.")
        return
    with state_locked():
        s = st.load()
        until = time.time() + hours * 3600
        s["paper"]["halt_until"] = until
        st.save(s)
    reply("⏸️ تم إيقاف الشراء لمدة %.1f ساعة (حتى %s UTC).\n"
          "الصفقات المفتوحة تستمر في المتابعة."
          % (hours, time.strftime("%Y-%m-%d %H:%M", time.gmtime(until))))


def cmd_resume():
    with state_locked():
        s = st.load()
        s["paper"]["halt_until"] = 0
        st.save(s)
    reply("▶️ تم استئناف الشراء.")


def handle_update(u):
    msg = u.get("message") or u.get("edited_message")
    if not msg:
        return
    chat_id = str((msg.get("chat") or {}).get("id"))
    # ---- أمن: المالك فقط ----
    if chat_id != CHAT_ID:
        print("[SECURITY] ignored update from chat_id=%s" % chat_id)
        return
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return
    parts = text.split(None, 1)
    cmd = parts[0].split("@")[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    print("cmd from owner: %s" % cmd)
    try:
        if cmd in ("/help", "/start"):
            reply(HELP_TEXT)
        elif cmd == "/status":
            cmd_status()
        elif cmd == "/sell":
            cmd_sell(arg)
        elif cmd == "/halt":
            cmd_halt(arg)
        elif cmd == "/resume":
            cmd_resume()
        else:
            reply("أمر غير معروف. /help لعرض القائمة.")
    except TimeoutError:
        reply("⏳ النظام مشغول بفحص جارٍ — أعد المحاولة بعد دقيقة.")


# ---------- التحديث الذاتي ----------
def maybe_self_update():
    """إن تغير origin/main: إعادة الضبط على الكود الجديد وإعادة التشغيل."""
    try:
        cur = subprocess.run(
            ["git", "-C", REPO_DIR, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        subprocess.run(["git", "-C", REPO_DIR, "fetch", "-q", "origin"],
                       timeout=90, check=False)
        new = subprocess.run(
            ["git", "-C", REPO_DIR, "rev-parse", "origin/main"],
            capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as e:
        print("self-update check failed:", e)
        return
    if cur and new and cur != new:
        print("self-update: %s -> %s — restarting" % (cur[:7], new[:7]))
        subprocess.run(["git", "-C", REPO_DIR, "reset", "-q", "--hard",
                        "origin/main"], timeout=120, check=False)
        # الـoffset محفوظ مسبقاً — إعادة التشغيل آمنة
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])


# ---------- الحلقة الرئيسية ----------
def run():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    offset = load_offset()
    if offset is None:
        offset = drain_first_run()
    else:
        print("resuming with offset=%d" % offset)
    last_update_check = 0.0
    while True:
        try:
            if time.time() - last_update_check >= SELF_UPDATE_EVERY:
                maybe_self_update()  # قد يعيد التشغيل عبر execv
                last_update_check = time.time()
            data = tg("getUpdates", offset=offset, timeout=POLL_TIMEOUT,
                      allowed_updates=["message"])
            if not data.get("ok"):
                print("getUpdates not ok:", str(data)[:200])
                time.sleep(5)
                continue
            for upd in data.get("result", []):
                offset = max(offset, upd.get("update_id", offset - 1) + 1)
                try:
                    handle_update(upd)
                except Exception:
                    # أمر واحد فاشل لا يقتل الحلقة أبداً
                    traceback.print_exc()
            save_offset(offset)
        except Exception:
            traceback.print_exc()
            time.sleep(10)  # لا خروج صامت أبداً


if __name__ == "__main__":
    if not TOKEN or not CHAT_ID:
        print("FATAL: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing — "
              "refusing to start (fail-closed)")
        sys.exit(2)
    print("commander starting — owner chat_id=%s…%s"
          % (CHAT_ID[:3], CHAT_ID[-2:]))
    run()
