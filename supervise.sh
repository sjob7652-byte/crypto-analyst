#!/bin/bash
# supervise.sh — يحافظ على عامل مختبر الأبحاث (research2.py) حيّاً.
# (flow.py انتقل إلى systemd: quant-engine.service — لا يُدار من cron هنا،
#  وstream.py حُذف: flow.py هو البديل الشامل.)
# خامل وآمن: يُشغَّل كل دقيقة عبر cron، يبدأ الغائب فقط.
# لا يقتل شيئاً أبداً، ولا يبدأ نسخة مكررة (pgrep قبل كل بدء).
set -u

PY="/home/ubuntu/bot/venv/bin/python"
REPO="/home/ubuntu/bot/crypto-analyst"
BOT="/home/ubuntu/bot"

start_if_missing() {
  local script="$1" log="$2"
  if pgrep -f "[p]ython.*${script}" >/dev/null 2>&1 || \
     pgrep -f "${script}" >/dev/null 2>&1; then
    return 0
  fi
  if [ ! -x "$PY" ]; then
    echo "[supervise] python missing: $PY" >> "$BOT/heavy.log"
    return 1
  fi
  if [ ! -f "$REPO/$script" ]; then
    echo "[supervise] script missing: $REPO/$script" >> "$BOT/heavy.log"
    return 1
  fi
  echo "[supervise] starting $script at $(date -u +%FT%TZ)" >> "$BOT/heavy.log"
  # nice -n 19: أولوية منخفضة — الفاحص والمراقب يفوزان دائماً بالمعالج
  nohup nice -n 19 "$PY" "$REPO/$script" >> "$log" 2>&1 &
  disown 2>/dev/null || true
}

start_if_missing "research2.py" "$BOT/research2.log"
