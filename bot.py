import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from messages import t
from processor import (
    column_letter_to_index,
    detect_inn_column,
    find_column_by_name,
    process_excel,
    process_inns,
)

INN_PATTERN = re.compile(r"\b\d{9}\b")
OUTPUT_ROOT = Path(os.getenv("OUTPUT_ROOT", "/data/output"))
TELEGRAM_MAX_FILE_MB = 48


def _new_output_dir(stem: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)[:60] or "run"
    out = OUTPUT_ROOT / f"{safe}_{int(time.time())}"
    out.mkdir(parents=True, exist_ok=True)
    return out


async def _send_files(update, context, files: list[str]):
    for file_path in files:
        name = os.path.basename(file_path)
        size_mb = os.path.getsize(file_path) / 1024 / 1024
        logger.info("Sending %s (%.1f MB)", file_path, size_mb)
        if size_mb > TELEGRAM_MAX_FILE_MB:
            logger.error("%s exceeds Telegram limit (%.1f MB)", name, size_mb)
            await update.message.reply_text(
                t(context, "file_too_large", name=name, size=round(size_mb, 1), path=file_path)
            )
            continue
        try:
            with open(file_path, "rb") as f:
                await update.message.reply_document(document=f, filename=name)
        except Exception as e:
            logger.exception("Failed to send %s", file_path)
            await update.message.reply_text(
                t(context, "send_failed", name=name, path=file_path, error=e)
            )

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

WAITING_FOR_COLUMN = 1


def language_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Русский 🇷🇺", callback_data="lang_ru"),
            InlineKeyboardButton("O'zbek 🇺🇿", callback_data="lang_uz"),
        ]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        MESSAGES_CHOOSE,
        reply_markup=language_keyboard(),
    )

MESSAGES_CHOOSE = "Выберите язык / Tilni tanlang:"


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        MESSAGES_CHOOSE,
        reply_markup=language_keyboard(),
    )


async def handle_language_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    lang = query.data.replace("lang_", "")
    context.user_data["lang"] = lang

    await query.edit_message_text(t(context, "language_set"))
    await query.message.reply_text(t(context, "welcome"))


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "lang" not in context.user_data:
        context.user_data["lang"] = "ru"

    doc = update.message.document
    if not doc.file_name.lower().endswith(".xlsx"):
        await update.message.reply_text(t(context, "not_excel"))
        return ConversationHandler.END

    msg = await update.message.reply_text(t(context, "downloading"))

    tmpdir = tempfile.mkdtemp()
    input_path = str(Path(tmpdir) / doc.file_name)

    try:
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(input_path)
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        await msg.edit_text(t(context, "download_failed", error=e))
        return ConversationHandler.END

    col_idx = detect_inn_column(input_path)

    context.user_data.update({
        "input_path": input_path,
        "tmpdir": tmpdir,
        "filename": doc.file_name,
        "status_msg": msg,
    })

    if col_idx is not None:
        context.user_data["col_idx"] = col_idx
        await msg.edit_text(t(context, "inn_detected"))
        context.user_data["processing_task"] = asyncio.create_task(run_processing(update, context))
        return ConversationHandler.END

    await msg.edit_text(t(context, "ask_column"))
    return WAITING_FOR_COLUMN


async def handle_column_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    input_path = context.user_data["input_path"]
    msg = context.user_data["status_msg"]

    if text.isdigit():
        col_idx = int(text)
    elif text.replace(" ", "").isalpha() and len(text) <= 3:
        col_idx = column_letter_to_index(text)
    else:
        col_idx = find_column_by_name(input_path, text)
        if col_idx is None:
            await update.message.reply_text(t(context, "column_not_found", col=text))
            return WAITING_FOR_COLUMN

    context.user_data["col_idx"] = col_idx
    await msg.edit_text(t(context, "using_column", col=text))
    context.user_data["processing_task"] = asyncio.create_task(run_processing(update, context))
    return ConversationHandler.END


async def run_processing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_path = context.user_data["input_path"]
    tmpdir = context.user_data["tmpdir"]
    filename = context.user_data["filename"]
    col_idx = context.user_data["col_idx"]
    msg = context.user_data["status_msg"]

    stem = Path(filename).stem
    output_dir = _new_output_dir(stem)
    last_reported = [0]

    async def progress(current: int, total: int):
        if current - last_reported[0] >= 50 or current == total:
            last_reported[0] = current
            try:
                await msg.edit_text(t(context, "processing", current=current, total=total))
            except Exception as e:
                logger.warning("edit progress failed: %s", e)

    try:
        count, files = await process_excel(input_path, str(output_dir), col_idx, progress)
        try:
            await msg.edit_text(t(context, "done", count=count))
        except Exception as e:
            logger.warning("edit done failed: %s", e)
        await _send_files(update, context, files)
    except asyncio.CancelledError:
        pass
    except ValueError:
        await msg.edit_text(t(context, "no_inns"))
    except Exception as e:
        logger.exception("Processing error")
        try:
            await msg.edit_text(t(context, "error", error=e))
        except Exception:
            pass
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def handle_text_inns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "lang" not in context.user_data:
        context.user_data["lang"] = "ru"

    text = update.message.text or ""
    inns = list(dict.fromkeys(INN_PATTERN.findall(text)))

    if not inns:
        await update.message.reply_text(t(context, "no_inns_in_text"))
        return

    msg = await update.message.reply_text(t(context, "processing", current=0, total=len(inns)))
    output_dir = _new_output_dir(f"text_{len(inns)}")
    last_reported = [0]

    async def progress(current: int, total: int):
        if current - last_reported[0] >= 10 or current == total:
            last_reported[0] = current
            try:
                await msg.edit_text(t(context, "processing", current=current, total=total))
            except Exception as e:
                logger.warning("edit progress failed: %s", e)

    try:
        count, files = await process_inns(inns, str(output_dir), progress)
        try:
            await msg.edit_text(t(context, "done", count=count))
        except Exception as e:
            logger.warning("edit done failed: %s", e)
        await _send_files(update, context, files)
    except Exception as e:
        logger.exception("Processing error (text)")
        try:
            await msg.edit_text(t(context, "error", error=e))
        except Exception:
            pass


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = context.user_data.get("processing_task")
    if task and not task.done():
        task.cancel()
    tmpdir = context.user_data.get("tmpdir")
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)
    await update.message.reply_text(t(context, "cancelled"))
    return ConversationHandler.END


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    asyncio.set_event_loop(asyncio.new_event_loop())
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.ALL, handle_file)],
        states={
            WAITING_FOR_COLUMN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_column_input)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
        per_chat=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CallbackQueryHandler(handle_language_choice, pattern="^lang_"))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_inns))
    try:
        app.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
