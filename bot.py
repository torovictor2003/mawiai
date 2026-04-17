import os
import requests
import anthropic
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CMC_API_KEY = os.environ.get("CMC_API_KEY")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
TRACKED_COINS = ["BTC", "ETH", "SOL", "BNB", "DOGE"]

def get_crypto_prices():
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
    params = {"symbol": ",".join(TRACKED_COINS), "convert": "USD"}
    try:
        r = requests.get(url, headers=headers, params=params)
        data = r.json()
        prices = {}
        for coin in TRACKED_COINS:
            if coin in data["data"]:
                q = data["data"][coin]["quote"]["USD"]
                prices[coin] = {
                    "price": q["price"],
                    "change_1h": q["percent_change_1h"],
                    "change_24h": q["percent_change_24h"],
                    "change_7d": q["percent_change_7d"],
                }
        return prices
    except:
        return None

def format_prices(prices):
    if not prices:
        return "Could not fetch prices."
    text = "Live Market Data:\n\n"
    for coin, d in prices.items():
        text += f"{coin}: ${d['price']:,.4f} | 1h: {d['change_1h']:+.2f}% | 24h: {d['change_24h']:+.2f}%\n"
    return text

async def get_ai_signal(market_data, question=None):
    system = """You are an aggressive crypto trading AI for a trader with $150.
Always give signals in this format:
- 🟢 BUY / 🔴 SELL / 🟡 HOLD
- Coin:
- Entry price:
- Take profit:
- Stop loss:
- Confidence: X/10
- Reason: (1 sentence)
⚠️ Always add risk warning."""

    msg = f"{market_data}\n\n{question or 'Give me the best trade signal right now.'}"
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": msg}]
    )
    return response.content[0].text

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Crypto AI Bot Ready*\n\n"
        "/signal — Best trade right now\n"
        "/prices — Live prices\n"
        "/analysis — Full market breakdown\n"
        "Or just ask me anything!",
        parse_mode="Markdown"
    )

async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analyzing market...")
    prices = get_crypto_prices()
    result = await get_ai_signal(format_prices(prices))
    await update.message.reply_text(f"📊 *Signal*\n\n{result}", parse_mode="Markdown")

async def prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_crypto_prices()
    if not data:
        await update.message.reply_text("❌ Could not fetch prices.")
        return
    msg = "💹 *Live Prices*\n\n"
    for coin, d in data.items():
        e = "🟢" if d['change_24h'] > 0 else "🔴"
        msg += f"{e} *{coin}*: ${d['price']:,.4f} ({d['change_24h']:+.2f}%)\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧠 Running analysis...")
    data = get_crypto_prices()
    result = await get_ai_signal(format_prices(data),
        "Give top 2 trade setups with full analysis. What is market sentiment?")
    await update.message.reply_text(f"📈 *Analysis*\n\n{result}", parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💭 Thinking...")
    data = get_crypto_prices()
    result = await get_ai_signal(format_prices(data), update.message.text)
    await update.message.reply_text(result)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("signal", signal))
    app.add_handler(CommandHandler("prices", prices))
    app.add_handler(CommandHandler("analysis", analysis))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
