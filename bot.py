import os
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
from garmin import login_to_garmin, upload_workout_to_garmin_async
from user import get_user, save_user, delete_user
from session import temp_sessions
from rate_limiter import check_rate_limit, record_request, get_user_stats, RateLimitExceeded, create_indexes
from workout_log import log_workout_request, get_workout_stats, create_indexes as create_workout_indexes

# Load environment variables
load_dotenv()
API_ID = int(os.getenv("TELEGRAM_API_ID", 0))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# States
AWAIT_USERNAME = "await_username"
AWAIT_PASSWORD = "await_password"
AUTHORIZED = "authorized"

# Initialize Pyrogram Client
app = Client("garmin_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# /start command: begin login flow
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    # Initialize persistent state
    await save_user(user_id, {"state": AWAIT_USERNAME})
    # Prepare temp session storage
    temp_sessions[user_id] = {}
    await message.reply("Welcome! To get started, please enter your Garmin Connect username.")

# /logout command: remove authorization
@app.on_message(filters.command("logout") & filters.private)
async def logout_handler(client: Client, message: Message):
    user_id = message.from_user.id
    user_data = await get_user(user_id)
    if user_data and user_data.get("state") == AUTHORIZED:
        await message.reply("You have been logged out of Garmin Connect.")
    else:
        await message.reply("You are not logged in. Use /start to log in.")

# /stats command: show current rate limit usage
@app.on_message(filters.command("stats") & filters.private)
async def stats_handler(client: Client, message: Message):
    user_id = message.from_user.id
    stats = await get_user_stats(user_id)
    
    response = (
        "📊 **Your API Usage:**\n\n"
        f"⏱ **Hourly:** {stats['hourly']['used']}/{stats['hourly']['limit']}\n"
        f"📅 **Daily:** {stats['daily']['used']}/{stats['daily']['limit']}\n"
        f"📆 **Monthly:** {stats['monthly']['used']}/{stats['monthly']['limit']}\n"
    )
    await message.reply(response)

# Text handler for login and workout messages
@app.on_message(filters.text & filters.private)
async def text_handler(client: Client, message: Message):
    user_id = message.from_user.id
    print(f"Received message from {user_id}: {message.text}")
    user_data = await get_user(user_id)

    # Ensure a login session has been started
    if not user_data:
        return await message.reply("Please use /start to log in first.")

    if message.text.lower() == "ping":
        return await message.reply("pong")

    state = user_data.get("state")

    # Handle username entry
    if state == AWAIT_USERNAME:
        # Store username in temp session
        temp_sessions[user_id]["username"] = message.text.strip()
        # Update persistent state to await password
        user_data["state"] = AWAIT_PASSWORD
        await save_user(user_id, user_data)
        await message.reply("Great! Now please enter your Garmin Connect password.")
        return

    # Handle password entry and attempt login
    if state == AWAIT_PASSWORD:
        session = temp_sessions.get(user_id)
        if not session or "username" not in session:
            await delete_user(user_id)
            return await message.reply("Session expired or invalid. Please use /start to log in again.")

        password = message.text.strip()
        username = session["username"]
        await message.reply("Logging in to Garmin Connect...")
        try:
            token = await login_to_garmin(username, password)
            # Clean up raw credentials
            temp_sessions.pop(user_id, None)
            # Store only token and authorized state
            user_data.update({"garmin_auth": token, "state": AUTHORIZED})
            await save_user(user_id, user_data)
            return await message.reply(
                "Successfully logged in! Send me any workout plan (text) to import into your Garmin Connect account."
            )
        except Exception as e:
            temp_sessions.pop(user_id, None)
            await delete_user(user_id)
            if "429" in str(e):
                return await message.reply(
                    "Garmin is temporarily rate limiting logins. Please wait a few minutes and try /start again."
                )
            return await message.reply(f"Login failed: {e}. Use /start to try again.")

    # Handle workout import for authorized users
    if state == AUTHORIZED:
        # Check rate limits before processing
        try:
            await check_rate_limit(user_id)
        except RateLimitExceeded as e:
            return await message.reply(f"⚠️ Rate limit exceeded:\n{e}")
        
        workout_data = message.text  # For file uploads, use filters.document and download
        await message.reply("Uploading your workout to Garmin Connect...")
        
        try:
            # Upload and get metadata
            workout_id, workout_json, processing_time = await upload_workout_to_garmin_async(
                user_data["garmin_auth"], 
                workout_data,
                user_id
            )
            
            # Record successful request
            await record_request(user_id)
            
            # Log to MongoDB
            await log_workout_request(
                user_id=user_id,
                prompt=workout_data,
                workout_json=workout_json,
                garmin_workout_id=workout_id,
                processing_time_ms=processing_time
            )
            
            return await message.reply(
                f"Workout successfully imported! 🎉\n"
                f"https://connect.garmin.com/modern/workout/{workout_id}\n\n"
                f"⚡ Processed in {processing_time:.0f}ms"
            )
        except Exception as e:
            # Log error
            await log_workout_request(
                user_id=user_id,
                prompt=workout_data,
                error=str(e)
            )
            return await message.reply(f"Failed to import workout: {e}. Please try again.")

async def startup():
    """Initialize indexes and other startup tasks"""
    await create_indexes()
    await create_workout_indexes()
    print("✓ Rate limiting initialized")
    print("✓ Workout log indexes created")

if __name__ == "__main__":
    import asyncio
    from pyrogram import idle

    async def main():
        await startup()
        await app.start()
        await idle()
        await app.stop()

    asyncio.run(main())
