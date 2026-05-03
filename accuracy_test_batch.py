#!/usr/bin/env python3
"""
Batch test script for PSI parameter scanning
Tests different prg_dd and mom_k combinations with statistical analysis
"""

import os
import sys
import subprocess
import time
import shutil
import re
import json
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import argparse

# ==================== CONFIGURABLE PARAMETERS ====================
# Fixed Default parameters
UNIVERSAL_SIZE_BIT = 24
SET_SIZE = 1 << 18  # 2^18
INTERSECTION_SIZE = SET_SIZE // 2  # Half of set size
NUM_CLIENTS_PER_SERVER = 1
# Test parameters
TIMEOUT_SECONDS = 300  # For both data generation and PSI execution
VERBOSE = True
PORT_BASE = 21000  # Base port for tests
NUM_RUNS_PER_POINT = 1000  # Total runs per parameter combination
# Output directories
RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
BASE_RUN_DIR = f"./experiments/run_{RUN_TIMESTAMP}"

RESULTS_DIR = os.path.join(BASE_RUN_DIR, "results")
DATA_DIR = os.path.join(BASE_RUN_DIR, "data")
PLOTS_DIR = os.path.join(BASE_RUN_DIR, "plots")

# Parameter ranges by mode
DEFAULT_TEST_MODE = "seed_optimization"
TEST_MODES = {
    "seed_optimization": {
        "description": "Fix k,t and scan seed size under different d values.",
        "prg_dd_values": [5, 6, 7, 8],
        "seed_size_bit_values": [6, 7, 8, 9],
        "mom_k": 400,
        "mom_t": 11,
    },
    "errorvsepsilon": {
        "description": "Fixed d and t, scan k to study error vs epsilon.",
        "prg_dd_values": [7],  # fixed d
        "mom_k_values": [100, 200, 400, 1000, 2500, 10000],
        "seed_size_bit_values": [6,7,8],
    },
    "maxsetsupport": {
        "description": "Find maximum supported set-size exponent per (n,d,k,t) by iterative backoff.",
        "prg_dd_values": [4, 5, 6, 7, 8],
        "mom_k_values": [10000],
        "seed_size_bit_values": [6,7,8,9,10],
    },
}

# Max-set-support baseline exponents (total set size across both sides)
MAXSETSUPPORT_SET_SIZE_EXPONENTS = {
    64: {4: 12, 5: 15, 6: 18, 7: 21, 8: 24},
    128: {4: 14, 5: 17, 6: 21, 7: 24},
    256: {4: 16, 5: 20, 6: 24},
    512: {4: 18, 5: 22},
    1024: {4: 20},
}
MAXSETSUPPORT_N_VALUES = [64, 128, 256, 512, 1024]
MAXSETSUPPORT_D_VALUES = [4, 5, 6, 7, 8]
MAXSETSUPPORT_K_VALUES = [400]
MAX_GENDATA_INT = (1 << 31) - 1
MAX_GENDATA_UNIVERSAL_SIZE_BIT = 25


ACTIVE_TEST_MODE = DEFAULT_TEST_MODE
PRG_DD_VALUES = []
MOM_K_VALUES = []
MOM_T = None
MOM_KT_PAIRS = []
MOM_T_VALUES = []
PSI_MODES = ["naive"]
PSI_MODE_DISPLAY_NAMES = {
    "naive": "naive",
    "naive_uniform": "naive_uniform",
    "naive_fourwise": "four_wise",
}
SEED_SIZE_BIT_VALUES = []
#Parallel execution parameters
FILTER_PRG_DD = None
FILTER_MOM_K = None
FILTER_MOM_T = None
SKIP_KILL_EXISTING = False
MAXSETSUPPORT_SKIP_BASELINE_ABOVE = None


def set_run_directory(run_dir):
    """Set the run directory and update all related paths"""
    global BASE_RUN_DIR, RESULTS_DIR, DATA_DIR, PLOTS_DIR
    BASE_RUN_DIR = run_dir
    RESULTS_DIR = os.path.join(BASE_RUN_DIR, "results")
    DATA_DIR = os.path.join(BASE_RUN_DIR, "data")
    PLOTS_DIR = os.path.join(BASE_RUN_DIR, "plots")

def apply_test_mode(mode_name):
    """Apply a named parameter mode to global scan settings."""
    global ACTIVE_TEST_MODE, PRG_DD_VALUES, MOM_K_VALUES, MOM_T, MOM_KT_PAIRS, MOM_T_VALUES, PSI_MODES, SEED_SIZE_BIT_VALUES

    if mode_name not in TEST_MODES:
        raise ValueError(f"Unknown test mode: {mode_name}")

    mode_cfg = TEST_MODES[mode_name]
    ACTIVE_TEST_MODE = mode_name
    PRG_DD_VALUES = list(mode_cfg["prg_dd_values"])
    MOM_KT_PAIRS = []
    raw_t_values = mode_cfg.get("mom_t_values")
    if raw_t_values is None and "mom_t" in mode_cfg:
        raw_t_values = [mode_cfg["mom_t"]]
    MOM_T_VALUES = [int(v) for v in (raw_t_values or [])]
    SEED_SIZE_BIT_VALUES = list(mode_cfg.get("seed_size_bit_values", []))
    t_options = MOM_T_VALUES if MOM_T_VALUES else [None]

    if mode_name == "seed_optimization":
        MOM_KT_PAIRS.append((int(mode_cfg["mom_k"]), int(mode_cfg["mom_t"])))
    elif "kt_factor_pairs" in mode_cfg:
        kt_values = mode_cfg.get("kt_values", sorted(mode_cfg["kt_factor_pairs"].keys()))
        for kt in kt_values:
            for mom_k, mom_t in mode_cfg["kt_factor_pairs"].get(kt, []):
                MOM_KT_PAIRS.append((int(mom_k), int(mom_t)))
        MOM_T_VALUES = sorted({int(mom_t) for _, mom_t in MOM_KT_PAIRS})
    else:
        for mom_k in mode_cfg["mom_k_values"]:
            for mom_t in t_options:
                MOM_KT_PAIRS.append((int(mom_k), mom_t))

    MOM_K_VALUES = sorted({mom_k for mom_k, _ in MOM_KT_PAIRS})
    if MOM_KT_PAIRS:
        first_t = MOM_KT_PAIRS[0][1]
        MOM_T = get_valid_mom_t(first_t)
    else:
        MOM_T = None

    if mode_name == "seed_optimization":
        PSI_MODES = ["naive"]
    elif mode_name == "errorvsepsilon":
        PSI_MODES = ["naive", "naive_uniform"]
    elif mode_name == "maxsetsupport":
        PSI_MODES = ["naive"]
    else:
        PSI_MODES = ["naive"]

def save_run_metadata():
    """Save active mode and core settings for this run."""
    metadata = {
    "mode": ACTIVE_TEST_MODE,
    "mode_config": TEST_MODES[ACTIVE_TEST_MODE],
    "mom_kt_pairs": MOM_KT_PAIRS,
    "mom_t_values": MOM_T_VALUES,
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
    if ACTIVE_TEST_MODE == "maxsetsupport":
        metadata["maxsetsupport"] = {
            "baseline_set_size_exponents": MAXSETSUPPORT_SET_SIZE_EXPONENTS,
            "rule": "total size is across two clients; each side uses half",
            "support_threshold_rule": "actual_error <= 1/sqrt(k)",
            "k_values": MAXSETSUPPORT_K_VALUES,
            "skip_baseline_above_exponent": MAXSETSUPPORT_SKIP_BASELINE_ABOVE,
        }
    metadata_file = f"{RESULTS_DIR}/run_metadata.json"
    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=2)
    log(f"Run metadata saved to {metadata_file}")

