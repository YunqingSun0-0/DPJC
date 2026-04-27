#!/usr/bin/env python3
"""
Real-world PSI accuracy test: error vs 1/sqrt(k), sweeping seed_size_bit

Uses UCI Bag-of-Words data. Two data-generation modes:

  SET_SIZE = None  (default)
      One document per client. Each client's set = the unique words in that
      one document (~100–500 elements depending on the corpus).

  SET_SIZE = N  (e.g. 1 << 18)
      Python selects enough documents per side so that their combined NNZ
      (total word-count entries across all selected docs) ≈ N.  Those docs
      are merged and distributed evenly across NUM_CLIENTS_PER_SERVER clients,
      so the client count stays small regardless of how many docs are needed.
      The UCI file is loaded once and cached; doc selection is fast.

      Practical numbers for SET_SIZE = 2^18 = 262144:
        NIPS   (avg 497 nnz/doc): ~528 docs/side → ~12 K unique words/side
        NYTimes (avg 333 nnz/doc): ~788 docs/side → ~60–90 K unique words/side

      Note: the PSI protocol sees the unique-word set (≤ vocab size W), not
      the raw NNZ.  SET_SIZE controls how much data goes in; the effective
      element count is bounded by W.

For each (k, seed) ONE fixed dataset is generated and reused for all
NUM_RUNS_PER_POINT runs.  Both PSI modes share the same dataset.

Usage
-----
  python accuracy_test_realworld.py --run-tests
  python accuracy_test_realworld.py --run-tests --set-size 262144
  python accuracy_test_realworld.py --parallel [--parallel-workers N]
  python accuracy_test_realworld.py --analyze --plot --run-dir ./experiments/realworld_YYYYMMDD
  python accuracy_test_realworld.py --plot-only --run-dir ./experiments/realworld_YYYYMMDD
"""

import os
import sys
import random
import subprocess
import time
import shutil
import re
import json
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
import matplotlib.pyplot as plt
import pandas as pd

# ==================== CONFIGURABLE PARAMETERS ====================

UCI_DATA_FILE = "./uci_words/docword.nips.txt"  # any UCI BoW file
NUM_CLIENTS_PER_SERVER = 1   # client processes per server side
UNIVERSAL_SIZE_BIT = 24      # must match psi_server / psi_client build
PORT_BASE = 22000

# If None: 1 doc per client (gendata handles it).
# If an integer: select enough docs so combined NNZ ≈ SET_SIZE per side,
# then merge them across NUM_CLIENTS_PER_SERVER clients.
SET_SIZE = None

# k values for the x-axis (1/sqrt(k))
MOM_K_VALUES = [100, 200, 400, 1000, 2500, 10000]
PRG_DD       = 7
MOM_T        = 11
SEED_VALUES  = [6, 7, 8]   # sweep seed_size_bit for naive mode
UNIFORM_SEED = 7           # fixed seed for naive_uniform (seed doesn't affect uniform draws)

PSI_MODES = ["naive", "naive_uniform"]
PSI_MODE_DISPLAY = {
    "naive":         "ours",
    "naive_uniform": "uniform r",
}

NUM_RUNS_PER_POINT = 1000
TIMEOUT_SECONDS    = 200
VERBOSE            = False

RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
BASE_RUN_DIR  = f"./experiments/realworld_{RUN_TIMESTAMP}"

# ==================== PATH HELPERS ====================

def results_dir(): return os.path.join(BASE_RUN_DIR, "results")
def data_dir():    return os.path.join(BASE_RUN_DIR, "data")
def plots_dir():   return os.path.join(BASE_RUN_DIR, "plots")

def set_run_dir(path):
    global BASE_RUN_DIR
    BASE_RUN_DIR = path

def ensure_directories():
    for d in [results_dir(), data_dir(), plots_dir()]:
        os.makedirs(d, exist_ok=True)

def results_file():  return os.path.join(results_dir(), "batch_test_results.json")
def analysis_file(): return os.path.join(results_dir(), "analysis_results.json")
def summary_csv():   return os.path.join(results_dir(), "realworld_errorvsepsilon_summary.csv")

# ==================== LOGGING ====================

def log(msg, level="INFO"):
    if VERBOSE or level in ("ERROR", "WARNING"):
        print(f"[{level}] {msg}", flush=True)

# ==================== LOW-LEVEL HELPERS ====================

