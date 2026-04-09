#!/usr/bin/env python3
"""
Batch test script for PSI parameter scanning
Tests different prg_dd and mom_k combinations with statistical analysis
"""

import os
import sys
import subprocess
import time
import signal
import shutil
import re
import json
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from datetime import datetime
import argparse

# ==================== CONFIGURABLE PARAMETERS ====================
# Fixed parameters
UNIVERSAL_SIZE_BIT = 24
SET_SIZE = 1 << 18  # 2^18
INTERSECTION_SIZE = SET_SIZE // 2  # Half of set size
NUM_CLIENTS_PER_SERVER = 1
PORT_BASE = 21000  # Base port for tests

# Parameter ranges by mode
DEFAULT_TEST_MODE = "seed_optimization"
TEST_MODES = {
        "seed_optimization": {
        "description": "Fix k,t and scan seed size under different d values.",
        "prg_dd_values": [ 8],
        "seed_size_bit_values": [9], 
        "mom_k": 500,
        "mom_t": 8,
    },
    "kttune": {
        "description": "Tune t under fixed sketch-size budgets using k = kbsize*1024/8/t.",
        "prg_dd_values": [7],
        "kb_size_values_kb": [8, 16, 32, 64, 128],
        "mom_t_values": [3, 5, 7, 9, 11, 13],
        "k_formula": "k = (kb_size_kb * 1024) // (8 * t)",
    },
    "errorvsepsilon": {
        "description": "Fixed d and t, scan k to study error vs epsilon.",
        "prg_dd_values": [7],  # fixed d
        "mom_k_values": [100, 200, 400, 1000, 2500, 10000],
        "mom_t": 11,            # fixed t
        "seed_size_bit_values": [7],
    },
}

ACTIVE_TEST_MODE = DEFAULT_TEST_MODE
PRG_DD_VALUES = []
MOM_K_VALUES = []
MOM_T = None
MOM_KT_PAIRS = []
PSI_MODES = ["naive"]
PSI_MODE_DISPLAY_NAMES = {
    "naive": "naive",
    "naive_uniform": "naive_uniform",
    "naive_fourwise": "four_wise",
}
NUM_RUNS_PER_POINT = 1000  # Total runs per parameter combination
SEED_SIZE_BIT_VALUES = []
FILTER_PRG_DD = None
FILTER_MOM_K = None
FILTER_MOM_T = None

# Test parameters
TIMEOUT_SECONDS = 200
VERBOSE = False

# Output directories
RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
BASE_RUN_DIR = f"./experiments/run_{RUN_TIMESTAMP}"

RESULTS_DIR = os.path.join(BASE_RUN_DIR, "results")
DATA_DIR = os.path.join(BASE_RUN_DIR, "data")
PLOTS_DIR = os.path.join(BASE_RUN_DIR, "plots")

def set_run_directory(run_dir):
    """Set the run directory and update all related paths"""
    global BASE_RUN_DIR, RESULTS_DIR, DATA_DIR, PLOTS_DIR
    BASE_RUN_DIR = run_dir
    RESULTS_DIR = os.path.join(BASE_RUN_DIR, "results")
    DATA_DIR = os.path.join(BASE_RUN_DIR, "data")
    PLOTS_DIR = os.path.join(BASE_RUN_DIR, "plots")

# def apply_test_mode(mode_name):
#     """Apply a named parameter mode to global scan settings."""
#     global ACTIVE_TEST_MODE, PRG_DD_VALUES, MOM_K_VALUES, MOM_T, MOM_KT_PAIRS, PSI_MODES
#     if mode_name not in TEST_MODES:
#         raise ValueError(f"Unknown test mode: {mode_name}")

#     mode_cfg = TEST_MODES[mode_name]
#     ACTIVE_TEST_MODE = mode_name
#     PRG_DD_VALUES = list(mode_cfg["prg_dd_values"])
#     MOM_KT_PAIRS = []

#     if "kt_factor_pairs" in mode_cfg:
#         kt_values = mode_cfg.get("kt_values", sorted(mode_cfg["kt_factor_pairs"].keys()))
#         for kt in kt_values:
#             for mom_k, mom_t in mode_cfg["kt_factor_pairs"].get(kt, []):
#                 MOM_KT_PAIRS.append((int(mom_k), int(mom_t)))
#     elif "kb_size_values_kb" in mode_cfg and "mom_t_values" in mode_cfg:
#         for kb_size_kb in mode_cfg["kb_size_values_kb"]:
#             for mom_t in mode_cfg["mom_t_values"]:
#                 mom_t = int(mom_t)
#                 mom_k = (int(kb_size_kb) * 1024) // (8 * mom_t)
#                 if mom_k > 0:
#                     MOM_KT_PAIRS.append((mom_k, mom_t))
#     else:
#         mom_t = int(mode_cfg["mom_t"])
#         for mom_k in mode_cfg["mom_k_values"]:
#             MOM_KT_PAIRS.append((int(mom_k), mom_t))

#     MOM_K_VALUES = sorted({mom_k for mom_k, _ in MOM_KT_PAIRS})
#     MOM_T = MOM_KT_PAIRS[0][1] if MOM_KT_PAIRS else None
#     PSI_MODES = ["naive", "naive_uniform", "naive_fourwise"] if mode_name == "errorvsepsilon" else ["naive"]
def apply_test_mode(mode_name):
    """Apply a named parameter mode to global scan settings."""
    global ACTIVE_TEST_MODE, PRG_DD_VALUES, MOM_K_VALUES, MOM_T, MOM_KT_PAIRS, PSI_MODES, SEED_SIZE_BIT_VALUES

    if mode_name not in TEST_MODES:
        raise ValueError(f"Unknown test mode: {mode_name}")

    mode_cfg = TEST_MODES[mode_name]
    ACTIVE_TEST_MODE = mode_name
    PRG_DD_VALUES = list(mode_cfg["prg_dd_values"])
    MOM_KT_PAIRS = []
    SEED_SIZE_BIT_VALUES = list(mode_cfg.get("seed_size_bit_values", []))

    if mode_name == "seed_optimization":
        MOM_KT_PAIRS.append((int(mode_cfg["mom_k"]), int(mode_cfg["mom_t"])))
    elif "kt_factor_pairs" in mode_cfg:
        kt_values = mode_cfg.get("kt_values", sorted(mode_cfg["kt_factor_pairs"].keys()))
        for kt in kt_values:
            for mom_k, mom_t in mode_cfg["kt_factor_pairs"].get(kt, []):
                MOM_KT_PAIRS.append((int(mom_k), int(mom_t)))
    elif "kb_size_values_kb" in mode_cfg and "mom_t_values" in mode_cfg:
        for kb_size_kb in mode_cfg["kb_size_values_kb"]:
            for mom_t in mode_cfg["mom_t_values"]:
                mom_t = int(mom_t)
                mom_k = (int(kb_size_kb) * 1024) // (8 * mom_t)
                if mom_k > 0:
                    MOM_KT_PAIRS.append((mom_k, mom_t))
    else:
        mom_t = int(mode_cfg["mom_t"])
        for mom_k in mode_cfg["mom_k_values"]:
            MOM_KT_PAIRS.append((int(mom_k), mom_t))

    MOM_K_VALUES = sorted({mom_k for mom_k, _ in MOM_KT_PAIRS})
    MOM_T = MOM_KT_PAIRS[0][1] if MOM_KT_PAIRS else None

    if mode_name == "seed_optimization":
        PSI_MODES = ["naive"]
    elif mode_name == "errorvsepsilon":
        PSI_MODES = ["naive", "naive_uniform"]
    else:
        PSI_MODES = ["naive"]

def save_run_metadata():
    """Save active mode and core settings for this run."""
    metadata = {
    "mode": ACTIVE_TEST_MODE,
    "mode_config": TEST_MODES[ACTIVE_TEST_MODE],
    "mom_kt_pairs": MOM_KT_PAIRS,
    "seed_size_bit_values": SEED_SIZE_BIT_VALUES,
    "psi_modes": PSI_MODES,
    "num_runs_per_point": NUM_RUNS_PER_POINT,
    "universal_size_bit": UNIVERSAL_SIZE_BIT,
    "set_size": SET_SIZE,
    "intersection_size": INTERSECTION_SIZE,
    "num_clients_per_server": NUM_CLIENTS_PER_SERVER,
    "port_base": PORT_BASE,
    "filters": {
        "prg_dd": FILTER_PRG_DD,
        "mom_k": FILTER_MOM_K,
        "mom_t": FILTER_MOM_T,
    },
    }
    metadata_file = f"{RESULTS_DIR}/run_metadata.json"
    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=2)
    log(f"Run metadata saved to {metadata_file}")

