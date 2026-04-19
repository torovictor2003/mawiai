“””
Crypto/Forex/Stock/Gold Paper Trading Bot
Telegram + Anthropic + CoinGecko + Yahoo Finance
─────────────────────────────────────────────────────────────────────────────
All trades are SIMULATED with real-time prices. No real money.
─────────────────────────────────────────────────────────────────────────────
“””

import asyncio
import io
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

from auto_trader import (
run_full_scan,
format_trade_alert,
check_drawdown,
get_portfolio,
save_portfolio,
load_json,
save_json,
)
from market_data import (
fetch_all_markets,
fetch_gold,
fetch_all_forex,
fetch_all_stocks,
format_asset_line,
)

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
format=”%(asctime)s - %(name)s - %(levelname)s - %(message)s”,
level=logging.INFO,
)
logger = logging.getLogger(**name**)

# ── Environment ────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = (
os.getenv(“TELEGRAM_BOT_TOKEN”)
or os.getenv(“BOT_TOKEN”)
or os.getenv(“TELEGRAM_TOKEN”)
)
ANTHROPIC_API_KEY  = os.getenv(“ANTHROPIC_API_KEY”)
ANTHROPIC_MODEL    = os.getenv(“ANTHROPIC_MODEL”, “claude-haiku-4-5”)
SCAN_INTERVAL      = int(os.getenv(“SCAN_INTERVAL_SECONDS”, “300”))   # 5 min
REPORT_INTERVAL    = int(os.getenv(“REPORT_INTERVAL_SECONDS”, “300”)) # 5 min
MAX_DRAWDOWN_PCT   = float(os.getenv(“MAX_DRAWDOWN_PCT”, “30”))

SYSTEM_PROMPT = (
“You are a multi-market trading analyst covering crypto, forex, gold, and stocks. “
“All analysis is for a paper trading simulator — no real money. “
“Be concise, analytical, and never guarantee profits.”
)

if not TELEGRAM_BOT_TOKEN:
raise RuntimeError(“Missing TELEGRAM_BOT_TOKEN in Railway Variables.”)
if not ANTHROPIC_API_KEY:
raise RuntimeError(“Missing ANTHROPIC_API_KEY in Railway Variables.”)

# ── Clients ────────────────────────────────────────────────────────────────────

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

WATCHLIST_FILE  = Path(“watchlist.json”)
PORTFOLIOS_FILE = Path(“portfolios.json”)
COINGECKO_BASE  = “https://api.coingecko.com/api/v3”

TICKER_MAP = {
“btc”: “bitcoin”, “eth”: “ethereum”, “sol”: “solana”,
“bnb”: “binancecoin”, “xrp”: “ripple”, “ada”: “cardano”,
“doge”: “dogecoin”, “avax”: “avalanche-2”, “dot”: “polkadot”,
“link”: “chainlink”, “matic”: “matic-network”, “ltc”: “litecoin”,
“shib”: “shiba-inu”, “uni”: “uniswap”, “atom”: “cosmos”,
“near”: “near”, “op”: “optimism”, “arb”: “arbitrum”,
“sui”: “sui”, “apt”: “aptos”, “trx”: “tron”,
“ton”: “the-open-network”, “pepe”: “pepe”,
“wif”: “dogwifcoin”, “bonk”: “bonk”,
}

def resolve_coin(s: str) -> str:
return TICKER_MAP.get(s.lower().strip(), s.lower().strip())

# ── Helpers ────────────────────────────────────────────────────────────────────

async def fetch_coin(coin_id: str) -> dict | None:
url = f”{COINGECKO_BASE}/coins/{coin_id}”
params = {“localization”: “false”, “tickers”: “false”,
“community_data”: “false”, “developer_data”: “false”}
async with httpx.AsyncClient(timeout=10) as http:
r = await http.get(url, params=params)
return r.json() if r.status_code == 200 else None

async def get_price(coin_id: str) -> float | None:
data = await fetch_coin(coin_id)
if data:
return data.get(“market_data”, {}).get(“current_price”, {}).get(“usd”)
return None

