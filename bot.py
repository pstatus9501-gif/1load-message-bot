
import os
import asyncio
import httpx

from telegram import Update
from telegram.request import HTTPXRequest
from telegram.error import Forbidden, RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", "10000"))


def headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


async def db_request(method, table, **kwargs):
    request_headers = kwargs.pop("headers", headers())

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(
            method,
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=request_headers,
            **kwargs
        )
        response.raise_for_status()
        return response


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await save_user(user)

    await update.message.reply_text(
        "Welcome! 👋\n\n"
        "Send LOAD to receive the latest update."
    )


async def handle_load(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    await save_user(user)

    data = await get_load()

    if not data:
        await update.message.reply_text(
            "Load message is currently unavailable. Please try later."
        )
        return

    photo = data.get("photo_file_id")
    caption = data.get("message_text") or ""

    if photo:
        await update.message.reply_photo(
            photo=photo,
            caption=caption or None
        )
    elif caption:
        await update.message.reply_text(caption)
    else:
        await update.message.reply_text("Load message is unavailable.")


def is_admin(update: Update):
    return update.effective_user.id == ADMIN_ID


async def setload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    msg = update.message.reply_to_message

    if not msg:
        await update.message.reply_text(
            "Reply to a photo or text message with /setload."
        )
        return

    photo_id = msg.photo[-1].file_id if msg.photo else None
    message_text = msg.caption or msg.text or ""

    if not photo_id and not message_text:
        await update.message.reply_text("No image or text found.")
        return

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

    await update.message.reply_text("✅ LOAD message saved successfully!")


async def showload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    data = await get_load()

    if not data:
        await update.message.reply_text("No LOAD message saved.")
        return

    photo = data.get("photo_file_id")
    caption = data.get("message_text") or ""

    if photo:
        await update.message.reply_photo(photo=photo, caption=caption or None)
    elif caption:
        await update.message.reply_text(caption)


async def removeload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    await db_request(
        "DELETE",
        "bot_messages",
        params={"message_key": "eq.load"}
    )

    await update.message.reply_text("🗑 LOAD message removed.")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    response = await db_request(
        "GET",
        "bot_users",
        params={"select": "user_id,is_active"}
    )

    users = response.json()
    total = len(users)
    active = sum(1 for u in users if u.get("is_active"))

    await update.message.reply_text(
        f"📊 BOT STATISTICS\n\n"
        f"Total Users: {total}\n"
        f"Active Users: {active}"
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    msg = update.message.reply_to_message

    if not msg:
        await update.message.reply_text(
            "Reply to the message you want to broadcast with /broadcast."
        )
        return

    response = await db_request(
        "GET",
        "bot_users",
        params={
            "select": "user_id",
            "is_active": "eq.true",
        }
    )

    users = response.json()
    sent = 0
    failed = 0

    status = await update.message.reply_text("📢 Broadcast started...")

    for user in users:
        user_id = user["user_id"]

        try:
            await msg.copy(chat_id=user_id)
            sent += 1
            await asyncio.sleep(0.05)

        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await msg.copy(chat_id=user_id)
                sent += 1
            except Exception:
                failed += 1

        except Forbidden:
            failed += 1
            await db_request(
                "PATCH",
                "bot_users",
                params={"user_id": f"eq.{user_id}"},
                headers={**headers(), "Prefer": "return=minimal"},
                json={"is_active": False}
            )

        except Exception:
            failed += 1

    await status.edit_text(
        f"✅ BROADCAST COMPLETED\n\n"
        f"Sent: {sent}\n"
        f"Failed: {failed}"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update):
        await update.message.reply_text(
            "ADMIN COMMANDS\n\n"
            "/setload - Save LOAD message (reply to photo/text)\n"
            "/showload - View saved message\n"
            "/removeload - Remove LOAD message\n"
            "/broadcast - Broadcast (reply to a message)\n"
            "/stats - User statistics"
        )
    else:
        await update.message.reply_text("Send LOAD to receive the latest update.")


def main():
    required = [
        BOT_TOKEN,
        SUPABASE_URL,
        SUPABASE_KEY,
        WEBHOOK_SECRET,
        RENDER_EXTERNAL_URL,
    ]

    if not all(required) or ADMIN_ID == 0:
        raise RuntimeError("Missing required environment variables.")

    telegram_request = HTTPXRequest(
    connect_timeout=60,
    read_timeout=60,
    write_timeout=60,
    pool_timeout=60,
)

app = (
    Application.builder()
    .token(BOT_TOKEN)
    .request(telegram_request)
    .build()
)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setload", setload))
    app.add_handler(CommandHandler("showload", showload))
    app.add_handler(CommandHandler("removeload", removeload))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))

    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"(?i)^LOAD$"),
            handle_load
        )
    )

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_SECRET,
        webhook_url=f"{RENDER_EXTERNAL_URL.rstrip('/')}/{WEBHOOK_SECRET}",
        secret_token=WEBHOOK_SECRET,
    )


if __name__ == "__main__":
    main()
