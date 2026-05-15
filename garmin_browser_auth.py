"""
Garmin OAuth via Playwright browser login.

Bypasses 429-blocked SSO programmatic endpoint by driving a real browser.
Two modes:
  - Interactive (headless=False): opens browser for manual login
  - Automated (headless=True): fills credentials programmatically

Usage (standalone):
    python garmin_browser_auth.py                    # interactive
    python garmin_browser_auth.py -u EMAIL -p PASS   # automated headless
"""

import base64
import json
import re
import time
from urllib.parse import parse_qs

import requests
from requests_oauthlib import OAuth1Session

OAUTH_CONSUMER_URL = "https://thegarth.s3.amazonaws.com/oauth_consumer.json"
ANDROID_UA = "com.garmin.android.apps.connectmobile"

SSO_EMBED_URL = (
    "https://sso.garmin.com/sso/embed"
    "?id=gauth-widget"
    "&embedWidget=true"
    "&gauthHost=https://sso.garmin.com/sso"
    "&clientId=GarminConnect"
    "&locale=en_US"
    "&redirectAfterAccountLoginUrl=https://sso.garmin.com/sso/embed"
    "&service=https://sso.garmin.com/sso/embed"
)

LOGIN_TIMEOUT = 300  # 5 minutes for interactive, overridden for automated