def market_summary(data: dict) -> str:
name    = data.get(“name”, “Unknown”)
symbol  = data.get(“symbol”, “”).upper()
md      = data.get(“market_data”, {})
price   = md.get(“current_price”, {}).get(“usd”, 0) or 0
ch24    = md.get(“price_change_percentage_24h”, 0) or 0
ch7d    = md.get(“price_change_percentage_7d”, 0) or 0
vol     = md.get(“total_volume”, {}).get(“usd”, 0) or 0
mcap    = md.get(“market_cap”, {}).get(“usd”, 0) or 0
high24  = md.get(“high_24h”, {}).get(“usd”, 0) or 0
low24   = md.get(“low_24h”, {}).get(“usd”, 0) or 0
ath     = md.get(“ath”, {}).get(“usd”, 0) or 0
ath_chg = md.get(“ath_change_percentage”, {}).get(“usd”, 0) or 0
rank    = data.get(“market_cap_rank”, “?”)

```
def p(v): return f"{v:,.4f}" if v < 1 else f"{v:,.2f}"
def pct(v): return f"{'▲' if v >= 0 else '▼'} {abs(v):.2f}%"

return (
    f"📊 *{name}* ({symbol}) — Rank \#{rank}\n"
    f"💵 Price: ${p(price)}\n"
    f"📈 24h: {pct(ch24)}  |  7d: {pct(ch7d)}\n"
    f"🔝 High: ${p(high24)}  |  Low: ${p(low24)}\n"
    f"📦 Volume: ${vol:,.0f}\n"
    f"🏦 Mkt Cap: ${mcap:,.0f}\n"
    f"🏆 ATH: ${p(ath)} ({pct(ath_chg)} from ATH)\n"
)
```

async def ask_claude(prompt: str) -> str:
r = await client.messages.create(
model=ANTHROPIC_MODEL, max_tokens=700,
system=SYSTEM_PROMPT,
messages=[{“role”: “user”, “content”: prompt}],
)
parts = [b.text for b in r.content if getattr(b, “type”, None) == “text”]
return “”.join(parts).strip() or “No response generated.”

def chunks(text: str, limit: int = 4000) -> list[str]:
if len(text) <= limit:
return [text]
parts, cur = [], “”
for line in text.splitlines(True):
if len(cur) + len(line) <= limit:
cur += line
else:
if cur:
parts.append(cur)
cur = line
if cur:
parts.append(cur)
return parts

async def send(update: Update, text: str) -> None:
for chunk in chunks(text):
try:
await update.message.reply_text(chunk, parse_mode=“Markdown”)
except Exception:
await update.message.reply_text(chunk)

def calc_pnl(portfolio: dict, current_prices: dict) -> dict:
cash  = portfolio.get(“cash”, 0)
start = portfolio.get(“starting_balance”, 0)
holdings = portfolio.get(“holdings”, {})
positions = []
total_h = 0

```
for asset_id, pos in holdings.items():
    qty  = pos.get("qty", 0)
    avg  = pos.get("avg_price", 0)
    now  = current_prices.get(asset_id, avg)
    val  = qty * now
    cost = qty * avg
    pnl  = val - cost
    pnl_pct = (pnl / cost * 100) if cost > 0 else 0
    total_h += val
    positions.append({
        "asset_id": asset_id,
        "symbol": pos.get("symbol", asset_id.upper()),
        "qty": qty, "avg_price": avg, "current_price": now,
        "value": val, "pnl": pnl, "pnl_pct": pnl_pct,
    })

total     = cash + total_h
total_pnl = total - start
total_pct = (total_pnl / start * 100) if start > 0 else 0

return {
    "cash": cash, "total_value": total,
    "starting_balance": start,
    "total_pnl": total_pnl, "total_pnl_pct": total_pct,
    "positions": positions,
}
```

# ── /help ──────────────────────────────────────────────────────────────────────

HELP_TEXT = “””
🤖 *Auto Trading Bot*
*Real-time prices. Paper money. Fully automatic.*

━━━━━━━━━━━━━━━━━━━━
🤖 *AUTO TRADER*
━━━━━━━━━━━━━━━━━━━━
`/autostart` — Start auto trading (needs portfolio)
`/autostop` — Pause auto trading
`/autostatus` — See if auto trader is running

