“””
Mawi Auto Trading Bot — Single File Version
Everything in one file. Just copy this as bot.py
Requires: anthropic<1, httpx>=0.25,<1, python-telegram-bot>=21,<22, matplotlib
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

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
format=”%(asctime)s - %(name)s - %(levelname)s - %(message)s”,
level=logging.INFO,
)
logger = logging.getLogger(**name**)

# ── Env vars ───────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = (
os.getenv(“TELEGRAM_BOT_TOKEN”)
or os.getenv(“BOT_TOKEN”)
or os.getenv(“TELEGRAM_TOKEN”)
)
ANTHROPIC_API_KEY = os.getenv(“ANTHROPIC_API_KEY”)
ANTHROPIC_MODEL   = os.getenv(“ANTHROPIC_MODEL”, “claude-haiku-4-5”)
SCAN_INTERVAL     = int(os.getenv(“SCAN_INTERVAL_SECONDS”, “300”))
MAX_DRAWDOWN_PCT  = 30.0

if not TELEGRAM_BOT_TOKEN:
raise RuntimeError(“Missing TELEGRAM_BOT_TOKEN in Railway Variables.”)
if not ANTHROPIC_API_KEY:
raise RuntimeError(“Missing ANTHROPIC_API_KEY in Railway Variables.”)

client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = (
“You are a multi-market trading analyst covering crypto, forex, gold, and stocks. “
“All analysis is for a paper trading simulator — no real money. “
“Be concise, analytical, and never guarantee profits.”
)

# ── Storage ────────────────────────────────────────────────────────────────────

PORTFOLIOS_FILE   = Path(“portfolios.json”)
WATCHLIST_FILE    = Path(“watchlist.json”)
PUMP_HISTORY_FILE = Path(“pump_history.json”)

def load_json(path: Path) -> dict:
if path.exists():
try:
return json.loads(path.read_text())
except Exception:
return {}
return {}

def save_json(path: Path, data: dict) -> None:
path.write_text(json.dumps(data, indent=2))

def get_portfolio(user_id: str) -> dict:
return load_json(PORTFOLIOS_FILE).get(user_id, {})

def save_portfolio(user_id: str, portfolio: dict) -> None:
data = load_json(PORTFOLIOS_FILE)
data[user_id] = portfolio
save_json(PORTFOLIOS_FILE, data)

# ── CoinGecko ──────────────────────────────────────────────────────────────────

COINGECKO_BASE = “https://api.coingecko.com/api/v3”

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

MOMENTUM_COINS = [“bitcoin”, “ethereum”, “solana”, “binancecoin”, “dogecoin”]

MEME_COINS = {
“shiba-inu”, “pepe”, “bonk”, “dogwifcoin”, “floki”,
“baby-doge-coin”, “mog-coin”, “brett”, “book-of-meme”,
}

COIN_SYMBOL = {
“bitcoin”: “BTC”, “ethereum”: “ETH”, “solana”: “SOL”,
“binancecoin”: “BNB”, “dogecoin”: “DOGE”, “shiba-inu”: “SHIB”,
“pepe”: “PEPE”, “bonk”: “BONK”, “dogwifcoin”: “WIF”,
}

def resolve_coin(s: str) -> str:
return TICKER_MAP.get(s.lower().strip(), s.lower().strip())

async def fetch_coin(coin_id: str) -> dict | None:
url = f”{COINGECKO_BASE}/coins/{coin_id}”
params = {“localization”: “false”, “tickers”: “false”,
“community_data”: “false”, “developer_data”: “false”}
try:
async with httpx.AsyncClient(timeout=12) as http:
r = await http.get(url, params=params)
return r.json() if r.status_code == 200 else None
except Exception as e:
logger.warning(f”fetch_coin({coin_id}): {e}”)
return None

async def get_price(coin_id: str) -> float | None:
data = await fetch_coin(coin_id)
if data:
return data.get(“market_data”, {}).get(“current_price”, {}).get(“usd”)
return None

async def fetch_market_scan(page: int = 1) -> list:
url = f”{COINGECKO_BASE}/coins/markets”
params = {
“vs_currency”: “usd”, “order”: “volume_desc”,
“per_page”: 100, “page”: page,
“price_change_percentage”: “1h,24h”,
“sparkline”: “false”,
}
try:
async with httpx.AsyncClient(timeout=15) as http:
r = await http.get(url, params=params)
return r.json() if r.status_code == 200 else []
except Exception as e:
logger.warning(f”fetch_market_scan: {e}”)
return []

# ── Yahoo Finance stocks ───────────────────────────────────────────────────────

STOCK_TICKERS = [“AAPL”, “TSLA”, “NVDA”, “MSFT”, “AMZN”, “META”, “GOOGL”, “SPY”]

async def fetch_stock(ticker: str) -> dict | None:
url = f”https://query1.finance.yahoo.com/v8/finance/chart/{ticker}”
headers = {“User-Agent”: “Mozilla/5.0”}
try:
async with httpx.AsyncClient(timeout=12, headers=headers) as http:
r = await http.get(url, params={“interval”: “1d”, “range”: “2d”})
if r.status_code != 200:
return None
meta = r.json()[“chart”][“result”][0][“meta”]
price = meta.get(“regularMarketPrice”, 0)
prev  = meta.get(“chartPreviousClose”) or meta.get(“previousClose”, price)
ch    = ((price - prev) / prev * 100) if prev else 0
return {
“ticker”: ticker, “name”: meta.get(“shortName”, ticker),
“price”: price, “change_24h”: ch,
“change_1h”: ch * 0.25, “asset_type”: “stock”,
}
except Exception as e:
logger.warning(f”fetch_stock({ticker}): {e}”)
return None

async def fetch_all_stocks() -> list:
results = await asyncio.gather(*[fetch_stock(t) for t in STOCK_TICKERS],
return_exceptions=True)
return [r for r in results if isinstance(r, dict)]

# ── Forex / Gold via CoinGecko ─────────────────────────────────────────────────

FOREX_PAIRS = {
“EURUSD”: (“euro”,              False),
“GBPUSD”: (“pound-sterling”,    False),
“AUDUSD”: (“australian-dollar”, False),
“USDCHF”: (“swiss-franc”,       True),
“USDJPY”: (“japanese-yen”,      True),
}

