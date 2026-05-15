#!/usr/bin/env python3
"""Generate a Garmin (garth-compatible) token on YOUR machine.

The bot runs on a cloud IP (Railway) that Garmin rate-limits (429) on the
OAuth token exchange. Run this locally instead: you sign in to Garmin in
your own browser (residential IP — not rate-limited; the password never
leaves your browser), this script does only the token exchange, and prints
a token string. Paste that whole string into the Telegram bot — it stores
it without calling Garmin at all.

    python make_garmin_token.py
    python make_garmin_token.py 'https://sso.garmin.com/sso/embed?ticket=ST-...'
"""

import re
import sys

# Light deps only (requests, requests_oauthlib; oauthlib/curl_cffi lazy).
from garmin_browser_auth import (
    _exchange_oauth1_for_oauth2_curl,
    _exchange_ticket_for_oauth1_curl,
    _get_oauth_consumer,
    _to_garth_token,
)

SSO_LOGIN_URL = (
    "https://sso.garmin.com/sso/embed"
    "?id=gauth-widget&embedWidget=true"
    "&gauthHost=https://sso.garmin.com/sso"
    "&clientId=GarminConnect&locale=en_US"
    "&service=https://sso.garmin.com/sso/embed"
    "&redirectAfterAccountLoginUrl=https://sso.garmin.com/sso/embed"
)
_TICKET_RE = re.compile(r"ST-[A-Za-z0-9._\-]+")


def main() -> int:
    if len(sys.argv) > 1:
        raw = sys.argv[1]
    else:
        print("1. Open this URL in your browser and sign in to Garmin:\n")
        print(f"   {SSO_LOGIN_URL}\n")
        print("2. After login the page shows a small JSON (or the address "
              "bar has ?ticket=ST-...).")
        raw = input("3. Paste the JSON or URL here, then press Enter:\n> ")

    m = _TICKET_RE.search(raw or "")
    if not m:
        print("No ST-... ticket found in that input.", file=sys.stderr)
        return 1
    ticket = m.group(0)

    consumer = _get_oauth_consumer()
    oauth1 = _exchange_ticket_for_oauth1_curl(ticket, consumer)
    oauth2 = _exchange_oauth1_for_oauth2_curl(oauth1, consumer)
    token = _to_garth_token(oauth1, oauth2)

    print("\n--- copy the entire line below and paste it into the bot ---\n")
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