*Bot scans every 5 min and trades automatically:*
• Crypto: BTC ETH SOL BNB DOGE
• Meme pumps: PEPE SHIB BONK WIF + 200 others
• Forex: EUR/USD GBP/USD AUD/USD USD/JPY USD/CHF
• Gold & Silver: XAU/USD XAG/USD
• Stocks: AAPL TSLA NVDA MSFT AMZN META GOOGL SPY

━━━━━━━━━━━━━━━━━━━━
💼 *PAPER PORTFOLIO*
━━━━━━━━━━━━━━━━━━━━
`/test [amount]` — Start with fake money
`/portfolio` — Holdings + live P&L
`/trades` — Trade history
`/daychart` — Daily P&L chart image
`/resetportfolio` — Start over

━━━━━━━━━━━━━━━━━━━━
📈 *MANUAL ANALYSIS*
━━━━━━━━━━━━━━━━━━━━
`/scan [coin]` — Price + AI analysis
`/signal [coin]` — Bullish/Bearish read
`/risk [coin]` — Risk tier
`/fomo [coin]` — Hype detector
`/markets` — Live forex, gold, stocks

━━━━━━━━━━━━━━━━━━━━
👁 *WATCHLIST*
━━━━━━━━━━━━━━━━━━━━
`/watch [coin]` — Track a coin
`/watchlist` — Live prices

━━━━━━━━━━━━━━━━━━━━
⚙️ *AUTO RULES*
━━━━━━━━━━━━━━━━━━━━
• Scans every *5 minutes*
• Stop-loss at *-8%* per position
• Take-profit at *+12%* per position
• Max *8 positions* at once
• *30% drawdown* kills all trading
• P&L report every *5 minutes*
“””

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if update.message:
await send(update, HELP_TEXT)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if update.message:
await send(update, HELP_TEXT)

# ── /test ──────────────────────────────────────────────────────────────────────

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id = str(update.effective_user.id)
if not context.args:
await send(update, “Usage: `/test 10000`\nGives you fake money to start auto trading.”)
return
try:
amount = float(context.args[0].replace(”,”, “”))
if amount < 10 or amount > 10_000_000:
raise ValueError
except ValueError:
await send(update, “❌ Enter a valid amount. Example: `/test 10000`”)
return

```
existing = get_portfolio(user_id)
if existing:
    await send(update,
        f"⚠️ You already have a portfolio (${existing.get('starting_balance', 0):,.2f}).\n"
        "Use `/resetportfolio` to start fresh."
    )
    return

portfolio = {
    "starting_balance": amount, "cash": amount,
    "holdings": {}, "trades": [],
    "created_at": datetime.now(timezone.utc).isoformat(),
    "auto_trading": False, "drawdown_alerted": False,
}
save_portfolio(user_id, portfolio)
await send(update,
    f"✅ *Portfolio Started!*\n\n"
    f"💰 Balance: ${amount:,.2f}\n\n"
    f"Now start the auto trader:\n"
    f"`/autostart` — bot trades automatically every 5 min\n\n"
    f"_Covers crypto, meme coins, forex, gold, and stocks._"
)
```

async def resetportfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id = str(update.effective_user.id)
data = load_json(PORTFOLIOS_FILE)
if user_id in data:
del data[user_id]
save_json(PORTFOLIOS_FILE, data)
await send(update, “🗑 Portfolio reset. Use `/test 10000` to start fresh.”)

# ── /autostart /autostop /autostatus ──────────────────────────────────────────

async def autostart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id = str(update.effective_user.id)
portfolio = get_portfolio(user_id)
if not portfolio:
await send(update, “No portfolio yet. Run `/test 10000` first.”)
return
portfolio[“auto_trading”] = True
save_portfolio(user_id, portfolio)
await send(update,
“🤖 *Auto Trader Started!*\n\n”
“Scanning every 5 minutes across:\n”
“• Crypto — BTC ETH SOL BNB DOGE\n”
“• Meme pumps — top 200 coins\n”
“• Forex — EUR/USD GBP/USD and more\n”
“• Gold & Silver\n”
“• Stocks — AAPL TSLA NVDA and more\n\n”
“You’ll get a Telegram alert for every trade.\n”
“Use `/autostop` to pause.”
)