def log_config_seedsize():
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

    config_header = os.path.join(os.path.dirname(os.path.abspath(__file__)), "include", "config.h")
    try:
        with open(config_header, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        log(
            "[errorvsepsilon] seed_size from config: <unknown> "
            f"(failed to parse {config_header})",
            "WARNING"
        )
        return

    match = re.search(
        r"seed_size\s*=\s*([^,;]+)\s*,\s*seed_size_bit\s*=\s*(\d+)",
        text
    )
    if not match:
        log(
            "[errorvsepsilon] seed_size from config: <unknown> "
            f"(failed to parse {config_header})",
            "WARNING"
        )
        return

    seed_expr = match.group(1).strip()
    seed_size_bit = int(match.group(2))

    # Prefer bit-derived value, and parse common "1<<N" expression if present.
    seed_size = 1 << seed_size_bit
    expr_match = re.search(r"1\s*<<\s*(\d+)", seed_expr)
    if expr_match:
        seed_size = 1 << int(expr_match.group(1))

    log(
        f"[errorvsepsilon] seed_size from config: {seed_size} "
        f"(seed_size_bit={seed_size_bit}, source={config_header})"
    )

def get_valid_mom_t(mom_t=None):
    """Resolve a valid mom_t: explicit value or config.h default (fallback 11)."""
    if mom_t is not None:
        return int(mom_t)

    config_header = os.path.join(os.path.dirname(os.path.abspath(__file__)), "include", "config.h")
    try:
        with open(config_header, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return 11

    match = re.search(r"mom_tt\s*=\s*(\d+)", text)
    return int(match.group(1)) if match else 11

def get_maxsetsupport_total_set_size(seed_size, prg_dd, exponent_override=None):
    """Return total set size for (n,d), using baseline exponent or override."""
    exponent = exponent_override
    if exponent is None:
        exponent = MAXSETSUPPORT_SET_SIZE_EXPONENTS.get(int(seed_size), {}).get(int(prg_dd))
    if exponent is None:
        return None, None
    return 1 << int(exponent), int(exponent)

def get_maxsetsupport_generation_params(total_set_size):
    """
    Convert total set size to gendata parameters.
    Total size is across two clients; each side gets half.
    """
    if total_set_size is None or total_set_size <= 0:
        return False, {"reason": "invalid_total_set_size"}

    if total_set_size % 2 != 0:
        return False, {"reason": "odd_total_set_size"}

    set_size_per_server = total_set_size // 2
    intersection_size = set_size_per_server // 2
    required_universal_size = 2 * set_size_per_server - intersection_size
    required_universal_bit = max(
        UNIVERSAL_SIZE_BIT,
        int(max(2, required_universal_size - 1)).bit_length()
    )

    if set_size_per_server > MAX_GENDATA_INT:
        return False, {
            "reason": "set_size_exceeds_int32",
            "set_size_per_server": set_size_per_server,
            "max_set_size": MAX_GENDATA_INT,
        }

    if required_universal_bit > MAX_GENDATA_UNIVERSAL_SIZE_BIT:
        return False, {
            "reason": "required_universal_size_bit_too_large",
            "required_universal_size_bit": required_universal_bit,
            "max_universal_size_bit": MAX_GENDATA_UNIVERSAL_SIZE_BIT,
        }

    return True, {
        "set_size_per_server": set_size_per_server,
        "intersection_size": intersection_size,
        "universal_size_bit": required_universal_bit,
    }

# ==================== HELPER FUNCTIONS ====================

def log(message, level="INFO"):
    """Print log message"""
    if VERBOSE or level in ["ERROR", "WARNING"]:
        print(f"[{level}] {message}")

def ensure_directories():
    """Ensure all required directories exist"""
    for directory in [RESULTS_DIR, DATA_DIR, PLOTS_DIR]:
        os.makedirs(directory, exist_ok=True)

def compute_median_abs_error(test_results):
    """Return median absolute error_ratio for a list of run results."""
    if not test_results:
        return None
    errors = []
    for row in test_results:
        try:
            errors.append(abs(float(row["error_ratio"])))
        except Exception:
            continue
    if not errors:
        return None
    return float(np.median(errors))

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

def generate_test_data(
    test_id,
    prg_dd,
    mom_k,
    set_size_override=None,
    intersection_size_override=None,
    universal_size_bit_override=None,
):
    """Generate test data for a specific test"""
    test_data_dir = f"{DATA_DIR}/test_{test_id:06d}"
    os.makedirs(test_data_dir, exist_ok=True)
    
    # Generate test data
    set_size = int(set_size_override) if set_size_override is not None else int(SET_SIZE)
    intersection_size = (
        int(intersection_size_override)
        if intersection_size_override is not None
        else int(INTERSECTION_SIZE)
    )
    universal_size_bit = (
        int(universal_size_bit_override)
        if universal_size_bit_override is not None
        else int(UNIVERSAL_SIZE_BIT)
    )

    cmd = f"./bin/gendata --intersection_size={intersection_size} " \
          f"--universal_size_bit={universal_size_bit} " \
          f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
          f"--set_size={set_size} " \
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
        'server2_files': server2_files,
        'set_size_per_server': set_size,
        'intersection_size': intersection_size,
        'universal_size_bit': universal_size_bit,
    }

# ==================== SINGLE TEST EXECUTION ====================

def run_single_test(
    test_id,
    prg_dd,
    mom_k,
    mom_t,
    test_data_info,
    psi_mode="naive",
    seed_size_bit=None,
    return_error=False,
):
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
    
    effective_mom_t = get_valid_mom_t(mom_t)
    seed_arg = f" --seed_size_bit={seed_size_bit}" if seed_size_bit is not None else ""
    mom_t_arg = f" --mom_t={mom_t}" if mom_t is not None else ""

    server1_cmd = (
        f"./bin/psi_server -p 1 --port={port} --psi_mode={psi_mode} "
        f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode "
        f"--mom_k={mom_k}{mom_t_arg} --prg_dd={prg_dd}"
        f"{seed_arg}"
    )

    server2_cmd = (
        f"./bin/psi_server -p 2 --port={port} --psi_mode={psi_mode} "
        f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode "
        f"--mom_k={mom_k}{mom_t_arg} --prg_dd={prg_dd}"
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
            f"--test_mode --mom_k={mom_k}{mom_t_arg} --prg_dd={prg_dd}"
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
            f"--test_mode --mom_k={mom_k}{mom_t_arg} --prg_dd={prg_dd}"
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
        if return_error:
            return False, None, "timeout"
        return False, None
    
    # Check if all processes completed successfully
    for i, process in enumerate(all_processes):
        if process.returncode != 0:
            log(f"Test {test_id} process {i+1} failed with return code {process.returncode}", "ERROR")
            if return_error:
                return False, None, f"process_{i+1}_returncode_{process.returncode}"
            return False, None
    
    # Extract PSI size from server output
    actual_intersection_size = extract_psi_size_from_output([server1_process, server2_process])
    
    if actual_intersection_size is None:
        log(f"Test {test_id} could not extract PSI size", "ERROR")
        if return_error:
            return False, None, "psi_size_parse_failed"
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
        'mom_t': effective_mom_t,
        'kt': mom_k * effective_mom_t,
        'plain_sketct_size': (mom_k * effective_mom_t * 8) / 1024.0,
        'expected_intersection_size': expected_intersection_size,
        'actual_intersection_size': actual_intersection_size,
        'error_ratio': error_ratio,
        'timestamp': datetime.now().isoformat(),
        'set_size_per_server': test_data_info.get('set_size_per_server'),
        'total_set_size': test_data_info.get('total_set_size'),
        'intersection_size': test_data_info.get('intersection_size'),
        'universal_size_bit': test_data_info.get('universal_size_bit'),
    }

    if seed_size_bit is not None:
        result['seed_size_bit'] = seed_size_bit
        result['seed_size'] = 1 << seed_size_bit

    if return_error:
        return True, result, None
    return True, result

# ==================== BATCH TESTING ====================

# def get_test_key(prg_dd, mom_k, mom_t, mode_name=None, psi_mode="naive"):
#     """Get unique key for a parameter combination"""
#     mode_name = mode_name or ACTIVE_TEST_MODE
#     return f"mode_{mode_name}_psi_{psi_mode}_prg_dd_{prg_dd}_mom_k_{mom_k}_mom_t_{mom_t}"

def get_test_key(
    prg_dd,
    mom_k,
    mom_t,
    seed_size_bit=None,
    mode_name=None,
    psi_mode="naive",
    total_set_size_exponent=None,
):
    """Get unique key for a parameter combination"""
    mode_name = mode_name or ACTIVE_TEST_MODE
    key = (
        f"mode_{mode_name}_psi_{psi_mode}_prg_dd_{prg_dd}_mom_k_{mom_k}"
        f"_mom_t_{mom_t}_seedbit_{seed_size_bit}"
    )
    if total_set_size_exponent is not None:
        key += f"_setexp_{int(total_set_size_exponent)}"
    return key

def iter_filtered_param_pairs():
    """Yield active (prg_dd, mom_k, mom_t) tuples after optional CLI filters."""
    for prg_dd in PRG_DD_VALUES:
        if FILTER_PRG_DD is not None and prg_dd != FILTER_PRG_DD:
            continue
        for mom_k, mom_t in MOM_KT_PAIRS:
            if FILTER_MOM_K is not None and mom_k != FILTER_MOM_K:
                continue
            effective_mom_t = get_valid_mom_t(mom_t)
            if FILTER_MOM_T is not None and effective_mom_t != FILTER_MOM_T:
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

    if SKIP_KILL_EXISTING:
        log("Skipping global PSI process cleanup (--skip-kill-existing enabled)")
    else:
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
    elif ACTIVE_TEST_MODE == "maxsetsupport":
        # Baseline count (actual count may be higher if exponent reductions are needed)
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

    # ===== special handling for maxsetsupport =====
    if ACTIVE_TEST_MODE == "maxsetsupport":
        skipped_rows = []
        attempt_rows = []
        for psi_mode in PSI_MODES:
            for prg_dd, mom_k, mom_t in pair_list:
                for seed_size_bit in SEED_SIZE_BIT_VALUES:
                    seed_size = 1 << int(seed_size_bit)
                    _, baseline_exp = get_maxsetsupport_total_set_size(seed_size, prg_dd)
                    if baseline_exp is None:
                        log(
                            f"Skipping combo with no baseline exponent: n={seed_size}, d={prg_dd}",
                            "WARNING",
                        )
                        continue
                    if (
                        MAXSETSUPPORT_SKIP_BASELINE_ABOVE is not None
                        and int(baseline_exp) > int(MAXSETSUPPORT_SKIP_BASELINE_ABOVE)
                    ):
                        skipped_row = {
                            "mode": "maxsetsupport",
                            "psi_mode": psi_mode,
                            "seed_size": seed_size,
                            "seed_size_bit": seed_size_bit,
                            "prg_dd": prg_dd,
                            "mom_k": mom_k,
                            "mom_t": get_valid_mom_t(mom_t),
                            "baseline_total_set_size_exponent": int(baseline_exp),
                            "tested_total_set_size_exponent": None,
                            "total_set_size": None,
                            "attempt_index": 0,
                            "status": "skipped_baseline_above_cap",
                            "reason": (
                                f"baseline exponent {int(baseline_exp)} exceeds cap "
                                f"{int(MAXSETSUPPORT_SKIP_BASELINE_ABOVE)}"
                            ),
                            "max_setexp_cap": int(MAXSETSUPPORT_SKIP_BASELINE_ABOVE),
                        }
                        skipped_rows.append(skipped_row)
                        attempt_rows.append(skipped_row)
                        log(
                            f"Skipping combo baseline above cap: n={seed_size}, d={prg_dd}, "
                            f"baseline=2^{int(baseline_exp)}, cap=2^{int(MAXSETSUPPORT_SKIP_BASELINE_ABOVE)}",
                            "WARNING",
                        )
                        continue

                    epsilon = 1.0 / np.sqrt(mom_k)
                    current_exp = int(baseline_exp)
                    supported = False
                    attempt_idx = 0

                    while current_exp >= 1:
                        total_set_size, total_exp = get_maxsetsupport_total_set_size(
                            seed_size, prg_dd, exponent_override=current_exp
                        )
                        feasible, params = get_maxsetsupport_generation_params(total_set_size)
                        if not feasible:
                            reason = params.get("reason", "unknown")
                            log(
                                f"Infeasible at set_total=2^{total_exp} for n={seed_size}, d={prg_dd}, "
                                f"k={mom_k}, t={mom_t}: {reason}",
                                "WARNING",
                            )
                            skipped_row = {
                                "mode": "maxsetsupport",
                                "psi_mode": psi_mode,
                                "seed_size": seed_size,
                                "seed_size_bit": seed_size_bit,
                                "prg_dd": prg_dd,
                                "mom_k": mom_k,
                                "mom_t": get_valid_mom_t(mom_t),
                                "baseline_total_set_size_exponent": int(baseline_exp),
                                "tested_total_set_size_exponent": int(total_exp),
                                "total_set_size": total_set_size,
                                "attempt_index": attempt_idx,
                                "status": "infeasible",
                                "reason": reason,
                                **params,
                            }
                            skipped_rows.append(skipped_row)
                            attempt_rows.append(skipped_row)
                            current_exp -= 1
                            attempt_idx += 1
                            continue

                        test_key = get_test_key(
                            prg_dd,
                            mom_k,
                            mom_t,
                            seed_size_bit=seed_size_bit,
                            psi_mode=psi_mode,
                            total_set_size_exponent=total_exp,
                        )
                        if test_key not in results:
                            results[test_key] = []

                        log(
                            f"Testing psi_mode={psi_mode}, n={seed_size}, d={prg_dd}, k={mom_k}, "
                            f"t={mom_t}, set_total=2^{total_exp} "
                            f"(completed: {len(results[test_key])}/{NUM_RUNS_PER_POINT}, "
                            f"threshold=1/sqrt(k)={epsilon:.6f})"
                        )

                        for run in range(len(results[test_key]), NUM_RUNS_PER_POINT):
                            test_id += 1
                            completed_tests += 1

                            log(
                                f"Running test {test_id}/{total_tests}+ "
                                f"(run {run + 1}/{NUM_RUNS_PER_POINT} at setexp={total_exp})"
                            )

                            success, test_data_info = generate_test_data(
                                test_id,
                                prg_dd,
                                mom_k,
                                set_size_override=params["set_size_per_server"],
                                intersection_size_override=params["intersection_size"],
                                universal_size_bit_override=params["universal_size_bit"],
                            )
                            if not success:
                                log(f"Failed to generate test data for test {test_id}", "ERROR")
                                continue

                            test_data_info["total_set_size"] = total_set_size
                            test_data_info["total_set_size_exponent"] = total_exp

                            success, test_result = run_single_test(
                                test_id,
                                prg_dd,
                                mom_k,
                                mom_t,
                                test_data_info,
                                psi_mode=psi_mode,
                                seed_size_bit=seed_size_bit,
                            )

                            if success and test_result:
                                test_result["total_set_size_exponent"] = total_exp
                                test_result["baseline_total_set_size_exponent"] = int(baseline_exp)
                                results[test_key].append(test_result)
                                save_results(results)
                                log(
                                    f"Test {test_id} completed successfully. "
                                    f"n={seed_size}, d={prg_dd}, k={mom_k}, setexp={total_exp}, "
                                    f"error ratio: {test_result['error_ratio']:.3f}"
                                )
                            else:
                                log(f"Test {test_id} failed", "ERROR")

                            if os.path.exists(test_data_info['test_data_dir']):
                                shutil.rmtree(test_data_info['test_data_dir'])

                            progress = (completed_tests / max(1, total_tests)) * 100
                            log(f"Progress: {progress:.1f}% ({completed_tests}/{total_tests}+)")  # + due to backoff retries

                        median_error = compute_median_abs_error(results[test_key])
                        status = "missing_data"
                        if median_error is not None:
                            status = "supported" if median_error <= epsilon else "not_supported"

                        attempt_row = {
                            "mode": "maxsetsupport",
                            "psi_mode": psi_mode,
                            "seed_size": seed_size,
                            "seed_size_bit": seed_size_bit,
                            "prg_dd": prg_dd,
                            "mom_k": mom_k,
                            "mom_t": get_valid_mom_t(mom_t),
                            "kt": mom_k * get_valid_mom_t(mom_t),
                            "baseline_total_set_size_exponent": int(baseline_exp),
                            "tested_total_set_size_exponent": int(total_exp),
                            "total_set_size": total_set_size,
                            "attempt_index": attempt_idx,
                            "total_runs": len(results[test_key]),
                            "median_error": median_error,
                            "threshold_1_over_sqrt_k": float(epsilon),
                            "is_supported": bool(status == "supported"),
                            "status": status,
                            "test_key": test_key,
                        }
                        attempt_rows.append(attempt_row)

                        if status == "supported":
                            supported = True
                            log(
                                f"Supported at set_total=2^{total_exp} for n={seed_size}, d={prg_dd}, "
                                f"k={mom_k}, t={mom_t}, median_error={median_error:.6f}, "
                                f"threshold={epsilon:.6f}"
                            )
                            break

                        log(
                            f"Not supported at set_total=2^{total_exp} for n={seed_size}, d={prg_dd}, "
                            f"k={mom_k}, t={mom_t}, median_error={median_error}, "
                            f"threshold={epsilon:.6f}; reducing exponent by 1",
                            "WARNING",
                        )
                        current_exp -= 1
                        attempt_idx += 1

                    if not supported:
                        log(
                            f"No supported exponent found for n={seed_size}, d={prg_dd}, "
                            f"k={mom_k}, t={mom_t} (baseline was 2^{baseline_exp})",
                            "WARNING",
                        )

        if skipped_rows:
            skipped_file = f"{RESULTS_DIR}/maxsetsupport_skipped.json"
            with open(skipped_file, "w") as f:
                json.dump(skipped_rows, f, indent=2)
            log(f"maxsetsupport skipped combinations saved to {skipped_file}")

        attempts_file = f"{RESULTS_DIR}/maxsetsupport_attempts.json"
        with open(attempts_file, "w") as f:
            json.dump(attempt_rows, f, indent=2)
        log(f"maxsetsupport attempt history saved to {attempts_file}")

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

def run_maxsetsupport_single_exp_worker(seed_size_bit, total_set_size_exp, psi_mode="naive"):
    """
    Worker mode: run exactly one (prg_dd, mom_k, mom_t, seed_size_bit, setexp) combo
    for NUM_RUNS_PER_POINT runs inside the current run directory.
    """
    if ACTIVE_TEST_MODE != "maxsetsupport":
        log("Single-exp worker only supports maxsetsupport mode", "ERROR")
        return False

    ensure_directories()
    save_run_metadata()

    results = load_existing_results()
    # Ensure each worker has a visible results file from the start.
    results_file = f"{RESULTS_DIR}/batch_test_results.json"
    if not os.path.exists(results_file):
        with open(results_file, "w") as f:
            json.dump(results, f, indent=2)
        log(f"Initialized worker results file at {results_file}")
    failure_file = f"{RESULTS_DIR}/maxsetsupport_worker_failures.json"
    failure_rows = []
    if os.path.exists(failure_file):
        try:
            with open(failure_file, "r") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    failure_rows = loaded
        except Exception as exc:
            log(f"Failed to load existing worker failure file: {exc}", "WARNING")

    def save_failures():
        with open(failure_file, "w") as f:
            json.dump(failure_rows, f, indent=2)

    required_files = ["./bin/gendata", "./bin/psi_server", "./bin/psi_client"]
    for file in required_files:
        if not os.path.exists(file):
            log(f"Error: {file} not found. Please build the project first.", "ERROR")
            return False

    if SKIP_KILL_EXISTING:
        log("Skipping global PSI process cleanup (--skip-kill-existing enabled)")
    else:
        kill_existing_processes()

    pair_list = list(iter_filtered_param_pairs())
    if len(pair_list) != 1:
        log(
            f"Single-exp worker requires exactly one filtered pair, got {len(pair_list)}",
            "ERROR",
        )
        return False

    prg_dd, mom_k, mom_t = pair_list[0]
    seed_size = 1 << int(seed_size_bit)
    baseline_total_set_size, baseline_exp = get_maxsetsupport_total_set_size(seed_size, prg_dd)
    if baseline_exp is None:
        log(f"No baseline exponent for n={seed_size}, d={prg_dd}", "ERROR")
        return False
    if (
        MAXSETSUPPORT_SKIP_BASELINE_ABOVE is not None
        and int(baseline_exp) > int(MAXSETSUPPORT_SKIP_BASELINE_ABOVE)
    ):
        log(
            f"Skipping single-exp worker due to baseline cap: "
            f"n={seed_size}, d={prg_dd}, baseline=2^{int(baseline_exp)}, "
            f"cap=2^{int(MAXSETSUPPORT_SKIP_BASELINE_ABOVE)}",
            "WARNING",
        )
        return True

    total_set_size, total_exp = get_maxsetsupport_total_set_size(
        seed_size, prg_dd, exponent_override=int(total_set_size_exp)
    )
    feasible, params = get_maxsetsupport_generation_params(total_set_size)
    if not feasible:
        log(
            f"Infeasible worker request for n={seed_size}, d={prg_dd}, set_total=2^{total_exp}: "
            f"{params.get('reason', 'unknown')}",
            "ERROR",
        )
        return False

    test_key = get_test_key(
        prg_dd,
        mom_k,
        mom_t,
        seed_size_bit=seed_size_bit,
        psi_mode=psi_mode,
        total_set_size_exponent=total_exp,
    )
    if test_key not in results:
        results[test_key] = []
        # Persist empty combo entry so worker progress is visible even before first success.
        save_results(results)

    existing_runs = len(results[test_key])
    log(
        f"[single-exp-worker] combo: psi_mode={psi_mode}, n={seed_size}, d={prg_dd}, "
        f"k={mom_k}, t={mom_t}, set_total=2^{total_exp}, "
        f"completed={existing_runs}/{NUM_RUNS_PER_POINT}"
    )

    test_id = 0
    for run in range(existing_runs, NUM_RUNS_PER_POINT):
        test_id += 1
        log(
            f"[single-exp-worker] run {run + 1}/{NUM_RUNS_PER_POINT} "
            f"(local test_id={test_id})"
        )

        success, test_data_info = generate_test_data(
            test_id,
            prg_dd,
            mom_k,
            set_size_override=params["set_size_per_server"],
            intersection_size_override=params["intersection_size"],
            universal_size_bit_override=params["universal_size_bit"],
        )
        if not success:
            log(f"Failed to generate test data for worker test {test_id}", "ERROR")
            failure_rows.append({
                "mode": "maxsetsupport",
                "stage": "generate_test_data",
                "status": "failed",
                "error_reason": "generate_test_data_failed",
                "test_id": test_id,
                "run_index": run + 1,
                "target_num_runs": NUM_RUNS_PER_POINT,
                "psi_mode": psi_mode,
                "seed_size_bit": seed_size_bit,
                "seed_size": seed_size,
                "prg_dd": prg_dd,
                "mom_k": mom_k,
                "mom_t": get_valid_mom_t(mom_t),
                "baseline_total_set_size_exponent": int(baseline_exp),
                "tested_total_set_size_exponent": int(total_exp),
                "timestamp": datetime.now().isoformat(),
            })
            save_failures()
            continue

        test_data_info["total_set_size"] = total_set_size
        test_data_info["total_set_size_exponent"] = total_exp

        success, test_result, error_reason = run_single_test(
            test_id,
            prg_dd,
            mom_k,
            mom_t,
            test_data_info,
            psi_mode=psi_mode,
            seed_size_bit=seed_size_bit,
            return_error=True,
        )
        if success and test_result:
            test_result["total_set_size_exponent"] = total_exp
            test_result["baseline_total_set_size_exponent"] = int(baseline_exp)
            results[test_key].append(test_result)
            save_results(results)
        else:
            log(f"Worker test {test_id} failed", "ERROR")
            failure_rows.append({
                "mode": "maxsetsupport",
                "stage": "run_single_test",
                "status": "failed",
                "error_reason": error_reason or "unknown",
                "test_id": test_id,
                "run_index": run + 1,
                "target_num_runs": NUM_RUNS_PER_POINT,
                "psi_mode": psi_mode,
                "seed_size_bit": seed_size_bit,
                "seed_size": seed_size,
                "prg_dd": prg_dd,
                "mom_k": mom_k,
                "mom_t": get_valid_mom_t(mom_t),
                "baseline_total_set_size_exponent": int(baseline_exp),
                "tested_total_set_size_exponent": int(total_exp),
                "test_data_dir": test_data_info.get("test_data_dir"),
                "timestamp": datetime.now().isoformat(),
            })
            save_failures()

        if os.path.exists(test_data_info["test_data_dir"]):
            shutil.rmtree(test_data_info["test_data_dir"])

    if failure_rows:
        log(
            f"[single-exp-worker] failure diagnostics saved to {failure_file} "
            f"(rows={len(failure_rows)})"
        )
    log("[single-exp-worker] completed")
    return True

def estimate_maxsetsupport_peak_temp_bytes(parallel_workers):
    """
    Estimate peak temporary data footprint during maxsetsupport parallel runs.
    This estimates concurrently active test_data directories only.
    """
    if ACTIVE_TEST_MODE != "maxsetsupport":
        return 0, 0

    pair_list = list(iter_filtered_param_pairs())
    if not pair_list:
        return 0, 0

    active_workers = max(1, min(int(parallel_workers), len(pair_list)))
    bytes_per_value = len(str((1 << MAX_GENDATA_UNIVERSAL_SIZE_BIT) - 1)) + 1  # text int + '\n'

    worst_values_per_worker = 0
    for prg_dd, _, _ in pair_list:
        max_values_this_pair = 0
        for seed_size_bit in SEED_SIZE_BIT_VALUES:
            seed_size = 1 << int(seed_size_bit)
            _, baseline_exp = get_maxsetsupport_total_set_size(seed_size, prg_dd)
            if baseline_exp is None or baseline_exp < 1:
                continue
            set_size_per_server = 1 << (int(baseline_exp) - 1)
            values = 2 * NUM_CLIENTS_PER_SERVER * set_size_per_server
            if values > max_values_this_pair:
                max_values_this_pair = values
        if max_values_this_pair > worst_values_per_worker:
            worst_values_per_worker = max_values_this_pair

    peak_temp_bytes = active_workers * worst_values_per_worker * bytes_per_value
    return peak_temp_bytes, active_workers

def run_seed_optimization_parallel_pipeline(parallel_workers, worker_num_runs=34):
    """
    For each (prg_dd, mom_k, mom_t), launch N worker runs in parallel.
    Each worker runs this script in seed_optimization mode with worker_num_runs.
    Then merge worker result files, analyze, and draw figure.
    """
    if ACTIVE_TEST_MODE != "seed_optimization":
        log("Parallel orchestrator only supports seed_optimization mode", "ERROR")
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
                "--param-mode", "seed_optimization",
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

    orchestrator_file = f"{RESULTS_DIR}/seed_optimization_parallel_orchestrator_summary.json"
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
                "--port-base", str(worker_port_base),
                "--skip-kill-existing",
            ]
            if mom_t is not None:
                cmd.extend(["--only-mom-t", str(mom_t)])
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

