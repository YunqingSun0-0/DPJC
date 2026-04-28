#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd)


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def merge_results_dict(target, incoming, cap_runs=None):
    for test_key, rows in incoming.items():
        if not isinstance(rows, list):
            continue
        if test_key not in target:
            target[test_key] = []
        target[test_key].extend(rows)
        if cap_runs is not None and len(target[test_key]) > cap_runs:
            target[test_key] = target[test_key][:cap_runs]


def bootstrap_existing_results(run_dir, cap_runs):
    """
    Build/refresh run_dir/results/batch_test_results.json by merging all known worker result files.
    This supports both legacy workers/maxsetsupport/* and new workers/maxsetsupport_combo/* layouts.
    """
    aggregate = {}
    merged_files = []

    # Existing top-level aggregate (if present)
    top_results = run_dir / "results" / "batch_test_results.json"
    if top_results.exists():
        merge_results_dict(aggregate, load_json(top_results), cap_runs=cap_runs)
        merged_files.append(top_results)

    # Legacy layout: workers/maxsetsupport/prg_dd_*/results/batch_test_results.json
    legacy_root = run_dir / "workers" / "maxsetsupport"
    if legacy_root.exists():
        for p in sorted(legacy_root.glob("prg_dd_*/results/batch_test_results.json")):
            merge_results_dict(aggregate, load_json(p), cap_runs=cap_runs)
            merged_files.append(p)

    # New combo layout: workers/maxsetsupport_combo/**/worker_*/results/batch_test_results.json
    combo_root = run_dir / "workers" / "maxsetsupport_combo"
    if combo_root.exists():
        for p in sorted(combo_root.glob("**/worker_*/results/batch_test_results.json")):
            merge_results_dict(aggregate, load_json(p), cap_runs=cap_runs)
            merged_files.append(p)

    save_json(top_results, aggregate)
    total_rows = sum(len(v) for v in aggregate.values() if isinstance(v, list))
    return {
        "merged_file_count": len(merged_files),
        "keys": len(aggregate),
        "rows": total_rows,
        "output_file": top_results,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Resume maxsetsupport in combo-level mode on an existing run directory. "
            "Execution is delegated to accuracy_test_batch_clear.py --maxsetsupport-parallel, "
            "which processes (d, seed, setexp-attempt) combos, writes per-worker outputs, "
            "merges into the original run folder, and regenerates maxsetsupport_attempts.json."
        )
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="Existing run directory, e.g. experiments/run_20260417_234858",
    )
    parser.add_argument(
        "--target-cores",
        type=int,
        default=None,
        help="Target core budget for auto worker sizing (recommended).",
    )
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=None,
        help="Optional manual worker override (if set, --target-cores is ignored by runner).",
    )
    parser.add_argument(
        "--num-runs",
        type=int,
        default=1000,
        help="Target runs per combo (default: 1000).",
    )
    parser.add_argument(
        "--port-base",
        type=int,
        default=21000,
        help="Base port used by the orchestrator.",
    )
    parser.add_argument(
        "--only-prg-dd",
        type=int,
        default=None,
        help="Optional: resume only one d value.",
    )
    parser.add_argument(
        "--only-mom-k",
        type=int,
        default=None,
        help="Optional: resume only one k value.",
    )
    parser.add_argument(
        "--skip-baseline-above",
        type=int,
        default=None,
        help="Skip full (d,seed) combos whose baseline setexp is above this cap.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print command only.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the resume command.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Pass --verbose to runner.",
    )
    args = parser.parse_args()

    if args.target_cores is not None and args.target_cores <= 0:
        raise ValueError("--target-cores must be > 0")
    if args.parallel_workers is not None and args.parallel_workers <= 0:
        raise ValueError("--parallel-workers must be > 0")
    if args.num_runs <= 0:
        raise ValueError("--num-runs must be > 0")
    if args.skip_baseline_above is not None and args.skip_baseline_above < 1:
        raise ValueError("--skip-baseline-above must be >= 1")
    if not args.execute:
        args.dry_run = True

    run_dir = args.run_dir.resolve()
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    repo_root = Path(__file__).resolve().parent
    runner = repo_root / "accuracy_test_batch_clear.py"
    if not runner.exists():
        raise FileNotFoundError(f"Runner not found: {runner}")

    bootstrap = bootstrap_existing_results(run_dir, cap_runs=args.num_runs)
    print(
        "Bootstrap existing results:\n"
        f"  merged_files={bootstrap['merged_file_count']}\n"
        f"  keys={bootstrap['keys']}\n"
        f"  rows={bootstrap['rows']}\n"
        f"  output={bootstrap['output_file']}"
    )

    cmd = [
        sys.executable,
        str(runner),
        "--param-mode",
        "maxsetsupport",
        "--maxsetsupport-parallel",
        "--run-dir",
        str(run_dir),
        "--num-runs",
        str(args.num_runs),
        "--port-base",
        str(args.port_base),
    ]
    if args.target_cores is not None:
        cmd.extend(["--target-cores", str(args.target_cores)])
    if args.parallel_workers is not None:
        cmd.extend(["--parallel-workers", str(args.parallel_workers)])
    if args.only_prg_dd is not None:
        cmd.extend(["--only-prg-dd", str(args.only_prg_dd)])
    if args.only_mom_k is not None:
        cmd.extend(["--only-mom-k", str(args.only_mom_k)])
    if args.skip_baseline_above is not None:
        cmd.extend(["--skip-baseline-above", str(args.skip_baseline_above)])
    if args.verbose:
        cmd.append("--verbose")

    print("Resume mode: combo-level maxsetsupport orchestration")
    print("Command:")
    print("  " + " ".join(cmd))
    print(
        "Expected behavior:\n"
        "  1. For each (d, seed, setexp-attempt), launch workers sized from target cores.\n"
        "  2. Each worker writes its own results under workers/maxsetsupport_combo/.../worker_xx/results.\n"
        "  3. Worker outputs are merged into run-dir/results/batch_test_results.json.\n"
        "  4. Continue until each combo reaches target runs and support/backoff is decided.\n"
        "  5. Regenerate run-dir/results/maxsetsupport_attempts.json (and summary files)."
    )

    if args.dry_run:
        return 0

    result = run(cmd, cwd=repo_root)
    if result.returncode != 0:
        print(f"Resume command failed with return code {result.returncode}")
        return result.returncode

    print("Resume completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
