"""
Garmin OAuth primitives shared by the SSO login flow.

All Garmin OAuth exchanges run over curl_cffi Chrome TLS/HTTP2 impersonation:
connectapi.garmin.com sits behind the same Cloudflare JA3 classifier as
/sso/signin, so plain requests/urllib3 get 429 on the exchange. We sign OAuth1
with oauthlib but send the request via curl_cffi so the handshake looks like a
real Chrome browser. (The consumer key/secret is the public one from garth's S3
bucket, so computing the signature client-side is fine.)

On cloud egress IPs (Railway, etc.) Garmin 429s connectapi.garmin.com by source
IP regardless of TLS, so the exchange is optionally routed through a Cloudflare
Worker proxy — see GARMIN_OAUTH_PROXY below.
"""

import base64
import json
import os
import time
import re
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

OAUTH_CONSUMER_URL = "https://thegarth.s3.amazonaws.com/oauth_consumer.json"
ANDROID_UA = "com.garmin.android.apps.connectmobile"
IMPERSONATE = "chrome131"

# --- SSO endpoints / params (single source of truth) ------------------------
# Every SSO URL and the ticket regex are defined here so the login flow and any
# tooling share one definition instead of drifting copies.
SSO_BASE = "https://sso.garmin.com/sso"
SSO_EMBED = f"{SSO_BASE}/embed"
SSO_SIGNIN = f"{SSO_BASE}/signin"
SSO_MFA = f"{SSO_BASE}/verifyMFA/loginEnterMfaCode"

SSO_EMBED_PARAMS = {
    "id": "gauth-widget",
    "embedWidget": "true",
    "gauthHost": SSO_BASE,
}
SIGNIN_PARAMS = {
    **SSO_EMBED_PARAMS,
    "gauthHost": SSO_EMBED,
    "service": SSO_EMBED,
    "source": SSO_EMBED,
    "redirectAfterAccountLoginUrl": SSO_EMBED,
    "redirectAfterAccountCreationUrl": SSO_EMBED,
}

# The SSO success page redirects to /sso/embed?ticket=ST-...; pull the service
# ticket out of that HTML. Single definition shared across the login flow.
TICKET_RE = re.compile(r"embed\?ticket=(ST-[A-Za-z0-9._\-]+)")

# --- OAuth proxy (cloud egress IP workaround) -------------------------------
# Railway (and many cloud) egress IPs are on Garmin's blocklist: the OAuth
# exchange against connectapi.garmin.com returns 429 by *source IP* — curl_cffi
# TLS impersonation doesn't help because the throttle is IP-based. Route those
# requests through a Cloudflare Worker (CF's IP pool isn't on the blocklist).
#   GARMIN_OAUTH_PROXY=https://<worker>.workers.dev
#   GARMIN_OAUTH_PROXY_SECRET=<shared secret>   # REQUIRED when the proxy is set
# The OAuth1 signature is computed over the canonical Garmin URL; we only swap
# the wire host (path+query stay byte-identical) and tell the Worker the real
# host via X-Garmin-Host, so Garmin still validates the signature.
# NB: the SSO widget login (sso.garmin.com) is NOT IP-blocked, so only the
# exchange calls go through the proxy. Unset the env var to run direct.
_OAUTH_PROXY_BASE = (os.getenv("GARMIN_OAUTH_PROXY") or "").rstrip("/") or None
_OAUTH_PROXY_SECRET = os.getenv("GARMIN_OAUTH_PROXY_SECRET", "")

# Every user's Garmin OAuth token transits the Worker. Routing that through an
# unauthenticated endpoint is not a degraded mode worth supporting, so a proxy
# without a secret is a startup error rather than a silent downgrade.
if _OAUTH_PROXY_BASE and not _OAUTH_PROXY_SECRET:
    raise RuntimeError(
        "GARMIN_OAUTH_PROXY is set but GARMIN_OAUTH_PROXY_SECRET is empty. "
        "The proxy would accept unauthenticated requests — refusing to start."
    )


def _proxied_url(url: str) -> str:
    """Point a Garmin URL at the Worker, preserving path+query, when enabled."""
    if not _OAUTH_PROXY_BASE:
        return url
    parts = urlsplit(url)
    suffix = parts.path + (f"?{parts.query}" if parts.query else "")
    return _OAUTH_PROXY_BASE + suffix


