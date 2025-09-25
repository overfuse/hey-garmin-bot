import garth
from chatgpt import plan_to_json, plan_to_json_async
from garmin_convert import convert
import asyncio

def login_to_garmin(login: str, password: str) -> str:
    garth.login(
        login,
        password,
    )
    return garth.client.dumps()

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


async def upload_workout_to_garmin_async(token: str, workout_plan: str) -> str:
    # Генерация workout -> garmin JSON асинхронно
    workout_json = await plan_to_json_async(workout_plan)
    garmin_json = convert(workout_json)
    # garth синхронный — выполняем в thread, чтобы не блокировать loop
    def _upload():
        garth.client.loads(token)
        res = garth.connectapi("/workout-service/workout", method="POST", json=garmin_json)
        return res["workoutId"]
    return await asyncio.to_thread(_upload)
