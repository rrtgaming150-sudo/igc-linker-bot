import os
import json
import aiohttp
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ContextTypes
)

# --- Telegram Bot Setup ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable not set.")

BASE_URL = "https://www.indiageniuschallenge.com/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.indiageniuschallenge.com/login?redirect=%2Ffriends",
    "Origin": "https://www.indiageniuschallenge.com",
}

# --- Helper functions (same as before) ---
def load_cookies_from_json(cookie_json_text):
    try:
        cookie_list = json.loads(cookie_json_text)
        cookies = {}
        for c in cookie_list:
            if c['name'] in ("__Secure-better-auth.session_token", "__Secure-better-auth.session_data"):
                cookies[c['name']] = c['value']
        return cookies if cookies else (None, "Required session cookies not found.")
    except json.JSONDecodeError:
        return None, "Invalid JSON format."

async def fire_links(anon_ids, cookies):
    results = []
    async def link_request(session, anon_id, idx, start_event):
        await start_event.wait()
        send_ns = datetime.now().timestamp() * 1_000_000
        req_cookies = cookies.copy()
        req_cookies['anon_attempt_id'] = anon_id
        try:
            async with session.get(f"{BASE_URL}/attempt/linkAnon", headers=HEADERS, cookies=req_cookies) as resp:
                recv_ns = datetime.now().timestamp() * 1_000_000
                text = await resp.text()
                results.append({
                    "id": idx,
                    "anon_id": anon_id,
                    "status": resp.status,
                    "response": text,
                    "send_ms": send_ns / 1_000,
                    "recv_ms": recv_ns / 1_000,
                })
        except Exception as e:
            results.append({
                "id": idx,
                "anon_id": anon_id,
                "status": "Error",
                "response": str(e),
                "send_ms": send_ns / 1_000,
                "recv_ms": datetime.now().timestamp() * 1_000,
            })

    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=connector) as session:
        start_event = asyncio.Event()
        tasks = [link_request(session, anon_id, i+1, start_event) for i, anon_id in enumerate(anon_ids)]
        await asyncio.sleep(0)
        start_event.set()
        await asyncio.gather(*tasks)
    return results

# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔐 1. Set Cookies", callback_data="set_cookies")],
        [InlineKeyboardButton("➕ 2. Add Anon IDs", callback_data="add_ids")],
        [InlineKeyboardButton("🚀 3. Fire Requests", callback_data="fire")],
        [InlineKeyboardButton("🗑️ Clear Data", callback_data="clear")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome to IGC Linker Bot!\n\nFollow the steps in order.",
        reply_markup=reply_markup,
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "set_cookies":
        context.user_data['next_step'] = 'set_cookies'
        await query.edit_message_text("Please paste the cookie JSON exported from your browser.")
    elif action == "add_ids":
        if 'cookies' not in context.user_data:
            await query.edit_message_text("Set cookies first.")
            return
        context.user_data['next_step'] = 'add_ids'
        await query.edit_message_text("Send the 3 Anon IDs (one per line or comma-separated).")
    elif action == "fire":
        cookies = context.user_data.get('cookies')
        anon_ids = context.user_data.get('anon_ids', [])
        if not cookies:
            await query.edit_message_text("Missing cookies.")
            return
        if len(anon_ids) != 3:
            await query.edit_message_text(f"Need 3 Anon IDs. You have {len(anon_ids)}.")
            return
        await query.edit_message_text("🚀 Firing requests...")
        results = await fire_links(anon_ids, cookies)
        msg = "**Results:**\n"
        for res in results:
            msg += f"Req{res['id']}: status {res['status']} – {res['response'][:80]}\n"
        await query.edit_message_text(msg, parse_mode="Markdown")
    elif action == "clear":
        context.user_data.clear()
        await query.edit_message_text("Data cleared.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get('next_step')
    if step == 'set_cookies':
        cookies, err = load_cookies_from_json(update.message.text)
        if err:
            await update.message.reply_text(f"Error: {err}")
            return
        context.user_data['cookies'] = cookies
        context.user_data.pop('next_step', None)
        await update.message.reply_text("✅ Cookies saved. Now add Anon IDs.")
    elif step == 'add_ids':
        text = update.message.text.strip()
        ids = [x.strip() for x in text.replace(',', ' ').split() if x.strip()]
        context.user_data['anon_ids'] = ids[:3]
        context.user_data.pop('next_step', None)
        await update.message.reply_text(f"✅ Saved {len(context.user_data['anon_ids'])} IDs. Press 'Fire Requests'.")
    else:
        await update.message.reply_text("Please use the buttons.")

# --- Flask App for Webhook & Health Check ---
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "Bot is alive!", 200

@flask_app.route('/webhook', methods=['POST'])
async def webhook():
    # Get the update from Telegram
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400

    # Create an Update object and process it
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return jsonify({"status": "ok"}), 200

# Global variable to hold the bot application instance
bot_app = None

def setup_bot():
    global bot_app
    bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(button_callback))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return bot_app

if __name__ == "__main__":
    import asyncio
    # Set up the bot
    bot_app = setup_bot()
    # Start the bot webhook (no polling)
    # We need to run Flask in a separate thread because the asyncio loop is already running?
    # Actually, Flask will run in its own thread; we can set the webhook URL via Telegram API.
    # For simplicity, we'll run the webhook server with a background thread.
    # But Telegram needs to know the public URL. Render provides the URL automatically.
    # We'll set the webhook on startup using the environment variable RENDER_EXTERNAL_URL.
    public_url = os.environ.get('RENDER_EXTERNAL_URL', 'https://your-bot.onrender.com')
    webhook_url = f"{public_url}/webhook"
    # Set the webhook (run once)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot_app.bot.set_webhook(webhook_url))
    print(f"Webhook set to {webhook_url}")

    # Start Flask server (blocking)
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)
