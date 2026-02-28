"""
Solana Price Tracker Telegram Bot
----------------------------------
Requirements:
    pip install python-telegram-bot[job-queue] requests anthropic

Commands:
    /solana  - Start hourly SOL price updates
    /price   - Get current SOL price immediately
    /stopsol - Stop hourly updates
    /help    - Show all commands
"""

import os
import logging
import requests
import json
import io
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import timezone
from google import genai
from pathlib import Path
from datetime import datetime
from telegram import Update
from datetime import timezone
from telegram.ext import MessageHandler, filters
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ── Configuration ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
DEFAULT_CHAT_ID = os.getenv("CHAT_ID", "")
CHATS_FILE = "subscribed_chats.json"
PROTECTED_USERNAMES = ["boolishgini"]  # usernames to protect, no @ symbol
TRIGGER_KEYWORDS = ["bg"]              # trigger keywords, case insensitive
UPDATE_INTERVAL_SECONDS = 3600         # 1 hour

# ── Chat Persistence ───────────────────────────────────────────────────────────
def load_chats():
    if Path(CHATS_FILE).exists():
        with open(CHATS_FILE, "r") as f:
            return json.load(f)
    return []

def save_chats(chat_ids):
    with open(CHATS_FILE, "w") as f:
        json.dump(chat_ids, f)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── Price Fetcher ──────────────────────────────────────────────────────────────
def get_sol_price() -> dict:
    """Fetch SOL price from Binance (free, no API key required)."""
    price_url = "https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT"
    price_resp = requests.get(price_url, timeout=10)
    price_resp.raise_for_status()
    price = float(price_resp.json()["price"])

    kline_url = "https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval=1h&limit=2"
    kline_resp = requests.get(kline_url, timeout=10)
    kline_resp.raise_for_status()
    klines = kline_resp.json()
    open_1h = float(klines[0][1])
    change_1h = ((price - open_1h) / open_1h) * 100

    mcap_url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd&include_market_cap=true"
    mcap_resp = requests.get(mcap_url, timeout=10)
    market_cap = mcap_resp.json().get("solana", {}).get("usd_market_cap", 0)

    return {
        "price": price,
        "change_1h": change_1h,
        "market_cap": market_cap,
    }


def generate_chart() -> io.BytesIO:
    """Generate 1H SOL/USDT candlestick chart using Binance data."""
    kline_url = "https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval=5m&limit=12"
    resp = requests.get(kline_url, timeout=10)
    resp.raise_for_status()
    klines = resp.json()

    times = [datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc) for k in klines]
    opens = [float(k[1]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]

    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    for i in range(len(times)):
        color = "#26a69a" if closes[i] >= opens[i] else "#ef5350"
        # Candle body
        ax.bar(i, abs(closes[i] - opens[i]), bottom=min(opens[i], closes[i]),
               color=color, width=0.6, zorder=3)
        # Wick
        ax.plot([i, i], [lows[i], highs[i]], color=color, linewidth=1, zorder=2)

    ax.set_xlim(-0.5, len(times) - 0.5)
    ax.set_xticks(range(len(times)))
    ax.set_xticklabels(
        [t.strftime("%H:%M") for t in times],
        rotation=45, fontsize=7, color="#aaaaaa"
    )
    ax.tick_params(axis="y", colors="#aaaaaa", labelsize=8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.2f}"))

    for spine in ax.spines.values():
        spine.set_edgecolor("#333333")

    ax.grid(axis="y", color="#1f2937", linestyle="--", linewidth=0.5, zorder=1)
    ax.set_title("SOL/USDT — 1H Chart (5m candles)", color="white", fontsize=11, pad=10)

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    return buf

def format_price_message(data: dict, label: str = "📊 Solana Price Update") -> str:
    change = data["change_1h"]
    arrow = "🟢 ▲" if change >= 0 else "🔴 ▼"
    cap_b = data["market_cap"] / 1_000_000_000
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"*{label}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 Price:       *${data['price']:,.2f}*\n"
        f"📈 1h Change:  {arrow} `{abs(change):.2f}%`\n"
        f"🏦 Market Cap:  `${cap_b:.2f}B`\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🕐 `{now}`"
    )


