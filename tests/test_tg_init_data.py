"""tg_init_data.validate_init_data against synthesized Telegram payloads.

make_init_data builds initData exactly the way Telegram does (per
core.telegram.org/bots/webapps): URL-encoded fields plus an HMAC whose key is
derived from the bot token — so a payload signed here with the right token
must validate, and any mutation must not. test_webapp_server.py reuses the
helper to hit the API with authentic-looking requests.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from tg_init_data import InitDataError, validate_init_data

BOT_TOKEN = "12345:TEST-token_for-tests"


def make_init_data(
    bot_token: str = BOT_TOKEN,
    user_id: int = 42,
    auth_date: int | None = None,
    extra: dict | None = None,
    drop_user: bool = False,
) -> str:
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAF03QAAAAAAAA",
        **(extra or {}),
    }
    if not drop_user:
        fields["user"] = json.dumps({"id": user_id, "first_name": "Test", "language_code": "en"})
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_init_data_passes_and_decodes_user():
    fields = validate_init_data(make_init_data(user_id=777), BOT_TOKEN)
    assert fields["user"]["id"] == 777


def test_wrong_bot_token_rejected():
    with pytest.raises(InitDataError, match="hash mismatch"):
        validate_init_data(make_init_data(bot_token="999:other"), BOT_TOKEN)


def test_tampered_field_rejected():
    raw = make_init_data(user_id=42)
    tampered = raw.replace("query_id=AAF03QAAAAAAAA", "query_id=EVIL")
    with pytest.raises(InitDataError, match="hash mismatch"):
        validate_init_data(tampered, BOT_TOKEN)


def test_expired_auth_date_rejected():
    raw = make_init_data(auth_date=int(time.time()) - 7200)
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(raw, BOT_TOKEN, max_age_s=3600)


def test_fresh_auth_date_within_window_passes():
    raw = make_init_data(auth_date=int(time.time()) - 60)
    validate_init_data(raw, BOT_TOKEN, max_age_s=3600)


def test_missing_hash_rejected():
    with pytest.raises(InitDataError, match="no hash"):
        validate_init_data("auth_date=123&user=%7B%7D", BOT_TOKEN)


def test_missing_user_rejected():
    with pytest.raises(InitDataError, match="no user"):
        validate_init_data(make_init_data(drop_user=True), BOT_TOKEN)


def test_empty_inputs_rejected():
    with pytest.raises(InitDataError):
        validate_init_data("", BOT_TOKEN)
    with pytest.raises(InitDataError):
        validate_init_data(make_init_data(), "")


def test_signature_field_participates_in_hmac():
    # `signature` (the Ed25519 sibling) is a regular field for the HMAC check:
    # signed alongside → valid; altered afterwards → mismatch.
    raw = make_init_data(extra={"signature": "abc"})
    validate_init_data(raw, BOT_TOKEN)
    with pytest.raises(InitDataError, match="hash mismatch"):
        validate_init_data(raw.replace("signature=abc", "signature=xyz"), BOT_TOKEN)
