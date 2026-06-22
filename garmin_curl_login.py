"""
Garmin "widget+cffi" SSO login via curl_cffi with Chrome TLS/HTTP2 impersonation.

This is the no-browser, no-proxy auth path. It drives the legacy HTML embed
*widget* SSO flow (/sso/embed -> /sso/signin form POST with a _csrf token and
**no** clientId param). That path sidesteps Garmin's per-clientId 429 rate
limiter that broke the mobile/portal JSON endpoints (and deprecated garth) in
2026 — the same trick python-garminconnect's `widget+cffi` strategy uses.

The standard `garth` login uses requests/urllib3, whose JA3 fingerprint is
trivially classified as "Python script" by Cloudflare. From cloud egress IPs
(Railway, Heroku, etc.) Garmin's CF rules then return 429/403 on /sso/signin,
even though the same code works from a residential IP. curl_cffi wraps
curl-impersonate, so the TLS handshake and HTTP/2 SETTINGS frames look
identical to a real Chrome browser. The whole flow — SSO login *and* the
OAuth1/OAuth2 exchange against connectapi.garmin.com — runs over curl_cffi, so
no Cloudflare Worker proxy is needed for the exchange step.

Usage (standalone):
    python garmin_curl_login.py -u EMAIL -p PASS          # prompts for MFA if needed
    python garmin_curl_login.py -u EMAIL -p PASS --no-mfa # fail instead of prompting
"""

import re
import sys

from curl_cffi import requests as cffi_requests

from garmin_browser_auth import (
    _exchange_oauth1_for_oauth2_curl,
    _exchange_ticket_for_oauth1_curl,
    _get_oauth_consumer,
    _to_garth_token,
)

CSRF_RE = re.compile(r'name="_csrf"\s+value="(.+?)"')
TITLE_RE = re.compile(r"<title>(.+?)</title>")
TICKET_RE = re.compile(r'embed\?ticket=([^"]+)"')

SSO_BASE = "https://sso.garmin.com/sso"
SSO_EMBED = f"{SSO_BASE}/embed"
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


class GarminInvalidCredentials(GarminLoginFailed):
    """Raised when Garmin rejects the email/password (HTTP 401 on signin)."""


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


def _submit_mfa(sess, mfa_page_html: str, code: str):
    """POST an MFA code and return the resulting response (success page).

    Garmin renders a fresh CSRF token on the MFA page, so re-extract it from
    the page HTML before posting the code.
    """
    csrf = _extract(CSRF_RE, _FakeResp(mfa_page_html), "_csrf (MFA page)")
    r = sess.post(
        SSO_MFA,
        params=SIGNIN_PARAMS,
        headers={"Referer": f"{SSO_BASE}/signin"},
        data={
            "mfa-code": code,
            "embed": "true",
            "_csrf": csrf,
            "fromPage": "setupEnterMfaCode",
        },
        timeout=TIMEOUT,
    )
    if _looks_like_cf_challenge(r):
        raise GarminCloudflareBlocked(
            f"Cloudflare blocked POST {SSO_MFA} (status={r.status_code})"
        )
    _check_app_rate_limit(r, "POST verifyMFA")
    return r


class _FakeResp:
    """Minimal shim so _extract (which expects a response) can scan raw HTML."""

    def __init__(self, text: str):
        self.text = text
        self.url = "(mfa-page)"
        self.status_code = 200
        self.headers = {}


def _diagnose_login_failure(r, title: str) -> None:
    """Log the signin response body and raise a specific GarminLoginFailed."""
    body = r.text or ""
    low = body.lower()
    snippet = body[:1500].replace("\n", " ")
    print(
        f"[curl_login] login yielded no ticket. url={r.url} "
        f"status={r.status_code} title={title!r} len={len(body)} "
        f"body[:1500]={snippet!r}",
        flush=True,
    )
    # 401 on the signin POST is Garmin's "bad email/password". (The page always
    # links reCaptchaUtil.js, so a "recaptcha" substring is NOT a captcha
    # signal — only an actually rendered widget is.)
    if r.status_code == 401:
        raise GarminInvalidCredentials("Incorrect Garmin email or password.")
    if 'class="g-recaptcha"' in low or "data-sitekey" in low:
        raise GarminLoginFailed(
            "Garmin is asking for a CAPTCHA, which the automated login can't "
            "solve. Try again later, or use the browser sign-in method."
        )
    raise GarminLoginFailed(
        f"Garmin sign-in failed (status {r.status_code}). Please try again."
    )


def curl_login(username: str, password: str, mfa_callback=None) -> str:
    """Log in to Garmin SSO via curl_cffi and return a garth-compatible token.

    `mfa_callback` is called with no args and must return the MFA code as a
    string when Garmin requests one. If omitted and MFA is required, raises
    GarminLoginFailed (the bot's server-side path can't prompt interactively).
    """
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
        if "MFA" in title.upper():
            if mfa_callback is None:
                raise GarminLoginFailed(
                    "MFA is required for this account but no mfa_callback was "
                    "provided (server-side login can't prompt for a code)"
                )
            r = _submit_mfa(sess, r.text, mfa_callback())
            title = _extract(TITLE_RE, r, "<title> (post-MFA)")

        # Success is decided by the presence of the service ticket, not the page
        # title — Garmin's titles drift. The signin page itself is titled
        # "GARMIN Authentication Application" and comes back (no ticket) on bad
        # creds or when a CAPTCHA is required.
        m = TICKET_RE.search(r.text or "")
        if not m:
            _diagnose_login_failure(r, title)  # always raises
        ticket = m.group(1)

    # 4. Exchange ticket -> OAuth1 -> OAuth2, also over curl_cffi impersonation
    #    so the exchange endpoints clear Cloudflare without a proxy. The ticket
    #    is scoped to /sso/embed (SIGNIN_PARAMS' service), which is the curl
    #    exchange functions' default login_url.
    oauth1 = _exchange_ticket_for_oauth1_curl(ticket, consumer)
    oauth2 = _exchange_oauth1_for_oauth2_curl(oauth1, consumer)
    return _to_garth_token(oauth1, oauth2)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Garmin SSO login via curl_cffi")
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("-p", "--password", required=True)
    parser.add_argument(
        "--no-mfa",
        action="store_true",
        help="fail instead of prompting if the account requires MFA",
    )
    args = parser.parse_args()

    mfa_cb = None if args.no_mfa else (lambda: input("Enter Garmin MFA code: ").strip())
    try:
        token = curl_login(args.username, args.password, mfa_callback=mfa_cb)
    except GarminCloudflareBlocked as e:
        print(f"CF block: {e}", file=sys.stderr)
        sys.exit(2)
    except GarminLoginFailed as e:
        print(f"Login failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(token)
