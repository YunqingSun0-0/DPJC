#!/usr/bin/env python3
"""
Single FHE Test Script
Quick test to verify FHE PSI functionality
"""

import os
import sys
import subprocess
import time
import signal
import shutil
import re
import argparse
import threading
import math
import csv
from datetime import datetime

# ==================== CONFIGURABLE PARAMETERS ====================
# Test parameters - Small for quick testing
UNIVERSAL_SIZE_BIT = 24  # Small for quick testing
SET_SIZE = None  # Will be set by command-line argument or default
LOG_FILE = None  # Will be set based on set size
INTERSECTION_SIZE = None  # Will be set by command-line argument or default
MAX_WEIGHT = 0  # 0 = no weights (default); >0 = uniform random weights in [1, MAX_WEIGHT]
WEIGHT_SCALE_DIV = 1  # deprecated: weight scaling is disabled, kept for CLI compatibility
WEIGHTED_MULTILIMB_EXACT = False  # weighted mode only: limb-decomposed OLE path (no per-round residue reveal)
WEIGHTED_LIMB_BITS = 16
WEIGHTED_CHUNK_K = 0  # weighted mode only: split each tt bucket into chunks in server recovery (0 = disabled)
NUM_CLIENTS_PER_SERVER = 1
OUTPUT_DIR = "./test_fhe_single"
PORT = 22000
SEAL_PLAIN_MODULUS_BIT = 24
MOM_K = 400

# Test parameters
VERBOSE = False  # Set True via --verbose to also stream INFO logs to the terminal
CLEANUP_AFTER_TEST = True
TIMEOUT_SECONDS = 7200  # 120 minutes timeout for larger FHE tests
PRG_DD = 6  # Default value, will be overridden by command-line argument

# Levels that always reach the terminal regardless of VERBOSE.
ALWAYS_PRINT_LEVELS = {"ERROR", "WARNING", "RESULT"}

# Use the BFV centered-decode guard used in server recovery:
# values >= 2p/3 are interpreted as negative.
DECODE_POSITIVE_GUARD_NUM = 2
DECODE_POSITIVE_GUARD_DEN = 3

# ==================== HELPER FUNCTIONS ====================

