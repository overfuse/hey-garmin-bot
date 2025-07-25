import json

from dotenv import load_dotenv
import sys, os
from openai import OpenAI
from pathlib import Path

load_dotenv()
api_key = os.environ.get("OPENAI_API_KEY")

def plan_to_json(description: str) -> dict:
    client = OpenAI(api_key=api_key)

    workout_schema = Path("workout_schema.json").read_text(encoding="utf-8")

    system = {
        "role": "system",
        "content": (
            "You are a transform agent. "
            "Convert the workout text into JSON format.\n"
            "Return ONE JSON object, nothing else.\n"
            "Skip optional property if there's no value.\n"
            "Use the following JSON schema:\n"
            f"{workout_schema}"
         )
    }
    user = {
        "role": "user",
        "content": description,
        "response_format": {"type": "json_object"}
    }

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[system, user],
        temperature=0.0,
    )
    # Parse the returned JSON
    return json.loads(completion.choices[0].message.content)

def read_stdin():
    print("Paste/type your workout. Press Ctrl-D (Unix) or Ctrl-Z (Windows) then Enter to finish:")
    return sys.stdin.read()

if __name__ == "__main__":
    content = sys.stdin.read()
    workout_json = plan_to_json(content)
    print(workout_json)