def run_maxsetsupport_parallel_pipeline(parallel_workers):
    """
    Sequentially process each (prg_dd, seed_size_bit) combo.
    For the active combo, launch parallel workers to fill missing runs for one
    tested exponent; repeat until NUM_RUNS_PER_POINT is reached, then decide
    support/backoff and continue.
    """
    if ACTIVE_TEST_MODE != "maxsetsupport":
        log("Parallel orchestrator only supports maxsetsupport mode", "ERROR")
        return False

    ensure_directories()
    save_run_metadata()

    pair_list = list(iter_filtered_param_pairs())
    if not pair_list:
        log("No parameter pairs matched current filters", "ERROR")
        return False

    if parallel_workers <= 0:
        log("parallel_workers must be > 0", "ERROR")
        return False

    script_path = os.path.abspath(__file__)
    aggregate_results = load_existing_results()
    all_attempt_rows = []
    all_skipped_rows = []
    run_failures = 0

    peak_temp_bytes, active_workers = estimate_maxsetsupport_peak_temp_bytes(parallel_workers)
    log(
        f"[maxsetsupport-parallel] estimated peak temp data footprint: "
        f"{peak_temp_bytes / (1024 ** 3):.2f} GiB (active_workers={active_workers})"
    )

    def worker_task(worker_idx, worker_run_dir, prg_dd, mom_k, mom_t, seed_size_bit, total_exp, worker_runs, worker_port_base, psi_mode):
        started_at = datetime.now().isoformat()
        pair_label = (
            f"prg_dd_{prg_dd}_mom_k_{mom_k}_mom_t_{mom_t}"
            f"_seedbit_{seed_size_bit}_setexp_{total_exp}"
        )
        log(f"[maxsetsupport] worker {pair_label}#{worker_idx:02d} started at {started_at} (port_base={worker_port_base}, runs={worker_runs})")

        cmd = [
            sys.executable,
            script_path,
            "--run-tests",
            "--param-mode", "maxsetsupport",
            "--num-runs", str(worker_runs),
            "--run-dir", worker_run_dir,
            "--only-prg-dd", str(prg_dd),
            "--only-mom-k", str(mom_k),
            "--port-base", str(worker_port_base),
            "--skip-kill-existing",
            "--maxsetsupport-worker-single",
            "--worker-seed-size-bit", str(seed_size_bit),
            "--worker-total-set-exp", str(total_exp),
            "--worker-psi-mode", str(psi_mode),
        ]
        if mom_t is not None:
            cmd.extend(["--only-mom-t", str(mom_t)])
        if VERBOSE:
            cmd.append("--verbose")

        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        finished_at = datetime.now().isoformat()
        elapsed_sec = time.time() - t0

        log(f"[maxsetsupport] worker {pair_label}#{worker_idx:02d} finished at {finished_at} (elapsed={elapsed_sec:.2f}s, returncode={proc.returncode})")
        result_dir = os.path.join(worker_run_dir, "results")
        return {
            "worker_idx": worker_idx,
            "pair_label": pair_label,
            "prg_dd": prg_dd,
            "mom_k": mom_k,
            "mom_t": mom_t,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_sec": elapsed_sec,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "result_file": os.path.join(result_dir, "batch_test_results.json"),
            "attempt_file": os.path.join(result_dir, "maxsetsupport_attempts.json"),
            "skipped_file": os.path.join(result_dir, "maxsetsupport_skipped.json"),
        }
    worker_reports_all = []
    combos_processed = 0
    successful_combos = 0

    for psi_mode in PSI_MODES:
        for pair_idx, (prg_dd, mom_k, mom_t) in enumerate(pair_list, start=1):
            for seed_size_bit in SEED_SIZE_BIT_VALUES:
                seed_size = 1 << int(seed_size_bit)
                _, baseline_exp = get_maxsetsupport_total_set_size(seed_size, prg_dd)
                combos_processed += 1

                if baseline_exp is None:
                    log(
                        f"Skipping combo with no baseline exponent: n={seed_size}, d={prg_dd}",
                        "WARNING",
                    )
                    continue
                if (
                    MAXSETSUPPORT_SKIP_BASELINE_ABOVE is not None
                    and int(baseline_exp) > int(MAXSETSUPPORT_SKIP_BASELINE_ABOVE)
                ):
                    row = {
                        "mode": "maxsetsupport",
                        "psi_mode": psi_mode,
                        "seed_size": seed_size,
                        "seed_size_bit": seed_size_bit,
                        "prg_dd": prg_dd,
                        "mom_k": mom_k,
                        "mom_t": get_valid_mom_t(mom_t),
                        "baseline_total_set_size_exponent": int(baseline_exp),
                        "tested_total_set_size_exponent": None,
                        "total_set_size": None,
                        "attempt_index": 0,
                        "status": "skipped_baseline_above_cap",
                        "reason": (
                            f"baseline exponent {int(baseline_exp)} exceeds cap "
                            f"{int(MAXSETSUPPORT_SKIP_BASELINE_ABOVE)}"
                        ),
                        "max_setexp_cap": int(MAXSETSUPPORT_SKIP_BASELINE_ABOVE),
                    }
                    all_skipped_rows.append(row)
                    all_attempt_rows.append(row)
                    log(
                        f"Skipping combo baseline above cap: n={seed_size}, d={prg_dd}, "
                        f"baseline=2^{int(baseline_exp)}, cap=2^{int(MAXSETSUPPORT_SKIP_BASELINE_ABOVE)}",
                        "WARNING",
                    )
                    continue

                epsilon = 1.0 / np.sqrt(mom_k)
                current_exp = int(baseline_exp)
                attempt_idx = 0
                supported = False

                while current_exp >= 1:
                    total_set_size, total_exp = get_maxsetsupport_total_set_size(
                        seed_size, prg_dd, exponent_override=current_exp
                    )
                    feasible, params = get_maxsetsupport_generation_params(total_set_size)
                    if not feasible:
                        reason = params.get("reason", "unknown")
                        row = {
                            "mode": "maxsetsupport",
                            "psi_mode": psi_mode,
                            "seed_size": seed_size,
                            "seed_size_bit": seed_size_bit,
                            "prg_dd": prg_dd,
                            "mom_k": mom_k,
                            "mom_t": get_valid_mom_t(mom_t),
                            "baseline_total_set_size_exponent": int(baseline_exp),
                            "tested_total_set_size_exponent": int(total_exp),
                            "total_set_size": total_set_size,
                            "attempt_index": attempt_idx,
                            "status": "infeasible",
                            "reason": reason,
                            **params,
                        }
                        all_skipped_rows.append(row)
                        all_attempt_rows.append(row)
                        log(
                            f"Infeasible at set_total=2^{total_exp} for n={seed_size}, d={prg_dd}, "
                            f"k={mom_k}, t={mom_t}: {reason}",
                            "WARNING",
                        )
                        current_exp -= 1
                        attempt_idx += 1
                        continue

                    test_key = get_test_key(
                        prg_dd,
                        mom_k,
                        mom_t,
                        seed_size_bit=seed_size_bit,
                        psi_mode=psi_mode,
                        total_set_size_exponent=total_exp,
                    )
                    if test_key not in aggregate_results:
                        aggregate_results[test_key] = []

                    previous_completed = -1
                    while len(aggregate_results[test_key]) < NUM_RUNS_PER_POINT:
                        remaining = NUM_RUNS_PER_POINT - len(aggregate_results[test_key])
                        workers_this_round = max(1, min(int(parallel_workers), remaining))
                        base_quota = remaining // workers_this_round
                        extra = remaining % workers_this_round
                        quotas = [base_quota + (1 if idx < extra else 0) for idx in range(workers_this_round)]
                        quotas = [q for q in quotas if q > 0]
                        workers_this_round = len(quotas)
                        if workers_this_round == 0:
                            break

                        combo_label = (
                            f"prg_dd_{prg_dd}_mom_k_{mom_k}_mom_t_{mom_t}"
                            f"_seedbit_{seed_size_bit}_setexp_{total_exp}"
                        )
                        combo_root = os.path.join(BASE_RUN_DIR, "workers", "maxsetsupport_combo", combo_label)
                        os.makedirs(combo_root, exist_ok=True)
                        round_id = len(aggregate_results[test_key]) + 1
                        log(
                            f"[maxsetsupport] combo {combo_label}: launching {workers_this_round} workers "
                            f"(remaining={remaining}, target={NUM_RUNS_PER_POINT}, round_id={round_id})"
                        )

                        worker_reports = []
                        with ThreadPoolExecutor(max_workers=workers_this_round) as executor:
                            futures = []
                            for widx, worker_runs in enumerate(quotas, start=1):
                                worker_run_dir = os.path.join(combo_root, f"round_{round_id:04d}", f"worker_{widx:02d}")
                                os.makedirs(worker_run_dir, exist_ok=True)
                                # IMPORTANT:
                                # run_single_test uses ports in [port_base, port_base+999] (test_id % 1000).
                                # So concurrent workers must be spaced by at least 1000 to avoid overlap.
                                # Also keep ports in valid TCP range (< 65536).
                                worker_port_base = PORT_BASE + (widx - 1) * 1000
                                if worker_port_base > 64000:
                                    log(
                                        f"Computed worker port_base={worker_port_base} exceeds safe range; "
                                        f"reduce parallel workers or lower --port-base",
                                        "ERROR",
                                    )
                                    run_failures += 1
                                    continue
                                futures.append(
                                    executor.submit(
                                        worker_task,
                                        widx,
                                        worker_run_dir,
                                        prg_dd,
                                        mom_k,
                                        mom_t,
                                        seed_size_bit,
                                        total_exp,
                                        worker_runs,
                                        worker_port_base,
                                        psi_mode,
                                    )
                                )
                            for future in futures:
                                worker_reports.append(future.result())

                        for report in sorted(worker_reports, key=lambda x: x["worker_idx"]):
                            worker_reports_all.append(report)
                            if report["returncode"] != 0:
                                run_failures += 1
                                log(
                                    f"Worker failed for {report['pair_label']} "
                                    f"(return code {report['returncode']})",
                                    "WARNING",
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
                                    f"Missing results file for {report['pair_label']}: {report['result_file']}",
                                    "WARNING",
                                )
                                continue

                            with open(report["result_file"], "r") as f:
                                worker_results = json.load(f)
                            merge_results_dict(aggregate_results, worker_results)

                        # Keep this combo capped to exactly NUM_RUNS_PER_POINT after merge.
                        if len(aggregate_results[test_key]) > NUM_RUNS_PER_POINT:
                            aggregate_results[test_key] = aggregate_results[test_key][:NUM_RUNS_PER_POINT]

                        save_results(aggregate_results)
                        now_completed = len(aggregate_results[test_key])
                        log(
                            f"[maxsetsupport] combo progress n={seed_size}, d={prg_dd}, setexp={total_exp}: "
                            f"{now_completed}/{NUM_RUNS_PER_POINT}"
                        )

                        if now_completed <= previous_completed:
                            log(
                                f"No progress for combo n={seed_size}, d={prg_dd}, setexp={total_exp}; "
                                "aborting this exponent attempt",
                                "WARNING",
                            )
                            break
                        previous_completed = now_completed

                    median_error = compute_median_abs_error(aggregate_results[test_key])
                    status = "missing_data"
                    if median_error is not None:
                        status = "supported" if median_error <= epsilon else "not_supported"

                    attempt_row = {
                        "mode": "maxsetsupport",
                        "psi_mode": psi_mode,
                        "seed_size": seed_size,
                        "seed_size_bit": seed_size_bit,
                        "prg_dd": prg_dd,
                        "mom_k": mom_k,
                        "mom_t": get_valid_mom_t(mom_t),
                        "kt": mom_k * get_valid_mom_t(mom_t),
                        "baseline_total_set_size_exponent": int(baseline_exp),
                        "tested_total_set_size_exponent": int(total_exp),
                        "total_set_size": total_set_size,
                        "attempt_index": attempt_idx,
                        "total_runs": len(aggregate_results[test_key]),
                        "median_error": median_error,
                        "threshold_1_over_sqrt_k": float(epsilon),
                        "is_supported": bool(status == "supported"),
                        "status": status,
                        "test_key": test_key,
                    }
                    all_attempt_rows.append(attempt_row)

                    if status == "supported":
                        supported = True
                        successful_combos += 1
                        log(
                            f"Supported at set_total=2^{total_exp} for n={seed_size}, d={prg_dd}, "
                            f"k={mom_k}, t={mom_t}, median_error={median_error:.6f}, "
                            f"threshold={epsilon:.6f}"
                        )
                        break

                    log(
                        f"Not supported at set_total=2^{total_exp} for n={seed_size}, d={prg_dd}, "
                        f"k={mom_k}, t={mom_t}, median_error={median_error}, "
                        f"threshold={epsilon:.6f}; reducing exponent by 1",
                        "WARNING",
                    )
                    current_exp -= 1
                    attempt_idx += 1

                if not supported:
                    log(
                        f"No supported exponent found for n={seed_size}, d={prg_dd}, "
                        f"k={mom_k}, t={mom_t} (baseline was 2^{baseline_exp})",
                        "WARNING",
                    )

    if all_attempt_rows:
        attempts_file = f"{RESULTS_DIR}/maxsetsupport_attempts.json"
        with open(attempts_file, "w") as f:
            json.dump(all_attempt_rows, f, indent=2)
        log(f"Merged maxsetsupport attempts saved to {attempts_file}")

    if all_skipped_rows:
        skipped_file = f"{RESULTS_DIR}/maxsetsupport_skipped.json"
        with open(skipped_file, "w") as f:
            json.dump(all_skipped_rows, f, indent=2)
        log(f"Merged maxsetsupport skipped rows saved to {skipped_file}")

    orchestrator_file = f"{RESULTS_DIR}/parallel_orchestrator_summary.json"
    with open(orchestrator_file, "w") as f:
        json.dump(
            {
                "mode": ACTIVE_TEST_MODE,
                "parallel_workers_requested": parallel_workers,
                "parallel_workers_active": min(parallel_workers, active_workers),
                "run_failures": run_failures,
                "estimated_peak_temp_bytes": peak_temp_bytes,
                "combos_processed": combos_processed,
                "combos_supported": successful_combos,
                "worker_reports": [
                    {
                        "worker_idx": r["worker_idx"],
                        "pair_label": r["pair_label"],
                        "prg_dd": r["prg_dd"],
                        "mom_k": r["mom_k"],
                        "mom_t": r["mom_t"],
                        "started_at": r["started_at"],
                        "finished_at": r["finished_at"],
                        "elapsed_sec": r["elapsed_sec"],
                        "returncode": r["returncode"],
                    }
                    for r in sorted(worker_reports_all, key=lambda x: (x["pair_label"], x["worker_idx"]))
                ],
                "workers_merged": len([r for r in worker_reports_all if r["returncode"] == 0]),
            },
            f,
            indent=2
        )
    log(f"Parallel orchestrator summary saved to {orchestrator_file}")

    analysis_results = analyze_results()
    if not analysis_results:
        log("Analysis failed after merging worker outputs", "ERROR")
        return False

    success = summarize_maxsetsupport(analysis_results)
    if not success:
        log("maxsetsupport summary export failed after merge", "ERROR")
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
            'total_set_size': sample_result.get('total_set_size'),
            'total_set_size_exponent': sample_result.get('total_set_size_exponent'),
            'set_size_per_server': sample_result.get('set_size_per_server'),
        }
    
    # Save analysis results
    analysis_file = f"{RESULTS_DIR}/analysis_results.json"
    with open(analysis_file, 'w') as f:
        json.dump(analysis_results, f, indent=2)
    
    log(f"Analysis results saved to {analysis_file}")
    return analysis_results

