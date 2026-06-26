"""Run the workout-parsing eval across models and print a scored report.

Usage:
    uv run python -m evals.run                      # all models with a key set
    uv run python -m evals.run haiku gpt-5          # filter by label substring
    EVAL_RUNS=3 uv run python -m evals.run          # repeat each case N times

Scores are the fraction of per-case checks passed (see evals/cases.py). Reasoning
and thinking models are nondeterministic, so EVAL_RUNS>1 surfaces flakiness.
Raw parsed outputs are written to evals/last_results.json for inspection.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from .cases import CASES
from .models import MODELS

PROMPT_PATH = Path(__file__).resolve().parent.parent / "SYSTEM_PROMPT.md"
RESULTS_PATH = Path(__file__).resolve().parent / "last_results.json"
CONCURRENCY = 6


def _safe(check, result) -> bool:
    try:
        return bool(check(result))
    except Exception:
        return False


async def _run_one(sem, system_prompt, case, model, run_idx):
    async with sem:
        start = time.monotonic()
        try:
            out = await model.runner(system_prompt, case.prompt, model.model)
            dt = time.monotonic() - start
            checks = {label: _safe(fn, out) for label, fn in case.checks}
            return dict(case=case.name, model=model.label, run=run_idx,
                        ok=True, dt=dt, checks=checks, output=out)
        except Exception as e:  # network, 404, refusal, truncation, ...
            return dict(case=case.name, model=model.label, run=run_idx,
                        ok=False, dt=time.monotonic() - start,
                        error=f"{type(e).__name__}: {e}")


def _fmt_cell(passed: int, total: int) -> str:
    if total == 1:
        return " ok " if passed else " .. "
    return f"{passed}/{total}"


def _report(results, models, runs):
    by = {}  # (case, model) -> list of run dicts
    for r in results:
        by.setdefault((r["case"], r["model"]), []).append(r)

    totals = {m.label: [0, 0, 0.0, 0] for m in models}  # passed, total, dt_sum, dt_n

    for case in CASES:
        print(f"\n=== {case.name} ===")
        print(f"    expect: {case.expected}")
        labels = [label for label, _ in case.checks]
        for model in models:
            runs_data = by.get((case.name, model.label), [])
            errs = [r for r in runs_data if not r["ok"]]
            dts = [r["dt"] for r in runs_data]
            avg_dt = sum(dts) / len(dts) if dts else 0.0
            totals[model.label][2] += sum(dts)
            totals[model.label][3] += len(dts)

            if len(errs) == len(runs_data) and runs_data:
                msg = errs[0]["error"]
                print(f"    {model.label:<26} ERROR  {msg[:60]}")
                totals[model.label][1] += len(labels) * runs
                continue

            ok_runs = [r for r in runs_data if r["ok"]]
            cells = []
            case_passed = 0
            for label in labels:
                p = sum(1 for r in ok_runs if r["checks"].get(label))
                cells.append(_fmt_cell(p, runs))
                case_passed += p
            totals[model.label][0] += case_passed
            totals[model.label][1] += len(labels) * runs
            score = f"{case_passed}/{len(labels) * runs}"
            row = "  ".join(f"{labels[i][:18]}:{cells[i]}" for i in range(len(labels)))
            print(f"    {model.label:<26} {score:>6}  {avg_dt:4.1f}s  | {row}")

    print("\n=== leaderboard (checks passed across all cases) ===")
    ranked = sorted(models, key=lambda m: (-_rate(totals[m.label]), m.label))
    for m in ranked:
        passed, total, dt_sum, dt_n = totals[m.label]
        pct = 100 * passed / total if total else 0
        avg = dt_sum / dt_n if dt_n else 0
        print(f"    {m.label:<26} {passed:>3}/{total:<3} ({pct:5.1f}%)   avg {avg:4.1f}s")


def _rate(t):
    return t[0] / t[1] if t[1] else 0


async def main():
    load_dotenv()
    runs = int(os.environ.get("EVAL_RUNS", "1"))
    filters = [a.lower() for a in sys.argv[1:]]

    available = [m for m in MODELS if os.environ.get(m.api_key_env)]
    skipped = [m for m in MODELS if not os.environ.get(m.api_key_env)]
    if filters:
        available = [m for m in available if any(f in m.label.lower() for f in filters)]

    if not available:
        print("No models to run (no matching provider keys set).")
        if skipped:
            print("Skipped (no key):", ", ".join(f"{m.label}[{m.api_key_env}]" for m in skipped))
        return

    print(f"Running {len(CASES)} cases x {len(available)} models x {runs} run(s)")
    print("Models:", ", ".join(m.label for m in available))
    if skipped:
        print("Skipped (no key):", ", ".join(f"{m.label}[{m.api_key_env}]" for m in skipped))

    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [
        _run_one(sem, system_prompt, case, model, i)
        for case in CASES for model in available for i in range(runs)
    ]
    results = await asyncio.gather(*tasks)

    _report(results, available, runs)

    RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nRaw outputs -> {RESULTS_PATH.relative_to(Path.cwd())}")


if __name__ == "__main__":
    asyncio.run(main())
