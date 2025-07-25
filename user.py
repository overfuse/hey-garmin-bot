import os
import motor.motor_asyncio

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
db           = mongo_client["hey_garmin"]
users_col    = db["users"]

async def get_user(uid: int):
    return await users_col.find_one({"telegram_id": uid})

async def save_user(uid: int, data: dict):
    data["telegram_id"] = uid
    await users_col.replace_one({"telegram_id": uid}, data, upsert=True)

async def delete_user(uid: int):
    await users_col.delete_one({"telegram_id": uid})