async def fetch_forex(pair: str) -> dict | None:
if pair not in FOREX_PAIRS:
return None
coin_id, invert = FOREX_PAIRS[pair]
data = await fetch_coin(coin_id)
if not data:
return None
md    = data.get(“market_data”, {})
raw   = md.get(“current_price”, {}).get(“usd”, 0) or 0
if raw == 0:
return None
price = (1 / raw) if invert else raw
ch24  = md.get(“price_change_percentage_24h”, 0) or 0
ch1h  = md.get(“price_change_percentage_1h_in_currency”, {}).get(“usd”, 0) or 0
if invert:
ch24, ch1h = -ch24, -ch1h
return {
“ticker”: pair, “name”: pair[:3] + “/” + pair[3:],
“price”: price, “change_24h”: ch24,
“change_1h”: ch1h, “asset_type”: “forex”,
}

async def fetch_gold() -> dict | None:
data = await fetch_coin(“gold”)
if not data:
return None
md   = data.get(“market_data”, {})
return {
“ticker”: “XAU/USD”, “name”: “Gold”,
“price”: md.get(“current_price”, {}).get(“usd”, 0) or 0,
“change_24h”: md.get(“price_change_percentage_24h”, 0) or 0,
“change_1h”: md.get(“price_change_percentage_1h_in_currency”, {}).get(“usd”, 0) or 0,
“asset_type”: “commodity”,
}

async def fetch_silver() -> dict | None:
data = await fetch_coin(“silver”)
if not data:
return None
md   = data.get(“market_data”, {})
return {
“ticker”: “XAG/USD”, “name”: “Silver”,
“price”: md.get(“current_price”, {}).get(“usd”, 0) or 0,
“change_24h”: md.get(“price_change_percentage_24h”, 0) or 0,
“change_1h”: md.get(“price_change_percentage_1h_in_currency”, {}).get(“usd”, 0) or 0,
“asset_type”: “commodity”,
}

# ── Market summary formatters ──────────────────────────────────────────────────

def fmt_price(v: float, asset_type: str = “crypto”) -> str:
if asset_type == “forex”:
return f”{v:.4f}”
if v > 1000:
return f”${v:,.2f}”
if v > 1:
return f”${v:.4f}”
return f”${v:.8f}”

def fmt_pct(v: float) -> str:
arrow = “▲” if v >= 0 else “▼”
return f”{arrow} {abs(v):.2f}%”

def coin_summary(data: dict) -> str:
name    = data.get(“name”, “?”)
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
p = lambda v: f”{v:,.4f}” if v < 1 else f”{v:,.2f}”
return (
f”*{name}* ({symbol}) Rank #{rank}\n”
f”Price: ${p(price)}\n”
f”24h: {fmt_pct(ch24)}  7d: {fmt_pct(ch7d)}\n”
f”High: ${p(high24)}  Low: ${p(low24)}\n”
f”Vol: ${vol:,.0f}  MCap: ${mcap:,.0f}\n”
f”ATH: ${p(ath)} ({fmt_pct(ath_chg)} from ATH)”
)

def asset_line(a: dict) -> str:
ticker = a.get(“ticker”, “?”)
price  = a.get(“price”, 0)
ch1h   = a.get(“change_1h”, 0) or 0
ch24   = a.get(“change_24h”, 0) or 0
atype  = a.get(“asset_type”, “crypto”)
ps     = fmt_price(price, atype)
return (
f”*{ticker}*: {ps}  “
f”1h:{fmt_pct(ch1h)}  24h:{fmt_pct(ch24)}”
)

# ── Claude ─────────────────────────────────────────────────────────────────────

async def ask_claude(prompt: str) -> str:
try:
r = await client.messages.create(
model=ANTHROPIC_MODEL, max_tokens=600,
system=SYSTEM_PROMPT,
messages=[{“role”: “user”, “content”: prompt}],
)
parts = [b.text for b in r.content if getattr(b, “type”, None) == “text”]
return “”.join(parts).strip() or “No response.”
except Exception as e:
logger.error(f”Claude error: {e}”)
return “AI unavailable right now.”

# ── Message utils ──────────────────────────────────────────────────────────────

def split_msg(text: str, limit: int = 4000) -> list:
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
for chunk in split_msg(text):
try:
await update.message.reply_text(chunk, parse_mode=“Markdown”)
except Exception:
try:
await update.message.reply_text(chunk)
except Exception as e:
logger.error(f”send failed: {e}”)

# ── Portfolio helpers ──────────────────────────────────────────────────────────

MAX_POSITIONS        = 8
CRYPTO_POS_PCT       = 0.08
PUMP_POS_PCT         = 0.04
MARKET_POS_PCT       = 0.06
STOP_LOSS_PCT        = -8.0
TAKE_PROFIT_PCT      = 12.0
CRYPTO_BUY_TH        = 2.5
CRYPTO_SELL_TH       = -2.0
PUMP_PRICE_TH        = 8.0
PUMP_VOL_RATIO_TH    = 15.0
MARKET_BUY_TH        = 0.4
MARKET_SELL_TH       = -0.4

def position_exit(holding: dict, price: float) -> str:
avg = holding.get(“avg_price”, price)
if avg <= 0:
return “HOLD”
pct = (price - avg) / avg * 100
if pct <= STOP_LOSS_PCT:
return “STOP_LOSS”
if pct >= TAKE_PROFIT_PCT:
return “TAKE_PROFIT”
return “HOLD”

def do_buy(portfolio: dict, asset_id: str, price: float,
symbol: str, pos_pct: float) -> dict | None:
holdings = portfolio.get(“holdings”, {})
cash     = portfolio.get(“cash”, 0)
start    = portfolio.get(“starting_balance”, cash)
if asset_id in holdings:
return None
if len(holdings) >= MAX_POSITIONS:
return None
usd = start * pos_pct
if usd > cash or usd < 1:
return None
qty = usd / price
portfolio[“cash”] = cash - usd
holdings[asset_id] = {“qty”: qty, “avg_price”: price, “symbol”: symbol}
portfolio[“holdings”] = holdings
trade = {
“type”: “BUY”, “coin”: asset_id, “symbol”: symbol,
“qty”: qty, “price”: price, “usd”: usd,
“time”: datetime.now(timezone.utc).isoformat(),
}
portfolio.setdefault(“trades”, []).append(trade)
return trade

