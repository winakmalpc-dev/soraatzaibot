import os
import logging
import asyncio
from pathlib import Path
import hashlib
from typing import Dict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# State for ConversationHandler
UPLOAD_WAITING = 1

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("sora-history-bot")

# Directory for videos
VIDEO_DIR = Path("videos")
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

# In-memory map for long callback_data -> filename
video_hash_map: Dict[str, str] = {}

# Helper: list .mp4 files
def list_videos():
    return sorted([p.name for p in VIDEO_DIR.glob("*.mp4")])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("📥 /start from %s (%s)", user.full_name, user.id)

    videos = list_videos()
    if not videos:
        await update.message.reply_text("No videos available yet. Admin can upload with /upload 📤")
        return

    keyboard = []
    for name in videos:
        data = f"V:{name}"
        # If callback_data would be too long, store a hash mapping
        if len(data) > 60:
            h = hashlib.sha256(name.encode()).hexdigest()
            video_hash_map[h] = name
            data = f"H:{h}"
        keyboard.append([InlineKeyboardButton(text=name, callback_data=data)])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select a video to receive 🎬:", reply_markup=reply_markup)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "Sora History Bot - available commands:\n\n"
        "/start - list available videos 🎬\n"
        "/help - show this help message ℹ️\n"
        "/upload - (admin only) upload a new .mp4 file 📤\n"
        "/cancel - cancel current operation ❌"
    )
    await update.message.reply_text(help_text)


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data.startswith("V:"):
        filename = data[2:]
    elif data.startswith("H:"):
        h = data[2:]
        filename = video_hash_map.get(h)
        if not filename:
            await query.message.reply_text("Sorry, I can't find that video anymore. ⚠️")
            return
    else:
        await query.message.reply_text("Unknown action.")
        return

    path = VIDEO_DIR / filename
    if not path.exists():
        await query.message.reply_text("Video file not found on server. ⚠️")
        return

    size = path.stat().st_size
    max_bytes = 50 * 1024 * 1024
    if size > max_bytes:
        await query.message.reply_text("Sorry, this video is larger than 50MB and cannot be sent. ⚠️")
        logger.warning("Attempt to send large video %s (%d bytes)", filename, size)
        return

    try:
        await query.message.reply_video(video=InputFile(str(path)), caption=f"🎬 {filename}")
        logger.info("📤 Sent video %s to %s", filename, query.from_user.id)
    except Exception:
        logger.exception("Failed to send video %s", filename)
        await query.message.reply_text("Failed to send the video due to an error. 😞")


async def upload_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    admin_id = context.bot_data.get("ADMIN_ID")
    if admin_id is None or user.id != admin_id:
        await update.message.reply_text("You are not authorized to use /upload. 🔒")
        logger.warning("Unauthorized /upload attempt by %s (%s)", user.full_name, user.id)
        return ConversationHandler.END

    await update.message.reply_text(
        "Please send the .mp4 video file (under 50MB). Send /cancel to abort. 📤"
    )
    return UPLOAD_WAITING


async def receive_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    admin_id = context.bot_data.get("ADMIN_ID")
    if admin_id is None or user.id != admin_id:
        await update.message.reply_text("You are not authorized. 🔒")
        return ConversationHandler.END

    msg = update.message
    file_obj = None
    original_name = None
    file_size = None

    # Accept video or document
    if msg.video:
        file_obj = msg.video
        file_size = getattr(msg.video, "file_size", None)
        original_name = getattr(msg.video, "file_name", None) or f"video_{msg.video.file_id}.mp4"
    elif msg.document:
        file_obj = msg.document
        file_size = getattr(msg.document, "file_size", None)
        original_name = getattr(msg.document, "file_name", None)
    else:
        await msg.reply_text("Please send a .mp4 file as a video or document. ❗")
        return UPLOAD_WAITING

    # Basic checks
    if original_name is None:
        original_name = "video_upload.mp4"
    if not original_name.lower().endswith(".mp4"):
        # If document doesn't have mp4 extension, still allow if mimetype looks like video
        if msg.document and not (msg.document.mime_type and msg.document.mime_type.startswith("video")):
            await msg.reply_text("Only .mp4 video files are accepted. ❗")
            return UPLOAD_WAITING

    max_bytes = 50 * 1024 * 1024
    if file_size and file_size > max_bytes:
        await msg.reply_text("File is too large. Please upload a file under 50MB. ⚠️")
        logger.warning("Admin attempted to upload too-large file (%d bytes)", file_size)
        return ConversationHandler.END

    # Download file
    try:
        f = await msg.effective_attachment.get_file()
        safe_name = Path(original_name).name
        dest = VIDEO_DIR / safe_name
        # Avoid overwrite: append number if exists
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            i = 1
            while True:
                dest = VIDEO_DIR / f"{stem}_{i}{suffix}"
                if not dest.exists():
                    break
                i += 1

        await f.download_to_drive(custom_path=str(dest))
        await msg.reply_text(f"Upload successful! Saved as {dest.name} ✅")
        logger.info("📥 Admin %s uploaded %s (%d bytes)", user.id, dest.name, dest.stat().st_size)
    except Exception:
        logger.exception("Failed to download uploaded file from admin %s", user.id)
        await msg.reply_text("Failed to save the uploaded file due to an error. 😞")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Operation cancelled. ✖️")
    return ConversationHandler.END


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Unknown command. Use /help to see available commands. 🤖")


def main() -> None:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_ID = os.getenv("ADMIN_ID")

    if not BOT_TOKEN:
        logger.error("Environment variable BOT_TOKEN is not set. Exiting.")
        raise SystemExit("BOT_TOKEN not set")

    if not ADMIN_ID:
        logger.error("Environment variable ADMIN_ID is not set. Exiting.")
        raise SystemExit("ADMIN_ID not set")

    try:
        admin_id_int = int(ADMIN_ID)
    except ValueError:
        logger.error("ADMIN_ID must be an integer. Exiting.")
        raise SystemExit("ADMIN_ID is not an integer")

    application = Application.builder().token(BOT_TOKEN).build()

    # Store admin id in bot_data for easy access
    application.bot_data["ADMIN_ID"] = admin_id_int

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))

    # Upload conversation (admin only enforced in entry)
    conv = ConversationHandler(
        entry_points=[CommandHandler("upload", upload_entry)],
        states={
            UPLOAD_WAITING: [
                MessageHandler(filters.Document.ALL | filters.VIDEO, receive_upload)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv)

    application.add_handler(CallbackQueryHandler(callback_query_handler))

    # Unknowns
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    logger.info("🚀 Sora History Bot starting in polling mode...")
    try:
        application.run_polling()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
