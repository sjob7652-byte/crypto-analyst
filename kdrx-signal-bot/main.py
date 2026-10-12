# -*- coding: utf-8 -*-
"""المنسق الرئيسي: فحص كل 5 دقائق → إشارات → تنفيذ → نشر."""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import config
from scanner import scan_all, fetch_market_pulse
from wallet import PaperWallet
from executor import Executor
import publisher
import gist_pub
import sysstats
import sources

# ---------- السجلات ----------
os.makedirs(config.STATE_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("kdrx")


def load_signals_state():
    if os.path.exists(config.SIGNALS_FILE):
        try:
            with open(config.SIGNALS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"seen": {}, "last_summary": ""}


def save_signals_state(state):
    tmp = config.SIGNALS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, config.SIGNALS_FILE)


def is_cooldown_ok(state, symbol):
    """تحقق من cooldown الـ 12 ساعة لنفس الزوج."""
    last = state["seen"].get(symbol, 0)
    return (time.time() - last) > config.SIGNAL_COOLDOWN_HOURS * 3600


def run_scan_only():
    """فحص الإشارات فقط (cron كل 5 دقائق): فحص → فتح مراكز → نشر."""
    log.info("=== Scan cycle start ===")
    t0 = time.time()
    wallet = PaperWallet()
    state = load_signals_state()

    try:
        signals = scan_all()
        sources.report("binance", True)
    except Exception as e:
        log.warning(f"Scanner failed: {e}")
        sources.report("binance", False)
        signals = []

    fresh = [s for s in signals if is_cooldown_ok(state, s["symbol"])]
    log.info(f"{len(fresh)} signals pass cooldown filter")

    for sig in fresh:
        pos_id, result = wallet.open_position(sig)
        if pos_id:
            state["seen"][sig["symbol"]] = int(time.time())
            msg_id = publisher.publish_signal(sig, result)
            wallet.set_signal_msg_id(pos_id, msg_id)  # للاقتباس عند TP/SL مثل Kdrx
            log.info(f"Published signal {sig['symbol']} {sig['direction']}")
        else:
            log.info(f"Skipped {sig['symbol']}: {result}")

    save_signals_state(state)
    dt = time.time() - t0
    log.info(f"=== Scan cycle end: {dt:.1f}s, {len(fresh)} new, equity=${wallet.equity():.2f} ===")
    publish_gist(wallet)
    return {"signals": len(fresh), "seconds": round(dt, 1)}


def run_monitor_only():
    """مراقبة المراكز فقط (cron كل دقيقتين): TP/SL سريع → نشر."""
    log.info("=== Monitor cycle start ===")
    t0 = time.time()
    wallet = PaperWallet()
    ex = Executor(wallet)

    events = ex.check_positions()
    for ev in events:
        # اقتباس الإشارة الأصلية (reply) مثل Kdrx
        reply_to = None
        pos = wallet.data["positions"].get(ev.get("pos_id", ""))
        if pos:
            reply_to = pos.get("signal_msg_id")
        publisher.publish_event(ev, reply_to=reply_to)
        log.info(f"Published event {ev['reason']} {ev['symbol']} pnl={ev['pnl']}")

    # نسخ KDRX الحي: مراقبة المراكز المفتوحة من الإشارات المُحوّلة
    try:
        import kdrx_live
        for kev in kdrx_live.check_positions():
            kw = kdrx_live.get_wallet()
            pos = kw.data["positions"].get(kev.get("pos_id", ""))
            publisher.publish_kdrx_live_event(kev, reply_to=pos.get("signal_msg_id") if pos else None)
            log.info(f"KDRX-live event {kev['reason']} {kev['symbol']} pnl={kev['pnl']}")
    except Exception as e:
        log.warning(f"KDRX-live monitor failed: {e}")

    # نبض السوق كل 6 ساعات
    state = load_signals_state()
    last_pulse = state.get("last_pulse", 0)
    if time.time() - last_pulse > 6 * 3600:
        try:
            pulse = fetch_market_pulse()
            if pulse:
                publisher.publish_market_pulse(pulse)
                state["last_pulse"] = int(time.time())
                save_signals_state(state)
                log.info("Published market pulse")
        except Exception as e:
            log.warning(f"Market pulse failed: {e}")

    # حصيلة الأسبوع (مثل Kdrx) — مرة كل 7 أيام
    if time.time() - int(state.get("last_weekly", 0)) > 7 * 24 * 3600:
        try:
            week_ago = time.time() - 7 * 24 * 3600
            recent = [t for t in wallet.data.get("closed_trades", [])
                      if t.get("closed_at", 0) >= week_ago]
            publisher.publish_weekly_rollup(recent)
            state["last_weekly"] = int(time.time())
            save_signals_state(state)
            log.info("Published weekly rollup")
        except Exception as e:
            log.warning(f"Weekly rollup failed: {e}")

    dt = time.time() - t0
    log.info(f"=== Monitor cycle end: {dt:.1f}s, {len(events)} events, equity=${wallet.equity():.2f} ===")
    publish_gist(wallet)
    return {"events": len(events), "seconds": round(dt, 1)}


def publish_gist(wallet):
    """نشر الحالة إلى Gist للـ dashboard (مزامنة)."""
    try:
        kdrx_real = {}
        try:
            with open(os.path.join(os.path.dirname(__file__), "state", "kdrx_real.json")) as f:
                kdrx_real = json.load(f)
        except Exception:
            pass
        kdrx_live_state = {}
        try:
            import kdrx_live
            kdrx_live_state = kdrx_live.gist_state()
        except Exception:
            pass
        heatmap_state = {}
        try:
            with open(os.path.join(os.path.dirname(__file__), "state", "heatmap.json")) as f:
                heatmap_state = json.load(f)
        except Exception:
            pass
        gist_state = {
            "wallet": wallet.to_dict(),
            "system": sysstats.get_stats(),
            "sources": sources.get_status(),
            "kdrx_real": kdrx_real,
            "kdrx_live": kdrx_live_state,
            "heatmap": heatmap_state.get("heat", {}),
            "universe_at": heatmap_state.get("at", 0),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        gist_pub.publish(gist_state)
    except Exception as e:
        log.warning(f"Gist publish failed: {e}")


def main():
    log.info("KDRX signal bot starting (paper trading $100)")
    publisher.publish_startup()
    while True:
        try:
            run_scan_only()
            run_monitor_only()
        except Exception as e:
            log.exception(f"Cycle failed: {e}")
        log.info(f"Sleeping {config.SCAN_INTERVAL_MINUTES} min...")
        time.sleep(config.SCAN_INTERVAL_MINUTES * 60)


def run_once():
    """تشغيل دورة واحدة (للـ cron) — فحص + مراقبة."""
    r1 = run_scan_only()
    r2 = run_monitor_only()
    # الملخص اليومي
    state = load_signals_state()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("last_summary") != today:
        hour = datetime.now(timezone.utc).hour
        if hour >= 8:
            wallet = PaperWallet()
            publisher.publish_summary(wallet.stats())
            state["last_summary"] = today
            save_signals_state(state)
            log.info("Published daily summary")
    return {**r1, **r2}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--once":
            run_once()
        elif sys.argv[1] == "--scan":
            run_scan_only()
        elif sys.argv[1] == "--monitor":
            run_monitor_only()
    else:
        main()