def kill_stale():
    subprocess.run("pkill -f psi_server", shell=True, capture_output=True)
    subprocess.run("pkill -f psi_client", shell=True, capture_output=True)
    time.sleep(0.1)

def read_weighted_set(filename):
    values = {}
    with open(filename) as f:
        for line in f:
            parts = line.split()
            if parts:
                values[int(parts[0])] = int(parts[1]) if len(parts) > 1 else 1
    return values

def merge_weighted_sets(sets):
    merged = {}
    for s in sets:
        for v, w in s.items():
            merged[v] = merged.get(v, 0) + w
    return merged

def weighted_intersection_sum(w1, w2):
    return sum(w1[v] * w2[v] for v in w1 if v in w2)

def start_proc(cmd, capture=True):
    log(f"start: {cmd}")
    pipe = subprocess.PIPE if capture else subprocess.DEVNULL
    return subprocess.Popen(cmd, shell=True, stdout=pipe, stderr=pipe,
                            preexec_fn=os.setsid)

def wait_procs(procs, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(p.poll() is not None for p in procs):
            return True
        time.sleep(0.05)
    return False

def extract_psi_size(procs):
    for p in procs:
        if p.poll() is not None and p.stderr:
            for line in p.stderr.read().decode(errors="replace").splitlines():
                if "Final PSI size" in line:
                    nums = re.findall(r"\d+", line)
                    if nums:
                        return int(nums[-1])
    return None

# ==================== TEST KEY ====================

def test_key(mom_k, psi_mode, seed_bit):
    sz = SET_SIZE if SET_SIZE is not None else "1doc"
    return (f"realworld_psi_{psi_mode}_prg_dd_{PRG_DD}_mom_k_{mom_k}"
            f"_mom_t_{MOM_T}_seedbit_{seed_bit}"
            f"_clients_{NUM_CLIENTS_PER_SERVER}_setsize_{sz}")

# ==================== UCI DATASET CACHE ====================

# (num_docs, vocab_size, total_nnz, data, doc_ids, doc_starts)
#   data:       np.ndarray (NNZ, 3) int32, sorted by doc_id — [doc_id, word_id, count]
#               1.2 GB for NYTimes vs ~20 GB for nested Python dicts
#   doc_ids:    np.ndarray (D,) int32 — sorted unique doc IDs
#   doc_starts: np.ndarray (D,) int64 — row index in data where each doc begins
_UCI_CACHE = None

def load_uci_dataset():
    """
    Load the UCI BoW file once and cache in memory.

    First call: pandas parses the text file (~15-20 s for NYTimes), builds a
    sorted numpy (NNZ, 3) int32 array, saves a binary .npz alongside the
    source file.

    All subsequent calls (same or different process): loads the .npz in ~3-5 s.
    Parallel workers share the on-disk cache so only one ever pays the parse cost.
    """
    global _UCI_CACHE
    if _UCI_CACHE is not None:
        return _UCI_CACHE

    npz_path = UCI_DATA_FILE + ".cache.npz"

    if os.path.exists(npz_path):
        log(f"Loading binary cache: {npz_path} ...")
        c = np.load(npz_path)
        _UCI_CACHE = (int(c["num_docs"]), int(c["vocab_size"]), int(c["total_nnz"]),
                      c["data"], c["doc_ids"], c["doc_starts"])
        log(f"Cache loaded: {_UCI_CACHE[0]} docs, vocab={_UCI_CACHE[1]}, nnz={_UCI_CACHE[2]}")
        return _UCI_CACHE

    log(f"First-time load: {UCI_DATA_FILE} (building binary cache) ...")
    with open(UCI_DATA_FILE) as f:
        num_docs   = int(f.readline())
        vocab_size = int(f.readline())
        total_nnz  = int(f.readline())

    df = pd.read_csv(UCI_DATA_FILE, skiprows=3, sep=" ", header=None,
                     names=["d", "w", "c"],
                     dtype={"d": np.int32, "w": np.int32, "c": np.int32})
    df.sort_values("d", inplace=True, kind="mergesort")

    data       = df.to_numpy(dtype=np.int32)          # (NNZ, 3)
    doc_ids, doc_starts = np.unique(data[:, 0], return_index=True)
    doc_starts = doc_starts.astype(np.int64)

    log(f"Saving binary cache: {npz_path} ...")
    np.savez(npz_path, data=data, doc_ids=doc_ids, doc_starts=doc_starts,
             num_docs=num_docs, vocab_size=vocab_size, total_nnz=total_nnz)

    _UCI_CACHE = (num_docs, vocab_size, total_nnz, data, doc_ids, doc_starts)
    log(f"Loaded: {num_docs} docs, vocab={vocab_size}, nnz={total_nnz}")
    return _UCI_CACHE


def _doc_entries(data, doc_ids, doc_starts, idx):
    """Return (word_ids, counts) numpy arrays for the doc at position idx."""
    start = int(doc_starts[idx])
    end   = int(doc_starts[idx + 1]) if idx + 1 < len(doc_starts) else len(data)
    rows  = data[start:end]          # (n_words, 3) view — no copy
    return rows[:, 1], rows[:, 2]   # word_ids, counts

# ==================== DATA GENERATION ====================

def _generate_data_gendata(out):
    """1-doc-per-client mode: delegate entirely to the gendata binary."""
    cmd = (f"./bin/gendata "
           f"--uci_data_file={UCI_DATA_FILE} "
           f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} "
           f"--universal_size_bit={UNIVERSAL_SIZE_BIT} "
           f"--output_dir={out}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                            timeout=TIMEOUT_SECONDS)
    if result.returncode != 0:
        log(f"gendata failed: {result.stderr}", "ERROR")
        return None

    s1_files = [f"{out}/client{i+1}_1.txt" for i in range(NUM_CLIENTS_PER_SERVER)]
    s2_files = [f"{out}/client{i+1}_2.txt" for i in range(NUM_CLIENTS_PER_SERVER)]
    for fp in s1_files + s2_files:
        if not os.path.exists(fp):
            log(f"Missing generated file: {fp}", "ERROR")
            return None

    w1 = merge_weighted_sets([read_weighted_set(f) for f in s1_files])
    w2 = merge_weighted_sets([read_weighted_set(f) for f in s2_files])
    expected = weighted_intersection_sum(w1, w2)
    log(f"  1-doc mode: side1={len(w1)} words, side2={len(w2)} words, expected={expected}")
    return {"dir": out, "s1_files": s1_files, "s2_files": s2_files, "expected": expected}


def _generate_data_multidoc(out):
    """
    Multi-doc mode: select enough docs so combined NNZ ≈ SET_SIZE per side,
    merge them into NUM_CLIENTS_PER_SERVER client files, write directly.

    Uses the numpy-array cache for O(1) per-doc access and minimal memory.
    """
    num_docs, _, total_nnz, data, doc_ids, doc_starts = load_uci_dataset()

    avg_nnz_per_doc = total_nnz / max(num_docs, 1)
    docs_per_side   = max(NUM_CLIENTS_PER_SERVER,
                          int(np.ceil(SET_SIZE / avg_nnz_per_doc)))

    # Shuffle position indices (into doc_ids / doc_starts arrays), not raw IDs
    all_indices = list(range(len(doc_ids)))
    random.shuffle(all_indices)

    if len(all_indices) < 2 * docs_per_side:
        docs_per_side = len(all_indices) // 2
        log(f"  Not enough docs for SET_SIZE={SET_SIZE}; capped at {docs_per_side} docs/side",
            "WARNING")

    side1_idx = all_indices[:docs_per_side]
    side2_idx = all_indices[docs_per_side: 2 * docs_per_side]

    def build_and_write(indices, side_label):
        # Bucket indices by client (round-robin), then vectorized merge per client
        buckets = [[] for _ in range(NUM_CLIENTS_PER_SERVER)]
        for i, idx in enumerate(indices):
            buckets[i % NUM_CLIENTS_PER_SERVER].append(idx)

        paths = []
        for ci, bucket in enumerate(buckets):
            if not bucket:
                # Write empty file so psi_client still has a valid input
                fp = os.path.join(out, f"client{ci+1}_{side_label}.txt")
                open(fp, "w").close()
                paths.append(fp)
                continue

            # Concatenate all doc slices for this client — stays in numpy
            all_wids = np.concatenate([_doc_entries(data, doc_ids, doc_starts, idx)[0]
                                       for idx in bucket])
            all_cnts = np.concatenate([_doc_entries(data, doc_ids, doc_starts, idx)[1]
                                       for idx in bucket])

            # Sum counts for duplicate word_ids in one vectorized pass
            unique_wids, inv = np.unique(all_wids, return_inverse=True)
            merged_cnts = np.zeros(len(unique_wids), dtype=np.int64)
            np.add.at(merged_cnts, inv, all_cnts)

            fp = os.path.join(out, f"client{ci+1}_{side_label}.txt")
            lines = "\n".join(f"{w} {c}" for w, c in zip(unique_wids.tolist(),
                                                           merged_cnts.tolist()))
            with open(fp, "w") as f:
                f.write(lines + "\n")
            paths.append(fp)
        return paths

    s1_files = build_and_write(side1_idx, "1")
    s2_files = build_and_write(side2_idx, "2")

    w1 = merge_weighted_sets([read_weighted_set(f) for f in s1_files])
    w2 = merge_weighted_sets([read_weighted_set(f) for f in s2_files])
    expected = weighted_intersection_sum(w1, w2)

    s1_nnz = sum(int(doc_starts[i+1] if i+1 < len(doc_starts) else len(data)) - int(doc_starts[i])
                 for i in side1_idx)
    s2_nnz = sum(int(doc_starts[i+1] if i+1 < len(doc_starts) else len(data)) - int(doc_starts[i])
                 for i in side2_idx)
    log(f"  multi-doc: {docs_per_side} docs/side, "
        f"s1_nnz={s1_nnz} s1_unique={len(w1)}, "
        f"s2_nnz={s2_nnz} s2_unique={len(w2)}, expected={expected}")
    return {"dir": out, "s1_files": s1_files, "s2_files": s2_files, "expected": expected}


def generate_data(test_id):
    """Generate one fixed dataset and return info dict, or None on failure."""
    out = os.path.join(data_dir(), f"test_{test_id:06d}")
    os.makedirs(out, exist_ok=True)

    if SET_SIZE is None:
        return _generate_data_gendata(out)
    else:
        return _generate_data_multidoc(out)

# ==================== SINGLE PSI RUN ====================

def run_psi(test_id, mom_k, psi_mode, data_info, port, seed_bit):
    """Run one PSI protocol on the fixed dataset; return result dict or None."""
    expected = data_info["expected"]
    s1_files = data_info["s1_files"]
    s2_files = data_info["s2_files"]

    seed_arg = f" --seed_size_bit={seed_bit}"

    def srv_cmd(party):
        return (f"./bin/psi_server -p {party} --port={port} "
                f"--psi_mode={psi_mode} "
                f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} "
                f"--universal_set_size_bit={UNIVERSAL_SIZE_BIT} "
                f"--test_mode "
                f"--mom_k={mom_k} --mom_t={MOM_T} --prg_dd={PRG_DD}"
                f"{seed_arg}")

    def cli_cmd(party, data_file):
        return (f"./bin/psi_client -p {party} --port={port} "
                f"--data_file={data_file} "
                f"--psi_mode={psi_mode} "
                f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} "
                f"--universal_set_size_bit={UNIVERSAL_SIZE_BIT} "
                f"--test_mode "
                f"--mom_k={mom_k} --mom_t={MOM_T} --prg_dd={PRG_DD}"
                f"{seed_arg}")

    p_srv1 = start_proc(srv_cmd(1))
    p_srv2 = start_proc(srv_cmd(2))

    cli_procs = []
    for i, f in enumerate(s1_files):
        cli_procs.append(start_proc(cli_cmd(i + 1, f), capture=False))
    for i, f in enumerate(s2_files):
        cli_procs.append(start_proc(cli_cmd(i + 1 + NUM_CLIENTS_PER_SERVER, f), capture=False))

    all_procs = [p_srv1, p_srv2] + cli_procs
    if not wait_procs(all_procs, TIMEOUT_SECONDS):
        log(f"Test {test_id} timed out", "ERROR")
        for p in all_procs:
            try: os.killpg(os.getpgid(p.pid), 9)
            except Exception: pass
        return None

    for i, p in enumerate(all_procs):
        if p.returncode != 0:
            log(f"Test {test_id} proc {i} exited {p.returncode}", "ERROR")
            return None

    actual = extract_psi_size([p_srv1, p_srv2])
    if actual is None:
        log(f"Test {test_id}: could not read PSI size from server output", "ERROR")
        return None

    error = ((actual - expected) / expected) if expected > 0 else float("inf")
    return {
        "test_id":               test_id,
        "mode":                  "realworld_errorvsepsilon",
        "psi_mode":              psi_mode,
        "prg_dd":                PRG_DD,
        "mom_k":                 mom_k,
        "mom_t":                 MOM_T,
        "seed_size_bit":         seed_bit,
        "num_clients_per_server": NUM_CLIENTS_PER_SERVER,
        "set_size_target":       SET_SIZE,
        "expected":              expected,
        "actual":                actual,
        "error_ratio":           error,
        "timestamp":             datetime.now().isoformat(),
    }