def get_default_seed_size_from_cpp_config():
    """Read default seed size from include/config.h (GlobalConfig initializer)."""
    config_header = os.path.join(os.path.dirname(os.path.abspath(__file__)), "include", "config.h")
    try:
        with open(config_header, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return None, None, config_header

    match = re.search(
        r"seed_size\s*=\s*([^,;]+)\s*,\s*seed_size_bit\s*=\s*(\d+)",
        text
    )
    if not match:
        return None, None, config_header

    seed_expr = match.group(1).strip()
    seed_bit = int(match.group(2))

    # Prefer bit-derived value, and parse common "1<<N" expression if present.
    seed_size = 1 << seed_bit
    expr_match = re.search(r"1\s*<<\s*(\d+)", seed_expr)
    if expr_match:
        seed_size = 1 << int(expr_match.group(1))

    return seed_size, seed_bit, config_header

def log_errorvsepsilon_seed_size_once():
    """
    Log seed-size behavior for errorvsepsilon mode.
    """
    if ACTIVE_TEST_MODE != "errorvsepsilon":
        return
    if SEED_SIZE_BIT_VALUES:
        log(
            f"[errorvsepsilon] using explicit seed_size_bit_values={SEED_SIZE_BIT_VALUES}"
        )
        return
    seed_size, seed_size_bit, config_header = get_default_seed_size_from_cpp_config()
    if seed_size is None or seed_size_bit is None:
        log(
            "[errorvsepsilon] seed_size from config: <unknown> "
            f"(failed to parse {config_header})",
            "WARNING"
        )
        return
    log(
        f"[errorvsepsilon] seed_size from config: {seed_size} "
        f"(seed_size_bit={seed_size_bit}, source={config_header})"
    )

# ==================== HELPER FUNCTIONS ====================

def log(message, level="INFO"):
    """Print log message"""
    if VERBOSE or level in ["ERROR", "WARNING"]:
        print(f"[{level}] {message}")

def ensure_directories():
    """Ensure all required directories exist"""
    for directory in [RESULTS_DIR, DATA_DIR, PLOTS_DIR]:
        os.makedirs(directory, exist_ok=True)

def kill_existing_processes():
    """Kill any existing PSI processes"""
    try:
        subprocess.run("pkill -f psi_server", shell=True, capture_output=True)
        subprocess.run("pkill -f psi_client", shell=True, capture_output=True)
        time.sleep(0.1)
    except Exception as e:
        log(f"Warning: Could not check for existing processes: {e}", "WARNING")

def run_command(cmd, description=""):
    """Run a command and return success status"""
    log(f"Running: {description or cmd}")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        if result.returncode == 0:
            log(f"✓ {description or cmd} completed successfully")
            return True, result.stdout
        else:
            log(f"✗ {description or cmd} failed with return code {result.returncode}", "ERROR")
            return False, result.stderr
    except subprocess.TimeoutExpired:
        log(f"✗ {description or cmd} timed out", "ERROR")
        return False, "Timeout"
    except Exception as e:
        log(f"✗ {description or cmd} failed with exception: {e}", "ERROR")
        return False, str(e)

def start_process(cmd, description):
    """Start a process in background"""
    log(f"Starting {description}")
    process = subprocess.Popen(
        cmd, 
        shell=True, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid
    )
    time.sleep(0.2)
    return process

def wait_for_processes(processes, timeout=30):
    """Wait for multiple processes to complete"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        all_finished = True
        for process in processes:
            if process.poll() is None:
                all_finished = False
                break
        if all_finished:
            return True
        time.sleep(0.1)
    return False

def extract_psi_size_from_output(processes):
    """Extract PSI size from server output"""
    for process in processes:
        if process.poll() is not None:
            stderr = process.stderr.read().decode() if process.stderr else ""
            lines = stderr.split('\n')
            for line in lines:
                if 'Final PSI size' in line:
                    numbers = re.findall(r'\d+', line)
                    if numbers:
                        return int(numbers[-1])
    return None

# ==================== DATA GENERATION ====================

def generate_test_data(test_id, prg_dd, mom_k):
    """Generate test data for a specific test"""
    test_data_dir = f"{DATA_DIR}/test_{test_id:06d}"
    os.makedirs(test_data_dir, exist_ok=True)
    
    # Generate test data
    cmd = f"./bin/gendata --intersection_size={INTERSECTION_SIZE} " \
          f"--universal_size_bit={UNIVERSAL_SIZE_BIT} " \
          f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
          f"--set_size={SET_SIZE} " \
          f"--output_dir={test_data_dir}"
    
    success, output = run_command(cmd, f"Data generation for test {test_id}")
    if not success:
        return False, None
    
    # Read expected intersection from generated data
    server1_files = [f"{test_data_dir}/client{i}_1.txt" for i in range(1, NUM_CLIENTS_PER_SERVER + 1)]
    server2_files = [f"{test_data_dir}/client{i}_2.txt" for i in range(1, NUM_CLIENTS_PER_SERVER + 1)]
    
    # Check if all files exist
    all_files = server1_files + server2_files
    for file in all_files:
        if not os.path.exists(file):
            log(f"Error: Generated data file not found: {file}", "ERROR")
            return False, None
    
    # Read all sets and calculate expected intersection
    server1_sets = []
    server2_sets = []
    
    for f in server1_files:
        with open(f, 'r') as file:
            server1_sets.append(set(int(line.strip()) for line in file if line.strip()))
    
    for f in server2_files:
        with open(f, 'r') as file:
            server2_sets.append(set(int(line.strip()) for line in file if line.strip()))
    
    # Calculate intersection across all clients
    all_server1_elements = set()
    all_server2_elements = set()
    
    for s in server1_sets:
        all_server1_elements.update(s)
    for s in server2_sets:
        all_server2_elements.update(s)
    
    expected_intersection = all_server1_elements.intersection(all_server2_elements)
    expected_intersection_size = len(expected_intersection)
    
    return True, {
        'test_data_dir': test_data_dir,
        'expected_intersection_size': expected_intersection_size,
        'server1_files': server1_files,
        'server2_files': server2_files
    }

# ==================== SINGLE TEST EXECUTION ====================

def run_single_test(test_id, prg_dd, mom_k, mom_t, test_data_info, psi_mode="naive", seed_size_bit=None):
    """Run a single PSI test"""
    test_data_dir = test_data_info['test_data_dir']
    expected_intersection_size = test_data_info['expected_intersection_size']
    server1_files = test_data_info['server1_files']
    server2_files = test_data_info['server2_files']
    
    # Calculate port for this test
    port = PORT_BASE + test_id % 1000  # Use modulo to avoid port conflicts
    
    # Start servers
    # server1_cmd = f"./bin/psi_server -p 1 --port={port} --psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={mom_k} --mom_t={mom_t} --prg_dd={prg_dd}"
    # server2_cmd = f"./bin/psi_server -p 2 --port={port} --psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={mom_k} --mom_t={mom_t} --prg_dd={prg_dd}"
    
    seed_arg = f" --seed_size_bit={seed_size_bit}" if seed_size_bit is not None else ""

    server1_cmd = (
        f"./bin/psi_server -p 1 --port={port} --psi_mode={psi_mode} "
        f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode "
        f"--mom_k={mom_k} --mom_t={mom_t} --prg_dd={prg_dd}"
        f"{seed_arg}"
    )

    server2_cmd = (
        f"./bin/psi_server -p 2 --port={port} --psi_mode={psi_mode} "
        f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode "
        f"--mom_k={mom_k} --mom_t={mom_t} --prg_dd={prg_dd}"
        f"{seed_arg}"
    )
    # server1_cmd = f"./bin/psi_server -p 1 --port={port} --psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={mom_k} --mom_t={mom_t} --prg_dd={prg_dd} --seed_size_bit={seed_size_bit}"
    # server2_cmd = f"./bin/psi_server -p 2 --port={port} --psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={mom_k} --mom_t={mom_t} --prg_dd={prg_dd} --seed_size_bit={seed_size_bit}"
    server1_process = start_process(server1_cmd, f"Server 1 (test {test_id})")
    server2_process = start_process(server2_cmd, f"Server 2 (test {test_id})")
    
    # Start all clients
    client_processes = []
    
    # Start Server 1 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        data_file = server1_files[i]
        # client_cmd = f"./bin/psi_client -p {client_id} --port={port} --data_file={data_file} --psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={mom_k} --mom_t={mom_t} --prg_dd={prg_dd} --seed_size_bit={seed_size_bit}"
        # client_cmd = f"./bin/psi_client -p {client_id} --port={port} --data_file={data_file} --psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={mom_k} --mom_t={mom_t} --prg_dd={prg_dd}"
        client_cmd = (
            f"./bin/psi_client -p {client_id} --port={port} --data_file={data_file} "
            f"--psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} "
            f"--test_mode --mom_k={mom_k} --mom_t={mom_t} --prg_dd={prg_dd}"
            f"{seed_arg}"
        )
        client_process = start_process(client_cmd, f"Client {client_id} (Server 1, test {test_id})")
        client_processes.append(client_process)
    
    # Start Server 2 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        data_file = server2_files[i]
        client_cmd = (
            f"./bin/psi_client -p {client_id + NUM_CLIENTS_PER_SERVER} --port={port} --data_file={data_file} "
            f"--psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} "
            f"--test_mode --mom_k={mom_k} --mom_t={mom_t} --prg_dd={prg_dd}"
            f"{seed_arg}"
        )
        # client_cmd = f"./bin/psi_client -p {client_id + NUM_CLIENTS_PER_SERVER} --port={port} --data_file={data_file} --psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={mom_k} --mom_t={mom_t} --prg_dd={prg_dd} --seed_size_bit={seed_size_bit}"
        # client_cmd = f"./bin/psi_client -p {client_id + NUM_CLIENTS_PER_SERVER} --port={port} --data_file={data_file} --psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={mom_k} --mom_t={mom_t} --prg_dd={prg_dd}"
        client_process = start_process(client_cmd, f"Client {client_id} (Server 2, test {test_id})")
        client_processes.append(client_process)

    # Wait for all processes to complete
    all_processes = [server1_process, server2_process] + client_processes
    success = wait_for_processes(all_processes, TIMEOUT_SECONDS)
    
    if not success:
        log(f"Test {test_id} did not complete within timeout", "ERROR")
        return False, None
    
    # Check if all processes completed successfully
    for i, process in enumerate(all_processes):
        if process.returncode != 0:
            log(f"Test {test_id} process {i+1} failed with return code {process.returncode}", "ERROR")
            return False, None
    
    # Extract PSI size from server output
    actual_intersection_size = extract_psi_size_from_output([server1_process, server2_process])
    
    if actual_intersection_size is None:
        log(f"Test {test_id} could not extract PSI size", "ERROR")
        return False, None
    
    # Calculate error ratio
    error_ratio = (
    (actual_intersection_size - expected_intersection_size) / expected_intersection_size
    if expected_intersection_size > 0
    else float('inf')
    )
    
    result = {
        'test_id': test_id,
        'mode': ACTIVE_TEST_MODE,
        'psi_mode': psi_mode,
        'psi_mode_display': PSI_MODE_DISPLAY_NAMES.get(psi_mode, psi_mode),
        'prg_dd': prg_dd,
        'mom_k': mom_k,
        'mom_t': mom_t,
        'kt': mom_k * mom_t,
        'plain_sketct_size': (mom_k * mom_t * 8) / 1024.0,
        'expected_intersection_size': expected_intersection_size,
        'actual_intersection_size': actual_intersection_size,
        'error_ratio': error_ratio,
        'timestamp': datetime.now().isoformat()
    }

    if seed_size_bit is not None:
        result['seed_size_bit'] = seed_size_bit
        result['seed_size'] = 1 << seed_size_bit

    return True, result

# ==================== BATCH TESTING ====================

# def get_test_key(prg_dd, mom_k, mom_t, mode_name=None, psi_mode="naive"):
#     """Get unique key for a parameter combination"""
#     mode_name = mode_name or ACTIVE_TEST_MODE
#     return f"mode_{mode_name}_psi_{psi_mode}_prg_dd_{prg_dd}_mom_k_{mom_k}_mom_t_{mom_t}"

def get_test_key(prg_dd, mom_k, mom_t, seed_size_bit=None, mode_name=None, psi_mode="naive"):
    """Get unique key for a parameter combination"""
    mode_name = mode_name or ACTIVE_TEST_MODE
    return f"mode_{mode_name}_psi_{psi_mode}_prg_dd_{prg_dd}_mom_k_{mom_k}_mom_t_{mom_t}_seedbit_{seed_size_bit}"

def iter_filtered_param_pairs():
    """Yield active (prg_dd, mom_k, mom_t) tuples after optional CLI filters."""
    for prg_dd in PRG_DD_VALUES:
        if FILTER_PRG_DD is not None and prg_dd != FILTER_PRG_DD:
            continue
        for mom_k, mom_t in MOM_KT_PAIRS:
            if FILTER_MOM_K is not None and mom_k != FILTER_MOM_K:
                continue
            if FILTER_MOM_T is not None and mom_t != FILTER_MOM_T:
                continue
            yield prg_dd, mom_k, mom_t

def load_existing_results():
    """Load existing results from file"""
    results_file = f"{RESULTS_DIR}/batch_test_results.json"
    if os.path.exists(results_file):
        with open(results_file, 'r') as f:
            return json.load(f)
    return {}

def save_results(results):
    """Save results to file"""
    results_file = f"{RESULTS_DIR}/batch_test_results.json"
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2)
    log(f"Results saved to {results_file}")

# def run_batch_tests():
#     """Run batch tests for all parameter combinations"""
#     log("Starting batch tests...")
    
#     # Load existing results
#     results = load_existing_results()
#     save_run_metadata()
    
#     # Check if executables exist
#     required_files = ["./bin/gendata", "./bin/psi_server", "./bin/psi_client"]
#     for file in required_files:
#         if not os.path.exists(file):
#             log(f"Error: {file} not found. Please build the project first.", "ERROR")
#             return False
    
#     # Kill any existing PSI processes before starting
#     kill_existing_processes()
    
#     test_id = 0
#     total_tests = len(PSI_MODES) * len(PRG_DD_VALUES) * len(MOM_KT_PAIRS) * NUM_RUNS_PER_POINT
#     completed_tests = 0
    
#     for psi_mode in PSI_MODES:
#         for prg_dd in PRG_DD_VALUES:
#             for mom_k, mom_t in MOM_KT_PAIRS:
#                 test_key = get_test_key(prg_dd, mom_k, mom_t, psi_mode=psi_mode)

#                 if test_key in results and len(results[test_key]) >= NUM_RUNS_PER_POINT:
#                     log(f"Skipping completed parameter combination: psi_mode={psi_mode}, prg_dd={prg_dd}, mom_k={mom_k}, mom_t={mom_t}")
#                     completed_tests += NUM_RUNS_PER_POINT
#                     continue

#                 if test_key not in results:
#                     results[test_key] = []

#                 log(f"Testing psi_mode={psi_mode}, prg_dd={prg_dd}, mom_k={mom_k}, mom_t={mom_t} (completed: {len(results[test_key])}/{NUM_RUNS_PER_POINT})")

#                 for run in range(len(results[test_key]), NUM_RUNS_PER_POINT):
#                     test_id += 1
#                     completed_tests += 1

#                     log(f"Running test {test_id}/{total_tests} (run {run+1}/{NUM_RUNS_PER_POINT})")

#                     success, test_data_info = generate_test_data(test_id, prg_dd, mom_k)
#                     if not success:
#                         log(f"Failed to generate test data for test {test_id}", "ERROR")
#                         continue

#                     success, test_result = run_single_test(test_id, prg_dd, mom_k, mom_t, test_data_info, psi_mode=psi_mode)
#                     if success and test_result:
#                         results[test_key].append(test_result)
#                         save_results(results)
#                         log(f"Test {test_id} completed successfully. psi_mode={psi_mode}, error ratio: {test_result['error_ratio']:.3f}")
#                     else:
#                         log(f"Test {test_id} failed", "ERROR")

#                     if os.path.exists(test_data_info['test_data_dir']):
#                         shutil.rmtree(test_data_info['test_data_dir'])

#                     progress = (completed_tests / total_tests) * 100
#                     log(f"Progress: {progress:.1f}% ({completed_tests}/{total_tests})")
    
#     log("Batch tests completed!")
#     return True

def run_batch_tests():
    """Run batch tests for all parameter combinations"""
    log("Starting batch tests...")

    results = load_existing_results()
    save_run_metadata()

    required_files = ["./bin/gendata", "./bin/psi_server", "./bin/psi_client"]
    for file in required_files:
        if not os.path.exists(file):
            log(f"Error: {file} not found. Please build the project first.", "ERROR")
            return False

    kill_existing_processes()

    test_id = 0
    pair_list = list(iter_filtered_param_pairs())
    if not pair_list:
        log(
            "No parameter pairs matched current mode/filter settings",
            "ERROR"
        )
        return False

    if ACTIVE_TEST_MODE == "seed_optimization":
        total_tests = len(PSI_MODES) * len(pair_list) * len(SEED_SIZE_BIT_VALUES) * NUM_RUNS_PER_POINT
    elif ACTIVE_TEST_MODE == "errorvsepsilon":
        seed_size_bit_options = SEED_SIZE_BIT_VALUES if SEED_SIZE_BIT_VALUES else [None]
        total_tests = len(PSI_MODES) * len(pair_list) * len(seed_size_bit_options) * NUM_RUNS_PER_POINT
    else:
        total_tests = len(PSI_MODES) * len(pair_list) * NUM_RUNS_PER_POINT
    completed_tests = 0

        # ===== special handling for seed_optimization =====
    if ACTIVE_TEST_MODE == "seed_optimization":
        for psi_mode in PSI_MODES:
            for prg_dd, mom_k, mom_t in pair_list:
                for seed_size_bit in SEED_SIZE_BIT_VALUES:
                    test_key = get_test_key(
                        prg_dd, mom_k, mom_t,
                        seed_size_bit=seed_size_bit,
                        psi_mode=psi_mode
                    )

                    if test_key in results and len(results[test_key]) >= NUM_RUNS_PER_POINT:
                        log(
                            f"Skipping completed parameter combination: "
                            f"psi_mode={psi_mode}, prg_dd={prg_dd}, mom_k={mom_k}, "
                            f"mom_t={mom_t}, seed_size_bit={seed_size_bit}"
                        )
                        completed_tests += NUM_RUNS_PER_POINT
                        continue

                    if test_key not in results:
                        results[test_key] = []

                    log(
                        f"Testing psi_mode={psi_mode}, prg_dd={prg_dd}, mom_k={mom_k}, "
                        f"mom_t={mom_t}, seed_size_bit={seed_size_bit} "
                        f"(completed: {len(results[test_key])}/{NUM_RUNS_PER_POINT})"
                    )

                    for run in range(len(results[test_key]), NUM_RUNS_PER_POINT):
                        test_id += 1
                        completed_tests += 1

                        log(f"Running test {test_id}/{total_tests} (run {run+1}/{NUM_RUNS_PER_POINT})")

                        success, test_data_info = generate_test_data(test_id, prg_dd, mom_k)
                        if not success:
                            log(f"Failed to generate test data for test {test_id}", "ERROR")
                            continue

                        success, test_result = run_single_test(
                            test_id,
                            prg_dd,
                            mom_k,
                            mom_t,
                            test_data_info,
                            psi_mode=psi_mode,
                            seed_size_bit=seed_size_bit
                        )

                        if success and test_result:
                            results[test_key].append(test_result)
                            save_results(results)
                            log(
                                f"Test {test_id} completed successfully. "
                                f"psi_mode={psi_mode}, error ratio: {test_result['error_ratio']:.3f}"
                            )
                        else:
                            log(f"Test {test_id} failed", "ERROR")

                        if os.path.exists(test_data_info['test_data_dir']):
                            shutil.rmtree(test_data_info['test_data_dir'])

                        progress = (completed_tests / total_tests) * 100
                        log(f"Progress: {progress:.1f}% ({completed_tests}/{total_tests})")

        log("Batch tests completed!")
        return True

    # ===== special handling for errorvsepsilon =====
    if ACTIVE_TEST_MODE == "errorvsepsilon":
        seed_size_bit_options = SEED_SIZE_BIT_VALUES if SEED_SIZE_BIT_VALUES else [None]
        for prg_dd, mom_k, mom_t in pair_list:
            combo_specs = []
            for seed_size_bit in seed_size_bit_options:
                for psi_mode in PSI_MODES:
                    test_key = get_test_key(
                        prg_dd, mom_k, mom_t,
                        seed_size_bit=seed_size_bit,
                        psi_mode=psi_mode
                    )
                    if test_key not in results:
                        results[test_key] = []
                    combo_specs.append((seed_size_bit, psi_mode, test_key))

            min_completed_runs = min(len(results[test_key]) for _, _, test_key in combo_specs)

            if all(len(results[test_key]) >= NUM_RUNS_PER_POINT for _, _, test_key in combo_specs):
                log(
                    f"Skipping completed parameter combination: "
                    f"prg_dd={prg_dd}, mom_k={mom_k}, mom_t={mom_t}, "
                    f"seed_size_bits={seed_size_bit_options}, psi_modes={PSI_MODES}"
                )
                completed_tests += len(combo_specs) * NUM_RUNS_PER_POINT
                continue

            log(
                f"Testing shared-data group: prg_dd={prg_dd}, mom_k={mom_k}, mom_t={mom_t}, "
                f"seed_size_bits={seed_size_bit_options}, psi_modes={PSI_MODES} "
                f"(shared runs completed: {min_completed_runs}/{NUM_RUNS_PER_POINT})"
            )

            for run in range(min_completed_runs, NUM_RUNS_PER_POINT):
                pending_specs = [
                    (seed_size_bit, psi_mode, test_key)
                    for seed_size_bit, psi_mode, test_key in combo_specs
                    if len(results[test_key]) <= run
                ]

                already_done_count = len(combo_specs) - len(pending_specs)
                completed_tests += already_done_count
                if not pending_specs:
                    progress = (completed_tests / total_tests) * 100
                    log(f"Progress: {progress:.1f}% ({completed_tests}/{total_tests})")
                    continue

                test_id += 1
                pending_label = ", ".join(
                    f"{psi_mode}@seed={seed_size_bit if seed_size_bit is not None else 'default'}"
                    for seed_size_bit, psi_mode, _ in pending_specs
                )
                log(
                    f"Running shared-data test {test_id} for run {run+1}/{NUM_RUNS_PER_POINT} "
                    f"(pending: {pending_label})"
                )

                success, test_data_info = generate_test_data(test_id, prg_dd, mom_k)
                if not success:
                    log(f"Failed to generate test data for test {test_id}", "ERROR")
                    completed_tests += len(pending_specs)
                    progress = (completed_tests / total_tests) * 100
                    log(f"Progress: {progress:.1f}% ({completed_tests}/{total_tests})")
                    continue

                for seed_size_bit, psi_mode, test_key in pending_specs:
                    success, test_result = run_single_test(
                        test_id,
                        prg_dd,
                        mom_k,
                        mom_t,
                        test_data_info,
                        psi_mode=psi_mode,
                        seed_size_bit=seed_size_bit
                    )

                    completed_tests += 1

                    if success and test_result:
                        results[test_key].append(test_result)
                        save_results(results)
                        log(
                            f"Test {test_id} completed successfully. "
                            f"psi_mode={psi_mode}, seed_size_bit={seed_size_bit}, "
                            f"error ratio: {test_result['error_ratio']:.3f}"
                        )
                    else:
                        log(
                            f"Test {test_id} failed for psi_mode={psi_mode}, seed_size_bit={seed_size_bit}",
                            "ERROR"
                        )

                    progress = (completed_tests / total_tests) * 100
                    log(f"Progress: {progress:.1f}% ({completed_tests}/{total_tests})")

                if os.path.exists(test_data_info['test_data_dir']):
                    shutil.rmtree(test_data_info['test_data_dir'])

        log("Batch tests completed!")
        return True

    # ===== original logic for other modes =====
    for psi_mode in PSI_MODES:
        for prg_dd, mom_k, mom_t in pair_list:
                test_key = get_test_key(prg_dd, mom_k, mom_t, psi_mode=psi_mode)

                if test_key in results and len(results[test_key]) >= NUM_RUNS_PER_POINT:
                    log(f"Skipping completed parameter combination: psi_mode={psi_mode}, prg_dd={prg_dd}, mom_k={mom_k}, mom_t={mom_t}")
                    completed_tests += NUM_RUNS_PER_POINT
                    continue

                if test_key not in results:
                    results[test_key] = []

                log(f"Testing psi_mode={psi_mode}, prg_dd={prg_dd}, mom_k={mom_k}, mom_t={mom_t} (completed: {len(results[test_key])}/{NUM_RUNS_PER_POINT})")

                for run in range(len(results[test_key]), NUM_RUNS_PER_POINT):
                    test_id += 1
                    completed_tests += 1

                    log(f"Running test {test_id}/{total_tests} (run {run+1}/{NUM_RUNS_PER_POINT})")

                    success, test_data_info = generate_test_data(test_id, prg_dd, mom_k)
                    if not success:
                        log(f"Failed to generate test data for test {test_id}", "ERROR")
                        continue

                    success, test_result = run_single_test(test_id, prg_dd, mom_k, mom_t, test_data_info, psi_mode=psi_mode)
                    if success and test_result:
                        results[test_key].append(test_result)
                        save_results(results)
                        log(f"Test {test_id} completed successfully. psi_mode={psi_mode}, error ratio: {test_result['error_ratio']:.3f}")
                    else:
                        log(f"Test {test_id} failed", "ERROR")

                    if os.path.exists(test_data_info['test_data_dir']):
                        shutil.rmtree(test_data_info['test_data_dir'])

                    progress = (completed_tests / total_tests) * 100
                    log(f"Progress: {progress:.1f}% ({completed_tests}/{total_tests})")

    log("Batch tests completed!")
    return True

def merge_results_dict(target, incoming):
    """Merge {test_key: [results...]} dictionaries by list concatenation."""
    for test_key, test_results in incoming.items():
        if not isinstance(test_results, list):
            continue
        if test_key not in target:
            target[test_key] = []
        target[test_key].extend(test_results)

def run_errorvsepsilon_parallel_pipeline(parallel_workers, worker_num_runs=34):
    """
    For each (prg_dd, mom_k, mom_t), launch N worker runs in parallel.
    Each worker runs this script in errorvsepsilon mode with worker_num_runs.
    Then merge worker result files, analyze, and draw figure.
    """
    if ACTIVE_TEST_MODE != "errorvsepsilon":
        log("Parallel orchestrator only supports errorvsepsilon mode", "ERROR")
        return False

    ensure_directories()
    save_run_metadata()

    pair_list = list(iter_filtered_param_pairs())
    if not pair_list:
        log("No parameter pairs matched current filters", "ERROR")
        return False

    script_path = os.path.abspath(__file__)
    aggregate_results = {}
    pair_summaries = []
    run_failures = 0

    for pair_idx, (prg_dd, mom_k, mom_t) in enumerate(pair_list):
        pair_label = f"prg_dd_{prg_dd}_mom_k_{mom_k}_mom_t_{mom_t}"
        pair_root = os.path.join(BASE_RUN_DIR, "workers", pair_label)
        os.makedirs(pair_root, exist_ok=True)

        log(
            f"Launching {parallel_workers} workers for {pair_label}; "
            f"each worker uses num-runs={worker_num_runs}"
        )

        def worker_task(worker_idx):
            worker_run_dir = os.path.join(pair_root, f"worker_{worker_idx:02d}")
            worker_port_base = PORT_BASE + pair_idx * parallel_workers * 1000 + worker_idx * 1000
            started_at = datetime.now().isoformat()
            log(
                f"[{pair_label}] Worker {worker_idx:02d} started at {started_at} "
                f"(port_base={worker_port_base})"
            )
            cmd = [
                sys.executable,
                script_path,
                "--run-tests",
                "--param-mode", "errorvsepsilon",
                "--num-runs", str(worker_num_runs),
                "--run-dir", worker_run_dir,
                "--only-prg-dd", str(prg_dd),
                "--only-mom-k", str(mom_k),
                "--only-mom-t", str(mom_t),
                "--port-base", str(worker_port_base),
            ]
            if VERBOSE:
                cmd.append("--verbose")

            t0 = time.time()
            proc = subprocess.run(cmd, capture_output=True, text=True)
            finished_at = datetime.now().isoformat()
            elapsed_sec = time.time() - t0
            log(
                f"[{pair_label}] Worker {worker_idx:02d} finished at {finished_at} "
                f"(elapsed={elapsed_sec:.2f}s, returncode={proc.returncode})"
            )
            worker_result_file = os.path.join(worker_run_dir, "results", "batch_test_results.json")
            return {
                "worker_idx": worker_idx,
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_sec": elapsed_sec,
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "result_file": worker_result_file,
            }

        worker_reports = []
        with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
            futures = [executor.submit(worker_task, i + 1) for i in range(parallel_workers)]
            for future in futures:
                worker_reports.append(future.result())

        merged_workers = 0
        for report in worker_reports:
            if report["returncode"] != 0:
                run_failures += 1
                log(
                    f"Worker {report['worker_idx']} failed for {pair_label} "
                    f"(return code {report['returncode']})",
                    "WARNING"
                )
                if VERBOSE:
                    if report["stdout"]:
                        log(report["stdout"], "WARNING")
                    if report["stderr"]:
                        log(report["stderr"], "WARNING")
                continue

            if not os.path.exists(report["result_file"]):
                run_failures += 1
                log(
                    f"Worker {report['worker_idx']} missing results file for {pair_label}: "
                    f"{report['result_file']}",
                    "WARNING"
                )
                continue

            with open(report["result_file"], "r") as f:
                worker_results = json.load(f)
            merge_results_dict(aggregate_results, worker_results)
            merged_workers += 1

        pair_summaries.append({
            "pair_label": pair_label,
            "prg_dd": prg_dd,
            "mom_k": mom_k,
            "mom_t": mom_t,
            "workers_requested": parallel_workers,
            "workers_merged": merged_workers,
            "worker_reports": [
                {
                    "worker_idx": report["worker_idx"],
                    "started_at": report["started_at"],
                    "finished_at": report["finished_at"],
                    "elapsed_sec": report["elapsed_sec"],
                    "returncode": report["returncode"],
                }
                for report in sorted(worker_reports, key=lambda x: x["worker_idx"])
            ],
        })

        log(f"Merged {merged_workers}/{parallel_workers} worker result files for {pair_label}")

    save_results(aggregate_results)

    orchestrator_file = f"{RESULTS_DIR}/parallel_orchestrator_summary.json"
    with open(orchestrator_file, "w") as f:
        json.dump(
            {
                "mode": ACTIVE_TEST_MODE,
                "parallel_workers": parallel_workers,
                "worker_num_runs": worker_num_runs,
                "run_failures": run_failures,
                "pair_summaries": pair_summaries,
            },
            f,
            indent=2
        )
    log(f"Parallel orchestrator summary saved to {orchestrator_file}")

    analysis_results = analyze_results()
    if not analysis_results:
        log("Analysis failed after merging worker outputs", "ERROR")
        return False

    if not create_plots(analysis_results):
        log("Plot generation failed after merged analysis", "ERROR")
        return False

    return True

def recommend_parallel_workers_for_cores(target_cores=None):
    """Estimate worker count so concurrent PSI processes roughly match CPU cores."""
    detected_cores = os.cpu_count() or 1
    cores = target_cores if target_cores is not None else detected_cores
    # Per test we launch: 2 servers + 2 * NUM_CLIENTS_PER_SERVER clients.
    psi_processes_per_worker = 2 + 2 * NUM_CLIENTS_PER_SERVER
    workers = max(1, cores // psi_processes_per_worker)
    return workers, cores, psi_processes_per_worker

def compute_worker_num_runs(total_runs_per_point, workers):
    """Compute per-worker run count from total runs and worker count."""
    if workers <= 0:
        raise ValueError("workers must be > 0")
    base = total_runs_per_point // workers
    remainder = total_runs_per_point % workers
    # Use exact division when possible (e.g., 1000/8=125). If not divisible,
    # round up to avoid under-running the target.
    return base if remainder == 0 else base + 1
# ==================== DATA ANALYSIS ====================

def analyze_results():
    """Analyze test results and generate statistics"""
    log("Analyzing results...")
    
    results = load_existing_results()
    if not results:
        log("No results found", "ERROR")
        return False
    
    analysis_results = {}
    
    for test_key, test_results in results.items():
        if not test_results:
            continue

        mode_name = test_results[0].get('mode', DEFAULT_TEST_MODE)
        if mode_name != ACTIVE_TEST_MODE:
            continue

        if len(test_results) < NUM_RUNS_PER_POINT:
            log(
                f"Warning: {test_key} has only {len(test_results)} results, "
                f"expected {NUM_RUNS_PER_POINT}; including partial data in analysis",
                "WARNING"
            )
        
        sample_result = test_results[0]
        mode_name = sample_result.get('mode', DEFAULT_TEST_MODE)
        prg_dd = sample_result['prg_dd']
        mom_k = sample_result['mom_k']
        
        # Extract error ratios
        error_ratios = [result['error_ratio'] for result in test_results]
        
        # Take absolute values
        error_ratios = [abs(e) for e in error_ratios]
        
        # Remove highest and lowest 5%, but ensure we have at least 1 data point
        sorted_ratios = sorted(error_ratios)
        n_remove = max(1, int(len(sorted_ratios) * 0.05))
        
        # Ensure we don't remove all data points
        if n_remove * 2 >= len(sorted_ratios):
            n_remove = max(0, (len(sorted_ratios) - 1) // 2)
        
        filtered_ratios = sorted_ratios[n_remove:-n_remove] if n_remove > 0 else sorted_ratios
        
        # Check if we have data to analyze
        if len(filtered_ratios) == 0:
            log(f"Warning: No data left after filtering for {test_key}", "WARNING")
            continue
        
        # Calculate statistics (on absolute values)
        try:
            mean_error = np.mean(filtered_ratios)
            median_error = np.median(filtered_ratios)
            std_error = np.std(filtered_ratios) if len(filtered_ratios) > 1 else 0.0
            min_error = np.min(filtered_ratios)
            max_error = np.max(filtered_ratios)
        except Exception as e:
            log(f"Error calculating statistics for {test_key}: {e}", "ERROR")
            continue
        
        analysis_results[test_key] = {
            'mode': mode_name,
            'psi_mode': sample_result.get('psi_mode', 'naive'),
            'psi_mode_display': sample_result.get('psi_mode_display', PSI_MODE_DISPLAY_NAMES.get(sample_result.get('psi_mode', 'naive'), sample_result.get('psi_mode', 'naive'))),
            'prg_dd': prg_dd,
            'mom_k': mom_k,
            'mom_t': sample_result.get('mom_t', MOM_T),
            'kt': sample_result.get('kt', sample_result.get('sketch_size', sample_result['mom_k'] * sample_result.get('mom_t', MOM_T))),
            'mean_error': mean_error,
            'median_error': median_error,
            'std_error': std_error,
            'min_error': min_error,
            'max_error': max_error,
            'filtered_ratios': filtered_ratios,
            'total_runs': len(test_results),
            'filtered_runs': len(filtered_ratios),
            'seed_size_bit': sample_result.get('seed_size_bit'),
            'seed_size': sample_result.get('seed_size'),
        }
    
    # Save analysis results
    analysis_file = f"{RESULTS_DIR}/analysis_results.json"
    with open(analysis_file, 'w') as f:
        json.dump(analysis_results, f, indent=2)
    
    log(f"Analysis results saved to {analysis_file}")
    return analysis_results

def export_kttune_summary(analysis_results):
    """Export kttune summary: for each sketch size (kt), record t and mean error."""
    if not analysis_results:
        return False

    grouped = {}
    for result in analysis_results.values():
        if result.get('mode', DEFAULT_TEST_MODE) != "kttune":
            continue

        kt = int(result.get('kt', result['mom_k'] * result.get('mom_t', MOM_T)))
        mom_t = int(result.get('mom_t', MOM_T))
        key = (kt, mom_t)
        grouped.setdefault(key, []).append(float(result['mean_error']))

    if not grouped:
        log("No kttune data found to export", "WARNING")
        return False

    rows = []
    for (kt, mom_t), mean_errors in sorted(grouped.items(), key=lambda x: (x[0][0], x[0][1])):
        rows.append({
            "kt": kt,
            "plain_sketct_size": (kt * 8) / 1024.0,
            "mom_t": mom_t,
            "mean_error": float(np.mean(mean_errors)),
            "num_points": len(mean_errors),
        })

    summary_file = f"{RESULTS_DIR}/kttune_sketch_t_mean_error.json"
    with open(summary_file, "w") as f:
        json.dump(rows, f, indent=2)

    log(f"kttune summary saved to {summary_file}")
    return True

# ==================== PLOTTING ====================




def create_errorvsepsilon_boxplot_from_csv():
    """Create error-vs-epsilon plot from existing summary CSV without rewriting it."""
    if plt is None:
        return False

    summary_file = f"{RESULTS_DIR}/errorvsepsilon_median_summary.csv"
    if not os.path.exists(summary_file):
        log(f"Missing summary CSV: {summary_file}", "ERROR")
        return False

    df = pd.read_csv(summary_file)
    if df.empty:
        log("Summary CSV is empty", "ERROR")
        return False

    if 'seed_size_bit' not in df.columns:
        df['seed_size_bit'] = np.nan

    def normalize_seed_bit(value):
        if pd.isna(value):
            return None
        try:
            return int(value)
        except Exception:
            return None

    def seed_sort_key(seed_size_bit):
        if seed_size_bit is None:
            return (1, 0)
        return (0, int(seed_size_bit))

    def seed_label(seed_size_bit):
        if seed_size_bit is None:
            return "default seed"
        return f"seed=2^{seed_size_bit}"

    label_map = {
        'naive': '$\\Delta$-inadmissible distribution',
        'naive_uniform': 'Uniform distribution',
        'naive_fourwise': '4-wise independent distribution',
    }
    color_map = {
        'naive': 'navy',
        'naive_uniform': 'orange',
        'naive_fourwise': 'forestgreen',
    }
    marker_map = {
        'naive': 'o',
        'naive_uniform': 's',
        'naive_fourwise': '^',
    }
    linestyle_cycle = ['-', '--', '-.', ':']
    target_modes = ['naive', 'naive_uniform', 'naive_fourwise']

    fig, ax = plt.subplots(figsize=(15, 10))
    plotted_any = False

    for psi_mode in target_modes:
        mode_df = df[df['psi_mode'] == psi_mode]
        if mode_df.empty:
            continue

        mode_df = mode_df.copy()
        mode_df['seed_size_bit_norm'] = mode_df['seed_size_bit'].apply(normalize_seed_bit)
        seed_bits = sorted(mode_df['seed_size_bit_norm'].drop_duplicates().tolist(), key=seed_sort_key)

        for line_idx, seed_size_bit in enumerate(seed_bits):
            curve_df = mode_df[mode_df['seed_size_bit_norm'] == seed_size_bit].copy()
            if curve_df.empty:
                continue

            curve_df = curve_df.groupby('epsilon', as_index=False)['median_error'].mean()
            curve_df = curve_df.sort_values('epsilon')
            epsilons = curve_df['epsilon'].astype(float).to_list()
            medians = curve_df['median_error'].astype(float).to_list()
            if not epsilons:
                continue

            ax.plot(
                epsilons,
                medians,
                marker=marker_map.get(psi_mode, 'o'),
                linewidth=2.4,
                linestyle=linestyle_cycle[line_idx % len(linestyle_cycle)],
                color=color_map.get(psi_mode),
                label=f"{label_map.get(psi_mode, psi_mode)} ({seed_label(seed_size_bit)})"
            )
            plotted_any = True

    if not plotted_any:
        log("No valid rows for target psi modes in summary CSV", "ERROR")
        plt.close(fig)
        return False

    ax.set_xlabel(r'$1/\sqrt{k}$', fontsize=25)
    ax.set_ylabel('Median Accuracy Error', fontsize=25)
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper left', fontsize=25)

    box_plot_file = f"{PLOTS_DIR}/error_boxplot.png"
    fig.tight_layout()
    fig.savefig(box_plot_file, dpi=300, bbox_inches='tight')
    plt.close(fig)

    log(f"Error-vs-epsilon plot saved to {box_plot_file} (from existing CSV)")
    return True

def create_seed_optimization_d_stability_plot(analysis_results):
    """Create seed optimization plot with explicit y-axis truncation markers."""

    if not analysis_results:
        return False

    grouped = {}

    for result in analysis_results.values():
        if result.get('mode', DEFAULT_TEST_MODE) != "seed_optimization":
            continue

        d_val = int(result['prg_dd'])
        seed_size = int(result['seed_size'])
        median_error = float(result['median_error'])

        grouped.setdefault(d_val, []).append((seed_size, median_error))

    if not grouped:
        log("No valid data for seed optimization plot", "WARNING")
        return False

    fig, ax = plt.subplots(figsize=(10.5, 7.2))

    markers = ['o', 's', '^', 'D', 'v', '*', 'x']
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2']

    # Start truncation from the point (d=5, seed=2^7), as requested.
    trunc_start = None
    target_d = 5
    target_seed = 2**7
    for seed_size, y_val in grouped.get(target_d, []):
        if seed_size == target_seed:
            trunc_start = float(y_val)
            break

    all_y = np.array([float(y) for points in grouped.values() for _, y in points], dtype=float)
    if trunc_start is None:
        if all_y.size == 0:
            log("No valid y values for plotting", "WARNING")
            return False
        trunc_start = float(np.percentile(all_y, 80))
        log("Target point (d=5, 2^7) not found, fallback to percentile truncation.", "WARNING")

    y_top = max(trunc_start * 1.03, 0.05)

    clip_margin = max(y_top * 0.03, 1e-6)
    y_cap = y_top - clip_margin
    all_x = sorted({p[0] for pts in grouped.values() for p in pts})
    x_span = max(all_x) - min(all_x) if len(all_x) >= 2 else 1.0
    break_dx = max(x_span * 0.006, 0.6)

    # Plot curves with clipping markers
    for idx, d_val in enumerate(sorted(grouped.keys())):
        points = sorted(grouped[d_val], key=lambda x: x[0])
        x_vals = [p[0] for p in points]
        y_vals = [p[1] for p in points]

        color = colors[idx % len(colors)]
        marker_style = markers[idx % len(markers)]
        y_plot = []
        clipped_points = []
        for x, y in zip(x_vals, y_vals):
            forced_start = (d_val == target_d and x == target_seed)
            if forced_start or (y > trunc_start):
                y_plot.append(y_cap)
                clipped_points.append((x, y))
            else:
                y_plot.append(y)

        # For d=5, remove the connecting segment between 2^6 and 2^7 since
        # 2^7 is already shown as a truncated point.
        if d_val == 5 and (2**6 in x_vals) and (2**7 in x_vals):
            i1 = x_vals.index(2**6)
            i2 = x_vals.index(2**7)
            left_x = x_vals[:i1 + 1]
            left_y = y_plot[:i1 + 1]
            right_x = x_vals[i2:]
            right_y = y_plot[i2:]

            if len(left_x) >= 2:
                ax.plot(
                    left_x,
                    left_y,
                    linestyle='-',
                    linewidth=2.4,
                    color=color,
                    zorder=2
                )

            if len(right_x) >= 2:
                ax.plot(
                    right_x,
                    right_y,
                    linestyle='-',
                    linewidth=2.4,
                    color=color,
                    zorder=2
                )

            # Keep markers visible at every point.
            ax.plot(
                x_vals,
                y_plot,
                marker=marker_style,
                linestyle='None',
                markersize=7,
                color=color,
                zorder=3
            )
            # Proxy legend handle to show both line and marker for d=5.
            ax.plot(
                [], [],
                marker=marker_style,
                linestyle='-',
                linewidth=2.4,
                markersize=7,
                color=color,
                label=f'd = {d_val}'
            )
        else:
            ax.plot(
                x_vals,
                y_plot,
                marker=marker_style,
                linestyle='-',
                linewidth=2.4,
                markersize=7,
                color=color,
                label=f'd = {d_val}',
                zorder=2
            )

        # Draw break glyphs and clipped upward stubs for truncated points.
        for x, _ in clipped_points:
            dx = break_dx
            dy = y_top * 0.012
            y_break = y_cap + dy * 0.05

            # A short upward stub that exceeds y-limit and gets clipped by axes.
            ax.plot(
                [x, x],
                [y_cap + dy * 0.2, y_top + dy * 0.9],
                color=color,
                linewidth=2.2,
                alpha=0.9,
                solid_capstyle='round',
                zorder=8,
                clip_on=False
            )

            # Two small diagonal cuts on the line (similar to line-break style).
            ax.plot(
                [x - dx, x - dx / 3],
                [y_break - dy, y_break + dy],
                color='black',
                linewidth=2.4,
                solid_capstyle='round',
                zorder=10,
                clip_on=False
            )
            ax.plot(
                [x + dx / 3, x + dx],
                [y_break - dy, y_break + dy],
                color='black',
                linewidth=2.4,
                solid_capstyle='round',
                zorder=10,
                clip_on=False
            )

    # Labels
    ax.set_xlabel('Seed Size', fontsize=22, labelpad=10)
    ax.set_ylabel('Median Accuracy Error', fontsize=22, labelpad=10)

    # X ticks as powers of 2
    ax.set_xticks(all_x)
    ax.set_xticklabels(
        [f"$2^{{{int(np.log2(x))}}}$" for x in all_x],
        fontsize=17
    )
    ax.tick_params(axis='y', labelsize=17)

    ax.set_ylim(0, y_top)

    # Grid
    ax.grid(True, linestyle='--', alpha=0.25, linewidth=0.8)
    ax.set_axisbelow(True)

    # Keep all four spines
    ax.spines['top'].set_visible(True)
    ax.spines['right'].set_visible(True)
    ax.spines['left'].set_visible(True)
    ax.spines['bottom'].set_visible(True)

    ax.spines['top'].set_linewidth(1.2)
    ax.spines['right'].set_linewidth(1.2)
    ax.spines['left'].set_linewidth(1.2)
    ax.spines['bottom'].set_linewidth(1.2)

    # Draw // on y-axis, placed lower so it looks like a real axis break
    slash_kwargs = dict(
        transform=ax.transAxes,
        color='black',
        clip_on=False,
        linewidth=2.8,
        zorder=20,
        solid_capstyle='round'
    )

    y_axis_break = 0.88
    ax.plot(
        [-0.020, -0.008],
        [y_axis_break - 0.015, y_axis_break + 0.015],
        **slash_kwargs
    )
    ax.plot(
        [0.002, 0.014],
        [y_axis_break - 0.015, y_axis_break + 0.015],
        **slash_kwargs
    )

    # Legend
    ax.legend(
        fontsize=13,
        frameon=True,
        fancybox=True,
        framealpha=0.95,
        edgecolor='0.85',
        loc='upper right'
    )

    # Use fixed margins instead of tight_layout to avoid layout warnings
    # when using large fonts, legend, and off-axis break markers.
    fig.subplots_adjust(left=0.12, right=0.98, bottom=0.13, top=0.97)

    plot_file = f"{PLOTS_DIR}/seed_optimization_seedsize_vs_error.png"
    plt.savefig(plot_file, dpi=400)
    plt.close()

    log(f"Seed optimization plot saved to {plot_file}")
    return True

def create_epsilon_plots(analysis_results):
    """Create one combined plot for epsilon vs mean/max errors."""
    if plt is None:
        return False

    log("Creating epsilon analysis plots...")
    
    if not analysis_results:
        log("No analysis results for epsilon plots", "ERROR")
        return False
    
    epsilon_values = []
    mean_errors = []
    max_errors = []

    for result in analysis_results.values():
        if result.get('mode', DEFAULT_TEST_MODE) != ACTIVE_TEST_MODE:
            continue
        epsilon_values.append(1.0 / np.sqrt(result['mom_k']))
        mean_errors.append(result['mean_error'])
        max_errors.append(result['max_error'])

    if not epsilon_values:
        log("No valid epsilon data to plot", "WARNING")
        return False

    sorted_data = sorted(zip(epsilon_values, mean_errors, max_errors))
    epsilon_values, mean_errors, max_errors = zip(*sorted_data)

    plt.figure(figsize=(12, 8))
    plt.plot(epsilon_values, mean_errors, marker='o', linewidth=2.5, markersize=8, label='Mean Error')
    plt.plot(epsilon_values, max_errors, marker='s', linewidth=2.5, markersize=8, label='Max Error')
    plt.xlabel('ε', fontsize=14)
    plt.ylabel('Actual Error', fontsize=14)
    plt.title('Actual Error vs ε', fontsize=16)
    plt.legend(fontsize=12)
    plt.grid(True, alpha=0.3, which='both')

    plot_file = f"{PLOTS_DIR}/epsilon_vs_error_combined.png"
    plt.tight_layout()
    plt.savefig(plot_file, dpi=300, bbox_inches='tight')
    plt.close()

    log(f"Combined epsilon plot saved to {plot_file}")
    
    return True

def create_errorvsepsilon_boxplot(analysis_results):
    """Create error-vs-epsilon plot with one median-error curve per (psi_mode, seed_size_bit)."""
    if plt is None:
        return False

    if not analysis_results:
        log("No analysis results to plot", "ERROR")
        return False

    plt.rcParams.update({'font.size': 25})

    def seed_sort_key(seed_size_bit):
        if seed_size_bit is None:
            return (1, 0)
        return (0, int(seed_size_bit))

    def seed_label(seed_size_bit):
        if seed_size_bit is None:
            return "default seed"
        return f"seed=2^{seed_size_bit}"

    label_map = {
        'naive': '$\\Delta$-inadmissible distribution',
        'naive_uniform': 'Uniform distribution',
        'naive_fourwise': '4-wise independent distribution',
    }

    color_map = {
        'naive': 'navy',
        'naive_uniform': 'orange',
        'naive_fourwise': 'forestgreen',
    }

    marker_map = {
        'naive': 'o',
        'naive_uniform': 's',
        'naive_fourwise': '^',
    }
    linestyle_cycle = ['-', '--', '-.', ':']

    target_modes = ['naive', 'naive_uniform', 'naive_fourwise']

    grouped = {}
    summary_rows = []

    for test_key, result in analysis_results.items():
        if result.get('mode', DEFAULT_TEST_MODE) != ACTIVE_TEST_MODE:
            continue

        psi_mode = result.get('psi_mode', 'naive')
        psi_mode_display = result.get(
            'psi_mode_display',
            PSI_MODE_DISPLAY_NAMES.get(psi_mode, psi_mode)
        )

        mom_k = int(result['mom_k'])
        if psi_mode in {'naive_uniform', 'naive_fourwise'}:
            epsilon = 1.0 / np.sqrt(mom_k)
        elif psi_mode == 'naive':
            #epsilon = np.sqrt(np.log(np.log(2 ** 6)) / mom_k)
            epsilon = 1.0 / np.sqrt(mom_k)
        else:
            continue
        median_error = float(result['median_error'])
        seed_size_bit = result.get('seed_size_bit')
        if seed_size_bit is not None:
            seed_size_bit = int(seed_size_bit)
        grouped.setdefault((psi_mode, seed_size_bit), []).append((epsilon, median_error))

        summary_rows.append({
            'psi_mode': psi_mode,
            'psi_mode_display': psi_mode_display,
            'seed_size_bit': seed_size_bit,
            'prg_dd': int(result['prg_dd']),
            'mom_k': mom_k,
            'mom_t': int(result.get('mom_t', MOM_T)),
            'epsilon': epsilon,
            'median_error': median_error,
            'mean_error': float(result['mean_error']),
            'max_error': float(result['max_error']),
            'total_runs': int(result['total_runs']),
            'filtered_runs': int(result['filtered_runs']),
        })

    if not grouped:
        log("No valid data for error-vs-epsilon plot", "WARNING")
        return False

    # === 保存 summary ===
    summary_rows = sorted(
        summary_rows,
        key=lambda row: (
            row['psi_mode_display'],
            seed_sort_key(row['seed_size_bit']),
            row['prg_dd'],
            row['epsilon']
        )
    )
    summary_file = f"{RESULTS_DIR}/errorvsepsilon_median_summary.csv"
    pd.DataFrame(summary_rows).to_csv(summary_file, index=False)
    log(f"Median summary saved to {summary_file}")

    fig, ax = plt.subplots(figsize=(15, 10))
    plotted_any = False

    for psi_mode in target_modes:
        seed_size_bits = sorted(
            {seed_size_bit for mode, seed_size_bit in grouped if mode == psi_mode},
            key=seed_sort_key
        )
        if not seed_size_bits:
            continue

        for line_idx, seed_size_bit in enumerate(seed_size_bits):
            points = grouped.get((psi_mode, seed_size_bit), [])
            if not points:
                continue

            by_epsilon = {}
            for epsilon, median_error in points:
                by_epsilon.setdefault(float(epsilon), []).append(float(median_error))
            epsilons = sorted(by_epsilon.keys())
            medians = [float(np.mean(by_epsilon[eps])) for eps in epsilons]
            if not epsilons:
                continue

            ax.plot(
                epsilons,
                medians,
                marker=marker_map.get(psi_mode, 'o'),
                linewidth=2.4,
                linestyle=linestyle_cycle[line_idx % len(linestyle_cycle)],
                color=color_map.get(psi_mode),
                label=f"{label_map.get(psi_mode, psi_mode)} ({seed_label(seed_size_bit)})"
            )
            plotted_any = True

    if not plotted_any:
        log("No valid data lines for error-vs-epsilon plot", "WARNING")
        plt.close(fig)
        return False

    ax.set_xlabel(r'$1/\sqrt{k}$', fontsize=25)
    ax.set_ylabel('Accuracy Error', fontsize=25)

    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper left', fontsize=25)

    box_plot_file = f"{PLOTS_DIR}/error_boxplot.png"
    fig.tight_layout()
    fig.savefig(box_plot_file, dpi=300, bbox_inches='tight')
    plt.close(fig)

    log(f"Error-vs-epsilon plot saved to {box_plot_file}")

    return True

def create_plots(analysis_results, use_existing_csv=False):
    """Create plots from analysis results"""

    log("Creating plots...")
    if ACTIVE_TEST_MODE == "kttune":
        log("Skipping plot generation for kttune mode", "WARNING")
        return True
    if ACTIVE_TEST_MODE == "seed_optimization":
        return create_seed_optimization_d_stability_plot(analysis_results)
    if ACTIVE_TEST_MODE == "errorvsepsilon":
        if use_existing_csv:
            return create_errorvsepsilon_boxplot_from_csv()
        return create_errorvsepsilon_boxplot(analysis_results)


# ==================== MAIN FUNCTIONS ====================

def main():
    """Main function"""
    global VERBOSE, NUM_RUNS_PER_POINT, FILTER_PRG_DD, FILTER_MOM_K, FILTER_MOM_T, PORT_BASE

    parser = argparse.ArgumentParser(description='Batch PSI testing with parameter scanning')
    parser.add_argument('--run-tests', action='store_true', help='Run the batch tests')
    parser.add_argument('--analyze', action='store_true', help='Analyze existing results')
    parser.add_argument('--plot', action='store_true', help='Create plots (runs analysis first)')
    parser.add_argument('--plot-only', action='store_true', help='Create plots only from existing summary/analysis files (no re-analysis, no summary rewrite)')
    parser.add_argument('--errorvsepsilon-parallel', action='store_true', help='For each parameter pair in errorvsepsilon mode, launch parallel worker runs, merge results, then analyze+plot')
    parser.add_argument('--parallel-workers', type=int, default=None, help='Number of parallel workers per parameter pair in errorvsepsilon parallel mode (default: auto from core count)')
    parser.add_argument('--worker-num-runs', type=int, default=None, help='--num-runs value passed to each worker in errorvsepsilon parallel mode (default: auto = ceil(NUM_RUNS_PER_POINT / workers))')
    parser.add_argument('--target-cores', type=int, default=None, help='Target core budget for auto worker sizing (default: detected CPU cores)')
    parser.add_argument('--run-dir', type=str, default=None, help='Specify run directory (e.g., ./experiments/run_20260218_163843)')
    parser.add_argument('--param-mode', choices=sorted(TEST_MODES.keys()), default=DEFAULT_TEST_MODE, help='Select which parameter grid to use')
    parser.add_argument('--num-runs', type=int, default=NUM_RUNS_PER_POINT, help='Number of runs per parameter combination')
    parser.add_argument('--only-prg-dd', type=int, default=None, help='Optional filter: run only this prg_dd value')
    parser.add_argument('--only-mom-k', type=int, default=None, help='Optional filter: run only this mom_k value')
    parser.add_argument('--only-mom-t', type=int, default=None, help='Optional filter: run only this mom_t value')
    parser.add_argument('--port-base', type=int, default=PORT_BASE, help='Base port used by test runs (set automatically per worker in parallel mode)')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    
    args = parser.parse_args()
    
    VERBOSE = args.verbose
    NUM_RUNS_PER_POINT = args.num_runs
    FILTER_PRG_DD = args.only_prg_dd
    FILTER_MOM_K = args.only_mom_k
    FILTER_MOM_T = args.only_mom_t
    PORT_BASE = args.port_base
    apply_test_mode(args.param_mode)
    
    # Set run directory if specified
    if args.run_dir:
        set_run_directory(args.run_dir)
    
    # Ensure directories exist
    ensure_directories()
    log(f"Using param mode '{ACTIVE_TEST_MODE}' with prg_dd={PRG_DD_VALUES}, kt_pairs={MOM_KT_PAIRS}")
    log_errorvsepsilon_seed_size_once()

    if args.errorvsepsilon_parallel:
        if ACTIVE_TEST_MODE != "errorvsepsilon":
            log("--errorvsepsilon-parallel requires --param-mode errorvsepsilon", "ERROR")
            return 1
        if args.parallel_workers is not None and args.parallel_workers <= 0:
            log("--parallel-workers must be > 0", "ERROR")
            return 1
        if args.worker_num_runs is not None and args.worker_num_runs <= 0:
            log("--worker-num-runs must be > 0", "ERROR")
            return 1

        if args.target_cores is not None and args.target_cores <= 0:
            log("--target-cores must be > 0", "ERROR")
            return 1

        if args.parallel_workers is None:
            auto_workers, auto_cores, per_worker_psi = recommend_parallel_workers_for_cores(args.target_cores)
            selected_workers = auto_workers
            log(
                f"Auto worker sizing: target_cores={auto_cores}, "
                f"psi_processes_per_worker={per_worker_psi}, "
                f"selected_workers={selected_workers}"
            )
        else:
            selected_workers = args.parallel_workers

        if args.worker_num_runs is None:
            selected_worker_num_runs = compute_worker_num_runs(NUM_RUNS_PER_POINT, selected_workers)
            log(
                f"Auto worker-num-runs: total_runs_per_point={NUM_RUNS_PER_POINT}, "
                f"workers={selected_workers}, worker_num_runs={selected_worker_num_runs}"
            )
        else:
            selected_worker_num_runs = args.worker_num_runs

        log(
            f"Running parallel errorvsepsilon pipeline with workers={selected_workers}, "
            f"worker_num_runs={selected_worker_num_runs}"
        )
        success = run_errorvsepsilon_parallel_pipeline(
            parallel_workers=selected_workers,
            worker_num_runs=selected_worker_num_runs
        )
        if not success:
            log("Parallel errorvsepsilon pipeline failed", "ERROR")
            return 1
        log("Batch test pipeline completed successfully!")
        return 0
    
    if args.run_tests:
        log("Starting batch tests...")
        success = run_batch_tests()
        if not success:
            log("Batch tests failed", "ERROR")
            return 1
    
    if args.plot_only:
        if ACTIVE_TEST_MODE == "kttune":
            log("Exporting kttune summary from existing analysis...")
        elif ACTIVE_TEST_MODE == "errorvsepsilon":
            log("Creating plots from existing summary CSV (no re-analysis)...")
        else:
            log("Creating plots from existing analysis...")

        # plot-only for errorvsepsilon: use existing CSV only, do not rewrite CSV.
        if ACTIVE_TEST_MODE == "errorvsepsilon":
            success = create_plots(None, use_existing_csv=True)
            if not success:
                log("Plot-only failed: ensure errorvsepsilon_median_summary.csv exists", "ERROR")
                return 1
            return 0

        # plot-only for other modes: use existing analysis only (no auto-analyze).
        analysis_results = None
        analysis_file = f"{RESULTS_DIR}/analysis_results.json"
        if os.path.exists(analysis_file):
            with open(analysis_file, 'r') as f:
                analysis_results = json.load(f)
        if not analysis_results:
            log("No analysis results found", "ERROR")
            return 1
        if ACTIVE_TEST_MODE == "kttune":
            success = export_kttune_summary(analysis_results)
        else:
            success = create_plots(analysis_results)
        if not success:
            if ACTIVE_TEST_MODE == "kttune":
                log("kttune summary export failed", "WARNING")
            else:
                log("Plotting skipped because dependencies are unavailable", "WARNING")
            return 0
        return 0
    
    if args.analyze or args.plot:
        log("Analyzing results...")
        analysis_results = analyze_results()
        if not analysis_results:
            log("Analysis failed", "ERROR")
            return 1

    if args.plot:
        if ACTIVE_TEST_MODE == "kttune":
            log("Exporting kttune summary...")
            success = export_kttune_summary(analysis_results)
            if not success:
                log("kttune summary export failed", "WARNING")
        else:
            log("Creating plots...")
            success = create_plots(analysis_results)
            if not success:
                log("Plotting skipped because dependencies are unavailable", "WARNING")
    
    if not any([args.run_tests, args.analyze, args.plot]):
        # Default: run tests, then analyze and plot
        log("Running full batch test pipeline...")
        
        success = run_batch_tests()
        if not success:
            log("Batch tests failed", "ERROR")
            return 1
        
        analysis_results = analyze_results()
        if not analysis_results:
            log("Analysis failed", "ERROR")
            return 1

        if ACTIVE_TEST_MODE == "kttune":
            success = export_kttune_summary(analysis_results)
            if not success:
                log("kttune summary export failed", "WARNING")
        else:
            success = create_plots(analysis_results)
            if not success:
                log("Plotting skipped because dependencies are unavailable", "WARNING")
    
    log("Batch test pipeline completed successfully!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
