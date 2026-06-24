import garth
from garth.http import Client as GarthClient
import time
import os
from chatgpt import plan_to_json, plan_to_json_async
from garmin_convert import convert
import asyncio

# Global lock to serialize the OpenAI plan->JSON call (the only token-costing,
# billable step). Concurrent users can't spike token spend past one in-flight
# OpenAI request at a time; per-user quotas live in rate_limiter.py.
_openai_lock = asyncio.Lock()


# --- Login method: "garth" (default) or "curl" ---
#  * garth — plain requests; cheap but blocked by Cloudflare on cloud IPs.
#  * curl  — curl_cffi w/ Chrome TLS impersonation; bypasses CF JA3 fingerprinting.
LOGIN_METHOD = os.getenv("GARMIN_LOGIN_METHOD", "garth")

# Startup diagnostic: confirms from the deploy logs whether the OAuth proxy env
# was actually picked up (the #1 cause of "still 429 on Railway" is the proxy
# being unset/undeployed, so traffic still goes direct to Garmin's blocked IP).
_proxy_dbg = os.getenv("GARMIN_OAUTH_PROXY")
print(
    f"[garmin] login_method={LOGIN_METHOD} "
    f"oauth_proxy={'ON ' + _proxy_dbg if _proxy_dbg else 'OFF (direct)'}",
    flush=True,
)

def workout_url(workout_id) -> str:
    return f"https://connect.garmin.com/app/workout/{workout_id}?workoutType=running"


async def login_to_garmin(login: str, password: str) -> str:
    if LOGIN_METHOD == "curl":
        return await login_to_garmin_curl(login, password)

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

    return await asyncio.to_thread(_do_login)


async def login_to_garmin_curl(login: str, password: str) -> str:
    """Login via curl_cffi w/ Chrome TLS fingerprint. Bypasses CF JA3 blocks."""
    from garmin_curl_login import curl_login
    return await asyncio.to_thread(curl_login, login, password)


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

    from garmin_oauth import (
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


def _install_garth_proxy(client: GarthClient) -> None:
    """Route a garth client's connectapi.garmin.com calls through the OAuth proxy.

    Two garth call sites hit connectapi.garmin.com, which Railway's egress IP
    is 429-blocked on (the same IP block as the OAuth exchange):
      1. the workout upload (client.connectapi), and
      2. the OAuth2 *refresh* garth does internally when the token is expired
         (Client.request -> refresh_oauth2 -> sso.exchange).

    Mount a requests adapter that rewrites that host to the Cloudflare Worker
    (which re-originates from a non-blocked IP) and adds the X-Garmin-Host /
    shared-secret headers the Worker expects. The worker hop also sidesteps
    garth's Cloudflare JA3 fingerprint (TLS is now to workers.dev).

    Mount on the generic "https://" prefix, NOT "https://connectapi.garmin.com":
    garth's exchange session (GarminOAuth1Session) only inherits the parent's
    "https://" adapter, so a host-specific mount would miss the refresh path.
    The adapter only rewrites connectapi.garmin.com and passes everything else
    (e.g. sso.garmin.com) through untouched.

    No-op when GARMIN_OAUTH_PROXY is unset (local / residential IPs).
    """
    from garmin_oauth import _OAUTH_PROXY_BASE, _OAUTH_PROXY_SECRET

    if not _OAUTH_PROXY_BASE:
        return

    from requests.adapters import HTTPAdapter
    from urllib.parse import urlsplit

    base = _OAUTH_PROXY_BASE
    secret = _OAUTH_PROXY_SECRET

    # Preserve garth's existing retry policy on the adapter we replace.
    _existing = client.sess.adapters.get("https://")
    _max_retries = getattr(_existing, "max_retries", 0)

    class _GarminProxyAdapter(HTTPAdapter):
        def send(self, request, **kwargs):
            parts = urlsplit(request.url)
            if parts.netloc == "connectapi.garmin.com":
                request.headers["X-Garmin-Host"] = parts.netloc
                if secret:
                    request.headers["X-Proxy-Auth"] = secret
                # Drop the stale Host so urllib3 derives it from the worker URL.
                request.headers.pop("Host", None)
                request.url = base + parts.path + (
                    f"?{parts.query}" if parts.query else ""
                )
            return super().send(request, **kwargs)

    client.sess.mount("https://", _GarminProxyAdapter(max_retries=_max_retries))


def _client_for(token: str) -> GarthClient:
    """Build a fresh, per-call garth client authenticated with `token`.

    garth's module-level `garth.client` is a process-wide singleton, so loading
    a token into it races across concurrent uploads — user A's request could
    fire with user B's token. Each upload gets its own client instead.
    """
    client = GarthClient()
    client.loads(token)
    _install_garth_proxy(client)
    return client


def upload_workout_to_garmin(token: str, workout_plan: str) -> str:
    workout_json = plan_to_json(workout_plan)
    garmin_json = convert(workout_json)
    return upload_garmin_payload(token, garmin_json)

def upload_garmin_payload(token: str, garmin_json: dict) -> str:
    client = _client_for(token)
    result = client.connectapi("/workout-service/workout", method="POST", json=garmin_json)
    return result["workoutId"]


async def upload_workout_to_garmin_async(
    token: str,
    workout_plan: str,
) -> tuple[str, dict, float]:
    """
    Upload workout to Garmin asynchronously.

    Returns:
        Tuple of (workout_id, workout_json, processing_time_ms)
    """
    start_time = time.time()

    # Serialize the billable OpenAI call so concurrent users can't spike spend.
    async with _openai_lock:
        workout_json = await plan_to_json_async(workout_plan)
    garmin_json = convert(workout_json)

    def _upload():
        client = _client_for(token)
        res = client.connectapi("/workout-service/workout", method="POST", json=garmin_json)
        return res["workoutId"]

    workout_id = await asyncio.to_thread(_upload)

    processing_time = (time.time() - start_time) * 1000  # Convert to ms

    return workout_id, workout_json, processing_time
