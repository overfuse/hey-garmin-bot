"""One-shot backfill: encrypt plaintext `garmin_auth` into `garmin_auth_enc`.

Run AFTER the dual-read code is deployed (get_garmin_token falls back to the
plaintext field, so this is safe to run while the bot is live) and BEFORE
removing that fallback. Safe to re-run; documents already migrated no longer
match the query.

Usage:
    uv run python scripts/backfill_token_encryption.py [--dry-run]

Requires MONGODB_URI and TOKEN_ENC_KEY in the environment (or .env).

NB: the plaintext is still in every existing backup. Encryption at rest is not
complete until backup rotation has aged those out — note the date this ran.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

import token_crypto  # noqa: E402
from user import users_col  # noqa: E402


async def main() -> None:
    token_crypto.init()
    if not token_crypto.enabled():
        raise SystemExit("token encryption is disabled — nothing to backfill into")

    dry_run = "--dry-run" in sys.argv
    migrated = 0
    async for doc in users_col.find({"garmin_auth": {"$exists": True}}):
        uid = doc["telegram_id"]
        blob = token_crypto.encrypt_token(uid, doc["garmin_auth"])
        if not dry_run:
            await users_col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"garmin_auth_enc": blob}, "$unset": {"garmin_auth": ""}},
            )
        migrated += 1
        print(f"{'would migrate' if dry_run else 'migrated'} telegram_id={uid}")

    print(f"done: {migrated} user(s) {'would be ' if dry_run else ''}migrated")


if __name__ == "__main__":
    asyncio.run(main())