async def autostop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id = str(update.effective_user.id)
portfolio = get_portfolio(user_id)
if portfolio:
portfolio[“auto_trading”] = False
save_portfolio(user_id, portfolio)
await send(update, “⏸ Auto trader paused. Use `/autostart` to resume.”)

async def autostatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id = str(update.effective_user.id)
portfolio = get_portfolio(user_id)
if not portfolio:
await send(update, “No portfolio. Run `/test 10000` first.”)
return
active = portfolio.get(“auto_trading”, False)
positions = len(portfolio.get(“holdings”, {}))
trades_today = sum(
1 for t in portfolio.get(“trades”, [])
if t.get(“time”, “”).startswith(datetime.now(timezone.utc).strftime(”%Y-%m-%d”))
)
status = “🟢 RUNNING” if active else “🔴 PAUSED”
await send(update,
f”*Auto Trader Status*\n\n”
f”Status: {status}\n”
f”Open Positions: {positions}/8\n”
f”Trades Today: {trades_today}\n”
f”Scan Interval: every 5 minutes\n\n”
f”Markets: Crypto · Meme · Forex · Gold · Stocks”
)

# ── /markets ───────────────────────────────────────────────────────────────────

async def markets_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
markets = await fetch_all_markets()

```
lines = ["🌍 *Live Markets*\n"]

forex = markets.get("forex", [])
if forex:
    lines.append("💱 *Forex*")
    for a in forex:
        lines.append(f"  {format_asset_line(a)}")
    lines.append("")

commodities = markets.get("commodities", [])
if commodities:
    lines.append("🥇 *Commodities*")
    for a in commodities:
        lines.append(f"  {format_asset_line(a)}")
    lines.append("")

stocks = markets.get("stocks", [])
if stocks:
    lines.append("📈 *Stocks*")
    for a in stocks[:6]:
        lines.append(f"  {format_asset_line(a)}")

await send(update, "\n".join(lines))
```

# ── /scan ──────────────────────────────────────────────────────────────────────

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message or not context.args:
await send(update, “Usage: `/scan btc`”)
return
coin_id = resolve_coin(context.args[0])
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
data = await fetch_coin(coin_id)
if not data:
await send(update, f”❌ Couldn’t find `{context.args[0]}`.”)
return
summary  = market_summary(data)
desc     = (data.get(“description”, {}).get(“en”, “”) or “”)[:300]
analysis = await ask_claude(
f”Data:\n{summary}\nBackground: {desc}\n\n”
“3-sentence market snapshot. Cover trend, volume, ATH distance.”
)
await send(update, f”{summary}\n🤖 *AI Take:*\n{analysis}”)

# ── /signal ────────────────────────────────────────────────────────────────────

async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message or not context.args:
await send(update, “Usage: `/signal eth`”)
return
coin_id = resolve_coin(context.args[0])
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
data = await fetch_coin(coin_id)
if not data:
await send(update, f”❌ Couldn’t find `{context.args[0]}`.”)
return
md      = data.get(“market_data”, {})
summary = market_summary(data)
extras  = (
f”14d: {md.get(‘price_change_percentage_14d’,‘N/A’)}%\n”
f”30d: {md.get(‘price_change_percentage_30d’,‘N/A’)}%”
)
analysis = await ask_claude(
f”{summary}\n{extras}\n\n”
“Technical signal in 4 sentences. Verdict: Bullish / Bearish / Neutral.”
)
await send(update, f”📡 *Signal — {data.get(‘name’,’’).upper()}*\n\n{analysis}”)

# ── /risk ──────────────────────────────────────────────────────────────────────

async def risk_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message or not context.args:
await send(update, “Usage: `/risk sol`”)
return
coin_id = resolve_coin(context.args[0])
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
data = await fetch_coin(coin_id)
if not data:
await send(update, f”❌ Couldn’t find `{context.args[0]}`.”)
return
summary  = market_summary(data)
analysis = await ask_claude(
f”{summary}\n\nRisk assessment:\n”
“1. Tier: Low/Medium/High/Very High\n”
“2. Key risk factors\n3. Position size suggestion”
)
await send(update, f”⚠️ *Risk — {data.get(‘name’,’’).upper()}*\n\n{analysis}”)

