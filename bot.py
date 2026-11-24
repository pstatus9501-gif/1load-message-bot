import os
import json
import asyncio
import logging
import httpx
import tornado.web

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

# Hide HTTP request URLs containing the bot token
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


# ================= USER COMMANDS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    try:
        await save_user(user)
    except Exception:
        logger.exception("User registration failed")
        await update.message.reply_text(
            "Something went wrong. Please try again later."
        )
        return

    await update.message.reply_text(
        "Welcome! 👋\n\n"
        "Send LOAD to receive the latest update."
    )


async def handle_load(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    try:
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
            await update.message.reply_text(
                "Load message is unavailable."
            )

    except Exception:
        logger.exception("LOAD command failed")
        await update.message.reply_text(
            "Something went wrong. Please try again later."
        )


# ================= ADMIN CHECK =================

def is_admin(update: Update):
    return (
        update.effective_user is not None
        and update.effective_user.id == ADMIN_ID
    )


# ================= ADMIN COMMANDS =================

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
        await update.message.reply_text(
            "No image or text found."
        )
        return

    try:
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

        await update.message.reply_text(
            "✅ LOAD message saved successfully!"
        )

    except Exception:
        logger.exception("Saving LOAD message failed")
        await update.message.reply_text(
            "Could not save message. Please check database settings."
        )


async def showload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    try:
        data = await get_load()

        if not data:
            await update.message.reply_text("No LOAD message saved.")
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
            await update.message.reply_text("Saved message is empty.")

    except Exception:
        logger.exception("Showing LOAD message failed")
        await update.message.reply_text("Could not load saved message.")


async def removeload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    try:
        await db_request(
            "DELETE",
            "bot_messages",
            params={"message_key": "eq.load"}
        )

        await update.message.reply_text("🗑 LOAD message removed.")

    except Exception:
        logger.exception("Removing LOAD message failed")
        await update.message.reply_text("Could not remove LOAD message.")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    try:
        response = await db_request(
            "GET",
            "bot_users",
            params={"select": "user_id,is_active"}
        )

        users = response.json()
        total = len(users)
        active = sum(1 for user in users if user.get("is_active"))

        await update.message.reply_text(
            f"📊 BOT STATISTICS\n\n"
            f"Total Users: {total}\n"
            f"Active Users: {active}"
        )

    except Exception:
        logger.exception("Stats command failed")
        await update.message.reply_text("Could not fetch statistics.")


# ================= BROADCAST =================

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    msg = update.message.reply_to_message

    if not msg:
        await update.message.reply_text(
            "Reply to the message you want to broadcast with /broadcast."
        )
        return

    try:
        response = await db_request(
            "GET",
            "bot_users",
            params={
                "select": "user_id",
                "is_active": "eq.true",
            }
        )

        users = response.json()

    except Exception:
        logger.exception("Getting broadcast users failed")
        await update.message.reply_text("Could not fetch users.")
        return

    status = await update.message.reply_text(
        "📢 Broadcast started..."
    )

    sent = 0
    failed = 0

    for user in users:
        user_id = user["user_id"]

        while True:
            try:
                await msg.copy(chat_id=user_id)
                sent += 1
                await asyncio.sleep(0.05)
                break

            except RetryAfter as error:
                wait_time = int(error.retry_after) + 1
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
                    logger.exception("Could not deactivate blocked user")

                break

            except Exception:
                failed += 1
                logger.exception("Broadcast failed for user %s", user_id)
                break

    await status.edit_text(
        f"✅ BROADCAST COMPLETED\n\n"
        f"Sent: {sent}\n"
        f"Failed: {failed}"
    )


# ================= HELP =================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update):
        await update.message.reply_text(
            "ADMIN COMMANDS\n\n"
            "/setload - Save LOAD message (reply to photo/text)\n"
            "/showload - View saved message\n"
            "/removeload - Remove LOAD message\n"
            "/broadcast - Broadcast (reply to a message)\n"
            "/stats - User statistics\n"
            "/help - Show commands"
        )
    else:
        await update.message.reply_text(
            "Send LOAD to receive the latest update."
        )


# ================= ERROR HANDLER =================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(
        "Telegram update error",
        exc_info=context.error
    )


