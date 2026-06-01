import os
import asyncio
import threading
import aiohttp
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from flask import Flask

# --- Telegram Bot Setup ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
BASE_URL = "https://www.indiageniuschallenge.com/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.indiageniuschallenge.com/login?redirect=%2Ffriends",
    "Origin": "https://www.indiageniuschallenge.com",
}

# Helper functions (keep them exactly as they were)
def load_cookies_from_json(cookie_json_text):
    try:
        cookie_list = json.loads(cookie_json_text)
        cookies = {}
        for c in cookie_list:
            if c['name'] in ("__Secure-better-auth.session_token", "__Secure-better-auth.session_data"):
                cookies[c['name']] = c['value']
        if not cookies:
            return None, "Required session cookies not found in the provided JSON."
        return cookies, None
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

# Telegram bot command handlers (keep them exactly as they were)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔐 1. Set Cookies", callback_data="set_cookies")],
        [InlineKeyboardButton("➕ 2. Add Anon IDs", callback_data="add_ids")],
        [InlineKeyboardButton("🚀 3. Fire Requests", callback_data="fire")],
        [InlineKeyboardButton("🗑️ Clear Data", callback_data="clear")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome to the India Genius Challenge Linker Bot!\n\n"
        "Please follow the steps in order:\n"
        "1. Click **Set Cookies** and send the cookie JSON you exported from your browser.\n"
        "2. Click **Add Anon IDs** and provide exactly 3 Anonymous Attempt IDs.\n"
        "3. Click **Fire Requests** to link them all at the exact same time.",
        reply_markup=reply_markup,
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    action = query.data

    if action == "set_cookies":
        context.user_data['next_step'] = 'set_cookies'
        await query.edit_message_text(
            "Please export your cookies for indiageniuschallenge.com and send me the entire JSON data.\n\n"
            "**How to export cookies:**\n"
            "1. Install an extension like 'EditThisCookie' or 'Cookie-Editor' in your browser.\n"
            "2. Log in to indiageniuschallenge.com.\n"
            "3. Use the extension to export all cookies for the site.\n"
            "4. Copy the entire JSON output and paste it here."
        )
    elif action == "add_ids":
        if 'cookies' not in context.user_data:
            await query.edit_message_text("Please set your cookies first by clicking 'Set Cookies'.")
            return
        context.user_data['next_step'] = 'add_ids'
        await query.edit_message_text(
            "Please send me the 3 Anonymous Attempt IDs.\n\n"
            "You can send them one by one, or all three in a single message separated by commas.\n"
            "Example: `6a1c620b807de1cfadfb495e, 6a1c6144a8a0ec0a37016db8, 6a1c616f72d3f61733e2535a`"
        )
    elif action == "fire":
        cookies = context.user_data.get('cookies')
        anon_ids = context.user_data.get('anon_ids', [])
        if not cookies:
            await query.edit_message_text("Cookies are missing. Please set them first.")
            return
        if len(anon_ids) != 3:
            await query.edit_message_text(f"You have {len(anon_ids)} Anon IDs. Please add exactly 3 using the 'Add Anon IDs' button.")
            return
        await query.edit_message_text("🚀 Firing three simultaneous requests. This will take a few seconds...")
        try:
            results = await fire_links(anon_ids, cookies)
            msg_lines = ["**Results:**"]
            for res in results:
                msg_lines.append(
                    f"*Request {res['id']}* (ID: `{res['anon_id']}`)\n"
                    f"  Status: `{res['status']}`\n"
                    f"  Response: `{res['response'][:100]}`"
                )
            await query.edit_message_text("\n".join(msg_lines), parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"An error occurred: {e}")
    elif action == "clear":
        context.user_data.clear()
        await query.edit_message_text("Your data has been cleared. Use /start to begin again.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_step = context.user_data.get('next_step')
    if user_step == 'set_cookies':
        cookies, error = load_cookies_from_json(update.message.text)
        if error:
            await update.message.reply_text(f"Error: {error}")
            return
        context.user_data['cookies'] = cookies
        context.user_data['next_step'] = None
        await update.message.reply_text("✅ Cookies saved successfully! You can now proceed to add Anon IDs.")
    elif user_step == 'add_ids':
        text = update.message.text.strip()
        if ',' in text:
            ids = [x.strip() for x in text.split(',')]
        else:
            ids = [text]
        context.user_data['anon_ids'] = ids[:3]
        context.user_data['next_step'] = None
        await update.message.reply_text(f"✅ Saved {len(context.user_data['anon_ids'])} Anon ID(s). You can now click 'Fire Requests'.")
    else:
        await update.message.reply_text("Please use the buttons in the menu.")

# ================= FLASK WEB SERVER FOR RENDER HEALTH CHECKS =================
flask_app = Flask('')

@flask_app.route('/')
@flask_app.route('/health')
def health():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

# ================= MAIN ENTRY POINT =================
def main():
    # Start Flask web server in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Start the Telegram bot
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