# ── Command Handlers ───────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    current_jobs = ctx.job_queue.get_jobs_by_name(str(chat_id))
    for job in current_jobs:
        job.schedule_removal()

    ctx.job_queue.run_repeating(
        send_price_update,
        interval=UPDATE_INTERVAL_SECONDS,
        first=5,
        chat_id=chat_id,
        name=str(chat_id),
    )

    chats = load_chats()
    if chat_id not in chats:
        chats.append(chat_id)
        save_chats(chats)

    await update.message.reply_text(
        "👋 *Solana Price Bot activated!*\n\n"
        "I'll send you SOL price updates every hour.\n\n"
        "Commands:\n"
        "• /solana — get solana price every hour from command execution\n"
        "• /price — get price right now\n"
        "• /gems — find early Solana gems\n"
        "• /stopsol — stop hourly updates",
        parse_mode="Markdown",
    )

async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    jobs = ctx.job_queue.get_jobs_by_name(str(chat_id))
    if jobs:
        for job in jobs:
            job.schedule_removal()

        chats = load_chats()
        chats = [c for c in chats if c != chat_id]
        save_chats(chats)

        await update.message.reply_text("🛑 Hourly updates stopped. Use /solana to resume.")
    else:
        await update.message.reply_text("No active updates found. Use /solana to begin.")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*📖 Available Commands*\n"
        "━━━━━━━━━━━━━━━\n"
        "/solana — start hourly SOL price updates\n"
        "/price — get current SOL price instantly\n"
        "/stopsol — stop hourly updates\n"
        "/gems — find early Solana gems\n"
        "/og — find 2 oldest coins on PF\n"
        "/help — show this message",
        parse_mode="Markdown",
    )