# ================= WEB SERVER =================

class HealthHandler(tornado.web.RequestHandler):
    def get(self):
        self.write("OK")

    def head(self):
        self.set_status(200)


class WebhookHandler(tornado.web.RequestHandler):
    def initialize(self, tg_app):
        self.tg_app = tg_app

    async def post(self):
        token = self.request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )

        if token != WEBHOOK_SECRET:
            self.set_status(403)
            return

        try:
            data = json.loads(self.request.body)
            update = Update.de_json(data, self.tg_app.bot)
            await self.tg_app.update_queue.put(update)

        except Exception:
            logger.exception("Bad webhook payload")
            self.set_status(400)
            return

        self.set_status(200)


# ================= BOT STARTUP =================

async def start_bot(tg_app: Application):
    """Connects to Telegram in the background with safe retry/backoff."""

    webhook_url = (
        f"{RENDER_EXTERNAL_URL.rstrip('/')}/{WEBHOOK_SECRET}"
    )

    attempt = 0

    while True:
        attempt += 1
        initialized = False
        started = False
        wait_time = 10

        try:
            await tg_app.initialize()
            initialized = True

            await tg_app.start()
            started = True

            await tg_app.bot.set_webhook(
                url=webhook_url,
                secret_token=WEBHOOK_SECRET,
                allowed_updates=Update.ALL_TYPES,
            )

            logger.info("Webhook set. Bot is live.")
            return

        except RetryAfter as error:
            wait_time = int(error.retry_after) + 2

            logger.warning(
                "Telegram rate limit during startup. Waiting %s seconds.",
                wait_time,
            )

        except Exception:
            wait_time = min(
                10 * (2 ** min(attempt - 1, 4)),
                160
            )

            logger.exception(
                "Bot startup attempt %s failed. Retrying in %s seconds.",
                attempt,
                wait_time,
            )

        finally:
            if started:
                try:
                    await tg_app.stop()
                except Exception:
                    logger.exception(
                        "Could not stop bot after failed startup"
                    )

            if initialized:
                try:
                    await tg_app.shutdown()
                except Exception:
                    logger.exception(
                        "Could not shut down bot after failed startup"
                    )

        await asyncio.sleep(wait_time)


# ================= MAIN =================

async def run():
    required = [
        BOT_TOKEN,
        SUPABASE_URL,
        SUPABASE_KEY,
        WEBHOOK_SECRET,
        RENDER_EXTERNAL_URL,
    ]

    if not all(required) or ADMIN_ID == 0:
        raise RuntimeError(
            "Missing required environment variables. Check Render Environment."
        )

    telegram_request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=30,
    )

    tg_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(telegram_request)
        .updater(None)
        .build()
    )

    # Register commands

    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("help", help_command))
    tg_app.add_handler(CommandHandler("setload", setload))
    tg_app.add_handler(CommandHandler("showload", showload))
    tg_app.add_handler(CommandHandler("removeload", removeload))
    tg_app.add_handler(CommandHandler("stats", stats))
    tg_app.add_handler(CommandHandler("broadcast", broadcast))

    tg_app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"(?i)^LOAD$"),
            handle_load
        )
    )

    tg_app.add_error_handler(error_handler)

    web = tornado.web.Application([
        (r"/", HealthHandler),
        (r"/healthz", HealthHandler),
        (
            rf"/{WEBHOOK_SECRET}",
            WebhookHandler,
            {"tg_app": tg_app}
        ),
    ])

    # Open port FIRST

    web.listen(PORT, address="0.0.0.0")

    logger.info(
        "HTTP server listening on 0.0.0.0:%s",
        PORT
    )

    # Connect to Telegram in background

    asyncio.create_task(start_bot(tg_app))

    await asyncio.Event().wait()


if __name__ == "__main__":
    logger.info("Starting LOAD Message Bot...")
    asyncio.run(run())
