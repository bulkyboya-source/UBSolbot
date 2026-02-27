"""
Solana Price Tracker Telegram Bot
----------------------------------
Requirements:
    pip install python-telegram-bot requests

Setup:
    1. Create a bot via @BotFather on Telegram → get your BOT_TOKEN
    2. Get your CHAT_ID by messaging @userinfobot on Telegram
    3. Fill in BOT_TOKEN and CHAT_ID below (or use environment variables)
    4. Run: python solana_bot.py

Commands:
    /start  - Start the bot & subscribe to hourly updates
    /price  - Get current SOL price immediately
    /stop   - Stop hourly updates
"""

import os
import logging
import requests
import json
from pathlib import Path
from datetime import datetime
from telegram import Update
from telegram.ext import MessageHandler, filters
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ── Configuration ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "8740665446:AAFvR-Zh76G0hwgijO18V82KPfrJqOevG1E")
# If you want to hard-code a default chat for scheduled messages, set this:
DEFAULT_CHAT_ID = os.getenv("CHAT_ID", "")  # optional
CHATS_FILE = "subscribed_chats.json"

def load_chats():
    if Path(CHATS_FILE).exists():
        with open(CHATS_FILE, "r") as f:
            return json.load(f)
    return []

def save_chats(chat_ids):
    with open(CHATS_FILE, "w") as f:
        json.dump(chat_ids, f)

UPDATE_INTERVAL_SECONDS = 3600  # 1 hour

RESPONSES = {
    "bg": "Agya firse bkchodi krne lawde."
}

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── Price Fetcher ──────────────────────────────────────────────────────────────
def get_sol_price() -> dict:
    """Fetch SOL price from CoinGecko (free, no API key required)."""
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": "solana",
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        "include_market_cap": "true",
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()["solana"]
    return {
        "price": data["usd"],
        "change_24h": data.get("usd_24h_change", 0),
        "market_cap": data.get("usd_market_cap", 0),
    }


def format_price_message(data: dict, label: str = "📊 Solana Price Update") -> str:
    change = data["change_24h"]
    arrow = "🟢 ▲" if change >= 0 else "🔴 ▼"
    cap_b = data["market_cap"] / 1_000_000_000
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"*{label}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 Price:       *${data['price']:,.2f}*\n"
        f"📈 24h Change:  {arrow} `{abs(change):.2f}%`\n"
        f"🏦 Market Cap:  `${cap_b:.2f}B`\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🕐 `{now}`"
    )


# ── Handlers ───────────────────────────────────────────────────────────────────
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

    # Save chat to file
    chats = load_chats()
    if chat_id not in chats:
        chats.append(chat_id)
        save_chats(chats)

    await update.message.reply_text(
        "👋 *Solana Price Bot activated!*\n\n"
        "I'll send you SOL price updates every hour.\n\n"
        "Commands:\n"
        "• /price — get price right now\n"
        "• /stop_solana — stop hourly updates",
        parse_mode="Markdown",
    )


async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        data = get_sol_price()
        msg = format_price_message(data, label="📊 Solana — Live Price")
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Price fetch error: {e}")
        await update.message.reply_text("⚠️ Could not fetch price. Try again shortly.")


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    jobs = ctx.job_queue.get_jobs_by_name(str(chat_id))
    if jobs:
        for job in jobs:
            job.schedule_removal()

        # Remove chat from file
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
        "/help — show this message",
        parse_mode="Markdown",
    )
    
async def send_price_update(ctx: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: sends price to subscribed chat."""
    try:
        data = get_sol_price()
        msg = format_price_message(data)
        await ctx.bot.send_message(
            chat_id=ctx.job.chat_id,
            text=msg,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Scheduled update error: {e}")

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    username = update.effective_user.username  # e.g. "john_doe" (no @ symbol)

    # Restrict to specific usernames
    ALLOWED_USERNAMES = ["groot_crypt", "blackp619"]  # no @ symbol

    if ALLOWED_USERNAMES and username not in ALLOWED_USERNAMES:
        return  # ignore if not in list

    for keyword, response in RESPONSES.items():
        if keyword in text:
            await update.message.reply_text(response)
            return

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
                first=10,
                chat_id=chat_id,
                name=str(chat_id),
            )

    app.add_handler(CommandHandler("solana", cmd_start))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("stopsol", cmd_stop))
    app.add_handler(CommandHandler("help", cmd_help))

    logger.info("🚀 Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()