# ==================== BATCH TESTS ====================

def load_results():
    f = results_file()
    if os.path.exists(f):
        with open(f) as fh:
            return json.load(fh)
    return {}

def save_results(results):
    with open(results_file(), "w") as f:
        json.dump(results, f, indent=2)

def run_batch_tests():
    """
    For each k value:
      1. Generate ONE fixed dataset (shared across all runs).
      2. naive mode:         sweep SEED_VALUES — NUM_RUNS_PER_POINT runs each.
      3. naive_uniform mode: run once with UNIFORM_SEED — NUM_RUNS_PER_POINT runs.
    """
    log("Starting real-world errorvsepsilon batch tests...")
    ensure_directories()

    for exe in ["./bin/psi_server", "./bin/psi_client"]:
        if not os.path.exists(exe):
            log(f"Missing executable: {exe}", "ERROR")
            return False
    if SET_SIZE is None and not os.path.exists("./bin/gendata"):
        log("Missing executable: ./bin/gendata", "ERROR")
        return False

    kill_stale()

    if SET_SIZE is not None:
        try:
            load_uci_dataset()
        except Exception as e:
            log(f"Failed to load UCI dataset: {e}", "ERROR")
            return False

    results = load_results()

    test_id = 0
    for mom_k in MOM_K_VALUES:
        k_idx = MOM_K_VALUES.index(mom_k)

        # Initialise result buckets; check if everything at this k is done
        all_done = True
        for seed_bit in SEED_VALUES:
            key = test_key(mom_k, "naive", seed_bit)
            results.setdefault("naive", {}).setdefault(key, [])
            if len(results["naive"][key]) < NUM_RUNS_PER_POINT:
                all_done = False
        unif_key = test_key(mom_k, "naive_uniform", UNIFORM_SEED)
        results.setdefault("naive_uniform", {}).setdefault(unif_key, [])
        if len(results["naive_uniform"][unif_key]) < NUM_RUNS_PER_POINT:
            all_done = False

        if all_done:
            log(f"k={mom_k}: all tests complete, skipping")
            continue

        test_id += 1
        log(f"k={mom_k}: generating dataset (test_id={test_id})")
        data_info = generate_data(test_id)
        if data_info is None:
            log(f"k={mom_k}: dataset generation failed, skipping", "ERROR")
            continue

        # --- naive: one sweep per seed ---
        for seed_bit in SEED_VALUES:
            key = test_key(mom_k, "naive", seed_bit)
            already_done = len(results["naive"][key])
            if already_done >= NUM_RUNS_PER_POINT:
                log(f"k={mom_k} naive seed={seed_bit}: already complete, skipping")
                continue
            log(f"k={mom_k} naive seed={seed_bit}: running "
                f"{NUM_RUNS_PER_POINT - already_done} more runs")
            for run in range(already_done, NUM_RUNS_PER_POINT):
                port = PORT_BASE + k_idx * 100 + run % 100
                res  = run_psi(test_id, mom_k, "naive", data_info, port, seed_bit)
                if res:
                    results["naive"][key].append(res)
                    save_results(results)
                    log(f"  k={mom_k} naive seed={seed_bit} "
                        f"run {run+1}/{NUM_RUNS_PER_POINT}: "
                        f"error={res['error_ratio']:.4f}")
                else:
                    log(f"  k={mom_k} naive seed={seed_bit} run {run+1} FAILED", "ERROR")

        # --- naive_uniform: single seed ---
        already_done = len(results["naive_uniform"][unif_key])
        if already_done < NUM_RUNS_PER_POINT:
            log(f"k={mom_k} naive_uniform: running "
                f"{NUM_RUNS_PER_POINT - already_done} more runs")
            for run in range(already_done, NUM_RUNS_PER_POINT):
                port = PORT_BASE + k_idx * 100 + run % 100
                res  = run_psi(test_id, mom_k, "naive_uniform",
                               data_info, port, UNIFORM_SEED)
                if res:
                    results["naive_uniform"][unif_key].append(res)
                    save_results(results)
                    log(f"  k={mom_k} naive_uniform "
                        f"run {run+1}/{NUM_RUNS_PER_POINT}: "
                        f"error={res['error_ratio']:.4f}")
                else:
                    log(f"  k={mom_k} naive_uniform run {run+1} FAILED", "ERROR")
        else:
            log(f"k={mom_k} naive_uniform: already complete, skipping")

        shutil.rmtree(data_info["dir"], ignore_errors=True)

    log("Batch tests complete.")
    return True