def summarize_maxsetsupport(analysis_results):
    """Summarize max supported set-size exponent and full reduction history."""
    def safe_int(value, default=None):
        try:
            return int(value)
        except Exception:
            return default

    attempt_file = f"{RESULTS_DIR}/maxsetsupport_attempts.json"
    attempt_rows = []

    if os.path.exists(attempt_file):
        try:
            with open(attempt_file, "r") as f:
                attempt_rows = json.load(f)
        except Exception as exc:
            log(f"Failed to load maxsetsupport attempt file: {exc}", "WARNING")

    # Fallback for legacy runs without attempt history.
    if not attempt_rows and analysis_results:
        for row in analysis_results.values():
            if row.get("mode") != "maxsetsupport":
                continue
            mom_k = int(row.get("mom_k"))
            epsilon = float(1.0 / np.sqrt(mom_k))
            median_error = float(row.get("median_error"))
            attempt_rows.append({
                "mode": "maxsetsupport",
                "psi_mode": row.get("psi_mode", "naive"),
                "seed_size": int(row.get("seed_size")),
                "seed_size_bit": row.get("seed_size_bit"),
                "prg_dd": int(row.get("prg_dd")),
                "mom_k": mom_k,
                "mom_t": int(row.get("mom_t", get_valid_mom_t(None))),
                "kt": int(row.get("kt", mom_k * int(row.get("mom_t", get_valid_mom_t(None))))),
                "baseline_total_set_size_exponent": row.get("total_set_size_exponent"),
                "tested_total_set_size_exponent": row.get("total_set_size_exponent"),
                "total_set_size": row.get("total_set_size"),
                "attempt_index": 0,
                "total_runs": int(row.get("total_runs", 0)),
                "median_error": median_error,
                "threshold_1_over_sqrt_k": epsilon,
                "is_supported": bool(median_error <= epsilon),
                "status": ("supported" if median_error <= epsilon else "not_supported"),
            })

    if not attempt_rows:
        log("No maxsetsupport attempt data found", "ERROR")
        return False

    grouped = {}
    for row in attempt_rows:
        try:
            key = (
                int(row.get("seed_size")),
                int(row.get("prg_dd")),
                int(row.get("mom_k")),
                int(row.get("mom_t", get_valid_mom_t(None))),
                row.get("psi_mode", "naive"),
            )
        except Exception:
            continue
        grouped.setdefault(key, []).append(row)

    combo_rows = []
    for key, rows in grouped.items():
        seed_size, prg_dd, mom_k, mom_t, psi_mode = key
        rows_sorted = sorted(
            rows,
            key=lambda r: safe_int(r.get("tested_total_set_size_exponent"), -1),
            reverse=True,
        )

        baseline_exp = rows_sorted[0].get("baseline_total_set_size_exponent")
        if baseline_exp is None:
            baseline_exp = rows_sorted[0].get("tested_total_set_size_exponent")
        baseline_exp = safe_int(baseline_exp, -1)

        supported_rows = [r for r in rows_sorted if bool(r.get("is_supported"))]
        best_supported_exp = None
        best_supported_median = None
        final_status = "not_supported"

        if supported_rows:
            best_row = max(supported_rows, key=lambda r: safe_int(r.get("tested_total_set_size_exponent"), -1))
            best_supported_exp = safe_int(best_row.get("tested_total_set_size_exponent"), -1)
            best_supported_median = best_row.get("median_error")
            final_status = "supported"
        elif any(r.get("status") == "skipped_baseline_above_cap" for r in rows_sorted):
            final_status = "skipped_baseline_above_cap"
        elif any(r.get("status") == "infeasible" for r in rows_sorted):
            final_status = "infeasible"

        if best_supported_exp is not None and best_supported_exp >= 1:
            reductions = baseline_exp - best_supported_exp
            max_supported_total_set_size = int(1 << best_supported_exp)
        else:
            reductions = None
            max_supported_total_set_size = None
            best_supported_exp = None

        combo_rows.append({
            "seed_size": seed_size,
            "seed_size_bit": int(np.log2(seed_size)),
            "prg_dd": prg_dd,
            "mom_k": mom_k,
            "mom_t": mom_t,
            "kt": mom_k * mom_t,
            "psi_mode": psi_mode,
            "baseline_total_set_size_exponent": baseline_exp,
            "max_supported_total_set_size_exponent": best_supported_exp,
            "max_supported_total_set_size": max_supported_total_set_size,
            "reductions_from_baseline": reductions,
            "best_supported_median_error": best_supported_median,
            "threshold_1_over_sqrt_k": float(1.0 / np.sqrt(mom_k)),
            "num_attempts": len(rows_sorted),
            "final_status": final_status,
        })

    summary_json = {
        "mode": "maxsetsupport",
        "rule": "start from baseline exponent; reduce by 1 until median actual_error <= 1/sqrt(k)",
        "baseline_set_size_exponents": MAXSETSUPPORT_SET_SIZE_EXPONENTS,
        "attempt_rows": attempt_rows,
        "combo_rows": combo_rows,
    }

    summary_json_file = f"{RESULTS_DIR}/maxsetsupport_summary.json"
    with open(summary_json_file, "w") as f:
        json.dump(summary_json, f, indent=2)

    attempts_csv_file = f"{RESULTS_DIR}/maxsetsupport_attempts.csv"
    pd.DataFrame(attempt_rows).to_csv(attempts_csv_file, index=False)
    combo_csv_file = f"{RESULTS_DIR}/maxsetsupport_summary.csv"
    pd.DataFrame(combo_rows).to_csv(combo_csv_file, index=False)

    log(f"maxsetsupport summary saved to {summary_json_file}")
    log(f"maxsetsupport attempts CSV saved to {attempts_csv_file}")
    log(f"maxsetsupport combo summary CSV saved to {combo_csv_file}")

    print("\nmaxsetsupport summary (max supported exponent):")
    for row in sorted(combo_rows, key=lambda r: (r["seed_size"], r["prg_dd"], r["mom_k"], r["mom_t"])):
        max_exp = row["max_supported_total_set_size_exponent"]
        max_exp_str = f"2^{max_exp}" if max_exp is not None else "N/A"
        print(
            f"n={row['seed_size']}, d={row['prg_dd']}, k={row['mom_k']}, t={row['mom_t']}, "
            f"baseline=2^{row['baseline_total_set_size_exponent']}, max_supported={max_exp_str}, "
            f"reductions={row['reductions_from_baseline']}, status={row['final_status']}"
        )

    return True

