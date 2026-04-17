import os
import requests
from anthropic import Anthropic
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CMC_API_KEY = os.environ.get("CMC_API_KEY")

client = Anthropic(api_key=ANTHROPIC_API_KEY)
COINS = ["BTC", "ETH", "SOL", "BNB", "DOGE"]

def get_prices():
    try:
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        r = requests.get(url,
            headers={"X-CMC_PRO_API_KEY": CMC_API_KEY},
            params={"symbol": ",".join(COINS), "convert": "USD"},
            timeout=10
        )
        data = r.json()
        out = {}
        for coin in COINS:
            if coin in data.get("data", {}):
                q = data["data"][coin]["quote"]["USD"]
                out[coin] = {
                    "price": q["price"],
                    "change_24h": q["percent_change_24h"],
                    "change_1h": q["percent_change_1h"],
                }
        return out
    except Exception as e:
        print(f"Price error: {e}")
        return {}

def prices_text(prices):
    if not prices:
        return "Could not fetch prices."
    lines = ["Live Prices:\n"]
    for coin, d in prices.items():
        lines.append(f"{coin}: ${d['price']:,.4f} | 24h: {d['change_24h']:+.2f}%")
    return "\n".join(lines)

def ask_claude(market, question):
    try:
        r = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system="""You are a crypto trading AI for a $150 account.
Always respond in this format:
Signal: BUY/SELL/HOLD
Coin:
Entry:
Take Profit:
Stop Loss:
Confidence: X/10
Reason: (one sentence)
Risk Warning: (one sentence)""",
            messages=[{"role": "user", "content": f"{market}\n\n{question}"}]
        )
        return r.content[0].text
    except Exception as e:
        return f"AI error: {e}"

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Crypto AI Bot Ready\n\n"
        "/signal - Best trade now\n"
        "/prices - Live prices\n"
        "/analysis - Market breakdown\n\n"
        "Or just ask me anything!"
    )

async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Analyzing...")
    p = get_prices()
    result = ask_claude(prices_text(p), "What is the single best trade right now?")
    await update.message.reply_text(f"📊 Signal\n\n{result}")

async def cmd_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p = get_prices()
    await update.message.reply_text(prices_text(p))

async def cmd_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Running analysis...")
    p = get_prices()
    result = ask_claude(prices_text(p), "Give me the top 2 trade setups and overall market sentiment.")
    await update.message.reply_text(f"📈 Analysis\n\n{result}")

async def cmd_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Thinking...")
    p = get_prices()
    result = ask_claude(prices_text(p), update.message.text)
    await update.message.reply_text(result)

def main():
    print("Starting bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("prices", cmd_prices))
    app.add_handler(CommandHandler("analysis", cmd_analysis))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_message))
    print("✅ Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
