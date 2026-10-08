# hey-garmin-bot

Telegram bot that turns free-text workout descriptions into structured Garmin
workouts via an LLM (`workout_ai/`, prod default: anthropic/claude-haiku-5-5).

## Evals

Before running `evals/` or editing `SYSTEM_PROMPT*.md`, read
[evals/CLAUDE.md](evals/CLAUDE.md). The short version: routine updates need
only `EVAL_RUNS=2 uv run python -m evals.run haiku-5.5`; only a check at 0/2 blocks
(models are nondeterministic — ~98% is baseline, single flakes are noise, and
benchmarks comparing variants need `EVAL_RUNS=6+` on both arms).
