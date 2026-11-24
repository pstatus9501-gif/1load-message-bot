
import os
import asyncio
import logging
import httpx

from telegram import Update
from telegram.error import Forbidden, RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Hide Telegram API URLs containing the bot token
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ================= DATABASE =================

def headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


async def db_request(method, table, **kwargs):
    request_headers = kwargs.pop("headers", None) or headers()

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(
            method,
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=request_headers,
            **kwargs
        )

        response.raise_for_status()
        return response


# ================= USER DATABASE =================

async def save_user(user):
    await db_request(
        "POST",
        "bot_users",
        params={"on_conflict": "user_id"},
        headers={
            **headers(),
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=[{
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "is_active": True,
        }]
    )


async def get_users():
    users = []
    offset = 0
    batch_size = 1000

    while True:
        response = await db_request(
            "GET",
            "bot_users",
            params={
                "select": "user_id",
                "is_active": "eq.true",
                "order": "user_id.asc",
                "limit": batch_size,
                "offset": offset,
            }
        )

        data = response.json()

        if not data:
            break

        users.extend(row["user_id"] for row in data)
        offset += batch_size

        if len(data) < batch_size:
            break

    return users


# ================= LOAD MESSAGE DATABASE =================

async def get_load():
    response = await db_request(
        "GET",
        "bot_messages",
        params={
            "message_key": "eq.load",
            "select": "*",
            "limit": "1",
        }
    )

    data = response.json()
    return data[0] if data else None


async def save_load(photo_id, message_text):
    await db_request(
        "POST",
        "bot_messages",
        params={"on_conflict": "message_key"},
        headers={
            **headers(),
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=[{
            "message_key": "load",
            "photo_file_id": photo_id,
            "message_text": message_text,
        }]
    )


async def delete_load():
    await db_request(
        "DELETE",
        "bot_messages",
        params={"message_key": "eq.load"}
    )


# ================= ADMIN CHECK =================

def is_admin(update: Update):
    return (
        update.effective_user is not None
        and update.effective_user.id == ADMIN_ID
    )


# ================= START COMMAND =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    try:
        await save_user(user)

    except Exception:
        logger.exception("User registration failed")

        await update.effective_message.reply_text(
            "Something went wrong. Please try again later."
        )
        return

    await update.effective_message.reply_text(
        "Welcome! 👋\n\n"
        "Send LOAD to receive the latest update."
    )


# ================= LOAD COMMAND =================

async def handle_load(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message

    try:
        await save_user(user)

        data = await get_load()

        if not data:
            await msg.reply_text(
                "Load message is currently unavailable. Please try later."
            )
            return

        photo = data.get("photo_file_id")
        caption = data.get("message_text") or ""

        if photo:
            await msg.reply_photo(
                photo=photo,
                caption=caption or None
            )

        elif caption:
            await msg.reply_text(caption)

        else:
            await msg.reply_text(
                "Load message is unavailable."
            )

    except Exception:
        logger.exception("LOAD command failed")

        await msg.reply_text(
            "Something went wrong. Please try again later."
        )


# ================= SET LOAD =================

async def setload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    msg = update.effective_message
    replied = msg.reply_to_message

    if not replied:
        await msg.reply_text(
            "Reply to a photo or text message with /setload."
        )
        return

    photo_id = replied.photo[-1].file_id if replied.photo else None
    message_text = replied.caption or replied.text or ""

    if not photo_id and not message_text:
        await msg.reply_text(
            "No image or text found."
        )
        return

    try:
        await save_load(photo_id, message_text)

        await msg.reply_text(
            "✅ LOAD message saved successfully!"
        )

    except Exception:
        logger.exception("Saving LOAD message failed")

        await msg.reply_text(
            "Could not save message. Please check database settings."
        )


# ================= SHOW LOAD =================

async def showload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    msg = update.effective_message

    try:
        data = await get_load()

        if not data:
            await msg.reply_text("No LOAD message saved.")
            return

        photo = data.get("photo_file_id")
        caption = data.get("message_text") or ""

        if photo:
            await msg.reply_photo(
                photo=photo,
                caption=caption or None
            )

        elif caption:
            await msg.reply_text(caption)

        else:
            await msg.reply_text("Saved message is empty.")

    except Exception:
        logger.exception("Showing LOAD message failed")

        await msg.reply_text(
            "Could not load saved message."
        )


# ================= REMOVE LOAD =================

async def removeload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    try:
        await delete_load()

        await update.effective_message.reply_text(
            "🗑 LOAD message removed."
        )

    except Exception:
        logger.exception("Removing LOAD message failed")

        await update.effective_message.reply_text(
            "Could not remove LOAD message."
        )


# ================= STATS =================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    try:
        response = await db_request(
            "GET",
            "bot_users",
            params={
                "select": "user_id,is_active",
                "limit": "10000",
            }
        )

        users = response.json()

        total = len(users)
        active = sum(
            1 for user in users if user.get("is_active")
        )

        await update.effective_message.reply_text(
            "📊 BOT STATISTICS\n\n"
            f"Total Users: {total}\n"
            f"Active Users: {active}"
        )

    except Exception:
        logger.exception("Stats command failed")

        await update.effective_message.reply_text(
            "Could not fetch statistics."
        )


# ================= BROADCAST =================

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    msg = update.effective_message
    source_message = msg.reply_to_message

    if not source_message:
        await msg.reply_text(
            "Reply to the message you want to broadcast with /broadcast."
        )
        return

    try:
        users = await get_users()

    except Exception:
        logger.exception("Getting broadcast users failed")

        await msg.reply_text(
            "Could not fetch users."
        )
        return

    status = await msg.reply_text(
        f"📢 Broadcast started...\nTotal users: {len(users)}"
    )

    sent = 0
    failed = 0
    total = len(users)

    for index, user_id in enumerate(users, start=1):

        while True:
            try:
                await source_message.copy(chat_id=user_id)

                sent += 1
                await asyncio.sleep(0.05)
                break

            except RetryAfter as error:
                wait_time = error.retry_after

                if hasattr(wait_time, "total_seconds"):
                    wait_time = wait_time.total_seconds()

                wait_time = float(wait_time) + 1

                logger.warning(
                    "Broadcast rate limit. Waiting %s seconds.",
                    wait_time
                )

                await asyncio.sleep(wait_time)

            except Forbidden:
                failed += 1

                try:
                    await db_request(
                        "PATCH",
                        "bot_users",
                        params={"user_id": f"eq.{user_id}"},
                        headers={
                            **headers(),
                            "Prefer": "return=minimal",
                        },
                        json={"is_active": False}
                    )

                except Exception:
                    logger.exception(
                        "Could not deactivate blocked user %s",
                        user_id
                    )

                break

            except Exception:
                failed += 1

                logger.exception(
                    "Broadcast failed for user %s",
                    user_id
                )
                break

        if index % 100 == 0:
            try:
                await status.edit_text(
                    "📢 BROADCAST IN PROGRESS\n\n"
                    f"Progress: {index}/{total}\n"
                    f"Sent: {sent}\n"
                    f"Failed: {failed}"
                )
            except Exception:
                pass

            await asyncio.sleep(1)

    try:
        await status.edit_text(
            "✅ BROADCAST COMPLETED\n\n"
            f"Total Users: {total}\n"
            f"Sent: {sent}\n"
            f"Failed: {failed}"
        )

    except Exception:
        logger.exception("Could not update broadcast report")


# ================= HELP =================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update):
        await update.effective_message.reply_text(
            "ADMIN COMMANDS\n\n"
            "/setload - Save LOAD message (reply to photo/text)\n"
            "/showload - View saved message\n"
            "/removeload - Remove saved message\n"
            "/broadcast - Broadcast (reply to a message)\n"
            "/stats - User statistics\n"
            "/help - Show commands"
        )

    else:
        await update.effective_message.reply_text(
            "Send LOAD to receive the latest update."
        )


# ================= ERROR HANDLER =================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    error = context.error

    if error:
        logger.error(
            "Telegram update error",
            exc_info=(
                type(error),
                error,
                error.__traceback__,
            )
        )


# ================= MAIN =================

def main():
    required = [
        BOT_TOKEN,
        SUPABASE_URL,
        SUPABASE_KEY,
        WEBHOOK_SECRET,
        RENDER_EXTERNAL_URL,
    ]

    if not all(required) or ADMIN_ID == 0:
        raise RuntimeError(
            "Missing required environment variables. "
            "Check Render Environment."
        )

    if len(WEBHOOK_SECRET) < 1 or len(WEBHOOK_SECRET) > 256:
        raise RuntimeError(
            "WEBHOOK_SECRET must be between 1 and 256 characters."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    # Register commands

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CommandHandler("setload", setload))
    app.add_handler(CommandHandler("showload", showload))
    app.add_handler(CommandHandler("removeload", removeload))

    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))

    # LOAD message handler

    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"(?i)^LOAD$"),
            handle_load
        )
    )

    app.add_error_handler(error_handler)

    logger.info("Starting LOAD Message Bot...")

    # Same webhook method as the working Kaptaan bot

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_SECRET,
        webhook_url=(
            f"{RENDER_EXTERNAL_URL.rstrip('/')}/{WEBHOOK_SECRET}"
        ),
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
