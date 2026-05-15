import garth
from garth.http import Client as GarthClient
import re
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


# --- Login method: "garth" (default), "curl", "browser", or "web" ---
#  * garth   — plain requests; cheap but blocked by Cloudflare on cloud IPs.
#  * curl    — curl_cffi w/ Chrome TLS impersonation; bypasses CF JA3 fingerprinting.
#  * browser — full Playwright Chromium; heaviest but solves JS challenges.
#  * web     — user logs in to Garmin in *their own* browser (residential IP,
#              so no Cloudflare block; MFA/CAPTCHA handled by a human; the
#              password never reaches the bot), then pastes the resulting
#              .../sso/embed?ticket=ST-... URL back into the chat. We exchange
#              that ticket for a token server-side (not Cloudflare-gated).
LOGIN_METHOD = os.getenv("GARMIN_LOGIN_METHOD", "garth")

# Garmin's GAuth embed page. Garmin only honours service URLs on *.garmin.com,
# so after login the browser lands on this same page with ?ticket=ST-...
# appended — which the user copies from the address bar. The ticket is bound
# to this service, so the default login-url in the OAuth1 exchange matches.
GARMIN_SSO_LOGIN_URL = (
    "https://sso.garmin.com/sso/embed"
    "?id=gauth-widget"
    "&embedWidget=true"
    "&gauthHost=https://sso.garmin.com/sso"
    "&clientId=GarminConnect"
    "&locale=en_US"
    "&service=https://sso.garmin.com/sso/embed"
    "&redirectAfterAccountLoginUrl=https://sso.garmin.com/sso/embed"
)

_TICKET_RE = re.compile(r"ST-[A-Za-z0-9._\-]+")


def extract_ticket(text: str) -> str | None:
    """Pull an SSO service ticket (ST-...) out of a pasted URL or raw string."""
    if not text:
        return None
    m = _TICKET_RE.search(text)
    return m.group(0) if m else None


def workout_url(workout_id) -> str:
    return f"https://connect.garmin.com/app/workout/{workout_id}?workoutType=running"


async def login_to_garmin(login: str, password: str) -> str:
    if LOGIN_METHOD == "browser":
        return await login_to_garmin_browser(login, password)
    if LOGIN_METHOD == "curl":
        return await login_to_garmin_curl(login, password)

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


async def login_to_garmin_curl(login: str, password: str) -> str:
    """Login via curl_cffi w/ Chrome TLS fingerprint. Bypasses CF JA3 blocks."""
    from garmin_curl_login import curl_login
    return await asyncio.to_thread(curl_login, login, password)


def ticket_to_token(ticket: str, login_url: str | None = None) -> str:
    """Exchange an SSO service ticket (ST-...) for a garth-compatible token.

    Used by the browser-driven ("web") auth flow: the user completes the
    Garmin SSO login in *their own* browser (residential IP, MFA/CAPTCHA
    handled by a human), so only this token exchange runs server-side.

    connectapi.garmin.com is behind the same Cloudflare JA3 classifier as
    /sso/signin, so from cloud egress IPs (Railway) plain requests gets 429.
    We go through curl_cffi Chrome impersonation to look like a browser.

    `login_url` must equal the CAS `service` the widget issued the ticket
    for. The SSO embed page is the default (paste-URL/JSON flow).
    """
    from garmin_browser_auth import (
        _exchange_oauth1_for_oauth2_curl,
        _exchange_ticket_for_oauth1_curl,
        _get_oauth_consumer,
        _to_garth_token,
    )

    consumer = _get_oauth_consumer()
    if login_url:
        oauth1 = _exchange_ticket_for_oauth1_curl(ticket, consumer, login_url)
    else:
        oauth1 = _exchange_ticket_for_oauth1_curl(ticket, consumer)
    oauth2 = _exchange_oauth1_for_oauth2_curl(oauth1, consumer)
    return _to_garth_token(oauth1, oauth2)


async def ticket_to_token_async(ticket: str, login_url: str | None = None) -> str:
    return await asyncio.to_thread(ticket_to_token, ticket, login_url)


def looks_like_garth_token(text: str) -> str | None:
    """Return the cleaned token if `text` is a garth token, else None.

    A garth token is base64(json([oauth1_dict, oauth2_dict])). Lets users
    who generate the token off-server (residential IP, no 429) paste it in
    directly — the bot then never calls Garmin for auth at all.
    """
    import base64
    import json

    if not text:
        return None
    candidate = "".join(text.split())  # tolerate pasted whitespace/newlines
    try:
        decoded = base64.b64decode(candidate, validate=True)
        bundle = json.loads(decoded)
    except Exception:
        return None
    if (
        isinstance(bundle, (list, tuple))
        and len(bundle) == 2
        and isinstance(bundle[0], dict)
        and isinstance(bundle[1], dict)
        and "oauth_token" in bundle[0]
        and "access_token" in bundle[1]
    ):
        return candidate
    return None


def token_from_session(session_path: str = "~/.garth") -> str:
    """Load a garth token from a saved session directory."""
    path = os.path.expanduser(session_path)
    garth.resume(path)
    return garth.client.dumps()


def refresh_token(token: str) -> str:
    """Refresh OAuth2 using the stored OAuth1 token.

    Re-runs the OAuth1->OAuth2 exchange (no SSO, no ticket) via curl_cffi
    impersonation rather than garth's plain-requests refresh, since that
    endpoint 429s from Railway's IP just like the initial exchange.
    """
    import base64
    import json

    from garmin_browser_auth import (
        _exchange_oauth1_for_oauth2_curl,
        _get_oauth_consumer,
        _to_garth_token,
    )

    oauth1, _ = json.loads(base64.b64decode(token))
    consumer = _get_oauth_consumer()
    oauth2 = _exchange_oauth1_for_oauth2_curl(oauth1, consumer)
    return _to_garth_token(oauth1, oauth2)


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