# ── /fomo ──────────────────────────────────────────────────────────────────────

async def fomo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message or not context.args:
await send(update, “Usage: `/fomo pepe`”)
return
coin_id = resolve_coin(context.args[0])
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
data = await fetch_coin(coin_id)
if not data:
await send(update, f”❌ Couldn’t find `{context.args[0]}`.”)
return
md      = data.get(“market_data”, {})
summary = market_summary(data)
ch24    = md.get(“price_change_percentage_24h”, 0) or 0
vol     = md.get(“total_volume”, {}).get(“usd”, 0) or 0
mcap    = md.get(“market_cap”, {}).get(“usd”, 1) or 1
ratio   = vol / mcap * 100
analysis = await ask_claude(
f”{summary}\nVol/MCap ratio: {ratio:.1f}%\n24h: {ch24:.2f}%\n\n”
“FOMO score: Low/Medium/High/Extreme. “
“Is this real momentum or hype? What should a cautious trader do?”
)
score_emoji = “🟢” if ch24 < 5 else “🟡” if ch24 < 15 else “🔴”
await send(update, f”{score_emoji} *FOMO — {data.get(‘name’,’’).upper()}*\n\n{analysis}”)

# ── /portfolio ─────────────────────────────────────────────────────────────────

async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id = str(update.effective_user.id)
portfolio = get_portfolio(user_id)
if not portfolio:
await send(update, “No portfolio. Start with `/test 10000`”)
return

```
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
holdings = portfolio.get("holdings", {})

# Get current prices for crypto holdings
current_prices = {}
for asset_id, pos in holdings.items():
    current_prices[asset_id] = pos.get("avg_price", 0)  # fallback
    try:
        coin_data = await fetch_coin(asset_id)
        if coin_data:
            p = coin_data.get("market_data", {}).get("current_price", {}).get("usd")
            if p:
                current_prices[asset_id] = p
    except Exception:
        pass

pnl   = calc_pnl(portfolio, current_prices)
emoji = "🟢" if pnl["total_pnl"] >= 0 else "🔴"
auto  = "🤖 ON" if portfolio.get("auto_trading") else "⏸ OFF"

lines = [
    f"💼 *Paper Portfolio* | Auto: {auto}\n",
    f"💰 Started: ${pnl['starting_balance']:,.2f}",
    f"💵 Cash: ${pnl['cash']:,.2f}",
    f"📊 Total: ${pnl['total_value']:,.2f}",
    f"{emoji} P&L: ${pnl['total_pnl']:+,.2f} ({pnl['total_pnl_pct']:+.2f}%)\n",
]

if pnl["positions"]:
    lines.append("*Open Positions:*")
    for pos in pnl["positions"]:
        pe = "🟢" if pos["pnl"] >= 0 else "🔴"
        lines.append(
            f"{pe} *{pos['symbol']}*\n"
            f"   Val: ${pos['value']:,.2f} | "
            f"P&L: ${pos['pnl']:+,.2f} ({pos['pnl_pct']:+.2f}%)"
        )
else:
    lines.append("_No open positions._")

if pnl["total_pnl_pct"] <= -30:
    lines.append(
        f"\n⛔ *30% DRAWDOWN — Auto trader paused.*\n"
        f"Use `/resetportfolio` to start fresh."
    )

await send(update, "\n".join(lines))
```

# ── /trades ────────────────────────────────────────────────────────────────────

