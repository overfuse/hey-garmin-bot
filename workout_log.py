import os
from datetime import datetime
from typing import Optional
import motor.motor_asyncio

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
db = mongo_client["hey_garmin"]
workout_logs_col = db["workout_logs"]


async def log_workout_request(
    user_id: int,
    prompt: str,
    workout_json: Optional[dict] = None,
    garmin_workout_id: Optional[str] = None,
    error: Optional[str] = None,
    processing_time_ms: Optional[float] = None
) -> str:
    """
    Log a workout generation request with its result.
    
    Returns:
        The inserted document ID as string
    """
    log_entry = {
        "user_id": user_id,
        "prompt": prompt,
        "timestamp": datetime.utcnow(),
        "success": error is None,
        "workout_json": workout_json,
        "garmin_workout_id": garmin_workout_id,
        "error": error,
        "processing_time_ms": processing_time_ms,
    }
    
    result = await workout_logs_col.insert_one(log_entry)
    return str(result.inserted_id)


async def get_user_workout_history(user_id: int, limit: int = 10) -> list:
    """
    Get recent workout generation history for a user.
    """
    cursor = workout_logs_col.find(
        {"user_id": user_id}
    ).sort("timestamp", -1).limit(limit)
    
    return await cursor.to_list(length=limit)


async def get_workout_stats(user_id: int) -> dict:
    """
    Get statistics for user's workout generations.
    """
    pipeline = [
        {"$match": {"user_id": user_id}},
        {
            "$group": {
                "_id": None,
                "total": {"$sum": 1},
                "successful": {"$sum": {"$cond": ["$success", 1, 0]}},
                "failed": {"$sum": {"$cond": ["$success", 0, 1]}},
                "avg_processing_time": {"$avg": "$processing_time_ms"}
            }
        }
    ]
    
    result = await workout_logs_col.aggregate(pipeline).to_list(length=1)
    
    if not result:
        return {
            "total": 0,
            "successful": 0,
            "failed": 0,
            "avg_processing_time": 0
        }
    
    stats = result[0]
    return {
        "total": stats.get("total", 0),
        "successful": stats.get("successful", 0),
        "failed": stats.get("failed", 0),
        "avg_processing_time": round(stats.get("avg_processing_time", 0), 2)
    }


async def create_indexes() -> None:
    """
    Create indexes for efficient workout log queries.
    """
    # Index on user_id and timestamp for efficient user history queries
    await workout_logs_col.create_index([("user_id", 1), ("timestamp", -1)])
    # Index on timestamp for general time-based queries
    await workout_logs_col.create_index("timestamp")
