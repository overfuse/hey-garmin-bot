"""Telegram layer: handlers, per-user single-flighting, and what to reply.

The workout flow itself (quota, LLM parse, upload, token refresh) lives in
workout_service.py and returns a typed Outcome; this module only decides what
to say to the user. The wording, in Russian and English, lives in
locales/*.ftl (see i18n.py); the user's language is prefs["lang"]. Login/logout stay here because they are inherently
conversational (two-prompt handshake, scrubbing the password message).
"""

import os
import traceback

from dotenv import load_dotenv
from pyrogram import Client, filters, raw
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

import prefs
import session
import token_crypto
from audit import create_indexes as create_audit_indexes
from audit import log_auth_event
from garmin import login_to_garmin, workout_url
from garmin_curl_login import GarminInvalidCredentials
from i18n import t
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
    set_prefs,
)
from webapp_server import start_webapp
from workout_log import create_indexes as create_workout_indexes
from workout_service import FailureCode, Progress, Success, process_workout

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

# Message id (locales/*.ftl) for each service failure. RATE_LIMITED also gets
# the window, cap and wait from Failure.rate_limit — see handle_workout.
_FAILURE_MSG: dict[FailureCode, str] = {
    FailureCode.RATE_LIMITED: "fail-rate-limited",
    FailureCode.LIMITER_DOWN: "fail-limiter-down",
    FailureCode.LLM_BUSY: "fail-llm-busy",
    FailureCode.CONFIG_ERROR: "fail-config-error",
    FailureCode.PROVIDER_QUOTA: "fail-provider-quota",
    FailureCode.PARSE_TIMEOUT: "fail-parse-timeout",
    FailureCode.PARSE_FAILED: "fail-parse-failed",
    FailureCode.NOT_A_WORKOUT: "fail-not-a-workout",
    FailureCode.TOKEN_UNREADABLE: "fail-token-unreadable",
    FailureCode.AUTH_EXPIRED: "fail-auth-expired",
    FailureCode.UPLOAD_FAILED: "fail-upload-failed",
}

_PROGRESS_MSG: dict[Progress, str] = {
    Progress.SESSION_REFRESHED: "session-refreshed",
}

# /language buttons. Each label is in its own language, so a user stuck in
# the wrong one can still find theirs.
_LANGUAGE_BUTTONS = InlineKeyboardMarkup(
    [[
        InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
        InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
    ]]
)


def _lang(user_data: dict | None) -> str:
    """The user's reply language; the default for users with no document."""
    return prefs.resolve((user_data or {}).get("prefs"))["lang"]