def _proxy_headers(url: str, headers: dict) -> dict:
    """Add X-Garmin-Host (the real host) + shared secret, when enabled."""
    if not _OAUTH_PROXY_BASE:
        return headers
    return {
        **headers,
        "X-Garmin-Host": urlsplit(url).netloc,
        "X-Proxy-Auth": _OAUTH_PROXY_SECRET,  # guaranteed non-empty, see module init
    }


def _get_oauth_consumer() -> dict:
    """Fetch shared OAuth consumer key/secret from garth's S3 bucket."""
    resp = requests.get(OAUTH_CONSUMER_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _oauth1_signed(method: str, url: str, consumer: dict, oauth1: dict | None,
                   body: dict | None = None):
    """Build the OAuth1 Authorization header + (encoded) body via oauthlib."""
    from oauthlib.oauth1 import Client as OAuth1Client

    client = OAuth1Client(
        consumer["consumer_key"],
        client_secret=consumer["consumer_secret"],
        resource_owner_key=(oauth1 or {}).get("oauth_token"),
        resource_owner_secret=(oauth1 or {}).get("oauth_token_secret"),
    )
    headers = {"User-Agent": ANDROID_UA}
    enc_body = None
    if method == "POST":
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        enc_body = urlencode(body or {})
    uri, signed_headers, signed_body = client.sign(
        url, http_method=method, body=enc_body, headers=headers
    )
    return uri, signed_headers, signed_body


def _exchange_ticket_for_oauth1_curl(
    ticket: str, consumer: dict, login_url: str = SSO_EMBED
) -> dict:
    """Exchange an SSO ticket for an OAuth1 token over curl_cffi impersonation.

    `login_url` must match the CAS `service` the ticket was issued for; the
    SSO embed page (SIGNIN_PARAMS' service) is the default.
    """
    from curl_cffi import requests as cffi_requests

    url = (
        f"https://connectapi.garmin.com/oauth-service/oauth/"
        f"preauthorized?ticket={ticket}"
        f"&login-url={login_url}"
        f"&accepts-mfa-tokens=true"
    )
    uri, headers, _ = _oauth1_signed("GET", url, consumer, None)
    resp = cffi_requests.get(
        _proxied_url(uri),
        headers=_proxy_headers(uri, headers),
        impersonate=IMPERSONATE,
        timeout=15,
    )
    resp.raise_for_status()
    parsed = parse_qs(resp.text)
    token = {k: v[0] for k, v in parsed.items()}
    token["domain"] = "garmin.com"
    return token


def _exchange_oauth1_for_oauth2_curl(oauth1: dict, consumer: dict) -> dict:
    """Exchange OAuth1 token for OAuth2 token over curl_cffi impersonation."""
    from curl_cffi import requests as cffi_requests

    url = "https://connectapi.garmin.com/oauth-service/oauth/exchange/user/2.0"
    data = {}
    if oauth1.get("mfa_token"):
        data["mfa_token"] = oauth1["mfa_token"]
    uri, headers, body = _oauth1_signed("POST", url, consumer, oauth1, data)
    resp = cffi_requests.post(
        _proxied_url(uri),
        data=body,
        headers=_proxy_headers(uri, headers),
        impersonate=IMPERSONATE,
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()
    token["expires_at"] = int(time.time() + token["expires_in"])
    token["refresh_token_expires_at"] = int(
        time.time() + token["refresh_token_expires_in"]
    )
    return token


def _to_garth_token(oauth1: dict, oauth2: dict) -> str:
    """Encode OAuth1 + OAuth2 dicts into garth-compatible base64 token string.

    Format: base64(json([oauth1_dict, oauth2_dict]))
    Compatible with garth.Client.loads() / dumps().
    """
    oauth1_clean = {
        "oauth_token": oauth1["oauth_token"],
        "oauth_token_secret": oauth1["oauth_token_secret"],
        "mfa_token": oauth1.get("mfa_token"),
        "mfa_expiration_timestamp": oauth1.get("mfa_expiration_timestamp"),
        "domain": oauth1.get("domain", "garmin.com"),
    }
    bundle = json.dumps([oauth1_clean, oauth2])
    return base64.b64encode(bundle.encode()).decode()
