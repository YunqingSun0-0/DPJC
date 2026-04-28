#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


DEFAULT_BASELINE = {
    64: {4: 12, 5: 15, 6: 18, 7: 21, 8: 24},
    128: {4: 14, 5: 17, 6: 21, 7: 24},
    256: {4: 16, 5: 20, 6: 24},
    512: {4: 18, 5: 22},
    1024: {4: 20},
}
KEY_PATTERN = re.compile(r"seedbit_(\d+)_setexp_(\d+)$")


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_baseline_map(run_dir):
    metadata_path = run_dir / "results" / "run_metadata.json"
    if metadata_path.exists():
        metadata = load_json(metadata_path)
        baseline = metadata.get("maxsetsupport", {}).get("baseline_set_size_exponents")
        if baseline:
            parsed = {}
            for seed_size_str, d_map in baseline.items():
                seed_size = int(seed_size_str)
                parsed[seed_size] = {int(d): int(exp) for d, exp in d_map.items()}
            return parsed
    return DEFAULT_BASELINE


def summarize_worker(worker_results_path, d_value, baseline_map, num_runs):
    data = load_json(worker_results_path)
    expected = []
    for seed_size, d_map in baseline_map.items():
        if d_value in d_map:
            seed_size_bit = seed_size.bit_length() - 1
            expected.append((seed_size_bit, d_map[d_value]))
    expected.sort()

    present = []
    for key, runs in data.items():
        match = KEY_PATTERN.search(key)
        if not match:
            continue
        seed_size_bit, set_exp = map(int, match.groups())
        present.append((seed_size_bit, set_exp, len(runs)))
    present.sort()

    by_pair = {(seed_size_bit, set_exp): run_count for seed_size_bit, set_exp, run_count in present}
    baseline_started = 0
    baseline_done_runs = 0
    baseline_missing_runs = 0
    baseline_complete = []
    baseline_partial = []
    baseline_not_started = []

    for seed_size_bit, set_exp in expected:
        run_count = by_pair.get((seed_size_bit, set_exp), 0)
        baseline_done_runs += run_count
        missing = max(0, num_runs - run_count)
        baseline_missing_runs += missing
        if run_count == 0:
            baseline_not_started.append((seed_size_bit, set_exp, num_runs))
        else:
            baseline_started += 1
            if run_count >= num_runs:
                baseline_complete.append((seed_size_bit, set_exp, run_count))
            else:
                baseline_partial.append((seed_size_bit, set_exp, run_count, missing))

    non_baseline = []
    expected_set = set(expected)
    for seed_size_bit, set_exp, run_count in present:
        if (seed_size_bit, set_exp) not in expected_set:
            non_baseline.append((seed_size_bit, set_exp, run_count))

    return {
        "d": d_value,
        "expected": expected,
        "observed_keys": len(data),
        "observed_total_runs": sum(len(v) for v in data.values()),
        "baseline_started": baseline_started,
        "baseline_expected_count": len(expected),
        "baseline_done_runs": baseline_done_runs,
        "baseline_missing_runs": baseline_missing_runs,
        "baseline_complete": baseline_complete,
        "baseline_partial": baseline_partial,
        "baseline_not_started": baseline_not_started,
        "non_baseline": non_baseline,
    }


def print_summary(run_dir, num_runs):
    baseline_map = parse_baseline_map(run_dir)
    workers_root = run_dir / "workers" / "maxsetsupport"
    if not workers_root.exists():
        return False

    worker_dirs = sorted(
        p for p in workers_root.iterdir()
        if p.is_dir() and p.name.startswith("prg_dd_")
    )
    if not worker_dirs:
        return False

    print(f"Run: {run_dir.name}")
    found_any = False
    for worker_dir in worker_dirs:
        match = re.search(r"prg_dd_(\d+)_", worker_dir.name)
        if not match:
            continue
        d_value = int(match.group(1))
        results_path = worker_dir / "results" / "batch_test_results.json"
        if not results_path.exists():
            print(f"  d={d_value}: missing results file")
            continue

        found_any = True
        summary = summarize_worker(results_path, d_value, baseline_map, num_runs)
        print(f"  d={summary['d']}")
        print(
            f"    expected_baseline_combos={summary['baseline_expected_count']} "
            f"observed_keys={summary['observed_keys']} "
            f"observed_total_runs={summary['observed_total_runs']}"
        )
        print(
            f"    baseline_started={summary['baseline_started']}/{summary['baseline_expected_count']} "
            f"baseline_done_runs={summary['baseline_done_runs']} "
            f"baseline_missing_runs={summary['baseline_missing_runs']}"
        )
        if summary["baseline_complete"]:
            items = ", ".join(
                f"(seedbit={sb},setexp={se},runs={runs})"
                for sb, se, runs in summary["baseline_complete"]
            )
            print(f"    baseline_complete: {items}")
        if summary["baseline_partial"]:
            items = ", ".join(
                f"(seedbit={sb},setexp={se},runs={runs},missing={missing})"
                for sb, se, runs, missing in summary["baseline_partial"]
            )
            print(f"    baseline_partial: {items}")
        if summary["baseline_not_started"]:
            items = ", ".join(
                f"(seedbit={sb},setexp={se},missing={missing})"
                for sb, se, missing in summary["baseline_not_started"]
            )
            print(f"    baseline_not_started: {items}")
        if summary["non_baseline"]:
            items = ", ".join(
                f"(seedbit={sb},setexp={se},runs={runs})"
                for sb, se, runs in summary["non_baseline"]
            )
            print(f"    non_baseline(backoff): {items}")
    print()
    return found_any


def main():
    parser = argparse.ArgumentParser(
        description="Print maxsetsupport run progress from existing worker results."
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Specific run directory, e.g. experiments/run_20260414_185720",
    )
    parser.add_argument(
        "--experiments-dir",
        type=Path,
        default=Path("experiments"),
        help="Experiments root to scan when using --all-runs",
    )
    parser.add_argument(
        "--all-runs",
        action="store_true",
        help="Scan all run_* directories under --experiments-dir",
    )
    parser.add_argument(
        "--num-runs",
        type=int,
        default=1000,
        help="Target run count per combo (default: 1000)",
    )
    args = parser.parse_args()

    run_dirs = []
    if args.run_dir:
        run_dirs = [args.run_dir]
    elif args.all_runs:
        run_dirs = sorted(
            p for p in args.experiments_dir.iterdir()
            if p.is_dir() and p.name.startswith("run_")
        )
    else:
        parser.error("Specify either --run-dir or --all-runs")

    printed = 0
    for run_dir in run_dirs:
        if print_summary(run_dir, args.num_runs):
            printed += 1

    if printed == 0:
        print("No maxsetsupport worker runs found.")


if __name__ == "__main__":
    main()
