import json

from dotenv import load_dotenv
import sys, os
from openai import OpenAI
from openai import AsyncOpenAI
from pathlib import Path
from jsonschema import Draft7Validator

load_dotenv()
api_key = os.environ.get("OPENAI_API_KEY")

def plan_to_json(description: str) -> dict:
    client = OpenAI(api_key=api_key)

    schema_text = Path("workout_schema.json").read_text(encoding="utf-8")
    workout_schema = json.loads(schema_text)
    system_prompt = Path("SYSTEM_PROMPT.md").read_text(encoding="utf-8")
    system_with_schema = (
        f"{system_prompt}\n\nUse the following JSON Schema (Draft-07). Output must strictly conform to it:\n{schema_text}"
    )

    def _request(messages):
        return client.chat.completions.create(
            model="gpt-5-mini",
            messages=messages,
            max_completion_tokens=1200,
            seed=42,
            response_format={"type": "json_object"},
        )

    # First attempt
    messages = [
        {"role": "system", "content": system_with_schema},
        {"role": "user", "content": description},
    ]
    completion = _request(messages)
    draft = json.loads(completion.choices[0].message.content)

    # Validate locally against schema
    validator = Draft7Validator(workout_schema)
    errors = sorted(validator.iter_errors(draft), key=lambda e: e.path)
    if not errors:
        return draft

    # Retry once with validation errors to force correction
    err_msgs = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors]
    repair_instruction = (
        "Your previous output did not validate against the schema. "
        "Fix ALL issues below and re-output ONE valid JSON object only. Do not add explanations.\n"
        + "\n".join(f"- {m}" for m in err_msgs)
    )
    messages_repair = [
        {"role": "system", "content": system_with_schema},
        {"role": "user", "content": description},
        {"role": "system", "content": repair_instruction},
    ]
    completion2 = _request(messages_repair)
    fixed = json.loads(completion2.choices[0].message.content)

    # Final validation; raise with details if still invalid
    errors2 = sorted(validator.iter_errors(fixed), key=lambda e: e.path)
    if errors2:
        details = "\n".join(f"- {'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors2)
        raise ValueError(f"Model output does not conform to workout_schema.json:\n{details}")
    return fixed


async def plan_to_json_async(description: str) -> dict:
    client = AsyncOpenAI(api_key=api_key)

    schema_text = Path("workout_schema.json").read_text(encoding="utf-8")
    workout_schema = json.loads(schema_text)
    system_prompt = Path("SYSTEM_PROMPT.md").read_text(encoding="utf-8")
    system_with_schema = (
        f"{system_prompt}\n\nUse the following JSON Schema (Draft-07). Output must strictly conform to it:\n{schema_text}"
    )

    async def _request(messages):
        return await client.chat.completions.create(
            model="gpt-5-mini",
            messages=messages,
            max_completion_tokens=1200,
            seed=42,
            response_format={"type": "json_object"},
        )

    messages = [
        {"role": "system", "content": system_with_schema},
        {"role": "user", "content": description},
    ]
    completion = await _request(messages)
    draft = json.loads(completion.choices[0].message.content)

    validator = Draft7Validator(workout_schema)
    errors = sorted(validator.iter_errors(draft), key=lambda e: e.path)
    if not errors:
        return draft

    err_msgs = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors]
    repair_instruction = (
        "Your previous output did not validate against the schema. "
        "Fix ALL issues below and re-output ONE valid JSON object only. Do not add explanations.\n"
        + "\n".join(f"- {m}" for m in err_msgs)
    )
    messages_repair = [
        {"role": "system", "content": system_with_schema},
        {"role": "user", "content": description},
        {"role": "system", "content": repair_instruction},
    ]
    completion2 = await _request(messages_repair)
    fixed = json.loads(completion2.choices[0].message.content)

    errors2 = sorted(validator.iter_errors(fixed), key=lambda e: e.path)
    if errors2:
        details = "\n".join(f"- {'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors2)
        raise ValueError(f"Model output does not conform to workout_schema.json:\n{details}")
    return fixed

def read_stdin():
    print("Paste/type your workout. Press Ctrl-D (Unix) or Ctrl-Z (Windows) then Enter to finish:")
    return sys.stdin.read()

if __name__ == "__main__":
    content = sys.stdin.read()
    workout_json = plan_to_json(content)
    print(workout_json)