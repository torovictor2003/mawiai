import logging
import os

from anthropic import AsyncAnthropic
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful Telegram assistant. Keep replies clear, short, and useful.",
)

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in Railway Variables")

if not ANTHROPIC_API_KEY:
    raise RuntimeError("Missing ANTHROPIC_API_KEY in Railway Variables")

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


def split_long_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]

    parts = []
    current = ""

    for line in text.splitlines(True):
        if len(current) + len(line) <= limit:
            current += line
        else:
            if current:
                parts.append(current)
            current = line

    if current:
        parts.append(current)

    return parts


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Bot is live. Send me a message and I’ll reply with Claude."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send any text message and I’ll answer it."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.TYPING,
        )

        response = await client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=700,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_text}
            ],
        )

        text_parts = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)

        reply = "".join(text_parts).strip() or "I got your message, but I couldn’t generate a reply."

        for chunk in split_long_message(reply):
            await update.message.reply_text(chunk)

    except Exception:
        logger.exception("Error while processing Telegram message")
        await update.message.reply_text(
            "Something went wrong while contacting Claude. Check Railway logs."
        )


def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Starting Telegram bot...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
