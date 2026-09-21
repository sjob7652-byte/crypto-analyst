"""ولّد TG_SESSION على حاسوبك الخاص — بياناتك لا تغادر جهازك.

الخطوات:
  1) pip install telethon
  2) python gen_session.py
  3) أدخل API_ID و API_HASH (من https://my.telegram.org)
  4) أدخل الكود الذي يصلك على Telegram (وربما كلمة سر التحقق بخطوتين)
  5) انسخ السطر الناتج كاملاً → الصقه في GitHub secret باسم TG_SESSION

تحذير أمني: الـsession string بمثابة مفتاح حسابك — لا تشاركه مع أي شخص
أو خدمة، وخزّنه فقط في GitHub Secrets.
"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("TG API_ID: ").strip())
api_hash = input("TG API_HASH: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    me = client.get_me()
    print(f"\nتم تسجيل الدخول بنجاح: {getattr(me, 'first_name', '?')}")
    print("انسخ السطر التالي كاملاً وضعه في GitHub secret باسم TG_SESSION:")
    print(client.session.save())
