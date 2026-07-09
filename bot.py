"""Telegram layer: handlers, per-user single-flighting, and user-facing copy.

The workout flow itself (quota, LLM parse, upload, token refresh) lives in
workout_service.py and returns a typed Outcome; this module only decides what
to say to the user. Login/logout stay here because they are inherently
conversational (two-prompt handshake, scrubbing the password message).
"""

import os
import traceback

from dotenv import load_dotenv
from pyrogram import Client, filters, raw
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

import session
import token_crypto
from audit import create_indexes as create_audit_indexes
from audit import log_auth_event
from garmin import login_to_garmin, workout_url
from garmin_curl_login import GarminInvalidCredentials
from rate_limiter import (
    RateLimiterUnavailable,
    close_connections,
    get_user_stats,
)
from rate_limiter import (
    init as init_rate_limiter,
)
from user import (
    create_indexes as create_user_indexes,
)
from user import (
    delete_user,
    get_user,
    has_garmin_auth,
    save_user,
)
from webapp_server import start_webapp
from workout_log import create_indexes as create_workout_indexes
from workout_service import FailureCode, Success, process_workout

# Load environment variables
load_dotenv()
API_ID = int(os.getenv("TELEGRAM_API_ID", 0))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Public HTTPS URL of the settings Mini App (webapp_server.py behind the
# deploy's TLS). Unset means the /settings button can't be offered — Telegram
# refuses non-HTTPS web_app URLs — so the command degrades to an explanation.
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

# States
AWAIT_USERNAME = "await_username"
AWAIT_PASSWORD = "await_password"
AUTHORIZED = "authorized"

# One workout at a time per user. Maps a user_id to the live "Uploading..." notice
# while their workout is in flight (None during the brief window after the slot is
# claimed but before the notice is sent). Presence of the key — not its value — is
# the lock; a user already present has further messages ignored, not queued. In
# process only, which is all we need: a long-polling bot is a single Telegram
# consumer, so one process holds all of a user's traffic.
_active_notice: dict[int, "Message | None"] = {}

_PROCESSING_TEXT = "Uploading your workout to Garmin Connect..."

# Appended to the processing notice when we ignore a message sent mid-flight.
_BUSY_SUFFIX = (
    "\n\n⚠️ I can only process one workout at a time. "
    "I'm ignoring new messages until this one finishes — resend them after."
)

# What each service failure sounds like to the user. {detail} is filled from
# Failure.detail (only RATE_LIMITED uses it — the limiter's own message names
# the window and the wait).
_FAILURE_REPLIES: dict[FailureCode, str] = {
    FailureCode.RATE_LIMITED: "⚠️ {detail}",
    FailureCode.LIMITER_DOWN: (
        "Can't process workouts right now — usage tracking is unavailable. "
        "Please try again in a few minutes."
    ),
    FailureCode.LLM_BUSY: "I'm handling a lot of workouts right now. Send that again in a moment.",
    FailureCode.CONFIG_ERROR: "Something's broken on my side. Please try again later.",
    FailureCode.PARSE_TIMEOUT: "Parsing timed out. Please try again.",
    FailureCode.PARSE_FAILED: (
        "I couldn't turn that into a workout. Try describing the intervals "
        "with distances and paces, e.g. '2km warmup, 10x400m @ 3:45, 2km cooldown'."
    ),
    FailureCode.TOKEN_UNREADABLE: (
        "Your stored Garmin session is unreadable. Use /logout then /start to log in again."
    ),
    FailureCode.AUTH_EXPIRED: "Session expired and refresh failed. Use /logout then /start to re-login.",
    FailureCode.UPLOAD_FAILED: "Failed to import workout into Garmin. Please try again.",
}

# Initialize Pyrogram Client
app = Client("garmin_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# /start command: begin login flow
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    user_data = await get_user(user_id)
    if user_data and user_data.get("state") == AUTHORIZED and has_garmin_auth(user_data):
        return await message.reply(
            "You are already logged in! Send me a workout plan to import.\n"
            "Use /logout first if you want to switch accounts."
        )

    # Initialize persistent state. save_user replaces the whole document, so
    # carry the settings over — re-logging-in must not silently reset them.
    new_data = {"state": AWAIT_USERNAME}
    if user_data and user_data.get("prefs"):
        new_data["prefs"] = user_data["prefs"]
    await save_user(user_id, new_data)
    # Drop any stale half-finished login handshake
    await session.clear(user_id)
    await message.reply("Welcome! To get started, please enter your Garmin Connect username.")

# /logout command: remove authorization
@app.on_message(filters.command("logout") & filters.private)
async def logout_handler(client: Client, message: Message):
    user_id = message.from_user.id
    user_data = await get_user(user_id)
    if user_data and user_data.get("state") == AUTHORIZED:
        await delete_user(user_id)
        await log_auth_event(user_id, "logout")
        # Honest copy: there is no revocation endpoint reachable from this flow
        # (scraped SSO session, not a registered OAuth app), so deleting our copy
        # is all that actually happens. Don't imply Garmin-side access was
        # withdrawn when it wasn't.
        await message.reply(
            "You have been logged out — I deleted your stored Garmin credentials.\n\n"
            "Note: this doesn't invalidate the session on Garmin's side. If you "
            "want to be certain it can never be used again, change your Garmin "
            "password."
        )
    else:
        await message.reply("You are not logged in. Use /start to log in.")

# /settings command: open the preferences Mini App
@app.on_message(filters.command("settings") & filters.private)
async def settings_handler(client: Client, message: Message):
    if not WEBAPP_URL:
        return await message.reply(
            "Settings are not available on this deployment yet."
        )
    await message.reply(
        "Configure how your workouts are structured — warmup, cooldown, "
        "and how they end:",
        reply_markup=InlineKeyboardMarkup(
            # Must be an INLINE button: a reply-keyboard web_app button opens
            # the page with empty initData and the API couldn't authenticate.
            [[InlineKeyboardButton("⚙️ Workout settings", web_app=WebAppInfo(url=WEBAPP_URL))]]
        ),
    )


# /stats command: show current rate limit usage
@app.on_message(filters.command("stats") & filters.private)
async def stats_handler(client: Client, message: Message):
    user_id = message.from_user.id
    try:
        stats = await get_user_stats(user_id)
    except RateLimiterUnavailable:
        return await message.reply("Usage stats are unavailable right now. Try again shortly.")

    response = (
        "📊 **Your API Usage:**\n\n"
        f"⏱ **Hourly:** {stats['hourly']['used']}/{stats['hourly']['limit']}\n"
        f"📅 **Daily:** {stats['daily']['used']}/{stats['daily']['limit']}\n"
        f"📆 **Monthly:** {stats['monthly']['used']}/{stats['monthly']['limit']}\n"
    )
    await message.reply(response)


async def handle_username(message: Message, user_id: int, user_data: dict):
    # The handshake entry has a 5-minute TTL while `state` lives in Mongo, so
    # it can expire between /start and the username arriving. (Re)create it.
    await session.set_username(user_id, message.text.strip())
    # Update persistent state to await password
    user_data["state"] = AWAIT_PASSWORD
    await save_user(user_id, user_data)
    await message.reply("Great! Now please enter your Garmin Connect password.")


async def handle_password(message: Message, user_id: int, user_data: dict):
    username = await session.get_username(user_id)
    if not username:
        await delete_user(user_id)
        return await message.reply("Session expired or invalid. Please use /start to log in again.")

    password = message.text.strip()
    await message.reply("Logging in to Garmin Connect...")
    try:
        token = await login_to_garmin(username, password)
        # Clean up raw credentials
        await session.clear(user_id)
        # Scrub the password from the chat history now that login succeeded.
        try:
            await message.delete()
        except Exception:
            pass
        # Store only token and authorized state
        user_data.update({"garmin_auth": token, "state": AUTHORIZED})
        await save_user(user_id, user_data)
        await log_auth_event(user_id, "login_success")
        return await message.reply(
            "Successfully logged in! Send me any workout plan (text) to import into your Garmin Connect account."
        )
    except Exception as e:
        await session.clear(user_id)
        await delete_user(user_id)
        # Class name only — never the exception body, which can echo credentials.
        await log_auth_event(user_id, "login_failure", outcome="fail", detail=type(e).__name__)
        print(
            f"[login] user={user_id} method={os.getenv('GARMIN_LOGIN_METHOD', 'garth')} "
            f"err={type(e).__name__}: {e}",
            flush=True,
        )
        traceback.print_exc()
        if isinstance(e, GarminInvalidCredentials):
            return await message.reply(
                "❌ Incorrect Garmin email or password.\n"
                "Please use /start and try again."
            )
        if "429" in str(e):
            return await message.reply(
                "Garmin is temporarily rate limiting logins. Please wait a few minutes and try /start again."
            )
        return await message.reply(f"Login failed: {type(e).__name__}: {e}. Use /start to try again.")


async def handle_workout(message: Message, user_id: int, user_data: dict):
    # One workout at a time per user. Claim the slot synchronously — there is no
    # await between the `in` check and the assignment — so two messages racing on
    # separate dispatcher workers cannot both pass. A user already in flight has
    # this message IGNORED (not queued); we just annotate their live notice.
    if user_id in _active_notice:
        notice = _active_notice[user_id]
        if notice is not None:
            try:
                await notice.edit_text(_PROCESSING_TEXT + _BUSY_SUFFIX)
            except Exception:
                pass  # a repeat edit is "message not modified" — nothing to do
        return
    _active_notice[user_id] = None  # slot claimed; real notice stored below

    async def on_accepted():
        _active_notice[user_id] = await message.reply(_PROCESSING_TEXT)

    async def notify(text: str):
        await message.reply(text)

    try:
        outcome = await process_workout(
            user_id, user_data, message.text, notify=notify, on_accepted=on_accepted
        )
        if isinstance(outcome, Success):
            return await message.reply(
                f"Workout successfully imported! 🎉\n"
                f"{workout_url(outcome.workout_id)}\n\n"
                f"⚡ Processed in {outcome.processing_ms:.0f}ms"
            )
        reply = _FAILURE_REPLIES[outcome.code].format(detail=outcome.detail)
        return await message.reply(reply)
    finally:
        # Release the slot no matter how we leave — success, handled reply, or a
        # crash. Without this a single unexpected exception would wedge the user
        # into a permanent "busy" state with no workout ever processing.
        _active_notice.pop(user_id, None)


_STATE_HANDLERS = {
    AWAIT_USERNAME: handle_username,
    AWAIT_PASSWORD: handle_password,
    AUTHORIZED: handle_workout,
}


# Text handler for login and workout messages
@app.on_message(filters.text & filters.private)
async def text_handler(client: Client, message: Message):
    user_id = message.from_user.id
    user_data = await get_user(user_id)

    # NEVER log message.text here. This handler also receives the Garmin password
    # (state == AWAIT_PASSWORD), and stdout goes straight to the deploy logs —
    # which would defeat the message.delete() scrub below.
    print(f"[msg] user={user_id} state={(user_data or {}).get('state')} len={len(message.text)}")

    # Ensure a login session has been started
    if not user_data:
        return await message.reply("Please use /start to log in first.")

    if message.text.lower() == "ping":
        return await message.reply("pong")

    handler = _STATE_HANDLERS.get(user_data.get("state"))
    if handler is None:
        # A document with no login state exists when the user saved settings
        # in the Mini App before ever logging in — point them at /start
        # instead of ignoring them.
        return await message.reply("Please use /start to log in first.")
    await handler(message, user_id, user_data)


async def startup():
    """Initialize indexes and other startup tasks.

    init_rate_limiter() raises if REDIS_URL is absent and the bypass was not made
    explicit — a missing env var must crash the deploy, not silently disable the
    only thing bounding our LLM spend. token_crypto.init() applies the same
    policy to TOKEN_ENC_KEY: no key and no explicit TOKEN_ENC_DISABLED=1 means
    no deploy, not silent plaintext writes.
    """
    await init_rate_limiter()
    token_crypto.init()
    await create_user_indexes()
    await create_workout_indexes()
    await create_audit_indexes()
    print("✓ Mongo indexes created (users, workout_logs, auth_events)")


async def shutdown():
    """Release what startup() acquired. Mirrors it in reverse."""
    if await close_connections():
        print("✓ Redis connections closed")

if __name__ == "__main__":
    from pyrogram import idle

    async def main():
        await startup()
        webapp_runner = await start_webapp()
        print("Starting Pyrogram...")
        await app.start()
        print(f"Bot started as @{app.me.username}")
        if WEBAPP_URL:
            # Make settings permanently reachable: the bot's default menu
            # button (☰ next to the message box in every private chat) opens
            # the Mini App. Menu-button launches carry full initData, same as
            # the /settings inline button. Raw API on purpose — pyrogram's
            # set_chat_menu_button(chat_id=None) resolves the peer "me"
            # instead of sending InputUserEmpty, so it never sets the DEFAULT
            # button. Re-run on every boot: it's idempotent and picks up a
            # changed WEBAPP_URL (e.g. a fresh dev tunnel) automatically.
            await app.invoke(
                raw.functions.bots.SetBotMenuButton(
                    user_id=raw.types.InputUserEmpty(),
                    button=raw.types.BotMenuButton(text="Settings", url=WEBAPP_URL),
                )
            )
            print("✓ Default menu button → settings Mini App")
        # finally, not a trailing statement: idle() returns on SIGTERM, which is how
        # Railway stops us. An exception escaping it must not skip the teardown.
        try:
            await idle()
        finally:
            await app.stop()
            await webapp_runner.cleanup()
            await shutdown()

    # Must use app.run() — it reuses the event loop that Pyrogram's
    # Dispatcher captured at import time.  asyncio.run() creates a
    # new loop, so handlers registered via decorators would be lost.
    app.run(main())
