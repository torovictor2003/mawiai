import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
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

# ── LOGGING ──
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── ENV ──
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
if not ANTHROPIC_API_KEY:
    raise RuntimeError("Missing ANTHROPIC_API_KEY")

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# ── STORAGE ──
PORTFOLIOS_FILE = Path("portfolios.json")

def load_data():
    if PORTFOLIOS_FILE.exists():
        return json.loads(PORTFOLIOS_FILE.read_text())
    return {}

def save_data(data):
    PORTFOLIOS_FILE.write_text(json.dumps(data, indent=2))

# ── AI ──
async def ask_ai(prompt):
    try:
        r = await client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return r.content[0].text
    except Exception as e:
        logger.error(e)
        return "AI error"

# ── COMMANDS ──

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Bot activo. Usa /help")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/test 10000\n/autostart\n/autostop\n/portfolio\n/markets"
    )

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if not context.args:
        await update.message.reply_text("Use: /test 10000")
        return

    amount = float(context.args[0])
    data = load_data()

    data[user_id] = {
        "cash": amount,
        "start": amount,
        "auto": False,
        "created": datetime.now(timezone.utc).isoformat()
    }

    save_data(data)
    await update.message.reply_text(f"Portfolio creado con ${amount}")

async def autostart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    data = load_data()

    if user_id not in data:
        await update.message.reply_text("Primero usa /test")
        return

    data[user_id]["auto"] = True
    save_data(data)

    await update.message.reply_text("🤖 Auto trading ACTIVADO")

async def autostop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    data = load_data()

    if user_id in data:
        data[user_id]["auto"] = False
        save_data(data)

    await update.message.reply_text("⛔ Auto trading DETENIDO")

async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    data = load_data()

    if user_id not in data:
        await update.message.reply_text("No tienes portfolio")
        return

    p = data[user_id]

    await update.message.reply_text(
        f"""
💼 Portfolio
Cash: ${p['cash']}
Start: ${p['start']}
Auto: {p['auto']}
"""
    )

async def markets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(ChatAction.TYPING)

    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd"
        )
        data = r.json()

    msg = f"""
📊 Markets
BTC: ${data['bitcoin']['usd']}
ETH: ${data['ethereum']['usd']}
SOL: ${data['solana']['usd']}
"""

    await update.message.reply_text(msg)

# ── CHAT AI ──
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await update.message.chat.send_action(ChatAction.TYPING)

    response = await ask_ai(text)
    await update.message.reply_text(response)

# ── AUTO LOOP ──
async def auto_loop(app):
    while True:
        await asyncio.sleep(120)

        data = load_data()

        for user_id, p in data.items():
            if not p.get("auto"):
                continue

            try:
                await app.bot.send_message(
                    chat_id=int(user_id),
                    text="📡 Bot escaneando mercado..."
                )
            except Exception as e:
                logger.warning(e)

# ── MAIN ──
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("test", test))
    app.add_handler(CommandHandler("autostart", autostart))
    app.add_handler(CommandHandler("autostop", autostop))
    app.add_handler(CommandHandler("portfolio", portfolio))
    app.add_handler(CommandHandler("markets", markets))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    async def post_init(application):
        asyncio.create_task(auto_loop(application))

    app.post_init = post_init

    print("BOT CORRIENDO 🔥")
    app.run_polling()

if __name__ == "__main__":
    main()
