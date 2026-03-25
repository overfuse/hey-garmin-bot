import garth
import time
from chatgpt import plan_to_json, plan_to_json_async
from garmin_convert import convert
import asyncio

# Global lock + timestamp to throttle SSO login attempts.
# Garmin rate-limits by IP, so we serialize all logins and enforce a
# minimum gap regardless of how many users hit the bot concurrently.
_login_lock = asyncio.Lock()
_last_login_time: float = 0.0
_MIN_LOGIN_INTERVAL = 5.0  # seconds between SSO logins


async def login_to_garmin(login: str, password: str) -> str:
    global _last_login_time
    async with _login_lock:
        elapsed = time.time() - _last_login_time
        if elapsed < _MIN_LOGIN_INTERVAL:
            await asyncio.sleep(_MIN_LOGIN_INTERVAL - elapsed)

        def _do_login():
            garth.login(login, password)
            return garth.client.dumps()

        token = await asyncio.to_thread(_do_login)
        _last_login_time = time.time()
        return token

def token_from_session(session_path: str = "~/.garth") -> str:
    """Try to resume an existing session file; fall back to interactive login.

    Returns a serialized token suitable for garth.client.loads(token).
    """
    garth.resume(session_path)
    return garth.client.dumps()

def upload_workout_to_garmin(token: str, workout_plan: str) -> str:
    workout_json = plan_to_json(workout_plan)
    garmin_json = convert(workout_json)
    garth.client.loads(token)
    return upload_garmin_payload(token, garmin_json)

def upload_garmin_payload(token: str, garmin_json: dict) -> str:
    garth.client.loads(token)
    result = garth.connectapi("/workout-service/workout", method="POST", json=garmin_json)
    return result["workoutId"]


async def upload_workout_to_garmin_async(
    token: str, 
    workout_plan: str,
    user_id: int = None
) -> tuple[str, dict, float]:
    """
    Upload workout to Garmin asynchronously.
    
    Returns:
        Tuple of (workout_id, workout_json, processing_time_ms)
    """
    start_time = time.time()
    
    # Генерация workout -> garmin JSON асинхронно
    workout_json = await plan_to_json_async(workout_plan)
    garmin_json = convert(workout_json)
    
    # garth синхронный — выполняем в thread, чтобы не блокировать loop
    def _upload():
        garth.client.loads(token)
        res = garth.connectapi("/workout-service/workout", method="POST", json=garmin_json)
        return res["workoutId"]
    
    workout_id = await asyncio.to_thread(_upload)
    
    processing_time = (time.time() - start_time) * 1000  # Convert to ms
    
    return workout_id, workout_json, processing_time
