"""Run the workout-parsing eval across models and print a scored report.

Usage:
    uv run python -m evals.run                      # all models with a key set
    uv run python -m evals.run haiku gpt-5          # filter by label substring
    uv run python -m evals.run haiku -c 200 -c km   # ...and by case-name substring
    EVAL_RUNS=3 uv run python -m evals.run          # repeat each case N times
    EVAL_RUNS=2 uv run python -m evals.run haiku --adaptive  # re-run only failures
    uv run python -m evals.run haiku --history      # accumulated results, no API calls

Scores are the fraction of per-case checks passed (see evals/cases.py). Reasoning
and thinking models are nondeterministic, so EVAL_RUNS>1 surfaces flakiness.
Raw parsed outputs are written to evals/last_results.json for inspection, and
every run is appended to evals/history.jsonl so results accumulate across
batches for as long as the prompt and case are unchanged.
"""

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .cases import CASES
from .models import MODELS

_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = _ROOT / "SYSTEM_PROMPT.md"
REASONING_PROMPT_PATH = _ROOT / "SYSTEM_PROMPT_REASONING.md"
RESULTS_PATH = Path(__file__).resolve().parent / "last_results.json"
HISTORY_PATH = Path(__file__).resolve().parent / "history.jsonl"
CONCURRENCY = 6


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _case_sha(case) -> str:
    # Input plus check labels and source, so editing a case's prompt or a check
    # starts a fresh history. Helpers the checks call are not covered.
    parts = [case.prompt]
    for label, fn in case.checks:
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            src = ""
        parts.append(f"{label}\0{src}")
    return _sha("\0".join(parts))


def _safe(check, result) -> bool:
    try:
        return bool(check(result))
    except Exception:
        return False


def _passed_all(r) -> bool:
    return r["ok"] and all(r["checks"].values())


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


def _report(results, models, cases):
    by = {}  # (case, model) -> list of run dicts
    for r in results:
        by.setdefault((r["case"], r["model"]), []).append(r)

    # checks passed, checks total, dt_sum, dt_n, clean case-runs, case-runs
    totals = {m.label: [0, 0, 0.0, 0, 0, 0] for m in models}

    for case in cases:
        print(f"\n=== {case.name} ===")
        print(f"    expect: {case.expected}")
        labels = [label for label, _ in case.checks]
        for model in models:
            runs_data = by.get((case.name, model.label), [])
            n = len(runs_data)  # varies per cell under --adaptive
            errs = [r for r in runs_data if not r["ok"]]
            dts = [r["dt"] for r in runs_data]
            avg_dt = sum(dts) / len(dts) if dts else 0.0
            totals[model.label][2] += sum(dts)
            totals[model.label][3] += len(dts)
            totals[model.label][4] += sum(1 for r in runs_data if _passed_all(r))
            totals[model.label][5] += n

            if len(errs) == n and runs_data:
                msg = errs[0]["error"]
                print(f"    {model.label:<26} ERROR  {msg[:60]}")
                totals[model.label][1] += len(labels) * n
                continue

            ok_runs = [r for r in runs_data if r["ok"]]
            cells = []
            case_passed = 0
            for label in labels:
                p = sum(1 for r in ok_runs if r["checks"].get(label))
                cells.append(_fmt_cell(p, n))
                case_passed += p
            totals[model.label][0] += case_passed
            totals[model.label][1] += len(labels) * n
            score = f"{case_passed}/{len(labels) * n}"
            row = "  ".join(f"{labels[i][:18]}:{cells[i]}" for i in range(len(labels)))
            print(f"    {model.label:<26} {score:>6}  {avg_dt:4.1f}s  | {row}")

    # Case-runs with every check green: one wrong step makes the watch workout
    # wrong, so this is the user-facing number; the check % is the diagnostic.
    print("\n=== leaderboard (checks passed | case-runs fully correct) ===")
    ranked = sorted(models, key=lambda m: (-_rate(totals[m.label]), m.label))
    for m in ranked:
        passed, total, dt_sum, dt_n, clean, n_runs = totals[m.label]
        pct = 100 * passed / total if total else 0
        cpct = 100 * clean / n_runs if n_runs else 0
        avg = dt_sum / dt_n if dt_n else 0
        print(f"    {m.label:<26} {passed:>3}/{total:<3} ({pct:5.1f}%)  |  "
              f"{clean:>2}/{n_runs:<2} ({cpct:5.1f}%)   avg {avg:4.1f}s")


def _rate(t):
    return t[0] / t[1] if t[1] else 0