def log(message, level="INFO"):
    """Always write to LOG_FILE; terminal prints depend on VERBOSE / level."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] [{level}] {message}"
    if VERBOSE or level in ALWAYS_PRINT_LEVELS:
        print(log_msg, flush=True)
    if LOG_FILE:
        with open(LOG_FILE, 'a') as f:
            f.write(log_msg + '\n')
            f.flush()

def kill_existing_processes():
    """Kill any existing PSI processes"""
    try:
        subprocess.run("pkill -f psi_server", shell=True, capture_output=True)
        subprocess.run("pkill -f psi_client", shell=True, capture_output=True)
        time.sleep(0.2)
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
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid
    )
    process._captured_output = []

    def _stream_output():
        if process.stdout is None:
            return
        for line in iter(process.stdout.readline, ""):
            process._captured_output.append(line)
            line = line.rstrip()
            if line:
                log(f"[{description}] {line}")
        process.stdout.close()

    process._output_thread = threading.Thread(target=_stream_output, daemon=True)
    process._output_thread.start()
    time.sleep(0.3)  # Wait for process to start
    return process

def wait_for_processes(processes, timeout=120):
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

def _next_power_of_two(x: int) -> int:
    """Return the smallest power of two >= x (x>=1)."""
    if x <= 1:
        return 1
    return 1 << ((x - 1).bit_length())


def estimate_plain_modulus_from_bit(plain_modulus_bit: int) -> int:
    """
    Estimate BFV batching prime from bit-size.
    SEAL chooses a prime close to 2^bit, so (2^bit - 1) is a practical estimator.
    """
    b = max(2, int(plain_modulus_bit))
    return (1 << b) - 1


def estimate_per_round_upper_bound(intersection_size: int, max_weight: int) -> int:
    """
    Conservative upper bound for the positive self-term contribution in one round:
      per_round_upper ~= intersection_size * max_weight^2
    """
    inter = max(0, int(intersection_size))
    w = max(0, int(max_weight))
    return inter * w * w


def estimate_weighted_chunk_k_from_bound(
    plain_modulus_est: int,
    per_round_upper_bound: int,
    mom_k: int,
) -> int:
    """
    Choose chunk size so one revealed chunk stays in decode-positive range (< 2p/3).
    Return 0 if no positive chunk size can satisfy the bound.
    """
    if mom_k <= 0:
        return 0
    if per_round_upper_bound <= 0:
        return mom_k

    safe_chunk_limit = (DECODE_POSITIVE_GUARD_NUM * int(plain_modulus_est)) // DECODE_POSITIVE_GUARD_DEN
    if safe_chunk_limit <= 0:
        return 0
    return max(0, min(int(mom_k), safe_chunk_limit // int(per_round_upper_bound)))

SCALE_VECTOR_BASE_WEIGHT = 8192
# Keep helper assets under helper/.
SCALE_VECTOR_RANDOM_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "helper",
    "scale_vector_random.csv",
)
# Fallback vector for max_weight=8192 under current random-mode estimator, indexed by set_size_bit in [1,24].
FALLBACK_SCALE_VECTOR_BASE_BY_SET_SIZE_BIT = [
    64, 64, 64, 128, 128, 256, 256, 512, 512, 1024, 1024, 2048,
    2048, 4096, 4096, 8192, 8192, 16384, 16384, 32768, 32768, 65536, 65536, 131072,
]

def _load_scale_vector_base_from_csv(csv_path: str, base_weight: int):
    """
    Load scale vector (set_size_bit 1..24) from helper CSV.
    Expected columns: mode,set_size_bit,...,max_weight,recommended_weight_scale_div
    """
    if not os.path.exists(csv_path):
        return None

    scales_by_bit = {}
    try:
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("mode") != "random":
                    continue
                try:
                    max_weight = int(row.get("max_weight", "0"))
                    set_size_bit = int(row.get("set_size_bit", "0"))
                    scale = int(row.get("recommended_weight_scale_div", "0"))
                except ValueError:
                    continue
                if max_weight != base_weight:
                    continue
                if set_size_bit < 1 or set_size_bit > 24 or scale < 1:
                    continue

                prev = scales_by_bit.get(set_size_bit)
                scales_by_bit[set_size_bit] = scale if prev is None else max(prev, scale)
    except OSError:
        return None

    if len(scales_by_bit) != 24:
        return None
    return [scales_by_bit[b] for b in range(1, 25)]


SCALE_VECTOR_BASE_BY_SET_SIZE_BIT = _load_scale_vector_base_from_csv(
    SCALE_VECTOR_RANDOM_CSV, SCALE_VECTOR_BASE_WEIGHT
) or FALLBACK_SCALE_VECTOR_BASE_BY_SET_SIZE_BIT


def auto_weight_scale_div_formula(
    set_size: int,
    max_weight: int,
    plain_modulus_bit: int = 24,
    mom_k: int = 400,
    overlap_factor: float = 1.0,
) -> int:
    """
    Formula-based estimator for weight_scale_div.

    Model (same random-weight mode as gendata):
      - intersection ~= set_size / 2
      - E[w] = (max_weight + 1) / 2
      - E[weighted_intersection_sum] ~= intersection * E[w]^2
      - one tt-bucket sums mom_k rounds, so expected bucket magnitude scales by mom_k

    overlap_factor:
      - 1.0 for current random-mode gendata assumption.
      - >1.0 for conservative client-overlap assumptions.

    We pick scale so expected bucket stays around <= plain_modulus/2, then round up to power-of-two.
    """
    if max_weight <= 0:
        return 1

    set_size = max(1, int(set_size))
    mom_k = max(1, int(mom_k))
    plain_modulus_bit = max(2, int(plain_modulus_bit))

    # BFV batching modulus is a prime close to 2^plain_modulus_bit.
    plain_modulus_est = float((1 << plain_modulus_bit) - 1)
    intersection_est = float(max(1, set_size // 2))
    overlap_factor = max(1.0, float(overlap_factor))
    effective_intersection_est = intersection_est * overlap_factor
    mean_weight = (float(max_weight) + 1.0) / 2.0
    expected_weighted_sum = effective_intersection_est * mean_weight * mean_weight
    expected_bucket_sum = expected_weighted_sum * float(mom_k)

    # Keep expected bucket around <= p/2 to limit wraparound in mod-p recovery.
    # For tiny intersections, the product-of-weights variance is very high, so
    # a pure mean-based estimate is often too optimistic. Add a small safety
    # factor that decays with sqrt(intersection_est) and is neutral for
    # moderate/large intersections.
    tail_safety = max(1.0, 2.0 / math.sqrt(effective_intersection_est))
    expected_bucket_sum *= tail_safety
    target_bucket = plain_modulus_est / 2.0
    required_from_bucket = math.sqrt(max(1.0, expected_bucket_sum / max(1.0, target_bucket)))

    # Also ensure encoded weight is representable (< plain_modulus).
    required_from_encoding = float(max_weight) / max(1.0, plain_modulus_est - 1.0)

    required = max(1.0, required_from_bucket, required_from_encoding)
    scale = _next_power_of_two(int(math.ceil(required)))
    return max(1, scale)


def auto_weight_scale_div_vector(set_size_bit: int, max_weight: int) -> int:
    """Vector estimator keyed by set_size_bit, scaled from base max_weight=8192."""
    if max_weight <= 0:
        return 1
    if set_size_bit < 1:
        set_size_bit = 1
    if set_size_bit > len(SCALE_VECTOR_BASE_BY_SET_SIZE_BIT):
        set_size_bit = len(SCALE_VECTOR_BASE_BY_SET_SIZE_BIT)
    base_scale = SCALE_VECTOR_BASE_BY_SET_SIZE_BIT[set_size_bit - 1]
    scaled = (base_scale * float(max_weight)) / float(SCALE_VECTOR_BASE_WEIGHT)
    return max(1, _next_power_of_two(int(math.ceil(scaled))))


def auto_weight_scale_div(
    set_size_bit: int,
    set_size: int,
    max_weight: int,
    num_clients_per_server: int,
    plain_modulus_bit: int = 24,
    mom_k: int = 400,
    model: str = "vector_conservative",
) -> int:
    """
    Unified auto estimator.

    model:
      - vector: set_size_bit vector + max_weight scaling.
      - vector_conservative: vector baseline, then max(...) with conservative client-overlap formula.
      - formula: pure formula with overlap_factor=1.0.
    """
    if max_weight <= 0:
        return 1

    if model == "vector":
        return auto_weight_scale_div_vector(set_size_bit=set_size_bit, max_weight=max_weight)

    if model == "vector_conservative":
        vec_scale = auto_weight_scale_div_vector(set_size_bit=set_size_bit, max_weight=max_weight)
        conservative_scale = auto_weight_scale_div_formula(
            set_size=set_size,
            max_weight=max_weight,
            plain_modulus_bit=plain_modulus_bit,
            mom_k=mom_k,
            overlap_factor=float(max(1, num_clients_per_server)),
        )
        return max(vec_scale, conservative_scale)

    if model == "formula":
        return auto_weight_scale_div_formula(
            set_size=set_size,
            max_weight=max_weight,
            plain_modulus_bit=plain_modulus_bit,
            mom_k=mom_k,
            overlap_factor=1.0,
        )

    raise ValueError(f"Unknown auto weight scale model: {model}")

def read_gendata_config(config_path):
    """Read key=value pairs from gendata config.txt"""
    config = {}
    if not os.path.exists(config_path):
        return config
    with open(config_path, "r") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip()
    return config

def extract_timing_from_output(processes):
    """Extract timing information from process output"""
    timing_data = {}
    
    for i, process in enumerate(processes):
        if process.poll() is not None:
            output = "".join(getattr(process, "_captured_output", []))
            
            if i < 3: # Servers
                log(f"Process {i+1} output captured ({len(output)} chars)")
            
            # Extract timing information
            key_gen_match = re.search(r'Key generation time: ([\d.]+)s', output)
            if key_gen_match:
                timing_data[f"key_gen_server_time_{i+1}"] = float(key_gen_match.group(1))
            
            client_compute_match = re.search(r'Client computation time: ([\d.]+)s', output)
            if client_compute_match:
                timing_data[f"client_compute_time_{i+1}"] = float(client_compute_match.group(1))
            
            server_recover_match = re.search(r'Server recovery time: ([\d.]+)s', output)
            if server_recover_match:
                timing_data[f"server_recover_time_{i+1}"] = float(server_recover_match.group(1))

            # server_recover_time = aggregation + 2PC; pure 2PC is also emitted separately
            server_2pc_match = re.search(r'server_2pc_time: ([\d.]+)s', output)
            if server_2pc_match:
                timing_data[f"server_2pc_time_{i+1}"] = float(server_2pc_match.group(1))

            # Extract per-server client-results aggregation time (printed in ms by psi.cpp)
            aggregation_match = re.search(r'Client results aggregation time:\s*([\d.]+)\s*ms', output)
            if aggregation_match:
                timing_data[f"aggregation_time_ms_{i+1}"] = float(aggregation_match.group(1))

            # Extract key generation communication breakdown
            key_gen_comm_match = re.search(r'key_gen_comm_mb: ([\d.]+)', output)
            if key_gen_comm_match:
                timing_data["key_gen_comm_mb"] = float(key_gen_comm_match.group(1))

            broadcast_comm_match = re.search(r'broadcast_comm_mb: ([\d.]+)', output)
            if broadcast_comm_match:
                timing_data["broadcast_comm_mb"] = float(broadcast_comm_match.group(1))

            # Extract client-to-server result communication size
            client_server_comm_match = re.search(r'client_server_comm_mb: ([\d.]+)', output)
            if client_server_comm_match:
                timing_data["client_server_comm_mb"] = float(client_server_comm_match.group(1))

            # Extract server recovery communication size
            comm_match = re.search(r'Server recovery Communication: ([\d.eE+-]+) MB', output)
            if comm_match:
                timing_data["server_recover_communication_mb"] = float(comm_match.group(1))

            # Extract noise budget before sharing on each server
            noise_before_sharing_match = re.search(r'noise budget - before sharing: (\d+)', output)
            if noise_before_sharing_match:
                timing_data[f"noise_before_sharing_server_{i+1}"] = int(noise_before_sharing_match.group(1))
            
            # Extract PSI size
            psi_match = re.search(r'Final PSI size: (-?\d+)', output)
            if psi_match:
                timing_data["psi_size"] = int(psi_match.group(1))
            
            # Extract total server time
            total_match = re.search(r'Total server time: ([\d.]+)s', output)
            if total_match:
                timing_data[f"total_server_{i+1}"] = float(total_match.group(1))
    
    return timing_data

# ==================== TEST FUNCTIONS ====================

def generate_test_data():
    """Generate test data"""
    log("=" * 50)
    log("GENERATING TEST DATA")
    log("=" * 50)
    
    # Clean up previous test data
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    
    # Generate test data
    cmd = f"./bin/gendata --intersection_size={INTERSECTION_SIZE} " \
          f"--universal_size_bit={UNIVERSAL_SIZE_BIT} " \
          f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
          f"--set_size={SET_SIZE} " \
          f"--output_dir={OUTPUT_DIR}"
    if MAX_WEIGHT > 0:
        cmd += f" --max_weight={MAX_WEIGHT}"
    
    success, output = run_command(cmd, "Data generation")
    if not success:
        return False, None, None, None
    
    # Read expected intersection from generated data
    server1_files = [f"{OUTPUT_DIR}/client{i}_1.txt" for i in range(1, NUM_CLIENTS_PER_SERVER + 1)]
    server2_files = [f"{OUTPUT_DIR}/client{i}_2.txt" for i in range(1, NUM_CLIENTS_PER_SERVER + 1)]
    
    # Check if all files exist
    all_files = server1_files + server2_files
    for file in all_files:
        if not os.path.exists(file):
            log(f"Error: Generated data file not found: {file}", "ERROR")
            return False, None, None, None
    
    # Prefer expected values produced by gendata itself.
    config_path = f"{OUTPUT_DIR}/config.txt"
    gendata_cfg = read_gendata_config(config_path)
    expected_metric = "intersection_size"
    expected_value = INTERSECTION_SIZE

    if MAX_WEIGHT > 0:
        if "weighted_intersection_sum" in gendata_cfg:
            expected_metric = "weighted_intersection_sum"
            expected_value = int(gendata_cfg["weighted_intersection_sum"])
        elif "actual_intersection_size" in gendata_cfg:
            expected_metric = "actual_intersection_size"
            expected_value = int(gendata_cfg["actual_intersection_size"])
            log("weighted_intersection_sum missing in config.txt; fallback to actual_intersection_size", "WARNING")
        else:
            log("config.txt missing weighted_intersection_sum; fallback to INTERSECTION_SIZE", "WARNING")
    else:
        if "actual_intersection_size" in gendata_cfg:
            expected_metric = "actual_intersection_size"
            expected_value = int(gendata_cfg["actual_intersection_size"])

    expected_target = {"metric": expected_metric, "value": expected_value}

    if MAX_WEIGHT > 0:
        plain_modulus_est = estimate_plain_modulus_from_bit(SEAL_PLAIN_MODULUS_BIT)
        actual_intersection = int(gendata_cfg.get("actual_intersection_size", INTERSECTION_SIZE))
        per_round_upper_bound = estimate_per_round_upper_bound(actual_intersection, MAX_WEIGHT)
        if per_round_upper_bound >= plain_modulus_est:
            max_weight_limit = int(math.isqrt(max(0, plain_modulus_est - 1) // max(1, actual_intersection)))
            max_intersection_limit = int((plain_modulus_est - 1) // max(1, MAX_WEIGHT * MAX_WEIGHT))
            log(
                "Estimated per-round upper bound exceeds plain modulus before 2PC: "
                f"intersection={actual_intersection}, max_weight={MAX_WEIGHT}, "
                f"bound=intersection*max_weight^2={per_round_upper_bound}, "
                f"plain_modulus_est={plain_modulus_est}. "
                f"For this intersection, recommended max_weight <= {max_weight_limit}; "
                f"for this max_weight, recommended intersection <= {max_intersection_limit}.",
                "ERROR",
            )
            return False, None, None, None

    log(f"Set size: {SET_SIZE}")
    log(f"Expected {expected_metric}: {expected_value}")
    
    return True, server1_files, server2_files, expected_target

def run_fhe_test(server1_files, server2_files, seed_size_bit):
    """Run FHE PSI test"""
    log("=" * 50)
    log("RUNNING FHE PSI TEST")
    log("=" * 50)
    
    # FHE parameters
    prg_dd = PRG_DD
    network_mode = "lan"
    
    # Start servers with FHE mode
    weighted_flag = " --weighted_mode" if MAX_WEIGHT > 0 else ""
    weighted_multilimb_flag = ""
    weighted_chunk_flag = ""
    if MAX_WEIGHT > 0 and WEIGHTED_MULTILIMB_EXACT:
        weighted_multilimb_flag = f" --weighted_multilimb_exact --weighted_limb_bits={WEIGHTED_LIMB_BITS}"
    if MAX_WEIGHT > 0 and WEIGHTED_CHUNK_K > 0:
        weighted_chunk_flag = f" --weighted_chunk_k={WEIGHTED_CHUNK_K}"
    server1_cmd = f"./bin/psi_server -p 1 --port={PORT} --psi_mode=fhe " \
                  f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                  f"--seed_size_bit={seed_size_bit} --prg_dd={prg_dd} " \
                  f"--network_mode={network_mode}{weighted_flag}{weighted_multilimb_flag}{weighted_chunk_flag}"
    server2_cmd = f"./bin/psi_server -p 2 --port={PORT} --psi_mode=fhe " \
                  f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                  f"--seed_size_bit={seed_size_bit} --prg_dd={prg_dd} " \
                  f"--network_mode={network_mode}{weighted_flag}{weighted_multilimb_flag}{weighted_chunk_flag}"
    server1_process = start_process(server1_cmd, "Server 1 (FHE)")
    server2_process = start_process(server2_cmd, "Server 2 (FHE)")
    
    # Start clients
    client_processes = []
    
    # Start Server 1 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        data_file = server1_files[i]
        client_cmd = f"./bin/psi_client -p {client_id} --port={PORT} " \
                     f"--data_file={data_file} --psi_mode=fhe " \
                     f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                     f"--seed_size_bit={seed_size_bit} --prg_dd={prg_dd} " \
                     f"--network_mode={network_mode}{weighted_flag}{weighted_multilimb_flag}{weighted_chunk_flag}"
        client_process = start_process(client_cmd, f"Client {client_id} (Server 1)")
        client_processes.append(client_process)
    
    # Start Server 2 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        data_file = server2_files[i]
        client_cmd = f"./bin/psi_client -p {client_id + NUM_CLIENTS_PER_SERVER} --port={PORT} " \
                     f"--data_file={data_file} --psi_mode=fhe " \
                     f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                     f"--seed_size_bit={seed_size_bit} --prg_dd={prg_dd} " \
                     f"--network_mode={network_mode}{weighted_flag}{weighted_multilimb_flag}{weighted_chunk_flag}"
        client_process = start_process(client_cmd, f"Client {client_id} (Server 2)")
        client_processes.append(client_process)
    
    # Wait for all processes to complete
    all_processes = [server1_process, server2_process] + client_processes
    success = wait_for_processes(all_processes, TIMEOUT_SECONDS)

    # Ensure output reader threads have drained process output
    for process in all_processes:
        output_thread = getattr(process, "_output_thread", None)
        if output_thread is not None:
            output_thread.join(timeout=1.0)
    
    if not success:
        log("Some processes did not complete within timeout", "ERROR")
        return False, None
    
    # Check if all processes completed successfully
    for i, process in enumerate(all_processes):
        if process.returncode != 0:
            log(f"Process {i+1} failed with return code {process.returncode}", "ERROR")
            return False, None
    
    # Extract timing information
    timing_data = extract_timing_from_output(all_processes)
    
    return True, timing_data

def validate_results(expected_target, timing_data):
    """Validate test results"""
    log("=" * 50)
    log("VALIDATING RESULTS")
    log("=" * 50)
    
    if not timing_data:
        log("No timing data extracted", "ERROR")
        return False
    
    # Print timing information grouped by protocol phase, in protocol order.
    # Each entry: (key_or_prefix, "exact" | "prefix")
    timing_groups = [
        ("Noise budget", [
            ("noise_before_sharing_server_", "prefix"),
        ]),
        ("Key generation", [
            ("key_gen_server_time_", "prefix"),
            ("key_gen_comm_mb", "exact"),
            ("broadcast_comm_mb", "exact"),
        ]),
        ("Client compute", [
            ("client_compute_time_", "prefix"),
        ]),
        ("Client -> Server comm", [
            ("client_server_comm_mb", "exact"),
        ]),
        ("Aggregation", [
            ("aggregation_time_ms_", "prefix"),
        ]),
        ("2PC", [
            ("server_2pc_time_", "prefix"),
            ("server_recover_communication_mb", "exact"),
        ]),
    ]

    display_names = {
        "server_recover_communication_mb": "2PC_mb",
    }

    def _unit_for(key):
        if key.endswith("_mb"):
            return "MB"
        if "_ms_" in key or key.endswith("_ms"):
            return "ms"
        if "_time_" in key or key.endswith("_time"):
            return "s"
        return ""

    log("Timing Results:", "RESULT")
    printed = set()
    for group_name, patterns in timing_groups:
        keys_in_group = []
        for pattern, mode in patterns:
            for key in sorted(timing_data.keys()):
                hit = key == pattern if mode == "exact" else key.startswith(pattern)
                if hit and key not in printed:
                    keys_in_group.append(key)
                    printed.add(key)
        if keys_in_group:
            log(f"  [{group_name}]", "RESULT")
            for key in keys_in_group:
                display_key = display_names.get(key, key)
                unit = _unit_for(display_key)
                suffix = f" {unit}" if unit else ""
                log(f"    {display_key}: {timing_data[key]}{suffix}", "RESULT")
    
    # Check PSI size if available
    if "psi_size" in timing_data:
        actual_size = timing_data["psi_size"]
        expected_value = int(expected_target["value"])
        expected_metric = expected_target["metric"]
        tolerance = max(1, expected_value // 10)  # 10% tolerance
        
        if abs(actual_size - expected_value) > tolerance:
            log(
                f"✗ PSI mismatch! Expected {expected_metric}={expected_value}, got psi_size={actual_size}",
                "ERROR"
            )
            return False
        else:
            log(f"✓ PSI result is correct (within tolerance)")
            log(f"  Expected {expected_metric}: {expected_value}, Actual psi_size: {actual_size}")
    else:
        log("Warning: Could not extract PSI size from output", "WARNING")
    
    return True

def cleanup():
    """Clean up test data"""
    if CLEANUP_AFTER_TEST and os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
        log("Cleaned up test data directory")

def test_fhe_psi(seed_size_bit):
    """Main test function for FHE PSI"""
    log("Starting FHE PSI test...")
    
    # Check if executables exist
    required_files = ["./bin/gendata", "./bin/psi_server", "./bin/psi_client"]
    for file in required_files:
        if not os.path.exists(file):
            log(f"Error: {file} not found. Please build the project first.", "ERROR")
            return False
    
    # Kill any existing PSI processes before starting
    kill_existing_processes()
    
    try:
        # Generate test data
        success, server1_files, server2_files, expected_target = generate_test_data()
        if not success:
            return False
        
        # Run FHE test
        success, timing_data = run_fhe_test(server1_files, server2_files, seed_size_bit)
        if not success:
            return False
        
        # Validate results
        success = validate_results(expected_target, timing_data)
        
        return success
        
    finally:
        cleanup()

# ==================== MAIN FUNCTION ====================

def main():
    """Main function"""
    global SET_SIZE, LOG_FILE, INTERSECTION_SIZE, PRG_DD, NUM_CLIENTS_PER_SERVER
    global VERBOSE, MAX_WEIGHT, WEIGHT_SCALE_DIV, WEIGHTED_MULTILIMB_EXACT, WEIGHTED_LIMB_BITS, WEIGHTED_CHUNK_K

    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='FHE PSI Single Test')
    parser.add_argument('--set_size_bit', type=int, default=5,
                       help='Set size as power of 2 (e.g., 17 for 2^17 = 131072)')
    parser.add_argument('--prg_dd', type=int, default=6,
                       help='PRG degree parameter (default: 6)')
    parser.add_argument('--seed_size_bit', type=int, default=6,
                       help='Seed size as power of 2 (e.g., 6 for 2^6 = 64 bits)')
    parser.add_argument('--num_clients_per_server', type=int, default=1,
                       help='Number of clients connected to each server (default: 1)')
    parser.add_argument('--max_weight', type=int, default=0,
                       help='If >0, gendata assigns each element a uniform random weight in [1, max_weight]. '
                            'Default 0 = no weight column (unweighted).')
    parser.add_argument('--weight_scale_div', type=int, default=0,
                       help='Deprecated and ignored. Weight scaling is currently disabled.')
    parser.add_argument('--weight_scale_auto_model', type=str, default="vector_conservative",
                       choices=["vector", "vector_conservative", "formula"],
                       help='Deprecated and ignored. Kept for CLI compatibility.')
    parser.add_argument('--weighted_multilimb_exact', action='store_true',
                       help='Weighted mode only: use limb-decomposed OLE recovery path in 2PC '
                            '(no per-round residue reveal; still modulo plain_modulus).')
    parser.add_argument('--weighted_limb_bits', type=int, default=16,
                       help='Weighted multi-limb mode only: limb bit-width in [1,16] (default: 16).')
    parser.add_argument('--weighted_chunk_k', type=int, default=0,
                       help='Weighted mode only: split each tt bucket into chunks of this size in 2PC recovery '
                            '(0=auto-estimate from p/intersection/max_weight, manual value must be <= mom_k=400).')
    parser.add_argument('--output_log', type=str, default=None,
                       help='Output log file (default: fhe_test_<set_size_bit>.log)')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Stream all logs to the terminal (default: only timing results and errors).')

    args = parser.parse_args()

    # Set global variables based on arguments
    SET_SIZE = 2 ** args.set_size_bit
    INTERSECTION_SIZE = SET_SIZE // 2
    PRG_DD = args.prg_dd
    NUM_CLIENTS_PER_SERVER = args.num_clients_per_server
    MAX_WEIGHT = args.max_weight
    WEIGHT_SCALE_DIV = 1
    WEIGHTED_MULTILIMB_EXACT = args.weighted_multilimb_exact
    WEIGHTED_LIMB_BITS = args.weighted_limb_bits
    WEIGHTED_CHUNK_K = args.weighted_chunk_k
    VERBOSE = args.verbose
    if WEIGHTED_LIMB_BITS < 1 or WEIGHTED_LIMB_BITS > 16:
        log("Error: --weighted_limb_bits must be in [1,16]", "ERROR")
        return False
    if WEIGHTED_CHUNK_K < 0:
        log("Error: --weighted_chunk_k must be >= 0", "ERROR")
        return False

    chunk_mode = "disabled"
    plain_modulus_est = estimate_plain_modulus_from_bit(SEAL_PLAIN_MODULUS_BIT)
    per_round_upper_bound = 0
    if MAX_WEIGHT > 0:
        per_round_upper_bound = estimate_per_round_upper_bound(INTERSECTION_SIZE, MAX_WEIGHT)
        if per_round_upper_bound >= plain_modulus_est:
            max_weight_limit = int(math.isqrt(max(0, plain_modulus_est - 1) // max(1, INTERSECTION_SIZE)))
            max_intersection_limit = int((plain_modulus_est - 1) // max(1, MAX_WEIGHT * MAX_WEIGHT))
            log(
                "Estimated per-round upper bound exceeds plain modulus: "
                f"intersection={INTERSECTION_SIZE}, max_weight={MAX_WEIGHT}, "
                f"bound=intersection*max_weight^2={per_round_upper_bound}, "
                f"plain_modulus_est={plain_modulus_est}. "
                f"For this intersection, recommended max_weight <= {max_weight_limit}; "
                f"for this max_weight, recommended intersection <= {max_intersection_limit}.",
                "ERROR",
            )
            return False

        if args.weighted_chunk_k == 0:
            auto_chunk = estimate_weighted_chunk_k_from_bound(
                plain_modulus_est=plain_modulus_est,
                per_round_upper_bound=per_round_upper_bound,
                mom_k=MOM_K,
            )
            if auto_chunk <= 0:
                log(
                    "Auto weighted_chunk_k estimation failed: no chunk size keeps one revealed chunk "
                    "inside decode-safe range. Reduce max_weight/intersection or increase plain_modulus.",
                    "ERROR",
                )
                return False
            WEIGHTED_CHUNK_K = auto_chunk
            chunk_mode = "auto"
        else:
            WEIGHTED_CHUNK_K = args.weighted_chunk_k
            chunk_mode = "manual"

        if WEIGHTED_CHUNK_K > MOM_K:
            log(f"Error: --weighted_chunk_k must be <= mom_k ({MOM_K})", "ERROR")
            return False
    else:
        WEIGHTED_CHUNK_K = 0
    
    if args.output_log:
        LOG_FILE = args.output_log
    else:
        LOG_FILE = f"fhe_test_{args.set_size_bit}.log"
    
    # Clear previous log file
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
    
    log(f"Starting FHE PSI test with set_size=2^{args.set_size_bit}={SET_SIZE}")
    log(f"Seed size: 2^{args.seed_size_bit}={2 ** args.seed_size_bit} bits")
    log(f"PRG DD: {PRG_DD}")
    log(f"Clients per server: {NUM_CLIENTS_PER_SERVER}")
    log(f"Total clients: {2 * NUM_CLIENTS_PER_SERVER}")
    log(f"Max weight: {MAX_WEIGHT} ({'unweighted' if MAX_WEIGHT == 0 else f'random in [1, {MAX_WEIGHT}]'})")
    if MAX_WEIGHT > 0:
        log(
            "Weighted bound model: "
            f"per_round_upper=intersection*max_weight^2={per_round_upper_bound}, "
            f"plain_modulus_est={plain_modulus_est}",
            "WARNING",
        )
        if args.weight_scale_div not in (0, 1):
            log(
                f"--weight_scale_div={args.weight_scale_div} is ignored; "
                "weight scaling is disabled in this build.",
                "WARNING",
            )
        if WEIGHTED_MULTILIMB_EXACT:
            log(
                f"Weighted multi-limb OLE mode enabled (limb_bits={WEIGHTED_LIMB_BITS}); "
                "no per-round residue reveal, bucket-level reveal only",
                "WARNING"
            )
        if WEIGHTED_CHUNK_K > 0:
            log(
                "Weighted chunk aggregation enabled in recovery: "
                f"weighted_chunk_k={WEIGHTED_CHUNK_K} (mom_k={MOM_K}, mode={chunk_mode})",
                "WARNING"
            )
    log(f"Logging to: {LOG_FILE}")
    
    # Run single correctness test
    success = test_fhe_psi(args.seed_size_bit)
    
    if success:
        log("=" * 50, "RESULT")
        log("✓ FHE PSI TEST PASSED", "RESULT")
        log("=" * 50, "RESULT")
    else:
        log("=" * 50, "RESULT")
        log("✗ FHE PSI TEST FAILED", "RESULT")
        log("=" * 50, "RESULT")

    log(f"Log file saved to: {LOG_FILE}", "RESULT")
    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
