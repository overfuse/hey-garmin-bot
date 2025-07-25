import garth
from chatgpt import plan_to_json
from garmin_convert import convert

def login_to_garmin(login: str, password: str) -> str:
    garth.login(
        login,
        password,
    )
    return garth.client.dumps()

def upload_workout_to_garmin(token: str, workout_plan: str) -> str:
    workout_json = plan_to_json(workout_plan)
    garmin_json = convert(workout_json)
    garth.client.loads(token)
    result = garth.connectapi("/workout-service/workout", method="POST", json=garmin_json)
    return result["workoutId"]
