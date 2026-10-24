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
uv run python -m evals.run haiku -c 200    # filter cases by name substring (repeatable)
EVAL_RUNS=3 uv run python -m evals.run     # repeat each case N times (recommended)
EVAL_RUNS=2 uv run python -m evals.run haiku --adaptive  # extra runs only for failing cases
uv run python -m evals.run haiku --history # accumulated results from history.jsonl, no calls
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

- Every run also appended to `evals/history.jsonl` (one JSON object per line, with
  `model_id`, `prompt_sha`, `case_sha`, `batch`, `ts`, `adaptive`). The report ends
  with a history section that sums every recorded run matching the current prompt
  and case hashes, so results accumulate across batches until either one changes.

## Models (`models.py`)

One runner per API family, because the call shape differs:

| family | example models | params |
|---|---|---|
| openai chat | `gpt-4.1-mini` | `temperature=0`, `seed`, `max_tokens` |
| openai reasoning | `gpt-6-luna`, `gpt-5.6-luna`, `gpt-5-mini`, `o3-mini` | `reasoning_effort`, `max_completion_tokens` (no temperature/seed) |
| anthropic thinking | `claude-haiku-5-5`, `claude-haiku-4-5`, `claude-sonnet-4-6` | extended thinking (adaptive on 5.x), `max_tokens` |
| gemini (openai-compatible) | `gemini-2.5-flash` | OpenAI SDK pointed at Google's endpoint |

**Add a model**: append a `ModelSpec(label, model_id, runner, api_key_env)` to
`MODELS`. It is skipped automatically unless `api_key_env` is set, so you can list
providers you don't have keys for. The suite sends each model the same prompt
production would: the full `SYSTEM_PROMPT.md` for chat models (and Claude), the
condensed `SYSTEM_PROMPT_REASONING.md` for OpenAI reasoning models — selection
comes from the production provider's `wants_reasoning_prompt`, not from the eval.

## Cases (`cases.py`)

Eleven cases, each probing a distinct skill (pruned 2026-10-24: a case that is a
subset of another costs a call per run and adds no detection power):

| case | source | probes |
|---|---|---|
| `subdivide+strides` | inline | subdividing a rep (`1500` → `1000+500`) inside a repeat, plus on/off easy recovery |
| `200/200-budget` | inline | distance-budgeted alternation with a leftover (`1 km of 200/200` = 2 pairs + 200) + fast/slow paces |
| `real-set-09-30` | `examples/prompts/hey-track-09-30.txt` | **time**-based active recovery + **ambiguous** count (4–5) + rest inside the repeat |
| `breaks-between-runs` | inline | strength lines as `break` steps, interior easy km kept, `5 раз 200/200` as a rep count |
| `unitless-km-skeleton` | inline | `6/2/6/4/6/2` skeleton with fewer annotation lines than segments |
| `pace-ceiling-25x400` | inline | "чуть из 4 мин" → 03:55, a large exact rep count, a rest range |
| `rep-pair-single-pace+walk-jog` | inline | pace binds to the work leg only; timed walk/jog → rest |
| `km-skeleton+budget` | inline | skeleton segments expanded in place by a distance budget |
| `minutes-skeleton+pace-range` | inline | skeleton in minutes, timed warmup/cooldown, pace range kept |
| `timed-reps+distance` | inline | timed work + recovery **inside** a repeat, then a distance step |
| `not-a-workout` | inline | chatter must yield an empty workout, not an invented one |

Warmup/cooldown is only checked where it is the hard part (`breaks-between-runs`,
`km-skeleton+budget`, `minutes-skeleton+pace-range`); elsewhere it always passed
and only padded the score. The leaderboard also reports the share of case-runs
with every check green — the number that matches "the watch workout is right".

The real case loads its prompt straight from `examples/prompts/`, so editing that
file updates the eval.

## Which example files are used?

Of the example files, only one input prompt is an eval input:

| file | used? | why |
|---|---|---|
| `prompts/hey-track-07-22.txt` | ❌ | real user prompt; its case was dropped (10× + rest inside is covered by `pace-ceiling-25x400` and `real-set-09-30`) |
| `prompts/hey-track-09-30.txt` | ✅ case `real-set-09-30` | real user prompt |
| `intervals/july-22.json` | ❌ | reference output of 07-22; the live pytest that used it was removed |
| `workouts/fartlek.json` | ❌ | Garmin **output** format, no source prompt |
| `workouts/hey-track-06-30.json` | ❌ | Garmin output, no prompt; also a dup of `-clean` |
| `workouts/hey-track-06-30-clean.json` | ❌ | Garmin output (two fartlek blocks), no prompt |
| `workouts/hey-track-07-15.json` | ❌ | Garmin output (ladder 2k-1.6k-800-400), no prompt |
| `workouts/hey-track-07-22.json` | ❌ | Garmin output of 07-22 |
| `workouts/hey-track-07-22-convert.json` | ❌ | converted dup of 07-22 |

Everything under `workouts/` is **Garmin output JSON with no matching input prompt**,
so it can't be an eval case without inventing the natural-language input. Two of
those represent patterns the suite does **not** otherwise cover — the **ladder**
(`07-15`) and the **multi-block fartlek** (`06-30`). If you want them evaluated,
add the real prompts that produced them to `examples/prompts/` and wire them up as
new cases.
