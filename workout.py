#!/usr/bin/env python

import sys
import garth
from chatgpt import plan_to_json
from garmin_convert import convert
from typing import Dict, Any

def import_workout(plan: str) -> Dict[str, Any]:
    workout_json = plan_to_json(plan)
    garmin_json = convert(workout_json)
    return upload_workout(garmin_json)

def upload_workout(workout_data) -> Dict[str, Any]:
    garth.resume("~/.garth")
    return garth.connectapi("/workout-service/workout", method="POST", json=workout_data)

def main():
    workout_plan = sys.stdin.read()
    result = import_workout(workout_plan)
    print(f"View copy at https://connect.garmin.com/modern/workout/{result['workoutId']}")

if __name__ == "__main__":
    main()