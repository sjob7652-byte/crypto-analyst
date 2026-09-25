"""قناة أوامر Brother — صندوق بريد العمليات (ops inbox).

الفكرة: Brother يحتاج أحياناً تنفيذ أمر داخل الـVM (تشخيص، إصلاح،
فحص ملفات) دون إزعاج Ayoub. Run Command الرسمي من Oracle لا يعمل
على هذا الـVM (الوكيل لا يلتقط الأوامر — مثبت كـsnap وتمت إعادة
تشغيله دون جدوى)، فهذه القناة البديلة تمر عبر البنية التي تعمل أصلاً:
الـVM يسحب main من GitHub كل تشغيل، والحالة تُرفع إلى الـGist.

آلية العمل:
  1. Brother يكتب أمراً في ops/inbox.json بصيغة:
       {"id": "unique-id", "cmd": "shell command", "timeout": 120}
     ويدفعه إلى main (commit عادي عبر GitHub).
  2. أول فحص (scan) بعد السحب يقرأ الصندوق، ينفذ الأمر مرة واحدة
     فقط (dedupe عبر state["ops"]["last_id"])، ويكتب النتيجة في
     state["ops"]["result"] فتظهر في الـGist تلقائياً.
  3. Brother يقرأ النتيجة من الـGist العام.

نموذج الثقة: أي شيء يصل إلى main موثوق — فقط حساب Ayoub على
GitHub يستطيع الدفع إلى main. لا تضع أسراراً في الأوامر
(المستودع عام): لا توكنات، لا مفاتيح، لا كلمات سر.
الأوامر تعمل كمستخدم ubuntu (مستخدم الـcron) — بلا sudo.
"""

import json
import os
import subprocess
import time
import traceback

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
INBOX_PATH = os.path.join(REPO_DIR, "ops", "inbox.json")

MAX_OUT = 8000          # حد إخراج stdout/stderr لكل أمر
DEFAULT_TIMEOUT = 120   # مهلة الأمر الافتراضية (ثوانٍ)
MAX_TIMEOUT = 300       # أقصى مهلة مسموحة


def _read_inbox():
    try:
        with open(INBOX_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def process_inbox(state):
    """تُنفَّذ داخل _scan بعد تحميل الحالة (وقبل حفظها).

    تُرجع True إذا نُفِّذ أمر جديد، False إذا لا شيء جديد/القناة معطلة.
    النتيجة تُكتب في state["ops"]["result"] وتُحفظ مع الحالة.
    """
    ops = state.setdefault("ops", {})
    inbox = _read_inbox()
    if not inbox:
        return False
    cmd_id = str(inbox.get("id") or "").strip()
    cmd = str(inbox.get("cmd") or "").strip()
    if not cmd_id or not cmd:
        return False
    if ops.get("last_id") == cmd_id:
        return False  # نُفِّذ من قبل — لا إعادة تنفيذ أبداً
    try:
        timeout = max(1, min(int(inbox.get("timeout", DEFAULT_TIMEOUT)),
                             MAX_TIMEOUT))
    except Exception:
        timeout = DEFAULT_TIMEOUT

    started = time.time()
    try:
        p = subprocess.run(["bash", "-c", cmd],
                           capture_output=True, text=True,
                           timeout=timeout,
                           cwd=os.path.expanduser("~"))
        rc, out, err = p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        rc = 124
        out = e.stdout or ""
        err = (e.stderr or "") + f"\n[TIMEOUT بعد {timeout}s]"
    except Exception:
        rc, out, err = 127, "", traceback.format_exc()[-2000:]

    ops["last_id"] = cmd_id
    # نحتفظ بآخر نتيجة فقط — لمنع تضخم الـGist
    ops["result"] = {
        "id": cmd_id,
        "rc": rc,
        "out": out[:MAX_OUT],
        "err": err[:MAX_OUT],
        "ts": time.time(),
        "dur_s": round(time.time() - started, 1),
    }
    return True