def do_sell(portfolio: dict, asset_id: str, price: float,
reason: str = “SIGNAL”) -> dict | None:
holdings = portfolio.get(“holdings”, {})
if asset_id not in holdings:
return None
pos    = holdings.pop(asset_id)
qty    = pos[“qty”]
avg    = pos[“avg_price”]
symbol = pos.get(“symbol”, asset_id.upper())
usd    = qty * price
pnl    = usd - (qty * avg)
portfolio[“cash”] = portfolio.get(“cash”, 0) + usd
portfolio[“holdings”] = holdings
trade = {
“type”: “SELL”, “coin”: asset_id, “symbol”: symbol,
“qty”: qty, “price”: price, “usd”: usd, “pnl”: pnl,
“reason”: reason,
“time”: datetime.now(timezone.utc).isoformat(),
}
portfolio.setdefault(“trades”, []).append(trade)
return trade

def portfolio_pnl(portfolio: dict, prices: dict) -> dict:
cash  = portfolio.get(“cash”, 0)
start = portfolio.get(“starting_balance”, 0)
positions = []
total_h = 0
for aid, pos in portfolio.get(“holdings”, {}).items():
qty  = pos.get(“qty”, 0)
avg  = pos.get(“avg_price”, 0)
now  = prices.get(aid, avg)
val  = qty * now
cost = qty * avg
pnl  = val - cost
pp   = (pnl / cost * 100) if cost > 0 else 0
total_h += val
positions.append({
“asset_id”: aid,
“symbol”: pos.get(“symbol”, aid.upper()),
“qty”: qty, “avg_price”: avg, “current_price”: now,
“value”: val, “pnl”: pnl, “pnl_pct”: pp,
})
total     = cash + total_h
total_pnl = total - start
total_pct = (total_pnl / start * 100) if start > 0 else 0
return {
“cash”: cash, “total_value”: total, “starting_balance”: start,
“total_pnl”: total_pnl, “total_pnl_pct”: total_pct,
“positions”: positions,
}

def check_drawdown(portfolio: dict) -> tuple:
start = portfolio.get(“starting_balance”, 0)
cash  = portfolio.get(“cash”, start)
if start <= 0:
return False, 0.0
holdings_cost = sum(
h.get(“qty”, 0) * h.get(“avg_price”, 0)
for h in portfolio.get(“holdings”, {}).values()
)
total = cash + holdings_cost
pct   = (total - start) / start * 100
return pct <= -MAX_DRAWDOWN_PCT, pct

# ── Auto trader scanners ───────────────────────────────────────────────────────

async def scan_crypto_momentum(user_id: str) -> list:
portfolio = get_portfolio(user_id)
if not portfolio:
return []
trades = []
for coin_id in MOMENTUM_COINS:
try:
data  = await fetch_coin(coin_id)
if not data:
continue
md    = data.get(“market_data”, {})
price = md.get(“current_price”, {}).get(“usd”, 0) or 0
ch1h  = md.get(“price_change_percentage_1h_in_currency”, {}).get(“usd”, 0) or 0
sym   = COIN_SYMBOL.get(coin_id, coin_id.upper())
if price <= 0:
continue
holdings = portfolio.get(“holdings”, {})
if coin_id in holdings:
es = position_exit(holdings[coin_id], price)
if es != “HOLD” or ch1h <= CRYPTO_SELL_TH:
t = do_sell(portfolio, coin_id, price,
es if es != “HOLD” else “MOMENTUM_SELL”)
if t:
trades.append(t)
save_portfolio(user_id, portfolio)
elif ch1h >= CRYPTO_BUY_TH:
t = do_buy(portfolio, coin_id, price, sym, CRYPTO_POS_PCT)
if t:
trades.append(t)
save_portfolio(user_id, portfolio)
await asyncio.sleep(1.2)
except Exception as e:
logger.warning(f”crypto scan ({coin_id}): {e}”)
return trades

async def scan_pumps(user_id: str) -> list:
portfolio = get_portfolio(user_id)
if not portfolio:
return []
pump_history = load_json(PUMP_HISTORY_FILE)
user_pumps   = pump_history.get(user_id, {})
trades = []
pages  = await asyncio.gather(fetch_market_scan(1), fetch_market_scan(2),
return_exceptions=True)
coins  = []
for p in pages:
if isinstance(p, list):
coins.extend(p)

```
for coin in coins:
    cid   = coin.get("id", "")
    price = coin.get("current_price") or 0
    sym   = coin.get("symbol", cid).upper()
    if price <= 0 or not cid:
        continue

    ch1h      = coin.get("price_change_percentage_1h_in_currency") or 0
    ch24h     = coin.get("price_change_percentage_24h") or 0
    volume    = coin.get("total_volume") or 0
    mcap      = coin.get("market_cap") or 1
    vol_ratio = volume / mcap * 100
    is_meme   = cid in MEME_COINS

    # Detect pump
    pumped = (
        ch1h >= PUMP_PRICE_TH or
        vol_ratio >= PUMP_VOL_RATIO_TH or
        (is_meme and ch1h >= 5)
    )
    if not pumped:
        continue

    # Skip if recently traded
    last = user_pumps.get(cid, "")
    if last:
        try:
            diff = (datetime.now(timezone.utc) -
                    datetime.fromisoformat(last)).seconds / 60
            if diff < 60:
                continue
        except Exception:
            pass

    holdings = portfolio.get("holdings", {})
    if cid in holdings:
        es = position_exit(holdings[cid], price)
        if es != "HOLD" or ch1h < 0:
            t = do_sell(portfolio, cid, price,
                        es if es != "HOLD" else "PUMP_REVERSAL")
            if t:
                trades.append(t)
                save_portfolio(user_id, portfolio)
    else:
        reason = []
        if ch1h >= PUMP_PRICE_TH:
            reason.append(f"+{ch1h:.1f}% 1h")
        if vol_ratio >= PUMP_VOL_RATIO_TH:
            reason.append(f"vol spike {vol_ratio:.0f}%")
        if is_meme:
            reason.append("meme FOMO")
        t = do_buy(portfolio, cid, price, sym, PUMP_POS_PCT)
        if t:
            t["pump_reason"] = " | ".join(reason)
            trades.append(t)
            save_portfolio(user_id, portfolio)
            user_pumps[cid] = datetime.now(timezone.utc).isoformat()

pump_history[user_id] = user_pumps
save_json(PUMP_HISTORY_FILE, pump_history)
return trades
```