def _get_oauth_consumer() -> dict:
    """Fetch shared OAuth consumer key/secret from garth's S3 bucket."""
    resp = requests.get(OAUTH_CONSUMER_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _exchange_ticket_for_oauth1(
    ticket: str, consumer: dict, login_url: str = "https://sso.garmin.com/sso/embed"
) -> dict:
    """Exchange an SSO ticket for an OAuth1 token.

    `login_url` must match the CAS `service` the ticket was issued for.
    Defaults to the embed page (garth/curl flow); the browser flow passes
    its own callback URL when the widget redirects there with the ticket.
    """
    sess = OAuth1Session(
        consumer["consumer_key"],
        consumer["consumer_secret"],
    )
    url = (
        f"https://connectapi.garmin.com/oauth-service/oauth/"
        f"preauthorized?ticket={ticket}"
        f"&login-url={login_url}"
        f"&accepts-mfa-tokens=true"
    )
    resp = sess.get(url, headers={"User-Agent": ANDROID_UA}, timeout=15)
    resp.raise_for_status()
    parsed = parse_qs(resp.text)
    token = {k: v[0] for k, v in parsed.items()}
    token["domain"] = "garmin.com"
    return token


def _exchange_oauth1_for_oauth2(oauth1: dict, consumer: dict) -> dict:
    """Exchange OAuth1 token for OAuth2 token."""
    sess = OAuth1Session(
        consumer["consumer_key"],
        consumer["consumer_secret"],
        resource_owner_key=oauth1["oauth_token"],
        resource_owner_secret=oauth1["oauth_token_secret"],
    )
    url = "https://connectapi.garmin.com/oauth-service/oauth/exchange/user/2.0"
    data = {}
    if oauth1.get("mfa_token"):
        data["mfa_token"] = oauth1["mfa_token"]
    resp = sess.post(
        url,
        headers={
            "User-Agent": ANDROID_UA,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=data,
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()
    token["expires_at"] = int(time.time() + token["expires_in"])
    token["refresh_token_expires_at"] = int(
        time.time() + token["refresh_token_expires_in"]
    )
    return token


# --- curl_cffi variants -----------------------------------------------------
# connectapi.garmin.com sits behind the same Cloudflare JA3 classifier as
# /sso/signin: from cloud egress IPs (Railway etc.) plain requests/urllib3
# get 429 on the OAuth exchange. We sign OAuth1 with oauthlib but send the
# request via curl_cffi's Chrome TLS/HTTP2 impersonation so the handshake
# looks like a real browser. (The consumer key/secret is the public one
# from garth's S3 bucket, so computing the signature client-side is fine.)

IMPERSONATE = "chrome131"


def _oauth1_signed(method: str, url: str, consumer: dict, oauth1: dict | None,
                   body: dict | None = None):
    """Build the OAuth1 Authorization header + (encoded) body via oauthlib."""
    from oauthlib.oauth1 import Client as OAuth1Client
    from urllib.parse import urlencode

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
    ticket: str, consumer: dict, login_url: str = "https://sso.garmin.com/sso/embed"
) -> dict:
    """Same as _exchange_ticket_for_oauth1 but over curl_cffi impersonation."""
    from curl_cffi import requests as cffi_requests

    url = (
        f"https://connectapi.garmin.com/oauth-service/oauth/"
        f"preauthorized?ticket={ticket}"
        f"&login-url={login_url}"
        f"&accepts-mfa-tokens=true"
    )
    uri, headers, _ = _oauth1_signed("GET", url, consumer, None)
    resp = cffi_requests.get(
        uri, headers=headers, impersonate=IMPERSONATE, timeout=15
    )
    resp.raise_for_status()
    parsed = parse_qs(resp.text)
    token = {k: v[0] for k, v in parsed.items()}
    token["domain"] = "garmin.com"
    return token


def _exchange_oauth1_for_oauth2_curl(oauth1: dict, consumer: dict) -> dict:
    """Same as _exchange_oauth1_for_oauth2 but over curl_cffi impersonation."""
    from curl_cffi import requests as cffi_requests

    url = "https://connectapi.garmin.com/oauth-service/oauth/exchange/user/2.0"
    data = {}
    if oauth1.get("mfa_token"):
        data["mfa_token"] = oauth1["mfa_token"]
    uri, headers, body = _oauth1_signed("POST", url, consumer, oauth1, data)
    resp = cffi_requests.post(
        uri, data=body, headers=headers, impersonate=IMPERSONATE, timeout=15
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
    # Ensure oauth1 has all fields garth expects
    oauth1_clean = {
        "oauth_token": oauth1["oauth_token"],
        "oauth_token_secret": oauth1["oauth_token_secret"],
        "mfa_token": oauth1.get("mfa_token"),
        "mfa_expiration_timestamp": oauth1.get("mfa_expiration_timestamp"),
        "domain": oauth1.get("domain", "garmin.com"),
    }
    bundle = json.dumps([oauth1_clean, oauth2])
    return base64.b64encode(bundle.encode()).decode()


def _wait_for_ticket(page, timeout: int) -> str:
    """Poll page content/URL for the SSO ticket (ST-...)."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            content = page.content()
            m = re.search(r"ticket=(ST-[A-Za-z0-9\-]+)", content)
            if m:
                return m.group(1)
            url = page.url
            if "ticket=" in url:
                m = re.search(r"ticket=(ST-[A-Za-z0-9\-]+)", url)
                if m:
                    return m.group(1)
        except Exception:
            pass
        page.wait_for_timeout(500)
    raise TimeoutError("Timed out waiting for Garmin login ticket")


def browser_login_interactive() -> str:
    """Open a visible browser for manual Garmin login. Returns garth token."""
    from playwright.sync_api import sync_playwright

    consumer = _get_oauth_consumer()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context().new_page()
        page.goto(SSO_EMBED_URL)
        print("Browser opened - log in with your Garmin credentials.")
        print("The window will close automatically when done.")
        ticket = _wait_for_ticket(page, LOGIN_TIMEOUT)
        browser.close()

    oauth1 = _exchange_ticket_for_oauth1(ticket, consumer)
    oauth2 = _exchange_oauth1_for_oauth2(oauth1, consumer)
    return _to_garth_token(oauth1, oauth2)


def browser_login_automated(username: str, password: str) -> str:
    """Headless browser login with credentials filled programmatically.

    Returns garth-compatible base64 token string.
    """
    from playwright.sync_api import sync_playwright

    consumer = _get_oauth_consumer()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.goto(SSO_EMBED_URL)

        # Wait for the login form to appear
        page.wait_for_selector("#username", timeout=15_000)
        page.fill("#username", username)
        page.fill("#password", password)
        page.click("#login-btn-signin")

        ticket = _wait_for_ticket(page, 60)
        browser.close()

    oauth1 = _exchange_ticket_for_oauth1(ticket, consumer)
    oauth2 = _exchange_oauth1_for_oauth2(oauth1, consumer)
    return _to_garth_token(oauth1, oauth2)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Garmin browser auth")
    parser.add_argument("-u", "--username", help="Garmin username/email")
    parser.add_argument("-p", "--password", help="Garmin password")
    args = parser.parse_args()

    if args.username and args.password:
        token = browser_login_automated(args.username, args.password)
    else:
        token = browser_login_interactive()

    print("\nGarth-compatible token (base64):")
    print(token)