async def trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id   = str(update.effective_user.id)
portfolio = get_portfolio(user_id)
if not portfolio:
await send(update, “No portfolio. Start with `/test 10000`”)
return
trades = portfolio.get(“trades”, [])
if not trades:
await send(update, “No trades yet.”)
return
lines = [f”📋 *Trades* ({len(trades)} total, last 15)\n”]
for t in reversed(trades[-15:]):
t_type = t.get(“type”, “?”)
sym    = t.get(“symbol”, t.get(“coin”, “?”)).upper()
usd    = t.get(“usd”, 0)
price  = t.get(“price”, 0)
time_s = t.get(“time”, “”)[:16].replace(“T”, “ “)
pnl_s  = “”
if t_type == “SELL”:
p = t.get(“pnl”, 0)
pnl_s = f” | P&L: ${p:+,.2f}”
reason = t.get(“reason”, t.get(“pump_reason”, “”))
icon   = “🟢” if t_type == “BUY” else “🔴”
lines.append(
f”{icon} {t_type} *{sym}* ${usd:,.2f} @ ${price:,.4f}{pnl_s}\n”
f”   *{time_s} UTC* {f’· {reason}’ if reason else ‘’}”
)
await send(update, “\n”.join(lines))

# ── /daychart ──────────────────────────────────────────────────────────────────

async def daychart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id   = str(update.effective_user.id)
portfolio = get_portfolio(user_id)
if not portfolio:
await send(update, “No portfolio. Start with `/test 10000`”)
return

```
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

try:
    from chart_generator import generate_daily_chart
    username = update.effective_user.first_name or "Trader"
    buf = generate_daily_chart(portfolio.get("trades", []), portfolio, username)
    await update.message.reply_photo(
        photo=buf,
        caption="📊 *Daily Trading Summary* — Paper Trading",
        parse_mode="Markdown",
    )
except Exception as e:
    logger.exception("Chart generation failed")
    await send(update, f"❌ Chart failed: {e}")
```

# ── /watch & /watchlist ────────────────────────────────────────────────────────

async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message or not context.args:
await send(update, “Usage: `/watch btc`”)
return
coin_id = resolve_coin(context.args[0])
user_id = str(update.effective_user.id)
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
data = await fetch_coin(coin_id)
if not data:
await send(update, f”❌ Couldn’t verify `{context.args[0]}`.”)
return
watchlist = load_json(WATCHLIST_FILE)
if user_id not in watchlist:
watchlist[user_id] = {}
if coin_id in watchlist[user_id]:
await send(update, f”👁 Already watching *{data.get(‘name’)}*.”)
return
watchlist[user_id][coin_id] = data.get(“name”, coin_id)
save_json(WATCHLIST_FILE, watchlist)
await send(update, f”✅ Added *{data.get(‘name’)}* to watchlist.”)

async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id   = str(update.effective_user.id)
watchlist = load_json(WATCHLIST_FILE)
coins     = watchlist.get(user_id, {})
if not coins:
await send(update, “Watchlist empty. Use `/watch btc`.”)
return
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
lines = [“👁 *Watchlist*\n”]
for coin_id, name in coins.items():
data = await fetch_coin(coin_id)
if data:
md    = data.get(“market_data”, {})
price = md.get(“current_price”, {}).get(“usd”, 0) or 0
ch    = md.get(“price_change_percentage_24h”, 0) or 0
arrow = “▲” if ch >= 0 else “▼”
lines.append(f”• *{name}*: ${price:,.4f}  {arrow} {abs(ch):.2f}%”)
else:
lines.append(f”• {name}: unavailable”)
await send(update, “\n”.join(lines))

# ── Free text ──────────────────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message or not update.message.text:
return
text = update.message.text.strip()
if not text:
return
try:
await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
reply = await ask_claude(text)
await send(update, reply)
except Exception:
logger.exception(“handle_text error”)
await send(update, “Something went wrong.”)

# ── Background: auto scanner ───────────────────────────────────────────────────

async def auto_scan_loop(app) -> None:
“””
Runs every SCAN_INTERVAL seconds.
For each user with auto_trading=True, runs full market scan
and sends trade alerts to Telegram.
“””
await asyncio.sleep(60)
while True:
try:
await asyncio.sleep(SCAN_INTERVAL)
all_portfolios = load_json(PORTFOLIOS_FILE)