# Initialize Pyrogram Client
app = Client("garmin_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# /start command: begin login flow
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    user_data = await get_user(user_id)
    lang = _lang(user_data)
    if user_data and user_data.get("state") == AUTHORIZED and has_garmin_auth(user_data):
        return await message.reply(t(lang, "start-already-logged-in"))

    # Initialize persistent state. save_user replaces the whole document, so
    # carry the settings over — re-logging-in must not silently reset them.
    new_data = {"state": AWAIT_USERNAME}
    if user_data and user_data.get("prefs"):
        new_data["prefs"] = user_data["prefs"]
    await save_user(user_id, new_data)
    # Drop any stale half-finished login handshake
    await session.clear(user_id)
    await message.reply(t(lang, "start-welcome"))

# /logout command: remove authorization
@app.on_message(filters.command("logout") & filters.private)
async def logout_handler(client: Client, message: Message):
    user_id = message.from_user.id
    user_data = await get_user(user_id)
    lang = _lang(user_data)
    if user_data and user_data.get("state") == AUTHORIZED:
        await delete_user(user_id)
        await log_auth_event(user_id, "logout")
        # Honest copy: there is no revocation endpoint reachable from this flow
        # (scraped SSO session, not a registered OAuth app), so deleting our copy
        # is all that actually happens. Don't imply Garmin-side access was
        # withdrawn when it wasn't.
        await message.reply(t(lang, "logout-done"))
    else:
        await message.reply(t(lang, "logout-not-logged-in"))

# /settings command: open the preferences Mini App
@app.on_message(filters.command("settings") & filters.private)
async def settings_handler(client: Client, message: Message):
    lang = _lang(await get_user(message.from_user.id))
    if not WEBAPP_URL:
        return await message.reply(t(lang, "settings-unavailable"))
    await message.reply(
        t(lang, "settings-prompt"),
        reply_markup=InlineKeyboardMarkup(
            # Must be an INLINE button: a reply-keyboard web_app button opens
            # the page with empty initData and the API couldn't authenticate.
            [[InlineKeyboardButton(t(lang, "settings-button"), web_app=WebAppInfo(url=WEBAPP_URL))]]
        ),
    )


# /language command: pick the reply language. Also settable in the Mini App,
# but this works on deployments without WEBAPP_URL.
@app.on_message(filters.command("language") & filters.private)
async def language_handler(client: Client, message: Message):
    lang = _lang(await get_user(message.from_user.id))
    await message.reply(t(lang, "language-prompt"), reply_markup=_LANGUAGE_BUTTONS)


@app.on_callback_query(filters.regex(r"^lang:(ru|en)$"))
async def language_chosen(client: Client, query: CallbackQuery):
    lang = query.matches[0].group(1)
    user_id = query.from_user.id
    # set_prefs stores the full resolved dict; merge so the other prefs keep
    # their values. $set, so this works before /start as well.
    user_data = await get_user(user_id)
    await set_prefs(user_id, {**prefs.resolve((user_data or {}).get("prefs")), "lang": lang})
    await query.answer()
    await query.message.edit_text(t(lang, "language-saved"))


# /stats command: show current rate limit usage
@app.on_message(filters.command("stats") & filters.private)
async def stats_handler(client: Client, message: Message):
    user_id = message.from_user.id
    lang = _lang(await get_user(user_id))
    try:
        stats = await get_user_stats(user_id)
    except RateLimiterUnavailable:
        return await message.reply(t(lang, "stats-unavailable"))

    counts = {
        f"{window}_{field}": stats[window][field]
        for window in ("hourly", "daily", "monthly")
        for field in ("used", "limit")
    }
    await message.reply(t(lang, "stats", **counts))


async def handle_username(message: Message, user_id: int, user_data: dict):
    # The handshake entry has a 5-minute TTL while `state` lives in Mongo, so
    # it can expire between /start and the username arriving. (Re)create it.
    await session.set_username(user_id, message.text.strip())
    # Update persistent state to await password
    user_data["state"] = AWAIT_PASSWORD
    await save_user(user_id, user_data)
    await message.reply(t(_lang(user_data), "ask-password"))


async def handle_password(message: Message, user_id: int, user_data: dict):
    lang = _lang(user_data)
    username = await session.get_username(user_id)
    if not username:
        await delete_user(user_id)
        return await message.reply(t(lang, "login-session-expired"))

    password = message.text.strip()
    await message.reply(t(lang, "login-in-progress"))
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
        return await message.reply(t(lang, "login-success"))
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
            return await message.reply(t(lang, "login-bad-credentials"))
        if "429" in str(e):
            return await message.reply(t(lang, "login-rate-limited"))
        return await message.reply(t(lang, "login-failed", error=f"{type(e).__name__}: {e}"))


async def handle_workout(message: Message, user_id: int, user_data: dict):
    lang = _lang(user_data)
    processing = t(lang, "processing")
    # One workout at a time per user. Claim the slot synchronously — there is no
    # await between the `in` check and the assignment — so two messages racing on
    # separate dispatcher workers cannot both pass. A user already in flight has
    # this message IGNORED (not queued); we just annotate their live notice.
    if user_id in _active_notice:
        notice = _active_notice[user_id]
        if notice is not None:
            try:
                await notice.edit_text(processing + t(lang, "busy-suffix"))
            except Exception:
                pass  # a repeat edit is "message not modified" — nothing to do
        return
    _active_notice[user_id] = None  # slot claimed; real notice stored below

    async def on_accepted():
        _active_notice[user_id] = await message.reply(processing)

    async def notify(event: Progress):
        await message.reply(t(lang, _PROGRESS_MSG[event]))

    try:
        outcome = await process_workout(
            user_id, user_data, message.text, notify=notify, on_accepted=on_accepted
        )
        if isinstance(outcome, Success):
            reply = t(lang, "upload-success", url=workout_url(outcome.workout_id))
            if prefs.resolve(user_data.get("prefs"))["show_processing_time"]:
                # Pre-formatted str, not an int: Fluent would group digits per
                # locale ("12 345" in ru). The number is always English-style
                # "12,345"; only the wording and unit are translated.
                reply += t(lang, "processing-time", ms=f"{round(outcome.processing_ms):,}")
            return await message.reply(reply)
        args = {}
        if limit := outcome.rate_limit:
            args = {"scope": limit.scope, "cap": limit.cap, "wait": limit.wait()}
        return await message.reply(t(lang, _FAILURE_MSG[outcome.code], **args))
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

    lang = _lang(user_data)

    # Ensure a login session has been started
    if not user_data:
        return await message.reply(t(lang, "not-logged-in"))

    if message.text.lower() == "ping":
        return await message.reply("pong")

    handler = _STATE_HANDLERS.get(user_data.get("state"))
    if handler is None:
        # A document with no login state exists when the user saved settings
        # in the Mini App before ever logging in — point them at /start
        # instead of ignoring them.
        return await message.reply(t(lang, "not-logged-in"))
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
