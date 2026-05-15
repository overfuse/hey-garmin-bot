"""
Garmin SSO login via curl_cffi with Chrome TLS/HTTP2 impersonation.

The standard `garth` login uses requests/urllib3, whose JA3 fingerprint is
trivially classified as "Python script" by Cloudflare. From cloud egress IPs
(Railway, Heroku, etc.) Garmin's CF rules then return 429/403 on /sso/signin,
even though the same code works from a residential IP.

curl_cffi wraps curl-impersonate, so the TLS handshake and HTTP/2 SETTINGS
frames look identical to a real Chrome browser. After we obtain the SSO
ticket, the OAuth1/OAuth2 exchange against connectapi.garmin.com goes
through plain `requests` — those endpoints aren't behind the same CF rules.

Usage (standalone):
    python garmin_curl_login.py -u EMAIL -p PASS
"""

import re
import sys

from curl_cffi import requests as cffi_requests

from garmin_browser_auth import (
    _exchange_oauth1_for_oauth2,
    _exchange_ticket_for_oauth1,
    _get_oauth_consumer,
    _to_garth_token,
)

CSRF_RE = re.compile(r'name="_csrf"\s+value="(.+?)"')
TITLE_RE = re.compile(r"<title>(.+?)</title>")
TICKET_RE = re.compile(r'embed\?ticket=([^"]+)"')

SSO_BASE = "https://sso.garmin.com/sso"
SSO_EMBED = f"{SSO_BASE}/embed"

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

IMPERSONATE = "chrome131"
TIMEOUT = 20


class GarminCloudflareBlocked(Exception):
    """Raised when the SSO response looks like a Cloudflare challenge page."""


class GarminLoginFailed(Exception):
    pass


class GarminRateLimited(GarminLoginFailed):
    """Raised when Garmin's SSO app returns its own 429 (account/IP cooldown).

    Distinct from a Cloudflare block — this is Garmin's own response
    (JSON body with a request-id), and clears on its own after a wait.
    """


def _looks_like_cf_challenge(resp) -> bool:
    if resp.status_code in (403, 429, 503):
        body = (resp.text or "")[:2000].lower()
        if "just a moment" in body or "cf-mitigated" in resp.headers.get("server", "").lower():
            return True
        if "cf-mitigated" in {k.lower() for k in resp.headers.keys()}:
            return True
    return False


def _check_app_rate_limit(resp, step: str) -> None:
    """Raise GarminRateLimited if Garmin's SSO app itself returned 429.

    The CF check above looks at HTML/headers; this one looks at Garmin's own
    JSON error envelope: {"error":{"status-code":"429","request-id":"..."}}.
    """
    if resp.status_code != 429:
        return
    if "application/json" not in resp.headers.get("content-type", "").lower():
        return
    request_id = ""
    try:
        body = resp.json()
        request_id = body.get("error", {}).get("request-id", "")
    except Exception:
        pass
    raise GarminRateLimited(
        f"Garmin returned 429 on {step} (request-id={request_id or '?'}). "
        "Account/IP is in cooldown — wait several minutes before retrying."
    )


def _extract(regex: re.Pattern, resp, what: str) -> str:
    text = resp.text or ""
    m = regex.search(text)
    if not m:
        snippet = text[:600].replace("\n", " ")
        ctype = resp.headers.get("content-type", "?")
        print(
            f"[curl_login] {what!r} not found. "
            f"url={resp.url} status={resp.status_code} ctype={ctype} "
            f"len={len(text)} body[:600]={snippet!r}",
            flush=True,
        )
        raise GarminLoginFailed(
            f"Couldn't find {what} in SSO response (status={resp.status_code}, len={len(text)})"
        )
    return m.group(1)


def curl_login(username: str, password: str) -> str:
    """Log in to Garmin SSO via curl_cffi and return a garth-compatible token."""
    consumer = _get_oauth_consumer()

    with cffi_requests.Session(impersonate=IMPERSONATE) as sess:
        # 1. Embed page — sets initial cookies.
        r = sess.get(f"{SSO_BASE}/embed", params=SSO_EMBED_PARAMS, timeout=TIMEOUT)
        if _looks_like_cf_challenge(r):
            raise GarminCloudflareBlocked(
                f"Cloudflare blocked GET /sso/embed (status={r.status_code})"
            )
        _check_app_rate_limit(r, "GET /sso/embed")
        embed_url = r.url

        # 2. Signin page — yields the CSRF token.
        r = sess.get(
            f"{SSO_BASE}/signin",
            params=SIGNIN_PARAMS,
            headers={"Referer": embed_url},
            timeout=TIMEOUT,
        )
        if _looks_like_cf_challenge(r):
            raise GarminCloudflareBlocked(
                f"Cloudflare blocked GET /sso/signin (status={r.status_code})"
            )
        _check_app_rate_limit(r, "GET /sso/signin")
        signin_url = r.url
        csrf = _extract(CSRF_RE, r, "_csrf")

        # 3. Submit credentials.
        r = sess.post(
            f"{SSO_BASE}/signin",
            params=SIGNIN_PARAMS,
            headers={"Referer": signin_url},
            data={
                "username": username,
                "password": password,
                "embed": "true",
                "_csrf": csrf,
            },
            timeout=TIMEOUT,
        )
        if _looks_like_cf_challenge(r):
            raise GarminCloudflareBlocked(
                f"Cloudflare blocked POST /sso/signin (status={r.status_code})"
            )
        _check_app_rate_limit(r, "POST /sso/signin")

        title = _extract(TITLE_RE, r, "<title>")
        if "MFA" in title:
            raise GarminLoginFailed(
                "MFA is required for this account; not supported by curl login yet"
            )
        if title != "Success":
            raise GarminLoginFailed(f"Unexpected SSO response title: {title!r}")

        ticket = _extract(TICKET_RE, r, "ticket")

    # 4. Exchange ticket for OAuth1, then OAuth2 — these endpoints aren't behind
    #    the same CF rule set, so plain requests via requests_oauthlib is fine.
    oauth1 = _exchange_ticket_for_oauth1(ticket, consumer)
    oauth2 = _exchange_oauth1_for_oauth2(oauth1, consumer)
    return _to_garth_token(oauth1, oauth2)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Garmin SSO login via curl_cffi")
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("-p", "--password", required=True)
    args = parser.parse_args()

    try:
        token = curl_login(args.username, args.password)
    except GarminCloudflareBlocked as e:
        print(f"CF block: {e}", file=sys.stderr)
        sys.exit(2)
    except GarminLoginFailed as e:
        print(f"Login failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(token)
