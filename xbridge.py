"""جسر أخبار X عبر قنوات Telegram العامة — Telethon (مجاني 100%).

الفكرة: بدل سكريبنغ X مباشرة (محظور تقنياً + الـAPI مدفوع)، نقرأ قنوات
Telegram عامة تنقل أخبار X والميم كوينز لحظة بلحظة، ونفلترها بالكلمات المفتاحية.

التفعيل (مرة واحدة — من طرف المستخدم، انظر XBRIDGE_SETUP_AR.md):
  TG_API_ID, TG_API_HASH — تطبيق مجاني من https://my.telegram.org
  TG_SESSION             — يُولَّد بسكربت gen_session.py على حاسوبك الخاص
  XBRIDGE_CHANNELS       — قنوات عامة مفصولة بفواصل (مثال: whale_alert, ...)
بدون هذه الأسرار: الدالة ترجع [] والبوت يشتغل عادي كأن شيئاً لم يكن.
"""

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone

# كلمات مفتاحية: إشارات ميم كوينز / عقود جديدة / إطلاق
KEYWORDS = [
    r"\bsolana\b", r"\bsol\b",
    r"new (pair|listing|launch|token|coin|contract)",
    r"contract address", r"just (launched|listed|deployed)",
    r"fair ?launch", r"stealth (launch|drop)",
    r"memecoin", r"meme coin", r"100x", r"\bgem\b", r"presale",
    r"0x[a-fA-F0-9]{40}", r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b",
    r"pump\.fun", r"raydium", r"dexscreener",
]


def _channels():
    raw = os.environ.get("XBRIDGE_CHANNELS", "")
    return [c.strip().lstrip("@") for c in raw.split(",") if c.strip()]


def is_configured():
    return bool(os.environ.get("TG_API_ID")
                and os.environ.get("TG_API_HASH")
                and os.environ.get("TG_SESSION")
                and _channels())


def _relevant(text):
    t = (text or "").lower()
    return any(re.search(kw, t) for kw in KEYWORDS)


def _to_item(channel, text, date):
    """نفس شكل عناصر NewsClient — tier=3 (مصدر غير موثوق، للتلميح فقط)."""
    title = " ".join((text or "").split())[:280]
    return {"title": f"[X⇄TG/{channel}] {title}",
            "published": date, "sentiment": 0.0, "tier": 3,
            "source": f"xbridge:{channel}"}


async def _fetch_async(limit=20, max_age_h=6):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    session = StringSession(os.environ["TG_SESSION"])
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_h)
    items = []
    async with TelegramClient(session, api_id, api_hash) as client:
        for ch in _channels():
            try:
                entity = await client.get_entity(ch)
                async for msg in client.iter_messages(entity, limit=limit):
                    if not msg.date or msg.date < cutoff:
                        break  # الرسائل مرتبة من الأحدث — ما بعدها أقدم
                    text = msg.text or ""
                    if len(text) >= 40 and _relevant(text):
                        items.append(_to_item(ch, text, msg.date))
            except Exception as e:
                print(f"  xbridge: قناة {ch} — {type(e).__name__}")
                continue
    return items


def fetch_xbridge_news(limit_per_channel=20, max_age_h=6):
    """يرجع عناصر أخبار — أو [] عند غياب الإعداد أو أي خطأ (لا يعطل البوت)."""
    if not is_configured():
        return []
    try:
        items = asyncio.run(_fetch_async(limit_per_channel, max_age_h))
        print(f"  xbridge: {len(items)} عنصراً من {len(_channels())} قنوات")
        return items
    except ImportError:
        print("  xbridge: مكتبة telethon غير مثبتة")
    except Exception as e:
        print(f"  xbridge error: {type(e).__name__}")
    return []
