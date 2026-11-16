import asyncio
import os
from flask import Flask, request, jsonify
from telegram import Update

import config
from bot import build_app

flask_app = Flask(__name__)
tg_app = build_app()

@flask_app.route("/", methods=["GET"])
def home():
    return "Metabot is running ✅ - ابعت لينك للبوت على تيليجرام", 200

@flask_app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, tg_app.bot)
        # شغل الـ update في loop بتاع الـ app
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(tg_app.process_update(update))
        else:
            loop.run_until_complete(tg_app.process_update(update))
    except Exception as e:
        print(f"webhook error: {e}")
    return "ok", 200

async def setup_webhook():
    await tg_app.initialize()
    if config.WEBHOOK_URL:
        url = config.WEBHOOK_URL.rstrip("/") + "/webhook"
        await tg_app.bot.set_webhook(url)
        print(f"Webhook set to {url}")
    else:
        print("WEBHOOK_URL مش متظبط - هستخدم polling كـ fallback للتجربة المحلية")

if __name__ == "__main__":
    # لو فيه WEBHOOK_URL -> شغل Flask webhook (وضع Render)
    # لو مفيش -> شغل polling (للتجربة على الكمبيوتر)
    if config.WEBHOOK_URL:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(setup_webhook())
        # Flask بيشتغل على PORT اللي Render بيديه
        flask_app.run(host="0.0.0.0", port=config.PORT)
    else:
        print("Running in polling mode (local)...")
        tg_app.run_polling()
