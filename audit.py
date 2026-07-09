"""Append-only auth audit trail.

Answers the question an incident will ask: who logged in, refreshed, decrypted,
or logged out — and when. Events are inserted once and never updated.

Never store the token, the ciphertext, or the password here. `detail` is for
exception class names and short tags, not payloads — stdout hygiene in bot.py's
text handler applies to this collection too.

Writes are best-effort: a broken audit write must not take down a login or an
upload, so failures are printed loudly and swallowed.
"""

import os
from datetime import datetime, timezone
from typing import Optional

from db import db

auth_events_col = db["auth_events"]

RETENTION_DAYS = int(os.getenv("AUTH_EVENTS_RETENTION_DAYS", "365"))

EVENTS = frozenset(
    {"login_success", "login_failure", "token_refresh", "token_decrypt", "logout"}
)


async def log_auth_event(
    telegram_id: int,
    event: str,
    outcome: str = "ok",
    detail: Optional[str] = None,
) -> None:
    assert event in EVENTS, f"unknown auth event: {event}"
    try:
        await auth_events_col.insert_one(
            {
                "telegram_id": telegram_id,
                "event": event,
                "ts": datetime.now(timezone.utc),
                "outcome": outcome,
                "detail": detail,
            }
        )
    except Exception as e:
        print(f"⚠️  auth event write failed ({event}, user={telegram_id}): {e}", flush=True)


async def create_indexes() -> None:
    await auth_events_col.create_index("ts", expireAfterSeconds=RETENTION_DAYS * 86400)
    await auth_events_col.create_index([("telegram_id", 1), ("ts", -1)])