# ==================== PARALLEL ORCHESTRATOR ====================

def run_parallel(parallel_workers=None):
    """
    Split NUM_RUNS_PER_POINT across parallel_workers child processes,
    each writing to its own run dir.  Merge all result files afterward.
    """
    ensure_directories()
    cpu_slots = 2 + 2 * NUM_CLIENTS_PER_SERVER
    if parallel_workers is None:
        parallel_workers = max(1, (os.cpu_count() or 4) // cpu_slots)
    log(f"Parallel: {parallel_workers} workers, {NUM_RUNS_PER_POINT} total runs/point")

    base_runs = NUM_RUNS_PER_POINT // parallel_workers
    extra     = NUM_RUNS_PER_POINT % parallel_workers
    run_plan  = [base_runs + (1 if i < extra else 0) for i in range(parallel_workers)]

    script_path = os.path.abspath(__file__)
    aggregate: dict = {}

    def worker_task(worker_idx, nruns):
        wdir  = os.path.join(BASE_RUN_DIR, "workers", f"w{worker_idx:02d}")
        wport = PORT_BASE + worker_idx * 1000
        cmd   = [sys.executable, script_path,
                 "--run-tests",
                 "--num-runs",  str(nruns),
                 "--run-dir",   wdir,
                 "--port-base", str(wport),
                 "--num-clients-per-server", str(NUM_CLIENTS_PER_SERVER),
                 "--seed-values"] + [str(s) for s in SEED_VALUES]
        if SET_SIZE is not None:
            cmd += ["--set-size", str(SET_SIZE)]
        if VERBOSE:
            cmd.append("--verbose")
        t0  = time.time()
        ret = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.time() - t0
        log(f"Worker {worker_idx:02d}: rc={ret.returncode} elapsed={elapsed:.1f}s")
        if ret.returncode != 0 and VERBOSE:
            log(ret.stderr, "WARNING")
        return os.path.join(wdir, "results", "batch_test_results.json")

    active = [(i + 1, r) for i, r in enumerate(run_plan) if r > 0]
    with ThreadPoolExecutor(max_workers=len(active)) as ex:
        futures = {ex.submit(worker_task, widx, nruns): widx
                   for widx, nruns in active}
        for fut in futures:
            rfile = fut.result()
            if not os.path.exists(rfile):
                log(f"Worker result file missing: {rfile}", "WARNING")
                continue
            with open(rfile) as f:
                data = json.load(f)
            for mode_key, key_results in data.items():
                if mode_key not in aggregate:
                    aggregate[mode_key] = {}
                if isinstance(key_results, dict):
                    for k, v in key_results.items():
                        aggregate[mode_key].setdefault(k, []).extend(v)

    save_results(aggregate)
    log("Parallel orchestrator done.")
    return True

# ==================== ANALYSIS ====================

def analyze_results():
    raw = load_results()
    if not raw:
        log("No results found.", "ERROR")
        return None

    analysis    = {}
    summary_rows = []

    for psi_mode in PSI_MODES:
        mode_data = raw.get(psi_mode, {})
        for key, runs in mode_data.items():
            if not runs:
                continue
            errors  = sorted(abs(r["error_ratio"]) for r in runs)
            n_trim  = max(0, int(len(errors) * 0.05))
            trimmed = (errors[n_trim: len(errors) - n_trim]
                       if n_trim and len(errors) - 2 * n_trim > 0 else errors)

            mom_k    = runs[0]["mom_k"]
            seed_bit = runs[0].get("seed_size_bit", SEED_VALUES[0])
            epsilon  = 1.0 / np.sqrt(mom_k)
            entry    = {
                "psi_mode":     psi_mode,
                "seed_bit":     seed_bit,
                "mom_k":        mom_k,
                "epsilon":      epsilon,
                "mean_error":   float(np.mean(trimmed)),
                "median_error": float(np.median(trimmed)),
                "std_error":    float(np.std(trimmed)) if len(trimmed) > 1 else 0.0,
                "max_error":    float(np.max(trimmed)),
                "min_error":    float(np.min(trimmed)),
                "total_runs":   len(runs),
                "trimmed_runs": len(trimmed),
            }
            analysis[key] = entry
            summary_rows.append(entry)

    if not analysis:
        log("No valid data to analyze.", "ERROR")
        return None

    with open(analysis_file(), "w") as f:
        json.dump(analysis, f, indent=2)
    pd.DataFrame(summary_rows).to_csv(summary_csv(), index=False)
    log(f"Analysis → {analysis_file()}")
    log(f"Summary  → {summary_csv()}")
    return analysis

# ==================== PLOTTING ====================

# Color / marker mappings — match reference figure (ours n=2^d + uniform r)
_SEED_COLORS  = {6: "#1f77b4", 7: "#d62728", 8: "#ff7f0e"}
_SEED_MARKERS = {6: "o",       7: "D",       8: "s"}
_SEED_HOLLOW  = {6: False,     7: True,      8: False}
_UNIFORM_COLOR  = "#2ca02c"
_UNIFORM_MARKER = "o"

_FIGSIZE = (8 * 1.2, 4.944271909999159 * 1.2)
_FONT_SIZE = 20
_LEGEND_FONTSIZE = 18

def _seed_label(sb):
    return rf"ours ($n=2^{{{sb}}}$)"

def _uniform_label():
    return r"uniform $\mathbf{r}$"


def _plot_naive_line(ax, sb, xs, ys):
    hollow = _SEED_HOLLOW.get(sb, False)
    color  = _SEED_COLORS.get(sb, "gray")
    ax.plot(xs, ys,
            marker=_SEED_MARKERS.get(sb, "o"),
            linewidth=2.4, markersize=7,
            linestyle="-",
            color=color,
            markerfacecolor="none" if hollow else color,
            markeredgecolor=color,
            markeredgewidth=2.0,
            alpha=0.75,
            label=_seed_label(sb))

def _plot_uniform_line(ax, xs, ys):
    ax.plot(xs, ys,
            marker=_UNIFORM_MARKER,
            linewidth=2.4, markersize=7,
            linestyle="--",
            color=_UNIFORM_COLOR,
            markeredgecolor=_UNIFORM_COLOR,
            markeredgewidth=2.0,
            alpha=0.75,
            label=_uniform_label())

def _draw_plot(fig, ax):
    ax.set_xlabel(r"$1/\sqrt{k}$", fontsize=_FONT_SIZE)
    ax.set_ylabel("Accuracy Error", fontsize=_FONT_SIZE)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", frameon=False, fontsize=_LEGEND_FONTSIZE)
    fig.tight_layout()
    png_path = os.path.join(plots_dir(), "realworld_error_vs_epsilon.png")
    pdf_path = os.path.join(plots_dir(), "realworld_error_vs_epsilon.pdf")
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    plt.close(fig)
    log(f"Plot saved → {png_path}")
    log(f"Plot saved → {pdf_path}")
    return True

def create_plots(analysis):
    if not analysis:
        log("No analysis data for plotting.", "ERROR")
        return False

    plt.rcParams.update({"font.size": _FONT_SIZE})

    naive_pts   = {}   # seed_bit -> [(epsilon, median_error)]
    uniform_pts = []   # [(epsilon, median_error)]

    for entry in analysis.values():
        m  = entry["psi_mode"]
        pt = (entry["epsilon"], entry["median_error"])
        if m == "naive":
            sb = entry.get("seed_bit", SEED_VALUES[0])
            naive_pts.setdefault(sb, []).append(pt)
        elif m == "naive_uniform":
            uniform_pts.append(pt)

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    plotted = False

    for sb in sorted(naive_pts):
        pts = sorted(naive_pts[sb])
        xs, ys = zip(*pts)
        _plot_naive_line(ax, sb, xs, ys)
        plotted = True

    if uniform_pts:
        pts = sorted(uniform_pts)
        xs, ys = zip(*pts)
        _plot_uniform_line(ax, xs, ys)
        plotted = True

    if not plotted:
        plt.close(fig)
        return False
    return _draw_plot(fig, ax)

def create_plots_from_csv():
    if not os.path.exists(summary_csv()):
        log(f"Summary CSV not found: {summary_csv()}", "ERROR")
        return False
    df = pd.read_csv(summary_csv())
    if df.empty:
        log("Summary CSV is empty.", "ERROR")
        return False

    plt.rcParams.update({"font.size": _FONT_SIZE})
    fig, ax = plt.subplots(figsize=_FIGSIZE)
    plotted = False

    naive_df = df[df["psi_mode"] == "naive"]
    seeds_in_data = sorted(naive_df["seed_bit"].unique()) if "seed_bit" in naive_df.columns else SEED_VALUES
    for sb in seeds_in_data:
        sub = naive_df[naive_df["seed_bit"] == sb].sort_values("epsilon") if "seed_bit" in naive_df.columns \
              else naive_df.sort_values("epsilon")
        if sub.empty:
            continue
        _plot_naive_line(ax, int(sb),
                         sub["epsilon"].astype(float).to_list(),
                         sub["median_error"].astype(float).to_list())
        plotted = True

    sub_unif = df[df["psi_mode"] == "naive_uniform"].sort_values("epsilon")
    if not sub_unif.empty:
        _plot_uniform_line(ax,
                           sub_unif["epsilon"].astype(float).to_list(),
                           sub_unif["median_error"].astype(float).to_list())
        plotted = True

    if not plotted:
        plt.close(fig)
        return False
    return _draw_plot(fig, ax)

# ==================== MAIN ====================

def main():
    global VERBOSE, NUM_RUNS_PER_POINT, PORT_BASE, NUM_CLIENTS_PER_SERVER, SET_SIZE, SEED_VALUES

    parser = argparse.ArgumentParser(
        description="Real-world PSI accuracy: error vs 1/sqrt(k), sweep seed")
    parser.add_argument("--run-tests",   action="store_true")
    parser.add_argument("--parallel",    action="store_true",
                        help="Launch parallel workers, merge, analyze, plot")
    parser.add_argument("--parallel-workers", type=int, default=None,
                        help="Number of parallel workers (default: auto)")
    parser.add_argument("--analyze",     action="store_true")
    parser.add_argument("--plot",        action="store_true")
    parser.add_argument("--plot-only",   action="store_true",
                        help="Plot from existing summary CSV, no re-analysis")
    parser.add_argument("--run-dir",     type=str, default=None)
    parser.add_argument("--num-runs",    type=int, default=NUM_RUNS_PER_POINT,
                        help="Runs per (k, seed, mode) combination")
    parser.add_argument("--port-base",   type=int, default=PORT_BASE)
    parser.add_argument("--num-clients-per-server", type=int,
                        default=NUM_CLIENTS_PER_SERVER,
                        help="Client processes per server side")
    parser.add_argument("--set-size",    type=int, default=None,
                        help="Target total NNZ per side (None = 1 doc/client)")
    parser.add_argument("--seed-values", type=int, nargs="+", default=None,
                        help="seed_size_bit values to sweep (default: 6 7 8)")
    parser.add_argument("--verbose",     action="store_true")
    args = parser.parse_args()

    VERBOSE                = args.verbose
    NUM_RUNS_PER_POINT     = args.num_runs
    PORT_BASE              = args.port_base
    NUM_CLIENTS_PER_SERVER = args.num_clients_per_server
    if args.set_size is not None:
        SET_SIZE = args.set_size
    if args.seed_values is not None:
        SEED_VALUES = args.seed_values

    if args.run_dir:
        set_run_dir(args.run_dir)
    ensure_directories()

    if args.parallel:
        ok = run_parallel(args.parallel_workers)
        if not ok:
            return 1
        analysis = analyze_results()
        create_plots(analysis)
        return 0

    if args.run_tests:
        if not run_batch_tests():
            return 1

    if args.analyze or args.plot:
        analysis = analyze_results()
        if args.plot:
            create_plots(analysis)
        return 0

    if args.plot_only:
        create_plots_from_csv()
        return 0

    if not any([args.run_tests, args.parallel,
                args.analyze, args.plot, args.plot_only]):
        parser.print_help()
    return 0

if __name__ == "__main__":
    sys.exit(main())
