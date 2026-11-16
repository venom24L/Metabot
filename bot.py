import asyncio
import re
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

import config
import downloader

URL_RE = re.compile(r"https?://\S+")
# pending: key -> {url, title, site, duration}
pending = {}
# semaphore للتحميل - 2 في نفس الوقت بس عشان Render Free ميتقلش
sem = asyncio.Semaphore(config.MAX_CONCURRENT)

WELCOME = (
    "أهلا بيك في Metabot 🎬\n\n"
    "ابعت أي لينك فيديو عام من:\n"
    "YouTube • YouTube Shorts • TikTok • Instagram • Facebook • Pinterest • Ph\n\n"
    "هبعتلك اختيارات الجودة، اختار MP4 أو MP3 وهبعتلك الملف على طول.\n\n"
    "⚠️ ملحوظة: الفيديوهات الخاصة أو اللي محتاجة تسجيل دخول مش هتشتغل، والملف لازم يكون أقل من 48MB عشان تيليجرام يقبله."
)

def kb_for(key: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 MP4 - أفضل جودة", callback_data=f"dl:best:{key}")],
        [InlineKeyboardButton("🎬 MP4 - 720p", callback_data=f"dl:720:{key}"),
         InlineKeyboardButton("🎬 MP4 - 480p", callback_data=f"dl:480:{key}")],
        [InlineKeyboardButton("🎬 MP4 - 360p (خفيف)", callback_data=f"dl:360:{key}")],
        [InlineKeyboardButton("🎵 MP3 - صوت بس", callback_data=f"dl:mp3:{key}")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME)

async def on_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    m = URL_RE.search(text)
    if not m:
        return
    url = m.group(0).strip().rstrip(").,")
    msg = await update.message.reply_text("🔍 جاري فحص الرابط...")
    try:
        info = await asyncio.to_thread(downloader.extract_info_with_fallback, url)
    except Exception as e:
        err = str(e)[:300]
        await msg.edit_text(f"❌ مش قادر أحمل الرابط ده.\n{err}\n\nجرب لينك عام تاني، أو جودة أقل.")
        await notify_admin(context, f"❌ فشل فحص: {url}\nالخطأ: {err}\nمن: @{update.effective_user.username or update.effective_user.id}")
        return
    key = f"{update.effective_user.id}_{update.message.message_id}_{int(time.time())}"
    pending[key] = {"url": url, "title": info["title"], "site": info["site"], "direct_url": info.get("direct_url")}
    dur = ""
    if info["duration"]:
        mins = int(info["duration"] // 60)
        secs = int(info["duration"] % 60)
        dur = f" • ⏱ {mins}:{secs:02d}"
    caption = f"✅ لقيته!\n\n🎬 {info['title'][:100]}\n📍 {info['site']}{dur}\n\nاختار الجودة:"
    await msg.edit_text(caption, reply_markup=kb_for(key))

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        _, quality, key = q.data.split(":", 2)
    except:
        return
    data = pending.get(key)
    if not data:
        await q.edit_message_text("⏰ الطلب ده انتهى، ابعت اللينك تاني.")
        return
    url = data["url"]
    title = data["title"]
    is_mp3 = (quality == "mp3")
    label = "MP3 🎵" if is_mp3 else f"MP4 {quality}"
    await q.edit_message_text(f"⏳ جاري تحميل {label}...\n🎬 {title[:80]}\nده ممكن ياخد شوية، استنى...")
    # تحميل في background مع semaphore
    async with sem:
        tmpdir = downloader.make_tmp_dir()
        try:
            # progress بسيط - نحدث الرسالة كل شوية مش كل ثانية
            last_edit = [0]
            def hook(d):
                # مش بنحدث كتير عشان تيليجرام ميعملش spam
                pass
            direct = data.get("direct_url")
            if direct:
                # Facebook/Pinterest fallback - direct MP4 found by scraping
                if is_mp3:
                    # download mp4 then convert to mp3
                    mp4_path = await asyncio.to_thread(downloader.download_direct_mp4, direct, url, tmpdir)
                    # convert with ffmpeg
                    import subprocess
                    ffmpeg = downloader.get_ffmpeg()
                    if not ffmpeg:
                        raise RuntimeError("ffmpeg not found for MP3 conversion")
                    mp3_path = tmpdir / "audio.mp3"
                    subprocess.run([ffmpeg, "-y", "-i", str(mp4_path), "-vn", "-ab", "192k", str(mp3_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
                    mp4_path.unlink(missing_ok=True)
                    path = mp3_path
                else:
                    path = await asyncio.to_thread(downloader.download_direct_mp4, direct, url, tmpdir)
            elif is_mp3:
                path = await asyncio.to_thread(downloader.download_audio_mp3, url, tmpdir, hook)
            else:
                path = await asyncio.to_thread(downloader.download_video, url, quality, tmpdir, hook)
            if not path or not Path(path).exists():
                raise RuntimeError("الملف مش موجود بعد التحميل")
            size_mb = Path(path).stat().st_size / (1024*1024)
            if size_mb > config.MAX_FILE_MB:
                await q.edit_message_text(
                    f"⚠️ الملف طلع كبير ({size_mb:.1f}MB) وتيليجرام بيقبل لحد 48MB بس.\n"
                    "جرب جودة 360p أو MP3، هتبقى أخف بكتير."
                )
                downloader.cleanup_dir(tmpdir)
                return
            await q.edit_message_text(f"📤 جاري رفع {label} ليك...")
            # ابعت الملف
            with open(path, "rb") as f:
                if is_mp3:
                    await context.bot.send_audio(
                        chat_id=q.message.chat_id,
                        audio=f,
                        title=title[:100],
                        caption=f"🎵 {title[:100]}",
                    )
                else:
                    await context.bot.send_video(
                        chat_id=q.message.chat_id,
                        video=f,
                        caption=f"🎬 {title[:100]} • {label}",
                        supports_streaming=True,
                    )
            await q.edit_message_text(f"✅ تم! {label} اتبعت.\nابعت لينك تاني لو عايز.")
        except Exception as e:
            err = str(e)[:400]
            await q.edit_message_text(f"❌ فشل التحميل: {err}\n\nجرب جودة أقل أو لينك تاني.")
            await notify_admin(context, f"❌ فشل تحميل {label}: {url}\nالخطأ: {err}\nمن: @{update.effective_user.username or update.effective_user.id}")
        finally:
            downloader.cleanup_dir(tmpdir)
            pending.pop(key, None)

async def notify_admin(context, text: str):
    if not config.ADMIN_ID:
        return
    try:
        await context.bot.send_message(chat_id=int(config.ADMIN_ID), text=text[:1000])
    except Exception:
        pass

def build_app():
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN مش متظبط في Environment Variables")
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^dl:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_link))
    return app
