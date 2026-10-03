# -*- coding: utf-8 -*-
"""مستمع Telegram: 📊 حلّل أصلك الآن — تحليل فوري لأي عملة عند الطلب (مثل Kdrx).

يعمل عبر cron كل دقيقة مع flock: كل تشغيل يستطلع ~50 ثانية ثم يخرج طوعاً
ليُحرر القفل — هكذا لا يمكن لتشغيلٍ عالق أن يُسكت المستمع بصمتٍ للأبد.
يستجيب فقط لصاحب البوت (TELEGRAM_CHAT_ID) — يتجاهل أي شخص آخر بصمت.
"""

import json
import logging
import os
import re
import sys
import time

import requests

import config
from scanner import analyze_asset, fetch_market_pulse, fetch_klines
from wallet import PaperWallet
import publisher
import kdrx_live

os.makedirs(config.STATE_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [LISTENER] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(config.STATE_DIR, "listener.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("listener")

OFFSET_FILE = os.path.join(config.STATE_DIR, "listener_offset.txt")
API = f"{config.TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}"

# أسماء عربية شائعة → رمز
AR_ALIASES = {
    "بتكوين": "BTCUSDT", "بيتكوين": "BTCUSDT", "البتكوين": "BTCUSDT",
    "إيثيريوم": "ETHUSDT", "الايثيريوم": "ETHUSDT", "ايثيريوم": "ETHUSDT",
    "سولانا": "SOLUSDT", "ريبل": "XRPUSDT",
    "دوج": "DOGEUSDT", "دوجكوين": "DOGEUSDT",
    "بينانس": "BNBUSDT", "كاردانو": "ADAUSDT", "أفالانش": "AVAXUSDT",
    "تشين": "LINKUSDT", "لينك": "LINKUSDT",
    "بولكادوت": "DOTUSDT", "لايتكوين": "LTCUSDT",
    "شيبا": "SHIBUSDT", "بيبي": "PEPEUSDT",
    "أربيتروم": "ARBUSDT", "أوبتيميزم": "OPUSDT",
    "نيير": "NEARUSDT", "أبتوس": "APTUSDT", "سوي": "SUIUSDT",
    "أفاكس": "AVAXUSDT", "يونيسواب": "UNIUSDT",
}

_last_reply = {}  # chat_id → timestamp (منع الإغراق)


def tg(method, **params):
    # getUpdates uses long-polling (Telegram waits up to `timeout` seconds),
    # so the HTTP timeout must exceed it — otherwise every poll times out.
    # (نُبقي الاستطلاع قصيراً لأن التشغيل كله محدود بـ ~50 ثانية — انظر main)
    http_timeout = 40 if method == "getUpdates" else 20
    try:
        r = requests.post(f"{API}/{method}", json=params, timeout=http_timeout)
        if r.status_code == 200:
            return r.json().get("result")
        log.warning(f"tg {method} failed: {r.status_code} {r.text[:150]}")
    except Exception as e:
        log.warning(f"tg {method} error: {e}")
    return None


def send(chat_id, text, reply_to=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    return tg("sendMessage", **payload)


def load_offset():
    try:
        with open(OFFSET_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return 0


def save_offset(offset):
    tmp = OFFSET_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(offset))
    os.replace(tmp, OFFSET_FILE)


def parse_symbol(text):
    """تحويل نص المستخدم إلى رمز Binance مثل BTCUSDT."""
    t = text.strip()
    if t in AR_ALIASES:
        return AR_ALIASES[t]
    # إزالة المسافات والشرطات: "ETH / USDT" → "ETHUSDT"
    t = re.sub(r"[\s\-/_]+", "", t).upper()
    t = re.sub(r"[^A-Z0-9]", "", t)
    if not t:
        return None
    if t.endswith("USDT"):
        sym = t
    else:
        sym = t + "USDT"
    if not re.fullmatch(r"[A-Z0-9]{2,20}USDT", sym):
        return None
    return sym


def help_text():
    return (
        "📊 <b>بوت التحليل الفوري</b>\n"
        "\n"
        "أرسل اسم أي عملة وسأحلّلها فوراً (فريم 4 ساعات):\n"
        "مثال: <code>BTC</code> أو <code>ETH/USDT</code> أو <code>سولانا</code>\n"
        "\n"
        "الأوامر:\n"
        "📈 <code>نبض</code> — نبض السوق الآن\n"
        "💰 <code>محفظة</code> — حالة المحفظة الورقية\n"
        "📋 <b>حوّل أي إشارة KDRX</b> (forward) لأفتح نسختها الورقية فوراً\n"
        "❓ <code>مساعدة</code> — هذه الرسالة"
    )


def handle(chat_id, text, msg_id):
    t = text.strip()
    low = t.lower()

    # إشارة KDRX مُحوّلة من أيوب → فتح نسخة ورقية حية
    if kdrx_live.looks_like_kdrx(t):
        parsed, reason = kdrx_live.parse_kdrx_signal(t)
        if not parsed:
            send(chat_id,
                 f"⚠️ وصلتني رسالة KDRX لكن ما فهمت الأرقام: {reason}\n"
                 "صيفط الإشارة كاملة (الدخول + الوقف + الأهداف الثلاثة).",
                 reply_to=msg_id)
            return
        pos_id, res = kdrx_live.open_kdrx_trade(parsed)
        if pos_id:
            w = kdrx_live.get_wallet()
            w.set_signal_msg_id(pos_id, msg_id)  # للاقتباس عند TP/SL
            send(chat_id, publisher.kdrx_live_open_message(parsed, res), reply_to=msg_id)
            log.info(f"KDRX-live opened {parsed['symbol']} {parsed['direction']} entry={parsed['entry']}")
        else:
            send(chat_id, f"⚠️ ما فتحت الصفقة: {kdrx_live.describe_open_error(res)}",
                 reply_to=msg_id)
        return

    if low in ("/start", "مساعدة", "مساعده", "help", "؟", "?"):
        send(chat_id, help_text(), reply_to=msg_id)
        return

    if low in ("نبض", "النبض", "/pulse", "pulse"):
        pulse = fetch_market_pulse()
        if pulse:
            send(chat_id, publisher.market_pulse_message(pulse), reply_to=msg_id)
        else:
            send(chat_id, "⚠️ تعذّر جلب نبض السوق الآن.", reply_to=msg_id)
        return

    if low in ("محفظة", "المحفظة", "رصيد", "الرصيد", "/wallet", "wallet"):
        w = PaperWallet()
        send(chat_id, publisher.summary_message(w.stats()), reply_to=msg_id)
        return

    # منع الإغراق: تحليل واحد كل 5 ثوانٍ
    now = time.time()
    if now - _last_reply.get(chat_id, 0) < 5:
        return
    _last_reply[chat_id] = now

    symbol = parse_symbol(t)
    if not symbol:
        send(chat_id, "⚠️ لم أفهم العملة. مثال: <code>BTC</code> أو <code>سولانا</code>", reply_to=msg_id)
        return

    # تحقق سريع من وجود الزوج
    if fetch_klines(symbol, limit=2) is None:
        send(chat_id, f"⚠️ الزوج <code>{symbol}</code> غير موجود على Binance.", reply_to=msg_id)
        return

    send(chat_id, f"⏳ جارٍ تحليل <b>{symbol.replace('USDT', '/USDT')}</b>...", reply_to=msg_id)
    a = analyze_asset(symbol)
    if not a:
        send(chat_id, "⚠️ تعذّر التحليل (بيانات ناقصة).", reply_to=msg_id)
        return
    publisher.publish_analysis(a, reply_to=msg_id)
    log.info(f"Analyzed {symbol}: {a['direction']} {a['strength']} → {a['verdict']}")


def main():
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.error("Telegram not configured — exiting")
        return
    me = tg("getMe")
    log.info(f"Listener started as @{me.get('username') if me else '?'}")

    offset = load_offset()
    if offset == 0:
        # عند أول تشغيل: تجاهل الرسائل القديمة
        updates = tg("getUpdates", timeout=5)
        if updates:
            offset = max(u["update_id"] for u in updates) + 1
            save_offset(offset)
            log.info(f"Skipped history, offset={offset}")

    my_chat = str(config.TELEGRAM_CHAT_ID)
    # لا حلقة أبدية: كل تشغيل cron يعمل ~50 ثانية ثم يخرج ويُحرر قفل flock،
    # فيستلم التشغيل التالي مباشرة. هذا يمنع سيناريو "علق مرة → مات للأبد".
    deadline = time.time() + 50
    while time.time() < deadline:
        try:
            updates = tg("getUpdates", offset=offset, timeout=25)
            if not updates:
                continue
            for u in updates:
                offset = max(offset, u["update_id"] + 1)
                msg = u.get("message") or {}
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")
                if not text or chat_id != my_chat:
                    continue  # تجاهل أي شخص آخر + الرسائل غير النصية
                log.info(f"Message from owner: {text[:60]}")
                try:
                    handle(chat_id, text, msg.get("message_id"))
                except Exception as e:
                    log.exception(f"handle failed: {e}")
            save_offset(offset)
        except Exception as e:
            log.warning(f"loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