# ==================== PLOTTING ====================

PLOT_WIDTH = 8 * 1.2
PLOT_HEIGHT = 4.944271909999159 * 1.2
PLOT_MARKER_SIZE = 11


def init_plotting(fig_width=8 * 1.2, fig_height=4.944271909999159 * 1.2, font=20):
    """Apply shared plotting style for figure ratio and text sizing."""
    plt.rcParams['figure.figsize'] = [fig_width, fig_height]
    plt.rcParams['font.size'] = font
    plt.rcParams['font.family'] = 'serif'
    plt.rcParams['font.serif'] = ['Times New Roman', 'Times', 'TeX Gyre Termes', 'Liberation Serif', 'DejaVu Serif']
    plt.rcParams['axes.labelsize'] = plt.rcParams['font.size']
    plt.rcParams['axes.titlesize'] = 1.5 * plt.rcParams['font.size']
    plt.rcParams['legend.fontsize'] = plt.rcParams['font.size']
    plt.rcParams['xtick.labelsize'] = plt.rcParams['font.size']
    plt.rcParams['ytick.labelsize'] = plt.rcParams['font.size']
    plt.rcParams['legend.frameon'] = False
    plt.rcParams['legend.loc'] = 'upper center'
    plt.rcParams['axes.linewidth'] = 1




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

    # Match paper panel scale more closely: same style, smaller layout footprint.
    init_plotting(7.2, 4.2, 16)
    fig, ax = plt.subplots(figsize=(PLOT_WIDTH, PLOT_HEIGHT))

    markers = ['o', 's', '^', 'D', 'v', '*', 'x']
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2']

    all_y = np.array([float(y) for points in grouped.values() for _, y in points], dtype=float)
    if all_y.size == 0:
        log("No valid y values for plotting", "WARNING")
        return False

    # Show the second-largest value on-axis (e.g., d=6 at 2^6 ~= 1.61),
    # and clip only the extreme outlier(s) above this range.
    if all_y.size >= 2:
        visible_max = float(np.partition(all_y, -2)[-2])
    else:
        visible_max = float(all_y.max())
    y_top = max(visible_max * 1.08, 0.05)

    all_x = sorted({int(np.log2(p[0])) for pts in grouped.values() for p in pts})
    x_span = max(all_x) - min(all_x) if len(all_x) >= 2 else 1.0
    break_dx = max(x_span * 0.02, 0.05)

    # Plot curves with clipping markers
    for idx, d_val in enumerate(sorted(grouped.keys())):
        points = sorted(grouped[d_val], key=lambda x: x[0])
        x_vals = [int(np.log2(p[0])) for p in points]
        y_vals = [p[1] for p in points]

        color = colors[idx % len(colors)]
        marker_style = markers[idx % len(markers)]
        y_plot = [y if y <= y_top else np.nan for y in y_vals]
        clipped_points = []
        for x, y in zip(x_vals, y_vals):
            if y > y_top:
                clipped_points.append((x, float(y)))

        # Plot true values so slope is physically correct; axis clipping handles overflow.
        line_alpha = 0.75
        marker_face = color
        marker_edge_width = 1.2
        marker_size = PLOT_MARKER_SIZE
        if d_val == 8:
            # Keep d=7 visible when d=8 overlaps it.
            marker_face = 'none'
            marker_edge_width = 2.0
            marker_size = PLOT_MARKER_SIZE + 1
        ax.plot(
            x_vals,
            y_plot,
            marker=marker_style,
            linestyle='-',
            linewidth=2.4,
            markersize=marker_size,
            color=color,
            alpha=line_alpha,
            markerfacecolor=marker_face,
            markeredgewidth=marker_edge_width,
            label=f'd = {d_val}',
            zorder=2
        )

        # Draw top-boundary clip markers where the true line crosses y_top.
        dy = y_top * 0.012
        for i in range(len(x_vals) - 1):
            x1, y1 = float(x_vals[i]), float(y_vals[i])
            x2, y2 = float(x_vals[i + 1]), float(y_vals[i + 1])
            above1 = y1 > y_top
            above2 = y2 > y_top
            if above1 == above2:
                continue
            if abs(y2 - y1) < 1e-12 or abs(x2 - x1) < 1e-12:
                continue

            t = (y_top - y1) / (y2 - y1)
            x_cross = x1 + t * (x2 - x1)
            # For readability, enforce a minimum horizontal span for the visible
            # clipped transition so it does not look nearly vertical.
            min_in_dx = min(max(abs(x2 - x1) * 0.22, 0.20), abs(x2 - x1) * 0.45)
            if above1 and not above2:
                x_cross_vis = min(x_cross, x2 - min_in_dx)
                x_in_start, y_in_start = x_cross_vis, y_top
                x_in_end, y_in_end = x2, y2
            elif (not above1) and above2:
                x_cross_vis = max(x_cross, x1 + min_in_dx)
                x_in_start, y_in_start = x1, y1
                x_in_end, y_in_end = x_cross_vis, y_top
            else:
                continue

            # Inside-border clipped continuation (stylized for readability).
            ax.plot(
                [x_in_start, x_in_end],
                [y_in_start, y_in_end],
                color=color,
                linewidth=2.4,
                alpha=0.95,
                solid_capstyle='round',
                zorder=8
            )

            # // marker just above the top border at the crossing.
            y_break = y_top + dy * 0.22
            dx = break_dx * 0.75
            ax.plot(
                [x_cross_vis - dx, x_cross_vis - dx / 3],
                [y_break - dy, y_break + dy],
                color='black',
                linewidth=2.4,
                solid_capstyle='round',
                zorder=10,
                clip_on=False
            )
            ax.plot(
                [x_cross_vis + dx / 3, x_cross_vis + dx],
                [y_break - dy, y_break + dy],
                color='black',
                linewidth=2.4,
                solid_capstyle='round',
                zorder=10,
                clip_on=False
            )

    # Labels
    ax.set_xlabel('Seed Size n')
    ax.set_ylabel('Accuracy Error')

    # X ticks as powers of 2
    ax.set_xticks(all_x)
    ax.set_xticklabels([f"$2^{{{int(x)}}}$" for x in all_x])

    ax.set_ylim(0, y_top)

    # Reference target line.
    ax.axhline(
        0.05,
        color='forestgreen',
        linestyle='--',
        linewidth=1.8,
        alpha=0.75,
        label=r'$\epsilon = 0.05$',
        zorder=1
    )

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

    # Draw // on the top of y-axis spine with the same style/size as line clips.
    dy = y_top * 0.012
    dx = break_dx * 0.75
    x_min, x_max = ax.get_xlim()
    y_min, y_max = ax.get_ylim()
    x_range = max(x_max - x_min, 1e-12)
    y_range = max(y_max - y_min, 1e-12)
    dx_axes = dx / x_range
    dy_axes = dy / y_range
    x_break_axis = 0.0
    y_break_axis = 1.0 + dy_axes * 0.22
    ax.plot(
        [x_break_axis - dx_axes, x_break_axis - dx_axes / 3],
        [y_break_axis - dy_axes, y_break_axis + dy_axes],
        transform=ax.transAxes,
        color='black',
        linewidth=2.4,
        solid_capstyle='round',
        zorder=20,
        clip_on=False
    )
    ax.plot(
        [x_break_axis + dx_axes / 3, x_break_axis + dx_axes],
        [y_break_axis - dy_axes, y_break_axis + dy_axes],
        transform=ax.transAxes,
        color='black',
        linewidth=2.4,
        solid_capstyle='round',
        zorder=20,
        clip_on=False
    )

    # Legend
    ax.legend(frameon=False, loc='upper right', fontsize=14)

    plot_file = f"{PLOTS_DIR}/seed_optimization_seedsize_vs_error.png"
    plot_file_pdf = f"{PLOTS_DIR}/seed_optimization_seedsize_vs_error.pdf"
    fig.tight_layout()
    fig.savefig(plot_file, dpi=220, bbox_inches='tight')
    fig.savefig(plot_file_pdf, bbox_inches='tight')
    plt.close(fig)

    log(f"Seed optimization plot saved to {plot_file} and {plot_file_pdf}")
    return True

