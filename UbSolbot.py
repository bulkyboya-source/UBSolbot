0"""
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
import google.generativeai as genai
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
        "• /price — get price right now\n"
        "• /stopsol — stop hourly updates",
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


# ── Witty Defense Handler ──────────────────────────────────────────────────────
async def handle_witty_defense(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    text = message.text
    text_lower = text.lower()
    words = text_lower.split()

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
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=(
                "You are a witty, sharp-tongued defender in a chat group. "
                "When someone is being rude or abusive, you reply with a clever, "
                "witty, and humorous response that defends the target and embarrasses "
                "the abuser. "
                "IMPORTANT RULES:\n"
                "1. Detect the language of the message and reply in the SAME language\n"
                "2. Be witty and clever, not just rude back\n"
                "3. Make the abuser look foolish\n"
                "4. Keep it short — 1-2 sentences max\n"
                "5. Use humor and sarcasm\n"
                "6. If the message is in Hindi/Hinglish, reply in Hindi/Hinglish\n"
                "7. If the message uses slang, use similar slang back"
            )
        )
        response = model.generate_content(
            f"Someone said this in the chat: \"{text}\". Give a witty defense response."
        )
        await message.reply_text(response.text)

    except Exception as e:
        logger.error(f"Witty defense error: {e}")


# ── Combined Message Handler ───────────────────────────────────────────────────
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await handle_witty_defense(update, ctx)


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
                await context.bot.send_message(
                    chat_id=cid,
                    text="✅ *Bot has been updated and is back online!*\nHourly SOL updates will continue as scheduled.",
                    parse_mode="Markdown"
                )

            app.job_queue.run_once(send_restart_notice, when=5, chat_id=chat_id)

    app.add_handler(CommandHandler("solana", cmd_start))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("stopsol", cmd_stop))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