async def scan_markets(user_id: str) -> list:
portfolio = get_portfolio(user_id)
if not portfolio:
return []
trades = []

```
forex_pairs  = ["EURUSD", "GBPUSD", "AUDUSD", "USDCHF", "USDJPY"]
forex_tasks  = [fetch_forex(p) for p in forex_pairs]
other_tasks  = [fetch_gold(), fetch_silver()]
stock_task   = fetch_all_stocks()

forex_results  = await asyncio.gather(*forex_tasks, return_exceptions=True)
other_results  = await asyncio.gather(*other_tasks, return_exceptions=True)
stocks         = await stock_task

all_assets = (
    [r for r in forex_results if isinstance(r, dict)] +
    [r for r in other_results if isinstance(r, dict)] +
    stocks
)

for asset in all_assets:
    try:
        ticker   = asset.get("ticker", "")
        price    = asset.get("price", 0)
        ch1h     = asset.get("change_1h", 0) or 0
        atype    = asset.get("asset_type", "other")
        asset_id = ticker.replace("/", "")
        if not ticker or price <= 0:
            continue

        holdings = portfolio.get("holdings", {})
        signal   = ("BUY" if ch1h >= MARKET_BUY_TH
                    else "SELL" if ch1h <= MARKET_SELL_TH else "HOLD")

        if asset_id in holdings:
            es = position_exit(holdings[asset_id], price)
            if es != "HOLD" or signal == "SELL":
                t = do_sell(portfolio, asset_id, price,
                            es if es != "HOLD" else "MOMENTUM_SELL")
                if t:
                    t["asset_type"] = atype
                    trades.append(t)
                    save_portfolio(user_id, portfolio)
        elif signal == "BUY":
            t = do_buy(portfolio, asset_id, price, ticker, MARKET_POS_PCT)
            if t:
                t["asset_type"] = atype
                trades.append(t)
                save_portfolio(user_id, portfolio)
    except Exception as e:
        logger.warning(f"market scan ({asset.get('ticker')}): {e}")
return trades
```

async def run_full_scan(user_id: str) -> list:
portfolio = get_portfolio(user_id)
if not portfolio:
return []
hit_dd, pct = check_drawdown(portfolio)
if hit_dd:
return []
results = await asyncio.gather(
scan_crypto_momentum(user_id),
scan_pumps(user_id),
scan_markets(user_id),
return_exceptions=True,
)
all_trades = []
for r in results:
if isinstance(r, list):
all_trades.extend(r)
return all_trades

def format_trade_alert(trade: dict) -> str:
t_type  = trade.get(“type”, “?”)
symbol  = trade.get(“symbol”, trade.get(“coin”, “?”)).upper()
price   = trade.get(“price”, 0)
usd     = trade.get(“usd”, 0)
pnl     = trade.get(“pnl”, None)
reason  = trade.get(“reason”, trade.get(“pump_reason”, “SIGNAL”))
atype   = trade.get(“asset_type”, “crypto”)
icons   = {“forex”: “FX”, “commodity”: “GOLD”, “stock”: “STOCK”, “crypto”: “CRYPTO”}
icon    = “BUY “ if t_type == “BUY” else “SELL”
emoji   = “🟢” if t_type == “BUY” else “🔴”
tag     = icons.get(atype, “CRYPTO”)

```
if price > 1000:
    ps = f"${price:,.2f}"
elif price > 1:
    ps = f"${price:.4f}"
else:
    ps = f"${price:.8f}"

lines = [
    f"{emoji} *AUTO {icon}* [{tag}] *{symbol}*",
    f"Price: {ps}",
    f"Size: ${usd:,.2f}",
]
if pnl is not None:
    pe = "+" if pnl >= 0 else ""
    lines.append(f"PnL: {pe}${pnl:,.2f}")
lines.append(f"Reason: {reason}")
lines.append(datetime.now(timezone.utc).strftime("%H:%M UTC"))
return "\n".join(lines)
```

# ── Daily chart ────────────────────────────────────────────────────────────────

def generate_chart(trades: list, portfolio: dict, username: str) -> io.BytesIO:
import matplotlib
matplotlib.use(“Agg”)
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch

