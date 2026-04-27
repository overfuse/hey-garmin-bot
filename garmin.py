import garth
from garth.http import Client as GarthClient
import time
import os
from chatgpt import plan_to_json, plan_to_json_async
from garmin_convert import convert
import asyncio

# Global lock + timestamp to throttle SSO login attempts.
# Garmin rate-limits by IP, so we serialize all logins and enforce a
# minimum gap regardless of how many users hit the bot concurrently.
_login_lock = asyncio.Lock()
_last_login_time: float = 0.0
_MIN_LOGIN_INTERVAL = 60.0  # seconds between SSO logins


# --- Login method: "garth" (default) or "browser" ---
# Set GARMIN_LOGIN_METHOD=browser to use Playwright-based login.
LOGIN_METHOD = os.getenv("GARMIN_LOGIN_METHOD", "garth")


def workout_url(workout_id) -> str:
    return f"https://connect.garmin.com/app/workout/{workout_id}?workoutType=running"


async def login_to_garmin(login: str, password: str) -> str:
    if LOGIN_METHOD == "browser":
        return await login_to_garmin_browser(login, password)

    global _last_login_time
    async with _login_lock:
        elapsed = time.time() - _last_login_time
        if elapsed < _MIN_LOGIN_INTERVAL:
            await asyncio.sleep(_MIN_LOGIN_INTERVAL - elapsed)

        def _do_login():
            # Use a fresh client that does NOT retry on 429 —
            # retrying rate-limited SSO requests only digs a deeper hole.
            # NB: passing status_forcelist to GarthClient(...) collides with
            # the class default that __init__ already forwards to configure().
            client = GarthClient()
            client.configure(status_forcelist=(408, 500, 502, 503, 504))
            # Override the default garth User-Agent — Garmin's SSO blocks the
            # library's UA, so masquerade as a regular desktop Chrome browser.
            client.sess.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                )
            })
            client.login(login, password)
            return client.dumps()

        token = await asyncio.to_thread(_do_login)
        _last_login_time = time.time()
        return token


async def login_to_garmin_browser(login: str, password: str) -> str:
    """Login via headless Playwright browser. Bypasses SSO 429 rate limits."""
    from garmin_browser_auth import browser_login_automated
    return await asyncio.to_thread(browser_login_automated, login, password)


def token_from_session(session_path: str = "~/.garth") -> str:
    """Load a garth token from a saved session directory."""
    path = os.path.expanduser(session_path)
    garth.resume(path)
    return garth.client.dumps()


def refresh_token(token: str) -> str:
    """Refresh OAuth2 using the stored OAuth1 token.

    This calls the token exchange endpoint, NOT the SSO login page,
    so it won't trigger SSO rate limits.
    """
    garth.client.loads(token)
    garth.client.refresh_oauth2()
    return garth.client.dumps()


async def refresh_token_async(token: str) -> str:
    return await asyncio.to_thread(refresh_token, token)


def upload_workout_to_garmin(token: str, workout_plan: str) -> str:
    workout_json = plan_to_json(workout_plan)
    garmin_json = convert(workout_json)
    garth.client.loads(token)
    return upload_garmin_payload(token, garmin_json)

def upload_garmin_payload(token: str, garmin_json: dict) -> str:
    garth.client.loads(token)
    result = garth.connectapi("/workout-service/workout", method="POST", json=garmin_json)
    return result["workoutId"]


async def upload_workout_to_garmin_async(
    token: str,
    workout_plan: str,
    user_id: int = None
) -> tuple[str, dict, float]:
    """
    Upload workout to Garmin asynchronously.

    Returns:
        Tuple of (workout_id, workout_json, processing_time_ms)
    """
    start_time = time.time()

    workout_json = await plan_to_json_async(workout_plan)
    garmin_json = convert(workout_json)

    def _upload():
        garth.client.loads(token)
        res = garth.connectapi("/workout-service/workout", method="POST", json=garmin_json)
        return res["workoutId"]

    workout_id = await asyncio.to_thread(_upload)

    processing_time = (time.time() - start_time) * 1000  # Convert to ms

    return workout_id, workout_json, processing_time
