# Running evals efficiently (guide for agents)

How to use `evals/` without wasting runs. Mechanics of the harness are in
[README.md](README.md); this file is about *when to run what* and *how to read
the numbers*.

## Commands

```bash
uv run python -m evals.run luna              # filter by model label substring
EVAL_RUNS=2 uv run python -m evals.run luna  # N runs per case
```

There is no case filter — every batch runs all cases (see backlog #1).

Scale (Luna, 2026-08): one run = 7 cases ≈ 27 checks, ~5 s/call. A 6-run batch
is 42 calls ≈ 1 min wall / ~$0.15. Cost is time and attention, not money.

## The two questions — and the two run sizes

**"Did my edit break something?" → `EVAL_RUNS=2`, prod model only.**
This is the routine per-update check. Only a *systematic* failure blocks: a
check at 0/2 that passed before. A 1/2 flake is noise — do not iterate on it.

**"What is the true score / is variant A better?" → `EVAL_RUNS=6+`, rarely.**
Only for deliberate benchmarks (new model, prompt A/B, release gate). Both arms
at the same n. Do not conclude a winner unless the gap is >4 points; anything
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

## Improvement backlog (agreed 2026-08-03, not yet implemented)

1. **Case filter in `run.py`** — iterate on one rule without re-running all 7
   cases (mirror the model-label filter). Smallest change, biggest win.
2. **`history.jsonl` accumulation** — append batches keyed by
   (model, prompt-file hash, case) instead of overwriting `last_results.json`,
   so confidence accumulates across sessions and unchanged configurations
   never need re-measuring.
3. **Adaptive re-runs** — run everything once, re-run only cases with a
   failure; halves a batch at equal detection power.
4. **`reasoning_effort="high"` benchmark** — may cut arithmetic flakes at some
   latency cost; one 6-run batch would answer it.
5. **Production post-parse validator + one retry** — deterministic checks
   (e.g. alternation segments sum to the stated budget) on the parsed dict
   would lift ~98% model output to ~99.9% effective and make small eval deltas
   irrelevant to users. Highest real-world impact of the list.
