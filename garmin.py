import asyncio
import os

from garth.exc import GarthHTTPError
from garth.http import Client as GarthClient
from requests import HTTPError

from garmin_convert import convert


class GarminAuthExpired(Exception):
    """Garmin rejected the OAuth token (401). Refreshable without an SSO hit."""


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

    from urllib.parse import urlsplit

    from requests.adapters import HTTPAdapter

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
                request.headers["X-Proxy-Auth"] = secret  # non-empty; garmin_oauth asserts it
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


def _http_status(e: Exception) -> int | None:
    """The HTTP status behind an exception, or None if it isn't an HTTP error.

    Takes `Exception` because callers hand it whatever `connectapi` raised — a
    connection reset is a legitimate input, answered with None, not a type error.

    garth does not raise requests.HTTPError directly: it wraps it in a
    GarthHTTPError dataclass whose `.error` holds the original, so the status lives
    one level down. Reading `.response` off the outer exception always yields None,
    which silently turned the 401 check below — and the token refresh it gates —
    into dead code.
    """
    inner = e.error if isinstance(e, GarthHTTPError) else e
    if isinstance(inner, HTTPError) and inner.response is not None:
        return inner.response.status_code
    return None


def _raise_if_auth_expired(e: Exception) -> None:
    """Translate a Garmin 401 into a typed auth failure.

    Matching on `"401" in str(e)` (the old approach) also fires on any error whose
    body happens to contain "401" — e.g. a Garmin 500 echoing a workout named
    "401 repeats" — sending a healthy token down the refresh path.
    """
    if _http_status(e) == 401:
        raise GarminAuthExpired(str(e)) from e


def upload_garmin_payload(token: str, garmin_json: dict) -> tuple[str, str | None]:
    """Upload one workout. Returns (workout_id, refreshed_token_or_None).

    garth refreshes OAuth2 internally when `expires_at` has passed (Client.request
    -> refresh_oauth2 -> sso.exchange). Discarding the client here used to discard
    that refresh with it, so one hour after login every upload paid a refresh
    round-trip forever — the stored token never advanced past its original OAuth2
    half. Compare dumps() against what we loaded and surface the new blob so the
    caller can persist it.

    The first upload after a curl-path login reports a "refresh" that is really
    just dumps() canonicalising the JSON field order; the caller persists it once
    and the comparison is stable from then on.
    """
    client = _client_for(token)
    try:
        result = client.connectapi("/workout-service/workout", method="POST", json=garmin_json)
    except Exception as e:
        _raise_if_auth_expired(e)
        raise
    refreshed = client.dumps()
    return result["workoutId"], (refreshed if refreshed != token else None)


async def upload_parsed_workout(token: str, workout_json: dict) -> tuple[str, str | None]:
    """Upload an already-parsed workout. Safe to retry — costs no LLM tokens.

    Returns (workout_id, refreshed_token_or_None); persist the second element
    when present or the next upload re-pays garth's internal refresh.

    Raises:
        GarminAuthExpired: the token is stale; refresh and call again.
    """
    garmin_json = convert(workout_json)
    return await asyncio.to_thread(upload_garmin_payload, token, garmin_json)
