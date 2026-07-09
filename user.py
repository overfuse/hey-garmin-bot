from pymongo.errors import DuplicateKeyError

import token_crypto
from audit import log_auth_event
from db import db

users_col = db["users"]

async def get_user(uid: int):
    return await users_col.find_one({"telegram_id": uid})

async def save_user(uid: int, data: dict):
    """Persist a user document. The Garmin token is encrypted on the way in.

    Callers keep passing plaintext under "garmin_auth"; with encryption enabled
    it is stored as "garmin_auth_enc" instead. replace_one drops any field the
    new document doesn't carry, so the plaintext field disappears from the
    stored doc in the same write — no separate $unset needed.
    """
    data = dict(data)
    data["telegram_id"] = uid
    token = data.pop("garmin_auth", None)
    if token is not None and token_crypto.enabled():
        data["garmin_auth_enc"] = token_crypto.encrypt_token(uid, token)
    elif token is not None:
        data["garmin_auth"] = token
    await users_col.replace_one({"telegram_id": uid}, data, upsert=True)

async def delete_user(uid: int):
    await users_col.delete_one({"telegram_id": uid})


def has_garmin_auth(user_data: dict) -> bool:
    return bool(user_data.get("garmin_auth_enc") or user_data.get("garmin_auth"))


async def get_garmin_token(user_data: dict) -> str | None:
    """Plaintext Garmin token for this user, decrypting when stored encrypted.

    Dual-read during the C migration: encrypted field wins, plaintext is the
    legacy fallback (upgraded to encrypted on the next save_user). Raises
    InvalidTag if the ciphertext was swapped between user documents — that is
    the AAD binding doing its job, not a case to paper over.
    """
    uid = user_data["telegram_id"]
    blob = user_data.get("garmin_auth_enc")
    if blob is not None:
        token = token_crypto.decrypt_token(uid, blob)
        await log_auth_event(uid, "token_decrypt")
        return token
    return user_data.get("garmin_auth")


async def _dedupe_users() -> None:
    """Collapse duplicate documents per telegram_id before the unique index lands.

    save_user's upsert had no unique index behind it, so concurrent /start could
    leave two docs for one user. Keep the authorized one, else the newest.
    """
    dupes = users_col.aggregate([
        {"$group": {"_id": "$telegram_id", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
    ])
    async for group in dupes:
        docs = await users_col.find({"telegram_id": group["_id"]}).to_list(length=None)
        docs.sort(key=lambda d: (d.get("state") == "authorized", d["_id"]), reverse=True)
        losers = [d["_id"] for d in docs[1:]]
        await users_col.delete_many({"_id": {"$in": losers}})
        print(f"[users] deduped telegram_id={group['_id']}: removed {len(losers)} doc(s)", flush=True)


async def create_indexes() -> None:
    """Unique index on telegram_id — makes save_user's upsert race-safe.

    If the deployed collection already holds duplicates, index creation fails
    with E11000; dedupe and retry rather than crashing the deploy.
    """
    try:
        await users_col.create_index("telegram_id", unique=True)
    except DuplicateKeyError:
        await _dedupe_users()
        await users_col.create_index("telegram_id", unique=True)