```
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
today_trades = [t for t in trades if t.get("time", "").startswith(today)]
sells        = [t for t in today_trades if t.get("type") == "SELL"]
buys         = [t for t in today_trades if t.get("type") == "BUY"]
realized_pnl = sum(t.get("pnl", 0) for t in sells)
total_vol    = sum(t.get("usd", 0) for t in today_trades)
wins         = [t for t in sells if t.get("pnl", 0) > 0]
losses       = [t for t in sells if t.get("pnl", 0) <= 0]
win_rate     = (len(wins) / len(sells) * 100) if sells else 0

sorted_trades = sorted(today_trades, key=lambda t: t.get("time", ""))
cum, running, times = [], 0, []
for t in sorted_trades:
    if t.get("type") == "SELL":
        running += t.get("pnl", 0)
    cum.append(running)
    try:
        dt = datetime.strptime(t.get("time", "")[:16], "%Y-%m-%dT%H:%M")
        times.append(dt.strftime("%H:%M"))
    except Exception:
        times.append("")

BG, CARD     = "#0d0f14", "#141720"
GREEN, RED   = "#00e676", "#ff1744"
TEXT, GREY   = "#e8eaf0", "#6b7280"
GRID, ACCENT = "#1e2130", "#4fc3f7"

fig = plt.figure(figsize=(12, 8), facecolor=BG)
gs  = gridspec.GridSpec(3, 4, figure=fig, hspace=0.55, wspace=0.4,
                         left=0.06, right=0.97, top=0.88, bottom=0.08)

date_str = datetime.now(timezone.utc).strftime("%A, %B %d %Y")
fig.text(0.06, 0.95, "// Daily Trading Report",
         fontsize=18, fontweight="bold", color=TEXT,
         fontfamily="monospace", va="top")
fig.text(0.06, 0.915, f"{username}  .  {date_str}  .  Paper Trading",
         fontsize=9, color=GREY, fontfamily="monospace", va="top")

pnl_color = GREEN if realized_pnl >= 0 else RED
sign      = "+" if realized_pnl >= 0 else ""
fig.text(0.97, 0.95, f"{sign}${realized_pnl:,.2f}",
         fontsize=22, fontweight="bold", color=pnl_color,
         fontfamily="monospace", va="top", ha="right")
fig.text(0.97, 0.915, "Realized P&L Today",
         fontsize=8, color=GREY, fontfamily="monospace",
         va="top", ha="right")
fig.add_artist(plt.Line2D([0.06, 0.97], [0.9, 0.9],
                           transform=fig.transFigure, color=GRID, lw=1))

for i, (label, value, color) in enumerate([
    ("Trades", str(len(today_trades)), TEXT),
    ("Win Rate", f"{win_rate:.0f}%", GREEN if win_rate >= 50 else RED),
    ("Volume", f"${total_vol:,.0f}", ACCENT),
    ("Cash", f"${portfolio.get('cash', 0):,.0f}", TEXT),
]):
    ax = fig.add_subplot(gs[0, i])
    ax.set_facecolor(CARD)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.text(0.5, 0.72, value, ha="center", va="center",
            fontsize=16, fontweight="bold", color=color,
            fontfamily="monospace", transform=ax.transAxes)
    ax.text(0.5, 0.28, label, ha="center", va="center",
            fontsize=8, color=GREY, fontfamily="monospace",
            transform=ax.transAxes)

ax_line = fig.add_subplot(gs[1, :3])
ax_line.set_facecolor(CARD)
ax_line.set_title("Cumulative P&L", color=TEXT, fontsize=9,
                   fontfamily="monospace", loc="left", pad=6)
ax_line.grid(True, color=GRID, lw=0.5, alpha=0.6)
for s in ax_line.spines.values():
    s.set_color(GRID)
ax_line.tick_params(colors=GREY, labelsize=7)
if len(cum) >= 2:
    fc = GREEN if cum[-1] >= 0 else RED
    x  = list(range(len(cum)))
    ax_line.plot(x, cum, color=fc, lw=2, zorder=3)
    ax_line.fill_between(x, cum, 0, color=fc, alpha=0.12)
    ax_line.axhline(0, color=GREY, lw=0.8, ls="--", alpha=0.5)
    ax_line.set_xticks(x)
    ax_line.set_xticklabels(times, rotation=30, ha="right",
                             fontsize=6.5, fontfamily="monospace")
    ax_line.annotate(f"  ${cum[-1]:+,.2f}", xy=(x[-1], cum[-1]),
                      color=fc, fontsize=8, fontfamily="monospace",
                      fontweight="bold")
else:
    ax_line.text(0.5, 0.5, "No trades yet today",
                  ha="center", va="center", color=GREY,
                  fontsize=10, fontfamily="monospace",
                  transform=ax_line.transAxes)

ax_pie = fig.add_subplot(gs[1, 3])
ax_pie.set_facecolor(CARD)
ax_pie.set_title("Wins vs Losses", color=TEXT, fontsize=9,
                  fontfamily="monospace", loc="left", pad=6)
ax_pie.axis("off")
wc, lc = len(wins), len(losses)
if wc + lc > 0:
    sizes  = [wc, lc] if lc > 0 else [wc]
    colors = [GREEN, RED] if lc > 0 else [GREEN]
    ax_pie.pie(sizes, colors=colors, startangle=90,
                wedgeprops=dict(width=0.45, edgecolor=CARD, lw=2))
    ax_pie.text(0, 0, f"{win_rate:.0f}%", ha="center", va="center",
                 fontsize=14, fontweight="bold",
                 color=GREEN if win_rate >= 50 else RED,
                 fontfamily="monospace")
else:
    ax_pie.text(0.5, 0.5, "No closed\ntrades", ha="center", va="center",
                 color=GREY, fontsize=9, fontfamily="monospace",
                 transform=ax_pie.transAxes)

ax_bar = fig.add_subplot(gs[2, :3])
ax_bar.set_facecolor(CARD)
ax_bar.set_title("P&L Per Trade (Sells)", color=TEXT, fontsize=9,
                  fontfamily="monospace", loc="left", pad=6)
ax_bar.grid(True, axis="y", color=GRID, lw=0.5, alpha=0.6)
for s in ax_bar.spines.values():
    s.set_color(GRID)
ax_bar.tick_params(colors=GREY, labelsize=7)
if sells:
    labels = [
        f"{t.get('symbol', t.get('coin','?')).upper()}\n"
        f"{t.get('time','')[:16].split('T')[-1][:5]}"
        for t in sells
    ]
    vals   = [t.get("pnl", 0) for t in sells]
    cols   = [GREEN if v >= 0 else RED for v in vals]
    bars   = ax_bar.bar(range(len(vals)), vals, color=cols, width=0.6,
                         zorder=3, edgecolor=CARD, lw=0.5)
    ax_bar.axhline(0, color=GREY, lw=0.8, ls="--", alpha=0.5)
    ax_bar.set_xticks(range(len(labels)))
    ax_bar.set_xticklabels(labels, fontsize=6.5, fontfamily="monospace")
    for bar, val in zip(bars, vals):
        off = max(abs(val) * 0.05, 0.5)
        ax_bar.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + (off if val >= 0 else -off * 3),
            f"${val:+,.1f}", ha="center",
            va="bottom" if val >= 0 else "top",
            fontsize=6.5, color=GREEN if val >= 0 else RED,
            fontfamily="monospace", fontweight="bold",
        )
else:
    ax_bar.text(0.5, 0.5, "No closed trades today",
                 ha="center", va="center", color=GREY, fontsize=10,
                 fontfamily="monospace", transform=ax_bar.transAxes)

ax_sum = fig.add_subplot(gs[2, 3])
ax_sum.set_facecolor(CARD)
ax_sum.set_title("Summary", color=TEXT, fontsize=9,
                  fontfamily="monospace", loc="left", pad=6)
ax_sum.axis("off")
items = [
    ("Buys",  str(len(buys)),   ACCENT),
    ("Sells", str(len(sells)),  ACCENT),
    ("Wins",  str(wc),          GREEN),
    ("Losses",str(lc),          RED),
]
best  = max(sells, key=lambda t: t.get("pnl", 0), default=None)
worst = min(sells, key=lambda t: t.get("pnl", 0), default=None)
if best:
    bp = best.get("pnl", 0)
    bc = best.get("symbol", best.get("coin", "?")).upper()
    items.insert(0, ("Best",  f"{bc} +${bp:,.2f}", GREEN))
if worst and worst != best:
    wp = worst.get("pnl", 0)
    wc2 = worst.get("symbol", worst.get("coin", "?")).upper()
    items.insert(1, ("Worst", f"{wc2} ${wp:,.2f}", RED))
for i, (label, value, color) in enumerate(items[:6]):
    y = 0.88 - i * 0.16
    ax_sum.text(0.05, y,       label, color=GREY,  fontsize=7.5,
                 fontfamily="monospace", transform=ax_sum.transAxes)
    ax_sum.text(0.05, y - 0.08, value, color=color, fontsize=8,
                 fontweight="bold", fontfamily="monospace",
                 transform=ax_sum.transAxes)

fig.text(0.5, 0.01, "Paper Trading Only - Simulated Results",
         ha="center", fontsize=7, color=GREY, fontfamily="monospace")

buf = io.BytesIO()
plt.savefig(buf, format="png", dpi=150, facecolor=BG, bbox_inches="tight")
buf.seek(0)
plt.close(fig)
return buf
```

