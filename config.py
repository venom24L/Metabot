import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
# ADMIN_ID لازم يكون رقم مش يوزر، هاته من @userinfobot
# مثال: 123456789
ADMIN_ID = os.environ.get("ADMIN_ID", "").strip()
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()  # مثال: https://metabot-xxxx.onrender.com
PORT = int(os.environ.get("PORT", "10000"))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "2"))
MAX_FILE_MB = 48  # حد تيليجرام للبوت 50MB

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
)
