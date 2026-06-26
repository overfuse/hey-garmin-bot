# Workout-parsing evals

A check-based evaluation that runs the same workout prompts through several LLM
providers and scores how well each turns free-text workouts into the structured
`Workout` schema (`workout_ai/models.py`). The checks encode the specific failure
modes found while building the parser — dropped paces, mis-budgeted distances,
flaky rep counts, rest placed outside the repeat, and second/metre unit confusion.

## Quick start

Run from the repo root:

```bash
uv run python -m evals.run                 # every model that has a provider key set
uv run python -m evals.run haiku gpt-5     # filter models by label substring
EVAL_RUNS=3 uv run python -m evals.run     # repeat each case N times (recommended)
```

Keys are read from `.env`: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and optionally
`GEMINI_API_KEY`. A model whose key is absent is skipped (printed in the header).

> The eval drives each model **directly** with the right params for its family. It
> does **not** read `WORKOUT_AI_PROVIDER` / `WORKOUT_AI_MODEL` — those select the
> production provider, and are unrelated to which models the eval compares.

## How scoring works

- A **case** (`cases.py`) is a prompt plus a list of named **checks**. Each check
  inspects the parsed result dict and returns pass/fail for one property
  (e.g. "slow leg keeps its pace", "1 km of 200/200 = exactly 10×200 m segments").
- **Score = fraction of checks passed** — partial credit, so a model that gets the
  structure right but one pace wrong still scores. The report shows per-case results
  and a leaderboard summing checks across all cases, plus average latency.
- Checks are deliberately lenient where the input is genuinely ambiguous (e.g. the
  09-30 rep count accepts `4` or `5`) and strict where there is one right answer.

### Nondeterminism — run more than once

Reasoning models (o3, gpt-5) and thinking models (Claude) do **not** support a
seed, so their output varies run to run. A single run has been observed to swing a
model's score by ~25 points. **Use `EVAL_RUNS=3+` before trusting a ranking.** With
`EVAL_RUNS>1` each cell shows `passed/total` runs so you can see the flakiness.
`gpt-4.1-mini` is the only deterministic model here (temperature 0 + fixed seed).

## Output

- A per-case table and a leaderboard to stdout.
- Every raw parsed output (or error) written to `evals/last_results.json`. Inspect with:

  ```bash
  jq '.[] | select(.case=="200/200-budget" and .model=="anthropic/haiku-4.5") | .output' evals/last_results.json
  ```

## Models (`models.py`)

One runner per API family, because the call shape differs:

| family | example models | params |
|---|---|---|
| openai chat | `gpt-4.1-mini` | `temperature=0`, `seed`, `max_tokens` |
| openai reasoning | `gpt-5-mini`, `o3-mini` | `reasoning_effort`, `max_completion_tokens` (no temperature/seed) |
| anthropic thinking | `claude-haiku-4-5`, `claude-sonnet-4-6` | extended thinking, `max_tokens` |
| gemini (openai-compatible) | `gemini-2.5-flash` | OpenAI SDK pointed at Google's endpoint |

**Add a model**: append a `ModelSpec(label, model_id, runner, api_key_env)` to
`MODELS`. It is skipped automatically unless `api_key_env` is set, so you can list
providers you don't have keys for. The whole suite shares one `SYSTEM_PROMPT.md` —
the same prompt production uses.

## Cases (`cases.py`)

Five non-redundant cases, each probing a distinct skill:

| case | source | probes |
|---|---|---|
| `layered-slashes` | inline | flat skeleton (`4000/500/2000/...`) + binding pace-annotation lines, no duplication |
| `subdivide+strides` | inline | subdividing a rep (`1500` → `1000+500`) inside a repeat, plus on/off easy recovery |
| `200/200-budget` | inline | distance-budgeted alternation (`1 km of 200/200` = 5×200 m) + fast/slow paces |
| `explicit-10x+rest` | `examples/prompts/hey-track-07-22.txt` | a large **exact** rep count (10) + distance recovery + rest inside the repeat |
| `real-set-09-30` | `examples/prompts/hey-track-09-30.txt` | **time**-based active recovery + **ambiguous** count (4–5) + a "2 min" rest (sec≠metre trap) |

The two real cases load their prompt straight from `examples/prompts/`, so editing
those files updates the eval.

## Which example files are used?

Only the two **input prompts** are eval inputs:

| file | used? | why |
|---|---|---|
| `prompts/hey-track-07-22.txt` | ✅ case `explicit-10x+rest` | real user prompt |
| `prompts/hey-track-09-30.txt` | ✅ case `real-set-09-30` | real user prompt |
| `intervals/july-22.json` | ➖ not here | reference output for the **live test** (`tests/test_plan_to_json_live.py`), not the eval |
| `workouts/fartlek.json` | ❌ | Garmin **output** format, no source prompt |
| `workouts/hey-track-06-30.json` | ❌ | Garmin output, no prompt; also a dup of `-clean` |
| `workouts/hey-track-06-30-clean.json` | ❌ | Garmin output (two fartlek blocks), no prompt |
| `workouts/hey-track-07-15.json` | ❌ | Garmin output (ladder 2k-1.6k-800-400), no prompt |
| `workouts/hey-track-07-22.json` | ❌ | Garmin output of 07-22 (its prompt is already a case) |
| `workouts/hey-track-07-22-convert.json` | ❌ | converted dup of 07-22 |

Everything under `workouts/` is **Garmin output JSON with no matching input prompt**,
so it can't be an eval case without inventing the natural-language input. Two of
those represent patterns the suite does **not** otherwise cover — the **ladder**
(`07-15`) and the **multi-block fartlek** (`06-30`). If you want them evaluated,
add the real prompts that produced them to `examples/prompts/` and wire them up as
new cases.