# ── /help text ─────────────────────────────────────────────────────────────────

HELP_TEXT = “””
*MAWI AUTO TRADING BOT*
*Paper trading - real prices - no real money*

*AUTO TRADER*
/test 10000 - start with $10,000 fake money
/autostart - begin automatic trading
/autostop - pause auto trading
/autostatus - check if bot is running

*PORTFOLIO*
/portfolio - see holdings and P&L
/trades - trade history
/daychart - daily chart image

*ANALYSIS*
/scan btc - price and AI analysis
/signal eth - bullish or bearish
/risk sol - risk assessment
/fomo pepe - hype detector
/markets - forex gold stocks live

*WATCHLIST*
/watch btc - add to watchlist
/watchlist - see all prices

*OTHER*
/resetportfolio - start over
/help - this menu

*Bot trades every 5 min automatically*
*Stop loss -8%  Take profit +12%*
*Max 8 positions  30% drawdown kills trading*

*Covers: crypto meme coins forex gold stocks*
“””

# ── Command handlers ───────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if update.message:
await send(update, HELP_TEXT)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if update.message:
await send(update, HELP_TEXT)

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id = str(update.effective_user.id)
if not context.args:
await send(update, “Usage: /test 10000”)
return
try:
amount = float(context.args[0].replace(”,”, “”))
if amount < 10 or amount > 10_000_000:
raise ValueError
except ValueError:
await send(update, “Enter a valid amount. Example: /test 10000”)
return
existing = get_portfolio(user_id)
if existing:
await send(update,
f”You already have a portfolio (${existing.get(‘starting_balance’, 0):,.2f}).\n”
“Use /resetportfolio to start over.”
)
return
portfolio = {
“starting_balance”: amount, “cash”: amount,
“holdings”: {}, “trades”: [],
“created_at”: datetime.now(timezone.utc).isoformat(),
“auto_trading”: False, “drawdown_alerted”: False,
}
save_portfolio(user_id, portfolio)
await send(update,
f”Portfolio started with ${amount:,.2f}\n\n”
“Now run /autostart to begin automatic trading.\n”
“The bot will trade crypto, meme coins, forex, gold, and stocks automatically.”
)

async def resetportfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id = str(update.effective_user.id)
data    = load_json(PORTFOLIOS_FILE)
if user_id in data:
del data[user_id]
save_json(PORTFOLIOS_FILE, data)
await send(update, “Portfolio reset. Use /test 10000 to start fresh.”)

async def autostart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id   = str(update.effective_user.id)
portfolio = get_portfolio(user_id)
if not portfolio:
await send(update, “No portfolio yet. Run /test 10000 first.”)
return
portfolio[“auto_trading”] = True
save_portfolio(user_id, portfolio)
await send(update,
“AUTO TRADER STARTED\n\n”
“Scanning every 5 minutes:\n”
“- Crypto: BTC ETH SOL BNB DOGE\n”
“- Meme pumps: top 200 coins\n”
“- Forex: EUR/USD GBP/USD AUD/USD USD/JPY USD/CHF\n”
“- Gold and Silver\n”
“- Stocks: AAPL TSLA NVDA MSFT AMZN META GOOGL SPY\n\n”
“You will get an alert for every trade.\n”
“Use /autostop to pause.”
)

async def autostop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id   = str(update.effective_user.id)
portfolio = get_portfolio(user_id)
if portfolio:
portfolio[“auto_trading”] = False
save_portfolio(user_id, portfolio)
await send(update, “Auto trader paused. Use /autostart to resume.”)

async def autostatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id   = str(update.effective_user.id)
portfolio = get_portfolio(user_id)
if not portfolio:
await send(update, “No portfolio. Run /test 10000 first.”)
return
active     = portfolio.get(“auto_trading”, False)
positions  = len(portfolio.get(“holdings”, {}))
today      = datetime.now(timezone.utc).strftime(”%Y-%m-%d”)
trades_tod = sum(1 for t in portfolio.get(“trades”, [])
if t.get(“time”, “”).startswith(today))
status = “RUNNING” if active else “PAUSED”
await send(update,
f”Auto Trader: {status}\n”
f”Positions: {positions}/8\n”
f”Trades today: {trades_tod}\n”
f”Scan: every 5 minutes\n”
f”Markets: Crypto Meme Forex Gold Stocks”
)

async def markets_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
await context.bot.send_chat_action(chat_id=update.effective_chat.id,
action=ChatAction.TYPING)
forex_pairs  = [“EURUSD”, “GBPUSD”, “AUDUSD”, “USDCHF”, “USDJPY”]
forex_res    = await asyncio.gather(*[fetch_forex(p) for p in forex_pairs],
return_exceptions=True)
gold, silver = await asyncio.gather(fetch_gold(), fetch_silver())
stocks       = await fetch_all_stocks()

```
lines = ["*LIVE MARKETS*\n"]
lines.append("*Forex*")
for r in forex_res:
    if isinstance(r, dict):
        lines.append(f"  {asset_line(r)}")

lines.append("\n*Commodities*")
for r in [gold, silver]:
    if r:
        lines.append(f"  {asset_line(r)}")

lines.append("\n*Stocks*")
for s in stocks[:6]:
    lines.append(f"  {asset_line(s)}")

await send(update, "\n".join(lines))
```

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message or not context.args:
await send(update, “Usage: /scan btc”)
return
coin_id = resolve_coin(context.args[0])
await context.bot.send_chat_action(chat_id=update.effective_chat.id,
action=ChatAction.TYPING)
data = await fetch_coin(coin_id)
if not data:
await send(update, f”Could not find {context.args[0]}”)
return
summary  = coin_summary(data)
desc     = (data.get(“description”, {}).get(“en”, “”) or “”)[:300]
analysis = await ask_claude(
f”{summary}\n{desc}\n\n”
“3-sentence market snapshot. Trend, volume, ATH distance.”
)
await send(update, f”{summary}\n\nAI Analysis:\n{analysis}”)