def create_errorvsepsilon_boxplot(analysis_results):
    """Create error-vs-epsilon plot with one median-error curve per (psi_mode, seed_size_bit)."""
    if plt is None:
        return False
    init_plotting(8 * 1.2, 4.944271909999159 * 1.2, 20)

    if not analysis_results:
        log("No analysis results to plot", "ERROR")
        return False

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

    # === Save summary ===
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

    # ===== plot: per-seed `ours` lines + collapsed `uniform r` line (paper figure style) =====
    SEED_COLORS  = {6: "#1f77b4", 7: "#d62728", 8: "#ff7f0e"}
    SEED_MARKERS = {6: "o",       7: "D",       8: "s"}
    SEED_HOLLOW  = {6: False,     7: True,      8: False}
    UNIFORM_COLOR  = "#2ca02c"
    UNIFORM_MARKER = "o"

    def ours_label(sb):
        return rf"ours ($n=2^{{{sb}}}$)" if sb is not None else "ours"

    fig, ax = plt.subplots()
    plotted_any = False

    # naive: one solid line per seed, with realworld-style colors/markers
    naive_seeds = sorted(
        {sb for mode, sb in grouped if mode == 'naive'},
        key=seed_sort_key
    )
    for sb in naive_seeds:
        points = grouped.get(('naive', sb), [])
        if not points:
            continue
        by_epsilon = {}
        for epsilon, median_error in points:
            by_epsilon.setdefault(float(epsilon), []).append(float(median_error))
        epsilons = sorted(by_epsilon.keys())
        medians = [float(np.mean(by_epsilon[eps])) for eps in epsilons]
        if not epsilons:
            continue
        hollow = SEED_HOLLOW.get(sb, False)
        color  = SEED_COLORS.get(sb, "gray")
        ax.plot(
            epsilons, medians,
            marker=SEED_MARKERS.get(sb, "o"),
            linewidth=2.4, markersize=7,
            linestyle="-",
            color=color,
            markerfacecolor="none" if hollow else color,
            markeredgecolor=color,
            markeredgewidth=2.0,
            alpha=0.75,
            label=ours_label(sb),
        )
        plotted_any = True

    # naive_uniform: collapse across seeds into one dashed line (uniform randomness
    # is independent of seed_size_bit, so per-seed curves are redundant).
    uniform_pairs = []
    for (mode, sb), pts in grouped.items():
        if mode == 'naive_uniform':
            uniform_pairs.extend(pts)
    if uniform_pairs:
        by_epsilon = {}
        for epsilon, median_error in uniform_pairs:
            by_epsilon.setdefault(float(epsilon), []).append(float(median_error))
        epsilons = sorted(by_epsilon.keys())
        medians = [float(np.mean(by_epsilon[eps])) for eps in epsilons]
        if epsilons:
            ax.plot(
                epsilons, medians,
                marker=UNIFORM_MARKER,
                linewidth=2.4, markersize=7,
                linestyle="--",
                color=UNIFORM_COLOR,
                markeredgecolor=UNIFORM_COLOR,
                markeredgewidth=2.0,
                alpha=0.75,
                label=r"uniform $\mathbf{r}$",
            )
            plotted_any = True

    if not plotted_any:
        log("No valid data lines for error-vs-epsilon plot", "WARNING")
        plt.close(fig)
        return False

    ax.set_xlabel(r'$1/\sqrt{k}$')
    ax.set_ylabel('Accuracy Error')
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper left', frameon=False, fontsize=18)

    box_plot_file = f"{PLOTS_DIR}/error_boxplot.png"
    box_plot_pdf  = f"{PLOTS_DIR}/error_boxplot.pdf"
    fig.tight_layout()
    fig.savefig(box_plot_file, dpi=300, bbox_inches='tight')
    fig.savefig(box_plot_pdf, bbox_inches='tight')
    plt.close(fig)

    log(f"Error-vs-epsilon plot saved to {box_plot_file} and {box_plot_pdf}")

    return True