```
        for user_id, portfolio in all_portfolios.items():
            if not portfolio.get("auto_trading", False):
                continue

            # Check drawdown
            hit_dd, pct = check_drawdown(portfolio)
            if hit_dd:
                portfolio["auto_trading"] = False
                save_portfolio(user_id, portfolio)
                try:
                    await app.bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            "⛔ *AUTO TRADER STOPPED*\n\n"
                            f"Portfolio hit 30% drawdown ({pct:.1f}%).\n"
                            "Auto trading paused to protect remaining capital.\n\n"
                            "Use `/portfolio` to review.\n"
                            "Use `/resetportfolio` to start fresh."
                        ),
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.warning(f"Could not send drawdown alert to {user_id}: {e}")
                continue

            # Run all scanners
            trades = await run_full_scan(user_id)

            # Send alert for each trade
            for trade in trades:
                alert = format_trade_alert(trade)
                try:
                    await app.bot.send_message(
                        chat_id=int(user_id),
                        text=alert,
                        parse_mode="Markdown",
                    )
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.warning(f"Trade alert failed for {user_id}: {e}")

    except Exception:
        logger.exception("auto_scan_loop error")
```

# ── Background: 5-min P&L reporter ────────────────────────────────────────────

async def pnl_report_loop(app) -> None:
“”“Sends a P&L summary every 5 min for users with open positions.”””
await asyncio.sleep(90)
while True:
try:
await asyncio.sleep(REPORT_INTERVAL)
all_portfolios = load_json(PORTFOLIOS_FILE)

```
        for user_id, portfolio in all_portfolios.items():
            holdings = portfolio.get("holdings", {})
            if not holdings:
                continue

            start = portfolio.get("starting_balance", 0)
            cash  = portfolio.get("cash", 0)

            # Estimate total with cost basis
            holdings_cost = sum(
                h.get("qty", 0) * h.get("avg_price", 0)
                for h in holdings.values()
            )
            total    = cash + holdings_cost
            pnl      = total - start
            pnl_pct  = (pnl / start * 100) if start > 0 else 0
            emoji    = "🟢" if pnl >= 0 else "🔴"
            auto_tag = "🤖" if portfolio.get("auto_trading") else "⏸"
            trades_today = sum(
                1 for t in portfolio.get("trades", [])
                if t.get("time", "").startswith(
                    datetime.now(timezone.utc).strftime("%Y-%m-%d")
                )
            )

            lines = [
                f"⏱ *5-Min Report* {auto_tag}\n",
                f"💵 Cash: ${cash:,.2f}",
                f"📊 Total: ${total:,.2f}",
                f"{emoji} P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)",
                f"📋 Trades today: {trades_today}",
                f"📂 Positions: {len(holdings)}/8\n",
            ]

            for asset_id, pos in holdings.items():
                sym = pos.get("symbol", asset_id.upper())
                lines.append(f"  • {sym}")

            try:
                await app.bot.send_message(
                    chat_id=int(user_id),
                    text="\n".join(lines),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"P&L report failed for {user_id}: {e}")

    except Exception:
        logger.exception("pnl_report_loop error")
```

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
logger.info(“Starting Auto Trading Bot…”)
app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

```
app.add_handler(CommandHandler("start",           start_command))
app.add_handler(CommandHandler("help",            help_command))
app.add_handler(CommandHandler("test",            test_command))
app.add_handler(CommandHandler("resetportfolio",  resetportfolio_command))
app.add_handler(CommandHandler("autostart",       autostart_command))
app.add_handler(CommandHandler("autostop",        autostop_command))
app.add_handler(CommandHandler("autostatus",      autostatus_command))
app.add_handler(CommandHandler("markets",         markets_command))
app.add_handler(CommandHandler("scan",            scan_command))
app.add_handler(CommandHandler("signal",          signal_command))
app.add_handler(CommandHandler("risk",            risk_command))
app.add_handler(CommandHandler("fomo",            fomo_command))
app.add_handler(CommandHandler("portfolio",       portfolio_command))
app.add_handler(CommandHandler("trades",          trades_command))
app.add_handler(CommandHandler("daychart",        daychart_command))
app.add_handler(CommandHandler("watch",           watch_command))
app.add_handler(CommandHandler("watchlist",       watchlist_command))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

async def post_init(application):
    asyncio.create_task(auto_scan_loop(application))
    asyncio.create_task(pnl_report_loop(application))

app.post_init = post_init
app.run_polling(drop_pending_updates=True)
```

if **name** == “**main**”:
main()
