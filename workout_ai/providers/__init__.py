from . import claude, openai

# Each provider module exposes NAME, DEFAULT_MODEL, and an async plan() with the
# signature plan(system_prompt, description, model) -> Workout. To add a provider,
# drop a new module here and append it to _MODULES.
_MODULES = (openai, claude)

REGISTRY = {module.NAME: module for module in _MODULES}
