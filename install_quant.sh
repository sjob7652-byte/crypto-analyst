#!/bin/bash
# install_quant.sh — يثبت خدمة systemd لمحرك تدفق الأوامر (flow.py).
# يُشغَّل كـ root حصراً (يكتب في /etc/systemd/system).
# خامل: آمن لإعادة التشغيل — نفس النتيجة في كل مرة.
set -u

REPO="/home/ubuntu/bot/crypto-analyst"
UNIT="quant-engine.service"
SRC="$REPO/systemd/$UNIT"
DST="/etc/systemd/system/$UNIT"

if [ "$(id -u)" -ne 0 ]; then
  echo "يجب التشغيل كـ root (sudo)" >&2
  exit 1
fi
if [ ! -f "$SRC" ]; then
  echo "ملف الوحدة غائب: $SRC" >&2
  exit 1
fi

cp "$SRC" "$DST"
chmod 644 "$DST"
systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null
systemctl restart "$UNIT"
sleep 3
echo "--- status ---"
systemctl is-active "$UNIT"
echo "--- listening on 127.0.0.1:5559 ---"
ss -ltn 2>/dev/null | grep 5559 || echo "(not yet listening — engine may still be ranking symbols)"