def create_plots(analysis_results):
    """Create plots from analysis results"""

    log("Creating plots...")
    if ACTIVE_TEST_MODE == "seed_optimization":
        return create_seed_optimization_d_stability_plot(analysis_results)
    if ACTIVE_TEST_MODE == "errorvsepsilon":
        return create_errorvsepsilon_boxplot(analysis_results)
    if ACTIVE_TEST_MODE == "maxsetsupport":
        log("Skipping plot generation for maxsetsupport mode", "WARNING")
        return True


# ==================== MAIN FUNCTIONS ====================

def main():
    """Main function"""
    global VERBOSE, NUM_RUNS_PER_POINT, FILTER_PRG_DD, FILTER_MOM_K, FILTER_MOM_T, PORT_BASE, SKIP_KILL_EXISTING, MAXSETSUPPORT_SKIP_BASELINE_ABOVE

    parser = argparse.ArgumentParser(description='Batch PSI testing with parameter scanning')
    # ---------- Pipeline actions (what to run) ----------
    parser.add_argument('--run-tests', action='store_true', help='Run the batch tests')
    parser.add_argument('--analyze', action='store_true', help='Analyze existing results')
    parser.add_argument('--plot', action='store_true', help='Create plots (runs analysis first)')
    parser.add_argument('--plot-only', action='store_true', help='Create plots only from existing summary/analysis files (no re-analysis, no summary rewrite)')

    # ---------- Mode selection + parallel orchestration ----------
    parser.add_argument('--param-mode', choices=sorted(TEST_MODES.keys()), default=DEFAULT_TEST_MODE, help='Select which parameter grid to use')
    parser.add_argument('--seed-optimization-parallel', '--seed_optimization_parallel', dest='seed_optimization_parallel', action='store_true', help='For each parameter pair in seed_optimization mode, launch parallel worker runs, merge results, then analyze+plot')
    parser.add_argument('--errorvsepsilon-parallel', action='store_true', help='For each parameter pair in errorvsepsilon mode, launch parallel worker runs, merge results, then analyze+plot')
    parser.add_argument('--maxsetsupport-parallel', action='store_true', help='For maxsetsupport: process one (prg_dd,seed_size) combo at a time; parallelize workers within that combo until target runs are filled')
    parser.add_argument('--parallel-workers', type=int, default=None, help='Number of parallel workers for parallel pipelines (auto-sized from CPU when omitted)')
    parser.add_argument('--worker-num-runs', type=int, default=None, help='--num-runs value passed to each worker (default: auto = ceil(NUM_RUNS_PER_POINT / workers))')
    parser.add_argument('--target-cores', '--target-core', dest='target_cores', type=int, default=None, help='Target core budget for auto worker sizing (default: detected CPU cores)')

    # ---------- General run configuration ----------
    parser.add_argument('--run-dir', type=str, default=None, help='Specify run directory (e.g., ./experiments/run_20260218_163843)')
    parser.add_argument('--num-runs', type=int, default=NUM_RUNS_PER_POINT, help='Number of runs per parameter combination')
    parser.add_argument('--port-base', type=int, default=PORT_BASE, help='Base port used by test runs (set automatically per worker in parallel mode)')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    parser.add_argument('--skip-kill-existing', action='store_true', help='Do not pkill existing psi_server/psi_client before starting tests (useful for parallel worker subprocesses)')

    # ---------- Sweep filters (narrow the param grid for debugging / per-cell workers) ----------
    parser.add_argument('--only-prg-dd', type=int, default=None, help='Optional filter: run only this prg_dd value')
    parser.add_argument('--only-mom-k', type=int, default=None, help='Optional filter: run only this mom_k value')
    parser.add_argument('--only-mom-t', type=int, default=None, help='Optional filter: run only this mom_t value')

    # ---------- maxsetsupport-specific ----------
    parser.add_argument('--skip-baseline-above', type=int, default=None, help='For maxsetsupport: skip full (d,seed) combos whose baseline setexp is above this cap')

    # ---------- Internal: worker subprocess plumbing for --maxsetsupport-parallel ----------
    # These flags are set by the parent orchestrator when spawning workers; users should not pass them directly.
    parser.add_argument('--maxsetsupport-worker-single', action='store_true', help='Internal worker mode: run a single maxsetsupport (d,seed,setexp) combo')
    parser.add_argument('--worker-seed-size-bit', type=int, default=None, help='Internal worker arg for --maxsetsupport-worker-single')
    parser.add_argument('--worker-total-set-exp', type=int, default=None, help='Internal worker arg for --maxsetsupport-worker-single')
    parser.add_argument('--worker-psi-mode', type=str, default='naive', help='Internal worker arg for --maxsetsupport-worker-single')
    
    args = parser.parse_args()

    parallel_flag_count = sum([args.seed_optimization_parallel, args.errorvsepsilon_parallel, args.maxsetsupport_parallel])
    if parallel_flag_count > 1:
        log("Choose only one parallel mode flag: --seed-optimization-parallel, --errorvsepsilon-parallel, or --maxsetsupport-parallel", "ERROR")
        return 1
    
    VERBOSE = args.verbose
    NUM_RUNS_PER_POINT = args.num_runs
    FILTER_PRG_DD = args.only_prg_dd
    FILTER_MOM_K = args.only_mom_k
    FILTER_MOM_T = args.only_mom_t
    PORT_BASE = args.port_base
    SKIP_KILL_EXISTING = args.skip_kill_existing
    MAXSETSUPPORT_SKIP_BASELINE_ABOVE = args.skip_baseline_above
    if MAXSETSUPPORT_SKIP_BASELINE_ABOVE is not None and MAXSETSUPPORT_SKIP_BASELINE_ABOVE < 1:
        log("--skip-baseline-above must be >= 1", "ERROR")
        return 1
    apply_test_mode(args.param_mode)
    
    # Set run directory if specified
    if args.run_dir:
        set_run_directory(args.run_dir)

    # Ensure directories exist for normal test/analyze/plot pipelines
    ensure_directories()
    log(f"Using param mode '{ACTIVE_TEST_MODE}' with prg_dd={PRG_DD_VALUES}, kt_pairs={MOM_KT_PAIRS}")
    log_config_seedsize()

    if args.maxsetsupport_worker_single:
        if ACTIVE_TEST_MODE != "maxsetsupport":
            log("--maxsetsupport-worker-single requires --param-mode maxsetsupport", "ERROR")
            return 1
        if args.worker_seed_size_bit is None or args.worker_total_set_exp is None:
            log("--maxsetsupport-worker-single requires --worker-seed-size-bit and --worker-total-set-exp", "ERROR")
            return 1
        success = run_maxsetsupport_single_exp_worker(
            seed_size_bit=args.worker_seed_size_bit,
            total_set_size_exp=args.worker_total_set_exp,
            psi_mode=args.worker_psi_mode or "naive",
        )
        return 0 if success else 1

    if args.seed_optimization_parallel:
        if ACTIVE_TEST_MODE != "seed_optimization":
            log("--seed-optimization-parallel requires --param-mode seed_optimization", "ERROR")
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
            f"Running parallel seed_optimization pipeline with workers={selected_workers}, "
            f"worker_num_runs={selected_worker_num_runs}"
        )
        success = run_seed_optimization_parallel_pipeline(
            parallel_workers=selected_workers,
            worker_num_runs=selected_worker_num_runs
        )
        if not success:
            log("Parallel seed_optimization pipeline failed", "ERROR")
            return 1
        log("Batch test pipeline completed successfully!")
        return 0

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

    if args.maxsetsupport_parallel:
        if ACTIVE_TEST_MODE != "maxsetsupport":
            log("--maxsetsupport-parallel requires --param-mode maxsetsupport", "ERROR")
            return 1
        if args.target_cores is not None and args.target_cores <= 0:
            log("--target-cores must be > 0", "ERROR")
            return 1
        if args.parallel_workers is None:
            selected_workers, selected_cores, per_worker_psi = recommend_parallel_workers_for_cores(args.target_cores)
            log(
                f"Auto worker sizing for maxsetsupport: target_cores={selected_cores}, "
                f"psi_processes_per_worker={per_worker_psi}, "
                f"selected_workers={selected_workers}"
            )
        elif args.parallel_workers <= 0:
            log("--parallel-workers must be > 0", "ERROR")
            return 1
        else:
            selected_workers = args.parallel_workers

        log(f"Running maxsetsupport parallel pipeline with workers={selected_workers}")
        success = run_maxsetsupport_parallel_pipeline(parallel_workers=selected_workers)
        if not success:
            log("Parallel maxsetsupport pipeline failed", "ERROR")
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
        if ACTIVE_TEST_MODE == "maxsetsupport":
            log("Creating maxsetsupport summary from existing analysis...")
        else:
            log("Creating plots from existing analysis...")

        analysis_results = None
        analysis_file = f"{RESULTS_DIR}/analysis_results.json"
        if os.path.exists(analysis_file):
            with open(analysis_file, 'r') as f:
                analysis_results = json.load(f)
        if not analysis_results:
            log("No analysis results found", "ERROR")
            return 1
        if ACTIVE_TEST_MODE == "maxsetsupport":
            success = summarize_maxsetsupport(analysis_results)
        else:
            success = create_plots(analysis_results)
        if not success:
            log("Plotting skipped because dependencies are unavailable", "WARNING")
            return 0
        return 0
    
    if args.analyze or args.plot:
        log("Analyzing results...")
        analysis_results = analyze_results()
        if not analysis_results:
            log("Analysis failed", "ERROR")
            return 1
        if ACTIVE_TEST_MODE == "maxsetsupport" and not args.plot:
            summarize_maxsetsupport(analysis_results)

    if args.plot:
        if ACTIVE_TEST_MODE == "maxsetsupport":
            success = summarize_maxsetsupport(analysis_results)
            if not success:
                log("maxsetsupport summary export failed", "WARNING")
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

        if ACTIVE_TEST_MODE == "maxsetsupport":
            success = summarize_maxsetsupport(analysis_results)
            if not success:
                log("maxsetsupport summary export failed", "WARNING")
        else:
            success = create_plots(analysis_results)
            if not success:
                log("Plotting skipped because dependencies are unavailable", "WARNING")
    
    log("Batch test pipeline completed successfully!")
    return 0

if __name__ == "__main__":
    sys.exit(main())

# refresh-marker: 20260503T033330Z
