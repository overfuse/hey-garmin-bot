# hey-garmin-bot

Telegram bot that turns free-text workout descriptions into structured Garmin
workouts via an LLM (`workout_ai/`, prod default: anthropic/claude-haiku-5-5).

## Mini App compatibility

Mini App changes (`webapp/`, `webapp_server.py`, `prefs.py`) must stay
backward compatible: Telegram caches the page, so an older `app.js` keeps
talking to the newest server. Adding a pref must not break an old client —
`PUT /api/prefs` merges over stored prefs, so omitted keys keep their value.
Never require a new key, rename or remove a key, or change a key's meaning;
add a new key with a default instead. Cover it with an "older client" test
in `tests/test_webapp_server.py`.

## Evals

Before running `evals/` or editing `SYSTEM_PROMPT*.md`, read
[evals/CLAUDE.md](evals/CLAUDE.md). The short version: routine updates need
only `EVAL_RUNS=2 uv run python -m evals.run haiku-5.5 --adaptive`; only a check at 0/2 blocks
(models are nondeterministic — ~98% is baseline, single flakes are noise, and
benchmarks comparing variants need `EVAL_RUNS=6+` on both arms).
