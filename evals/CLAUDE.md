# Running evals efficiently (guide for agents)

How to use `evals/` without wasting runs. Mechanics of the harness are in
[README.md](README.md); this file is about *when to run what* and *how to read
the numbers*.

## Commands

```bash
uv run python -m evals.run haiku-5.5              # filter by model label substring
uv run python -m evals.run haiku-5.5 -c 200 -c km # ...and by case-name substring
EVAL_RUNS=2 uv run python -m evals.run haiku-5.5  # N runs per case
EVAL_RUNS=2 uv run python -m evals.run haiku-5.5 --adaptive  # re-run only failures
uv run python -m evals.run haiku-5.5 --history    # accumulated results, no calls
```

Scale (Luna, 2026-08): one run = 7 cases ≈ 27 checks, ~5 s/call. A 6-run batch
is 42 calls ≈ 1 min wall / ~$0.15. Cost is time and attention, not money.
(The suite has grown to 12 cases since.)

When iterating on one rule, filter to the case(s) it affects with `-c`, then
finish with a full-suite run before committing.

## The two questions — and the two run sizes

**"Did my edit break something?" → `EVAL_RUNS=2 --adaptive`, prod model only.**
This is the routine per-update check. `--adaptive` runs every case once and
re-runs only cases with a failure, so clean cases cost one call. A systematic
failure fails the first run too, so this still catches every 0/2. Its
leaderboard % is biased low (clean cells ran once), so don't compare it to a
uniform batch. Only a *systematic* failure blocks: a
check at 0/2 that passed before. A 1/2 flake is noise — do not iterate on it.

**"What is the true score / is variant A better?" → `EVAL_RUNS=6+`, rarely.**
Only for deliberate benchmarks (new model, prompt A/B, release gate). Both arms
at the same n, and never `--adaptive`. Do not conclude a winner unless the gap is >4 points; anything
in the 96–100% band is a tie.

## Reading the numbers honestly

- Every model in prod use is nondeterministic (reasoning/thinking models take
  no seed; even seeded gpt-4.1-mini is bimodal across API batches).
- Baseline (2026-08-03): all of luna / haiku / 4.1-mini sit at ~96–99% with
  occasional one-off flakes. **~98% is normal, not a regression.**
- A clean small batch is weak evidence: at a true 98% per-check rate, 3 clean
  runs happen ~19% of the time. Never claim "100%" from n≤3.
- Distinguishing 98% from 94% needs ~350 checks per arm (~14 batches). If a
  difference needs that many runs to see, it is too small to act on.
- Some flakes are genuine input ambiguity (e.g. "После 5 раз 100/100" parses
  two ways). More runs measure the mixture ratio; they can't remove it.

## History

Every run is appended to `evals/history.jsonl` (gitignored), keyed by model,
`prompt_sha` (the system prompt that was sent) and `case_sha` (case input plus
check labels and check source). After each batch, and on `--history` without
any calls, the report sums every recorded run that matches the *current*
prompt and case versions. Confidence accumulates across sessions, and an
unchanged configuration never needs re-measuring: check `--history` before
starting a benchmark. Editing a prompt or case starts that key over. Edits to
helpers that checks call are not hashed, so after changing one, trust only
batches run since. Records carry `adaptive: true` when they came from an
adaptive batch.

## After a batch

Inspect failures before touching anything:

```bash
jq '[.[] | select(.case=="<case>" and .model=="<label>") | {run, checks}]' evals/last_results.json
jq '[.[] | select(.case=="<case>" and .run==<n>) | .output.intervals]' evals/last_results.json
```

Fix systematic failures with a *concrete example* in the prompt — abstract
rules alone have lost to examples every time (dropping the 1500→1000+500
subdivision example broke that case 0/3; restoring it fixed it).

## Invariants (do not regress these)

- The eval sends what production sends: token caps and prompt selection are
  imported from `workout_ai/providers/*` (`ModelSpec.reasoning_prompt` comes
  from the provider's `wants_reasoning_prompt`). Never hardcode either here.
- Prompt edits: SYSTEM_PROMPT.md serves chat models (and Claude),
  SYSTEM_PROMPT_REASONING.md serves OpenAI reasoning models — an edit to one
  does not cover the other; smoke-test whichever variant(s) changed.

## Improvement backlog (revised 2026-10-24, highest impact first)

Done: case filter (`-c`), `history.jsonl` accumulation (`--history`), adaptive
re-runs (`--adaptive`), case pruning + whole-case pass rate (2026-10-24).

1. **Reject non-workout input** — `not-a-workout` is 1/2 on haiku-5.5:
   structured output forces a `Workout`, so chatter comes back empty or
   invented (`rest 3600`), and nothing stops it reaching Garmin. Two layers:
   (a) deterministic guard in `workout_service` — no `run`/`break` step →
   new `FailureCode.NOT_A_WORKOUT` (billed, so no refund); (b) wrap the
   output as `ParseResult(is_workout, workout?)` with `is_workout` FIRST
   (field order drives decoding, see `workout_ai/models.py`). (a) alone is
   safe to ship.
2. **Production post-parse validator + one retry** — deterministic checks
   (e.g. alternation segments sum to the stated budget, recoveries carry no
   pace) on the parsed dict would lift ~98% model output to ~99.9% effective
   and make small eval deltas irrelevant to users.
3. **Mine cases from production** — `workout_log` stores every prompt and
   error; `PARSE_FAILED` and prompts re-sent by the same user within minutes
   are real failures. Uncovered patterns: ladders (`07-15`), multi-block
   fartleks (`06-30`) — see README.
4. **Settle the timed-jog rule** — the prompt says a paceless timed jog is
   `rest`, but the schema also offers `TimedRecoveryStep`. haiku-5.5 picks
   `rest`; `timed-reps+distance` accepts both. Pick one, remove the other
   path from prompt or schema, then tighten the check.
5. **English coverage** — only `real-set-09-30` is English. Add one or two if
   English-speaking users are a meaningful share.
6. **`reasoning_effort="high"` benchmark** — OpenAI reasoning models only (the
   prod Claude model uses adaptive thinking); may cut arithmetic flakes at some
   latency cost; one 6-run batch would answer it.
