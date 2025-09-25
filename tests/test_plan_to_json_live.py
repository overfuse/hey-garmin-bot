import json
import os
from pathlib import Path
from jsonschema import Draft7Validator


def _load(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_plan_to_json_live_matches_example_when_api_key_present():
    """
    Интеграционный тест БЕЗ моков. Требует OPENAI_API_KEY в окружении.
    Сравнивает результат plan_to_json с эталонным примером из examples/intervals/july-22.json.
    Если ключа нет — тест пропускается.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        import pytest
        pytest.skip("OPENAI_API_KEY is not set; skipping live OpenAI test")

    import chatgpt

    prompt_text = _load("examples/prompts/hey-track-07-22.txt")
    expected_json = json.loads(_load("examples/intervals/july-22.json"))
    schema = json.loads(_load("workout_schema.json"))

    # Генерация реального результата
    result = chatgpt.plan_to_json(prompt_text)

    # Валидация по схеме — должна проходить, иначе тест падает с описанием ошибок
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(result), key=lambda e: e.path)
    assert not errors, "Model output does not conform to workout_schema.json: \n" + "\n".join(
        f"- {'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors
    )

    # Сопоставление ключевых частей (допускаем нестрогое совпадение имени)
    # Проверяем warmup/cooldown/intervals структуру и типы шагов
    assert "intervals" in result and isinstance(result["intervals"], list)

    # Проверяем, что есть repeat-группа с 10 итерациями и ожидаемым набором шагов
    repeat_groups = [e for e in result["intervals"] if e.get("type") == "repeat"]
    assert repeat_groups, "Expected at least one repeat group"
    rg = repeat_groups[0]
    assert rg.get("repeat") == 10
    steps = rg.get("steps")
    assert isinstance(steps, list) and len(steps) == 4
    assert steps[0]["type"] == "run" and steps[0]["distance"] == 300
    assert steps[1]["type"] in ("run", "recovery") and steps[1]["distance"] == 100
    assert steps[2]["type"] == "run" and steps[2]["distance"] == 200
    assert steps[3]["type"] == "rest" and steps[3]["rest"] == 90


