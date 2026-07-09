"""Validation of Telegram Mini App initData.

Implements the HMAC scheme from https://core.telegram.org/bots/webapps
("Validating data received via the Mini App"): the client sends the raw
`window.Telegram.WebApp.initData` query string, and we verify that Telegram
signed it with a key derived from our bot token. This is the ONLY
authentication the webapp API has — a request with valid initData is as
trusted as a chat message from that telegram user id, and a request without
it is nobody.

Stdlib only on purpose: the recipe is ~20 lines, and pulling a third-party
validator in would add a supply-chain dependency to the most
security-sensitive code path in the repo.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InitDataError(Exception):
    """initData missing, malformed, forged, or too old. Message is safe to
    log but should reach the client only as a generic 401."""


def validate_init_data(raw: str, bot_token: str, max_age_s: int = 3600) -> dict:
    """Verify raw initData and return its fields (with `user` JSON-decoded).

    Raises InitDataError unless ALL of: the hash matches the bot-token-derived
    HMAC, auth_date is within max_age_s, and a user id is present. max_age_s
    bounds the replay window — initData is a bearer credential for its whole
    lifetime, so keep the window at what a settings session actually needs.
    """
    if not raw:
        raise InitDataError("empty initData")
    if not bot_token:
        raise InitDataError("bot token not configured")

    try:
        fields = dict(parse_qsl(raw, keep_blank_values=True, strict_parsing=True))
    except ValueError as e:
        raise InitDataError(f"unparseable initData: {e}") from e

    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise InitDataError("initData has no hash")

    # data_check_string: remaining key=value pairs (URL-decoded), sorted by
    # key, newline-joined. Only `hash` is excluded — `signature` (the Ed25519
    # sibling) arrived as a regular field and stays in the string.
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, received_hash):
        raise InitDataError("hash mismatch")

    # Everything below runs on authenticated data — order matters, the HMAC
    # check must come first so a forger learns nothing from these errors.
    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, ValueError) as e:
        raise InitDataError("missing or non-numeric auth_date") from e
    if time.time() - auth_date > max_age_s:
        raise InitDataError("initData expired")

    try:
        fields["user"] = json.loads(fields["user"])
        int(fields["user"]["id"])
    except (KeyError, ValueError, TypeError) as e:
        raise InitDataError("initData has no user id") from e

    return fields