async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message or not context.args:
await send(update, “Usage: /signal eth”)
return
coin_id = resolve_coin(context.args[0])
await context.bot.send_chat_action(chat_id=update.effective_chat.id,
action=ChatAction.TYPING)
data = await fetch_coin(coin_id)
if not data:
await send(update, f”Could not find {context.args[0]}”)
return
md   = data.get(“market_data”, {})
s    = coin_summary(data)
ext  = (f”14d: {md.get(‘price_change_percentage_14d’,‘N/A’)}%  “
f”30d: {md.get(‘price_change_percentage_30d’,‘N/A’)}%”)
ans  = await ask_claude(f”{s}\n{ext}\n\nSignal in 4 sentences. Bullish/Bearish/Neutral verdict.”)
await send(update, f”Signal - {data.get(‘name’,’’).upper()}\n\n{ans}”)

async def risk_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message or not context.args:
await send(update, “Usage: /risk sol”)
return
coin_id = resolve_coin(context.args[0])
await context.bot.send_chat_action(chat_id=update.effective_chat.id,
action=ChatAction.TYPING)
data = await fetch_coin(coin_id)
if not data:
await send(update, f”Could not find {context.args[0]}”)
return
s   = coin_summary(data)
ans = await ask_claude(f”{s}\n\nRisk tier Low/Medium/High/VeryHigh. Key factors. Position size suggestion.”)
await send(update, f”Risk - {data.get(‘name’,’’).upper()}\n\n{ans}”)

async def fomo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message or not context.args:
await send(update, “Usage: /fomo pepe”)
return
coin_id = resolve_coin(context.args[0])
await context.bot.send_chat_action(chat_id=update.effective_chat.id,
action=ChatAction.TYPING)
data = await fetch_coin(coin_id)
if not data:
await send(update, f”Could not find {context.args[0]}”)
return
md    = data.get(“market_data”, {})
s     = coin_summary(data)
ch24  = md.get(“price_change_percentage_24h”, 0) or 0
vol   = md.get(“total_volume”, {}).get(“usd”, 0) or 0
mcap  = md.get(“market_cap”, {}).get(“usd”, 1) or 1
ratio = vol / mcap * 100
ans   = await ask_claude(
f”{s}\nVol/MCap: {ratio:.1f}%  24h: {ch24:.2f}%\n\n”
“FOMO score Low/Medium/High/Extreme. Real momentum or hype? What to do?”
)
await send(update, f”FOMO - {data.get(‘name’,’’).upper()}\n\n{ans}”)

async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id   = str(update.effective_user.id)
portfolio = get_portfolio(user_id)
if not portfolio:
await send(update, “No portfolio. Start with /test 10000”)
return
await context.bot.send_chat_action(chat_id=update.effective_chat.id,
action=ChatAction.TYPING)
holdings = portfolio.get(“holdings”, {})
prices   = {}
for aid, pos in holdings.items():
prices[aid] = pos.get(“avg_price”, 0)
try:
cd = await fetch_coin(aid)
if cd:
p = cd.get(“market_data”, {}).get(“current_price”, {}).get(“usd”)
if p:
prices[aid] = p
except Exception:
pass

```
pnl    = portfolio_pnl(portfolio, prices)
emoji  = "+" if pnl["total_pnl"] >= 0 else ""
auto   = "ON" if portfolio.get("auto_trading") else "OFF"
lines  = [
    f"*PORTFOLIO* | Auto: {auto}\n",
    f"Started: ${pnl['starting_balance']:,.2f}",
    f"Cash: ${pnl['cash']:,.2f}",
    f"Total: ${pnl['total_value']:,.2f}",
    f"P&L: {emoji}${pnl['total_pnl']:,.2f} ({emoji}{pnl['total_pnl_pct']:.2f}%)\n",
]
if pnl["positions"]:
    lines.append("*Open Positions:*")
    for pos in pnl["positions"]:
        sign = "+" if pos["pnl"] >= 0 else ""
        lines.append(
            f"- *{pos['symbol']}*: ${pos['value']:,.2f}  "
            f"P&L: {sign}${pos['pnl']:,.2f} ({sign}{pos['pnl_pct']:.2f}%)"
        )
else:
    lines.append("No open positions.")
if pnl["total_pnl_pct"] <= -30:
    lines.append("\nWARNING: 30% drawdown hit. Auto trader stopped.")
await send(update, "\n".join(lines))
```

async def trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id   = str(update.effective_user.id)
portfolio = get_portfolio(user_id)
if not portfolio:
await send(update, “No portfolio. Start with /test 10000”)
return
trades = portfolio.get(“trades”, [])
if not trades:
await send(update, “No trades yet.”)
return
lines = [f”*TRADES* ({len(trades)} total, last 15)\n”]
for t in reversed(trades[-15:]):
typ  = t.get(“type”, “?”)
sym  = t.get(“symbol”, t.get(“coin”, “?”)).upper()
usd  = t.get(“usd”, 0)
px   = t.get(“price”, 0)
ts   = t.get(“time”, “”)[:16].replace(“T”, “ “)
pnls = “”
if typ == “SELL”:
p    = t.get(“pnl”, 0)
sign = “+” if p >= 0 else “”
pnls = f” PnL:{sign}${p:,.2f}”
rsn  = t.get(“reason”, t.get(“pump_reason”, “”))
icon = “BUY” if typ == “BUY” else “SELL”
lines.append(f”{icon} *{sym}* ${usd:,.2f} @ ${px:,.4f}{pnls}\n_{ts}_ {rsn}”)
await send(update, “\n”.join(lines))

async def daychart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id   = str(update.effective_user.id)
portfolio = get_portfolio(user_id)
if not portfolio:
await send(update, “No portfolio. Start with /test 10000”)
return
await context.bot.send_chat_action(chat_id=update.effective_chat.id,
action=ChatAction.TYPING)
try:
username = update.effective_user.first_name or “Trader”
buf = generate_chart(portfolio.get(“trades”, []), portfolio, username)
await update.message.reply_photo(
photo=buf,
caption=“Daily Trading Summary - Paper Trading”,
)
except Exception as e:
logger.exception(“Chart error”)
await send(update, f”Chart failed: {e}”)