async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        data = get_sol_price()
        msg = format_price_message(data, label="📊 Solana — Live Price")
        chart = generate_chart()
        await update.message.reply_photo(photo=chart, caption=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Price fetch error: {e}")
        await update.message.reply_text("⚠️ Could not fetch price. Try again shortly.")


async def send_price_update(ctx: ContextTypes.DEFAULT_TYPE):
    try:
        data = get_sol_price()
        msg = format_price_message(data)
        chart = generate_chart()
        await ctx.bot.send_photo(
            chat_id=ctx.job.chat_id,
            photo=chart,
            caption=msg,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Scheduled update error: {e}")

# ── Witty Defense Handler ──────────────────────────────────────────────────────
async def handle_witty_defense(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    text = message.text
    text_lower = text.lower()
    words = text_lower.split()
    
    # Ignore if sender is a protected user
    sender_username = message.from_user.username
    if sender_username and sender_username.lower() in [u.lower() for u in PROTECTED_USERNAMES]:
        return

    keyword_triggered = any(keyword.lower() in text_lower for keyword in TRIGGER_KEYWORDS)
    

    is_reply_to_protected = (
        message.reply_to_message and
        message.reply_to_message.from_user.username and
        message.reply_to_message.from_user.username.lower() in [u.lower() for u in PROTECTED_USERNAMES]
    )

    is_mention_of_protected = any(
        user.lower() in text_lower for user in PROTECTED_USERNAMES
    )

    if not keyword_triggered and not is_reply_to_protected and not is_mention_of_protected:
        return

    try:
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        # Skip offensive check if keyword was directly triggered — respond regardless
        if not keyword_triggered:
            check = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"Is this message offensive, rude, insulting or abusive? Reply with only YES or NO: \"{text}\"",
            )
            if "yes" not in check.text.lower():
                return  # not offensive, ignore

        # If offensive, generate witty defense
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"Someone said this in the chat: \"{text}\". Give a witty defense response.",
            config=genai.types.GenerateContentConfig(
                system_instruction=(
                    "You are a witty, sharp-tongued defender in a chat group. "
                    "When someone is being rude or abusive, you reply with a clever, "
                    "witty, and humorous response that defends the target and embarrasses "
                    "the abuser. "
                    "IMPORTANT RULES:\n"
                    "1. Detect the language of the message and reply in the SAME language\n"
                    "2. Be witty and clever, not just rude back\n"
                    "3. Make the abuser look foolish and say bakchodi kam kar lawde to abuser in end\n"
                    "4. Keep it short — 1-2 sentences max\n"
                    "5. Use humor and sarcasm\n"
                    "6. If the message is in Hindi/Hinglish, reply in Hindi/Hinglish\n"
                    "7. If the message uses slang, use similar slang back"
                    "8. The person you are defending is straight male."
                )
            )
        )
        await message.reply_text(response.text)

    except Exception as e:
        logger.error(f"Witty defense error: {e}")


# ── Combined Message Handler ───────────────────────────────────────────────────
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await handle_witty_defense(update, ctx)

async def cmd_gems(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 *Scanning for early Solana gems...*", parse_mode="Markdown")
    
    try:
        gems = []
        seen = set()

        # Source 1: DexScreener new Solana pairs
        new_pairs_url = "https://api.dexscreener.com/latest/dex/search?q=solana"
        resp1 = requests.get(new_pairs_url, timeout=10)
        if resp1.status_code == 200:
            pairs = resp1.json().get("pairs", [])
            for pair in pairs:
                if pair.get("chainId") != "solana":
                    continue
                token_address = pair.get("baseToken", {}).get("address", "")
                if token_address in seen:
                    continue
                seen.add(token_address)

                market_cap = pair.get("marketCap", 0) or 0
                volume_24h = pair.get("volume", {}).get("h24", 0) or 0
                volume_1h = pair.get("volume", {}).get("h1", 0) or 0
                price_change_1h = pair.get("priceChange", {}).get("h1", 0) or 0
                price_change_24h = pair.get("priceChange", {}).get("h24", 0) or 0
                liquidity = pair.get("liquidity", {}).get("usd", 0) or 0
                created_at = pair.get("pairCreatedAt", 0) or 0
                name = pair.get("baseToken", {}).get("name", "Unknown")
                symbol = pair.get("baseToken", {}).get("symbol", "?")
                dex_url = pair.get("url", "")
                txns_1h = pair.get("txns", {}).get("h1", {})
                buys_1h = txns_1h.get("buys", 0) or 0
                sells_1h = txns_1h.get("sells", 0) or 0

                # Filter criteria
                if market_cap <= 0 or market_cap > 5_000_000:
                    continue
                if volume_24h < 30_000:
                    continue
                if liquidity < 10_000:
                    continue

                # Calculate age in hours
                age_hours = 0
                if created_at:
                    import time
                    age_hours = (time.time() * 1000 - created_at) / (1000 * 3600)

                gems.append({
                    "name": name,
                    "symbol": symbol,
                    "market_cap": market_cap,
                    "volume_24h": volume_24h,
                    "volume_1h": volume_1h,
                    "price_change_1h": price_change_1h,
                    "price_change_24h": price_change_24h,
                    "liquidity": liquidity,
                    "buys_1h": buys_1h,
                    "sells_1h": sells_1h,
                    "age_hours": age_hours,
                    "url": dex_url,
                })

        # Source 2: DexScreener boosted Solana tokens
        boost_url = "https://api.dexscreener.com/token-boosts/top/v1"
        resp2 = requests.get(boost_url, timeout=10)
        if resp2.status_code == 200:
            boosted = resp2.json()
            for token in boosted[:20]:
                if token.get("chainId") != "solana":
                    continue
                token_address = token.get("tokenAddress", "")
                if not token_address or token_address in seen:
                    continue
                seen.add(token_address)

                pair_resp = requests.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
                    timeout=10
                )
                if pair_resp.status_code != 200:
                    continue
                pairs = pair_resp.json().get("pairs", [])
                if not pairs:
                    continue

                pair = pairs[0]
                market_cap = pair.get("marketCap", 0) or 0
                volume_24h = pair.get("volume", {}).get("h24", 0) or 0
                volume_1h = pair.get("volume", {}).get("h1", 0) or 0
                price_change_1h = pair.get("priceChange", {}).get("h1", 0) or 0
                price_change_24h = pair.get("priceChange", {}).get("h24", 0) or 0
                liquidity = pair.get("liquidity", {}).get("usd", 0) or 0
                created_at = pair.get("pairCreatedAt", 0) or 0
                name = pair.get("baseToken", {}).get("name", "Unknown")
                symbol = pair.get("baseToken", {}).get("symbol", "?")
                dex_url = pair.get("url", "")
                txns_1h = pair.get("txns", {}).get("h1", {})
                buys_1h = txns_1h.get("buys", 0) or 0
                sells_1h = txns_1h.get("sells", 0) or 0

                if market_cap <= 0 or market_cap > 5_000_000:
                    continue
                if volume_24h < 30_000:
                    continue
                if liquidity < 10_000:
                    continue

                age_hours = 0
                if created_at:
                    import time
                    age_hours = (time.time() * 1000 - created_at) / (1000 * 3600)

                gems.append({
                    "name": name,
                    "symbol": symbol,
                    "market_cap": market_cap,
                    "volume_24h": volume_24h,
                    "volume_1h": volume_1h,
                    "price_change_1h": price_change_1h,
                    "price_change_24h": price_change_24h,
                    "liquidity": liquidity,
                    "buys_1h": buys_1h,
                    "sells_1h": sells_1h,
                    "age_hours": age_hours,
                    "url": dex_url,
                })

        if not gems:
            await update.message.reply_text("⚠️ No gems found right now. Try again in a few minutes.")
            return

        # Sort by 24h volume highest first
        gems.sort(key=lambda x: x["volume_24h"], reverse=True)
        gems = gems[:8]  # top 8 only

        msg = "*💎 Early Solana Gems*\n"
        msg += "━━━━━━━━━━━━━━━\n\n"

        for i, gem in enumerate(gems):
            change_1h = gem["price_change_1h"]
            change_24h = gem["price_change_24h"]
            arrow_1h = "🟢" if change_1h >= 0 else "🔴"
            arrow_24h = "🟢" if change_24h >= 0 else "🔴"

            # Format market cap
            mcap = gem["market_cap"]
            if mcap >= 1_000_000:
                mcap_str = f"${mcap/1_000_000:.2f}M"
            else:
                mcap_str = f"${mcap/1_000:.0f}K"

            # Format volume
            vol = gem["volume_24h"]
            if vol >= 1_000_000:
                vol_str = f"${vol/1_000_000:.2f}M"
            else:
                vol_str = f"${vol/1_000:.0f}K"

            # Format 1h volume
            vol1h = gem["volume_1h"]
            if vol1h >= 1_000_000:
                vol1h_str = f"${vol1h/1_000_000:.2f}M"
            else:
                vol1h_str = f"${vol1h/1_000:.0f}K"

            # Format liquidity
            liq = gem["liquidity"]
            if liq >= 1_000_000:
                liq_str = f"${liq/1_000_000:.2f}M"
            else:
                liq_str = f"${liq/1_000:.0f}K"

            # Format age
            age = gem["age_hours"]
            if age < 1:
                age_str = f"{int(age*60)}m"
            elif age < 24:
                age_str = f"{age:.1f}h"
            else:
                age_str = f"{age/24:.1f}d"

            msg += f"*{i+1}. {gem['name']} (${gem['symbol']})*\n"
            msg += f"💰 MCap: `{mcap_str}` | 🕐 Age: `{age_str}`\n"
            msg += f"📊 Vol 24h: `{vol_str}` | 1h: `{vol1h_str}`\n"
            msg += f"💧 Liq: `{liq_str}`\n"
            msg += f"{arrow_1h} 1h: `{abs(change_1h):.1f}%` | {arrow_24h} 24h: `{abs(change_24h):.1f}%`\n"
            msg += f"🛒 Buys: `{gem['buys_1h']}` | Sells: `{gem['sells_1h']}` _(1h)_\n"
            if gem["url"]:
                msg += f"🔗 [View Chart]({gem['url']})\n"
            msg += "\n"

        msg += "━━━━━━━━━━━━━━━\n"
        msg += "⚠️ _DYOR. Not financial advice._\n"
        msg += f"🕐 `{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}`"

        await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Gems fetch error: {e}")
        await update.message.reply_text("⚠️ Could not fetch gems. Try again shortly.")
        
async def cmd_og(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "Usage: `/og <contract_address>`\nExample: `/og EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`",
            parse_mode="Markdown"
        )
        return

    contract = args[0].strip()
    await update.message.reply_text("🔍 *Looking up token on Pump.fun...*", parse_mode="Markdown")

    try:
        # Fetch token data from Pump.fun API
        pumpfun_url = f"https://frontend-api.pump.fun/coins/{contract}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(pumpfun_url, headers=headers, timeout=10)

        if resp.status_code == 404:
            await update.message.reply_text("⚠️ Token not found on Pump.fun. Check the contract address.")
            return

        resp.raise_for_status()
        token = resp.json()

        name = token.get("name", "Unknown")
        symbol = token.get("symbol", "?")
        description = token.get("description", "No description available.")[:150]
        market_cap = token.get("usd_market_cap", 0) or 0
        created_timestamp = token.get("created_timestamp", 0) or 0
        reply_count = token.get("reply_count", 0) or 0
        website = token.get("website", "")
        twitter = token.get("twitter", "")
        telegram_link = token.get("telegram", "")
        total_supply = token.get("total_supply", 0) or 0
        nsfw = token.get("nsfw", False)

        # Format launch date
        if created_timestamp:
            launch_date = datetime.fromtimestamp(
                created_timestamp / 1000 if created_timestamp > 1e10 else created_timestamp,
                tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")

            # Calculate age
            now_ts = datetime.now(tz=timezone.utc).timestamp()
            ts = created_timestamp / 1000 if created_timestamp > 1e10 else created_timestamp
            age_seconds = now_ts - ts
            if age_seconds < 3600:
                age_str = f"{int(age_seconds/60)}m ago"
            elif age_seconds < 86400:
                age_str = f"{age_seconds/3600:.1f}h ago"
            else:
                age_str = f"{age_seconds/86400:.1f}d ago"
        else:
            launch_date = "Unknown"
            age_str = "Unknown"

        # Format market cap
        if market_cap >= 1_000_000:
            mcap_str = f"${market_cap/1_000_000:.2f}M"
        elif market_cap >= 1_000:
            mcap_str = f"${market_cap/1_000:.1f}K"
        else:
            mcap_str = f"${market_cap:.2f}"

        # Format total supply
        if total_supply >= 1_000_000_000:
            supply_str = f"{total_supply/1_000_000_000:.2f}B"
        elif total_supply >= 1_000_000:
            supply_str = f"{total_supply/1_000_000:.2f}M"
        else:
            supply_str = str(total_supply)

        # Build links
        links = []
        pumpfun_link = f"https://pump.fun/{contract}"
        links.append(f"[Pump.fun]({pumpfun_link})")
        if website:
            links.append(f"[Website]({website})")
        if twitter:
            links.append(f"[Twitter]({twitter})")
        if telegram_link:
            links.append(f"[Telegram]({telegram_link})")

        msg = f"*🪙 {name} (${symbol})*\n"
        msg += "━━━━━━━━━━━━━━━\n"
        msg += f"📋 Contract:\n_{contract}_\n\n"
        msg += f"📅 Launch Date: `{launch_date}`\n"
        msg += f"🕐 Age: `{age_str}`\n"
        msg += f"💰 Market Cap: `{mcap_str}`\n"
        msg += f"🪙 Total Supply: `{supply_str}`\n"
        msg += f"💬 Replies: `{reply_count}`\n"
        if nsfw:
            msg += f"🔞 NSFW: `Yes`\n"
        msg += f"\n📝 _{description}_\n\n"
        msg += " | ".join(links)
        msg += "\n━━━━━━━━━━━━━━━\n"
        msg += "⚠️ _DYOR. Not financial advice._"

        # Try to get token image
        image_uri = token.get("image_uri", "")
        if image_uri:
            try:
                await update.message.reply_photo(
                    photo=image_uri,
                    caption=msg,
                    parse_mode="Markdown"
                )
            except Exception:
                await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"OG lookup error: {e}")
        await update.message.reply_text("⚠️ Could not fetch token data. Check the contract address and try again.")   
        
# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Please set your BOT_TOKEN before running.")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Restore saved chats on startup
    chats = load_chats()
    if chats:
        logger.info(f"Restoring {len(chats)} saved chats...")
        for chat_id in chats:
            app.job_queue.run_repeating(
                send_price_update,
                interval=UPDATE_INTERVAL_SECONDS,
                first=UPDATE_INTERVAL_SECONDS,
                chat_id=chat_id,
                name=str(chat_id),
            )

            async def send_restart_notice(context, cid=chat_id):
               return

            app.job_queue.run_once(send_restart_notice, when=5, chat_id=chat_id)

    app.add_handler(CommandHandler("solana", cmd_start))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("stopsol", cmd_stop))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler("gems", cmd_gems))
    app.add_handler(CommandHandler("og", cmd_og))
    
    logger.info("🚀 Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
