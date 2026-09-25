#!/bin/bash
# مهمة الأبحاث الليلية: ملء تاريخي تكميلي + نموذج المشاعر + باكتست +
# إعادة معايرة — ثقيلة عمداً (تستعمل المعالجات كاملين) لتُشغَّل ليلاً.
# تُشغَّل عبر cron مع flock (يمنع التداخل) — كل خطوة مستقلة: فشل واحدة
# لا يوقف البقية، وإعادة التشغيل آمنة (idempotent).
REPO_DIR="$HOME/bot/crypto-analyst"
cd "$REPO_DIR" || exit 1
git fetch -q origin >/dev/null 2>&1 && git reset -q --hard origin/main >/dev/null 2>&1 || true
if [ -f "$HOME/bot/.env" ]; then set -a; . "$HOME/bot/.env"; set +a; fi
PY="$HOME/bot/venv/bin/python"

echo "=== research $(date -u +%FT%TZ) ==="

# 1) ملء تاريخي تكميلي (يكمل من حيث توقف — بلا تكرار)
$PY -c "from store import MarketStore; ms=MarketStore(); print('backfill:', ms.backfill(days=2), 'snapshots'); ms.close()" 2>&1 || true

# 2) نموذج المشاعر المحلي (يُنزَّل مرة واحدة فقط — يُتخطى إن وُجد)
$PY -c "import nlp; print('sentiment:', 'onnx-ready' if nlp.download_model() else 'keyword-fallback')" 2>&1 || true

# 3) الباكتست (4 عمليات متوازية) → state['research']['backtest']
$PY backtest.py --days 120 --workers 4 --write-state 2>&1 || true

# 4) إعادة تدريب المعايرة من أرشيف الصفقات → state['research']['calibrator']
$PY -c "
try:
    import time
    from statelock import state_locked
    import state as st, calibrate
    with state_locked():
        s = st.load()
        arch = (s.get('paper') or {}).get('closed_trades') or []
        status = calibrate.retrain_from_archive(arch)
        r = s.setdefault('research', {})
        r['calibrator'] = dict(status, time=time.time())
        st.save(s)
    print('calibrator:', status)
except Exception as e:
    print('calibrate step failed:', e)
" 2>&1 || true

echo "=== research done ==="