async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message or not context.args:
await send(update, “Usage: /watch btc”)
return
coin_id = resolve_coin(context.args[0])
user_id = str(update.effective_user.id)
await context.bot.send_chat_action(chat_id=update.effective_chat.id,
action=ChatAction.TYPING)
data = await fetch_coin(coin_id)
if not data:
await send(update, f”Could not find {context.args[0]}”)
return
wl = load_json(WATCHLIST_FILE)
wl.setdefault(user_id, {})
if coin_id in wl[user_id]:
await send(update, f”Already watching {data.get(‘name’)}”)
return
wl[user_id][coin_id] = data.get(“name”, coin_id)
save_json(WATCHLIST_FILE, wl)
await send(update, f”Added {data.get(‘name’)} to watchlist.”)

async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message:
return
user_id = str(update.effective_user.id)
wl      = load_json(WATCHLIST_FILE)
coins   = wl.get(user_id, {})
if not coins:
await send(update, “Watchlist empty. Use /watch btc”)
return
await context.bot.send_chat_action(chat_id=update.effective_chat.id,
action=ChatAction.TYPING)
lines = [”*WATCHLIST*\n”]
for coin_id, name in coins.items():
data = await fetch_coin(coin_id)
if data:
md    = data.get(“market_data”, {})
price = md.get(“current_price”, {}).get(“usd”, 0) or 0
ch    = md.get(“price_change_percentage_24h”, 0) or 0
arrow = “+” if ch >= 0 else “”
lines.append(f”- *{name}*: ${price:,.4f}  {arrow}{ch:.2f}%”)
else:
lines.append(f”- {name}: unavailable”)
await send(update, “\n”.join(lines))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
if not update.message or not update.message.text:
return
text = update.message.text.strip()
if not text:
return
try:
await context.bot.send_chat_action(chat_id=update.effective_chat.id,
action=ChatAction.TYPING)
reply = await ask_claude(text)
await send(update, reply)
except Exception:
logger.exception(“handle_text error”)
await send(update, “Something went wrong.”)

# ── Background loops ───────────────────────────────────────────────────────────

async def auto_scan_loop(app) -> None:
await asyncio.sleep(60)
while True:
try:
await asyncio.sleep(SCAN_INTERVAL)
all_portfolios = load_json(PORTFOLIOS_FILE)
for user_id, portfolio in all_portfolios.items():
if not portfolio.get(“auto_trading”, False):
continue
hit_dd, pct = check_drawdown(portfolio)
if hit_dd:
portfolio[“auto_trading”] = False
save_portfolio(user_id, portfolio)
try:
await app.bot.send_message(
chat_id=int(user_id),
text=(
“AUTO TRADER STOPPED\n\n”
f”Portfolio hit 30% drawdown ({pct:.1f}%).\n”
“Auto trading paused.\n”
“Use /portfolio to review.\n”
“Use /resetportfolio to start fresh.”
),
)
except Exception as e:
logger.warning(f”Drawdown alert failed {user_id}: {e}”)
continue
trades = await run_full_scan(user_id)
for trade in trades:
alert = format_trade_alert(trade)
try:
await app.bot.send_message(
chat_id=int(user_id), text=alert,
parse_mode=“Markdown”,
)
await asyncio.sleep(0.3)
except Exception as e:
logger.warning(f”Trade alert failed {user_id}: {e}”)
except Exception:
logger.exception(“auto_scan_loop error”)

async def pnl_report_loop(app) -> None:
await asyncio.sleep(120)
while True:
try:
await asyncio.sleep(SCAN_INTERVAL)
all_portfolios = load_json(PORTFOLIOS_FILE)
for user_id, portfolio in all_portfolios.items():
holdings = portfolio.get(“holdings”, {})
if not holdings:
continue
start  = portfolio.get(“starting_balance”, 0)
cash   = portfolio.get(“cash”, 0)
h_cost = sum(h.get(“qty”, 0) * h.get(“avg_price”, 0)
for h in holdings.values())
total  = cash + h_cost
pnl    = total - start
pct    = (pnl / start * 100) if start > 0 else 0
sign   = “+” if pnl >= 0 else “”
today  = datetime.now(timezone.utc).strftime(”%Y-%m-%d”)
tod    = sum(1 for t in portfolio.get(“trades”, [])
if t.get(“time”, “”).startswith(today))
auto   = “ON” if portfolio.get(“auto_trading”) else “OFF”
pos_list = “  “.join(
pos.get(“symbol”, aid.upper())
for aid, pos in holdings.items()
)
msg = (
f”5-MIN REPORT | Auto: {auto}\n”
f”Cash: ${cash:,.2f}\n”
f”Total: ${total:,.2f}\n”
f”P&L: {sign}${pnl:,.2f} ({sign}{pct:.2f}%)\n”
f”Trades today: {tod}\n”
f”Positions ({len(holdings)}): {pos_list}”
)
try:
await app.bot.send_message(chat_id=int(user_id), text=msg)
except Exception as e:
logger.warning(f”PnL report failed {user_id}: {e}”)
except Exception:
logger.exception(“pnl_report_loop error”)

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
logger.info(“Starting Mawi Auto Trading Bot…”)
app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

```
handlers = [
    ("start",           start_command),
    ("help",            help_command),
    ("test",            test_command),
    ("resetportfolio",  resetportfolio_command),
    ("autostart",       autostart_command),
    ("autostop",        autostop_command),
    ("autostatus",      autostatus_command),
    ("markets",         markets_command),
    ("scan",            scan_command),
    ("signal",          signal_command),
    ("risk",            risk_command),
    ("fomo",            fomo_command),
    ("portfolio",       portfolio_command),
    ("trades",          trades_command),
    ("daychart",        daychart_command),
    ("watch",           watch_command),
    ("watchlist",       watchlist_command),
]
for cmd, handler in handlers:
    app.add_handler(CommandHandler(cmd, handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

async def post_init(application):
    asyncio.create_task(auto_scan_loop(application))
    asyncio.create_task(pnl_report_loop(application))

app.post_init = post_init
app.run_polling(drop_pending_updates=True)
```

if **name** == “**main**”:
main()
