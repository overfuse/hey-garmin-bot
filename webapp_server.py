"""HTTP server for the Telegram Mini App (settings page + preferences API).

Runs inside the bot process on Pyrogram's event loop — same reasoning that
keeps the workout flow in one process: Mongo helpers, token crypto, and (in
the future login flow) the Garmin client are all here, and a Mini App backend
in a separate service would have to re-implement or RPC to every one of them.

Auth model: every /api request carries `Authorization: tma <initData>` where
initData is the raw signed launch payload from the Telegram webview. A valid
HMAC (tg_init_data.py) authenticates the request exactly as strongly as a
chat message from that telegram user id; there are no sessions or cookies to
manage, and nothing here is callable anonymously except the static page and
the health check.

The page itself must be reachable over public HTTPS (Telegram refuses plain
HTTP webapps) — TLS termination is the deploy's job (Railway domain, tunnel,
or a fronting Worker); this server speaks plain HTTP on WEBAPP_PORT.
"""

import os
from pathlib import Path

from aiohttp import web

import prefs
import user
from tg_init_data import InitDataError, validate_init_data

_WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"

BOT_TOKEN_KEY = web.AppKey("bot_token", str)
PAGE_HTML_KEY = web.AppKey("page_html", str)
PAGE_JS_KEY = web.AppKey("page_js", str)

# initData is a bearer credential for its whole lifetime; an hour covers a
# settings page left open on a slow decision without inviting week-old replays.
INIT_DATA_MAX_AGE_S = 3600

# The largest legitimate request is a four-boolean JSON object; initData in the
# header doesn't count against this. Anything bigger is not our client.
_MAX_BODY = 4096

_COMMON_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

# script-src stays free of 'unsafe-inline': all JS lives in app.js, so an
# injected inline <script> is dead on arrival. Styles are an inline <style>
# block — 'unsafe-inline' there doesn't grant script execution.
_CSP = (
    "default-src 'none'; "
    "script-src 'self' https://telegram.org; "
    "connect-src 'self'; "
    "style-src 'unsafe-inline'; "
    "base-uri 'none'; "
    "form-action 'none'"
)


def _authenticated_user_id(request: web.Request) -> int:
    """telegram user id from the Authorization header, or 401.

    The 401 body is deliberately generic — InitDataError details (hash
    mismatch vs expiry vs no user) go to stdout only, so a forger probing the
    endpoint learns nothing about which check failed.
    """
    scheme, _, raw = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "tma":
        raise web.HTTPUnauthorized(text="missing initData")
    try:
        fields = validate_init_data(
            raw, request.app[BOT_TOKEN_KEY], max_age_s=INIT_DATA_MAX_AGE_S
        )
    except InitDataError as e:
        print(f"[webapp] initData rejected: {e}", flush=True)
        raise web.HTTPUnauthorized(text="invalid initData") from None
    return int(fields["user"]["id"])


async def handle_page(request: web.Request) -> web.Response:
    return web.Response(
        text=request.app[PAGE_HTML_KEY],
        content_type="text/html",
        headers={**_COMMON_HEADERS, "Content-Security-Policy": _CSP},
    )


async def handle_app_js(request: web.Request) -> web.Response:
    return web.Response(
        text=request.app[PAGE_JS_KEY],
        content_type="application/javascript",
        headers=_COMMON_HEADERS,
    )


async def handle_healthz(request: web.Request) -> web.Response:
    return web.Response(text="ok", headers=_COMMON_HEADERS)


async def handle_get_prefs(request: web.Request) -> web.Response:
    uid = _authenticated_user_id(request)
    doc = await user.get_user(uid)
    return web.json_response(
        prefs.resolve((doc or {}).get("prefs")), headers=_COMMON_HEADERS
    )


async def handle_put_prefs(request: web.Request) -> web.Response:
    uid = _authenticated_user_id(request)
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="body must be JSON") from None

    # PUT is a full replacement: exactly the known keys, all booleans. A
    # partial dict would silently reset the missing keys to defaults on the
    # next resolve, so reject it instead of guessing.
    if (
        not isinstance(body, dict)
        or set(body) != set(prefs.KEYS)
        or not all(isinstance(v, bool) for v in body.values())
    ):
        raise web.HTTPBadRequest(text=f"expected booleans for keys: {sorted(prefs.KEYS)}")

    await user.set_prefs(uid, body)
    print(f"[webapp] prefs saved user={uid}", flush=True)
    return web.json_response(body, headers=_COMMON_HEADERS)


def create_app(bot_token: str | None = None) -> web.Application:
    """Build the app. Reads the static files once — a missing page is a
    packaging error and must fail the deploy here, not 500 at first open."""
    app = web.Application(client_max_size=_MAX_BODY)
    app[BOT_TOKEN_KEY] = bot_token if bot_token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
    app[PAGE_HTML_KEY] = (_WEBAPP_DIR / "index.html").read_text(encoding="utf-8")
    app[PAGE_JS_KEY] = (_WEBAPP_DIR / "app.js").read_text(encoding="utf-8")
    app.add_routes(
        [
            web.get("/", handle_page),
            web.get("/app.js", handle_app_js),
            web.get("/healthz", handle_healthz),
            web.get("/api/prefs", handle_get_prefs),
            web.put("/api/prefs", handle_put_prefs),
        ]
    )
    return app


async def start_webapp() -> web.AppRunner:
    """Start serving on WEBAPP_PORT (default 8080). Caller owns cleanup()."""
    port = int(os.getenv("WEBAPP_PORT", "8080"))
    runner = web.AppRunner(create_app())
    await runner.setup()
    await web.TCPSite(runner, host="0.0.0.0", port=port).start()
    print(f"✓ Webapp server listening on :{port}", flush=True)
    return runner