def _append_history(results, keys, adaptive):
    batch = uuid.uuid4().hex[:8]
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        for r in results:
            rec = dict(ts=ts, batch=batch, adaptive=adaptive,
                       **keys[(r["case"], r["model"])], **r)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _load_history():
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _history_report(models, cases, keys):
    """Accumulated results for the current (model, prompt sha, case sha) only."""
    by = {}
    for rec in _load_history():
        k = (rec["case"], rec["model"])
        want = keys.get(k)
        if want and rec.get("prompt_sha") == want["prompt_sha"] \
                and rec.get("case_sha") == want["case_sha"]:
            by.setdefault(k, []).append(rec)

    print("\n=== history (current prompt + case versions) ===")
    for model in models:
        passed = total = n_runs = 0
        weak = []  # cases with any failed check: (case, label, passed, n)
        unmeasured = []
        for case in cases:
            recs = by.get((case.name, model.label), [])
            if not recs:
                unmeasured.append(case.name)
                continue
            n = len(recs)
            n_runs += n
            for label, _ in case.checks:
                p = sum(1 for r in recs if r["ok"] and r["checks"].get(label))
                passed += p
                total += n
                if p < n:
                    weak.append((case.name, label, p, n))
        pct = 100 * passed / total if total else 0
        print(f"    {model.label:<26} {passed:>4}/{total:<4} ({pct:5.1f}%)  {n_runs} case-runs")
        for case_name, label, p, n in weak:
            print(f"        {case_name[:24]:<24} {label[:30]:<30} {p}/{n}")
        if unmeasured:
            print(f"        never measured: {', '.join(unmeasured)}")


def _rel(path: Path) -> Path:
    return path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path


def _parse_args():
    p = argparse.ArgumentParser(prog="python -m evals.run")
    p.add_argument("models", nargs="*", help="model label substrings")
    p.add_argument("-c", "--case", action="append", default=[],
                   help="case name substring (repeatable)")
    p.add_argument("--adaptive", action="store_true",
                   help="run every case once, then re-run only cases with a "
                        "failure up to EVAL_RUNS")
    p.add_argument("--history", action="store_true",
                   help="print accumulated history for the selection; no API calls")
    return p.parse_args()


async def main():
    load_dotenv()
    args = _parse_args()
    runs = int(os.environ.get("EVAL_RUNS", "1"))
    filters = [a.lower() for a in args.models]
    case_filters = [c.lower() for c in args.case]

    available = [m for m in MODELS if os.environ.get(m.api_key_env)]
    skipped = [m for m in MODELS if not os.environ.get(m.api_key_env)]
    if filters:
        available = [m for m in available if any(f in m.label.lower() for f in filters)]
    cases = [c for c in CASES
             if not case_filters or any(f in c.name.lower() for f in case_filters)]

    if not available:
        print("No models to run (no matching provider keys set).")
        if skipped:
            print("Skipped (no key):", ", ".join(f"{m.label}[{m.api_key_env}]" for m in skipped))
        return
    if not cases:
        print("No cases match:", ", ".join(case_filters))
        print("Cases:", ", ".join(c.name for c in CASES))
        return

    prompts = {
        False: PROMPT_PATH.read_text(encoding="utf-8"),
        True: REASONING_PROMPT_PATH.read_text(encoding="utf-8"),
    }
    prompt_files = {False: PROMPT_PATH.name, True: REASONING_PROMPT_PATH.name}
    case_shas = {c.name: _case_sha(c) for c in cases}
    keys = {
        (c.name, m.label): dict(model_id=m.model,
                                prompt_file=prompt_files[m.reasoning_prompt],
                                prompt_sha=_sha(prompts[m.reasoning_prompt]),
                                case_sha=case_shas[c.name])
        for c in cases for m in available
    }

    if args.history:
        _history_report(available, cases, keys)
        return

    mode = " (adaptive)" if args.adaptive and runs > 1 else ""
    print(f"Running {len(cases)} cases x {len(available)} models x {runs} run(s){mode}")
    print("Models:", ", ".join(m.label for m in available))
    if len(cases) < len(CASES):
        print("Cases:", ", ".join(c.name for c in cases))
    if skipped:
        print("Skipped (no key):", ", ".join(f"{m.label}[{m.api_key_env}]" for m in skipped))

    sem = asyncio.Semaphore(CONCURRENCY)
    if args.adaptive and runs > 1:
        # A systematic failure fails run 0 too, so re-running only failed cells
        # keeps the "0/N blocks" signal while skipping clean cells entirely.
        results = await asyncio.gather(*[
            _run_one(sem, prompts[model.reasoning_prompt], case, model, 0)
            for case in cases for model in available
        ])
        failed = {(r["case"], r["model"]) for r in results if not _passed_all(r)}
        results += await asyncio.gather(*[
            _run_one(sem, prompts[model.reasoning_prompt], case, model, i)
            for case in cases for model in available
            if (case.name, model.label) in failed
            for i in range(1, runs)
        ])
        print(f"Re-ran {len(failed)} of {len(cases) * len(available)} cells with a failure")
    else:
        results = await asyncio.gather(*[
            _run_one(sem, prompts[model.reasoning_prompt], case, model, i)
            for case in cases for model in available for i in range(runs)
        ])

    _report(results, available, cases)
    if mode:
        print("    (adaptive: clean cells ran once, so % is biased low; "
              "don't compare it to a uniform batch)")

    RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    _append_history(results, keys, adaptive=bool(mode))
    _history_report(available, cases, keys)
    print(f"\nRaw outputs -> {_rel(RESULTS_PATH)}")
    print(f"History     -> {_rel(HISTORY_PATH)}")


if __name__ == "__main__":
    asyncio.run(main())
