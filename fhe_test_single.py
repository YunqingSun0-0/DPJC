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
from datetime import datetime

# ==================== CONFIGURABLE PARAMETERS ====================
# Test parameters - Small for quick testing
UNIVERSAL_SIZE_BIT = 24  # Small for quick testing
SET_SIZE = None  # Will be set by command-line argument or default
LOG_FILE = None  # Will be set based on set size
INTERSECTION_SIZE = None  # Will be set by command-line argument or default
MAX_WEIGHT = 0  # 0 = no weights (default); >0 = uniform random weights in [1, MAX_WEIGHT]
WEIGHT_SCALE_DIV = 1  # weighted mode only: encode weight as round(weight / WEIGHT_SCALE_DIV)
WEIGHTED_MULTILIMB_EXACT = False  # weighted mode only: limb-decomposed OLE path (no per-round residue reveal)
WEIGHTED_LIMB_BITS = 16
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

def auto_weight_scale_div(set_size: int, max_weight: int, plain_modulus_bit: int = 24, mom_k: int = 400) -> int:
    """
    Auto-pick weight_scale_div from set size and max weight.

    Model (same random-weight mode as gendata):
      - intersection ~= set_size / 2
      - E[w] = (max_weight + 1) / 2
      - E[weighted_intersection_sum] ~= intersection * E[w]^2
      - one tt-bucket sums mom_k rounds, so expected bucket magnitude scales by mom_k

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
    mean_weight = (float(max_weight) + 1.0) / 2.0
    expected_weighted_sum = intersection_est * mean_weight * mean_weight
    expected_bucket_sum = expected_weighted_sum * float(mom_k)

    # Keep expected bucket around <= p/2 to limit wraparound in mod-p recovery.
    target_bucket = plain_modulus_est / 2.0
    required_from_bucket = math.sqrt(max(1.0, expected_bucket_sum / max(1.0, target_bucket)))

    # Also ensure encoded weight is representable (< plain_modulus).
    required_from_encoding = float(max_weight) / max(1.0, plain_modulus_est - 1.0)

    required = max(1.0, required_from_bucket, required_from_encoding)
    scale = _next_power_of_two(int(math.ceil(required)))
    return max(1, scale)

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
    weight_scale_flag = f" --weight_scale_div={WEIGHT_SCALE_DIV}" if MAX_WEIGHT > 0 else ""
    weighted_multilimb_flag = ""
    if MAX_WEIGHT > 0 and WEIGHTED_MULTILIMB_EXACT:
        weighted_multilimb_flag = f" --weighted_multilimb_exact --weighted_limb_bits={WEIGHTED_LIMB_BITS}"
    server1_cmd = f"./bin/psi_server -p 1 --port={PORT} --psi_mode=fhe " \
                  f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                  f"--seed_size_bit={seed_size_bit} --prg_dd={prg_dd} " \
                  f"--network_mode={network_mode}{weighted_flag}{weight_scale_flag}{weighted_multilimb_flag}"
    server2_cmd = f"./bin/psi_server -p 2 --port={PORT} --psi_mode=fhe " \
                  f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                  f"--seed_size_bit={seed_size_bit} --prg_dd={prg_dd} " \
                  f"--network_mode={network_mode}{weighted_flag}{weight_scale_flag}{weighted_multilimb_flag}"
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
                     f"--network_mode={network_mode}{weighted_flag}{weight_scale_flag}{weighted_multilimb_flag}"
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
                     f"--network_mode={network_mode}{weighted_flag}{weight_scale_flag}{weighted_multilimb_flag}"
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
    global VERBOSE, MAX_WEIGHT, WEIGHT_SCALE_DIV, WEIGHTED_MULTILIMB_EXACT, WEIGHTED_LIMB_BITS

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
                       help='Weighted mode only: encode each weight as round(weight / weight_scale_div), '
                            'then server restores estimate by multiplying back weight_scale_div^2. '
                            'Default 0 = auto-pick from set_size_bit and max_weight; >0 = manual override.')
    parser.add_argument('--weighted_multilimb_exact', action='store_true',
                       help='Weighted mode only: use limb-decomposed OLE recovery path in 2PC '
                            '(no per-round residue reveal; still modulo plain_modulus).')
    parser.add_argument('--weighted_limb_bits', type=int, default=16,
                       help='Weighted multi-limb mode only: limb bit-width in [1,16] (default: 16).')
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
    if args.weight_scale_div == 0 and MAX_WEIGHT > 0:
        WEIGHT_SCALE_DIV = auto_weight_scale_div(
            set_size=SET_SIZE,
            max_weight=MAX_WEIGHT,
            plain_modulus_bit=SEAL_PLAIN_MODULUS_BIT,
            mom_k=MOM_K
        )
    elif args.weight_scale_div == 0:
        WEIGHT_SCALE_DIV = 1
    else:
        WEIGHT_SCALE_DIV = args.weight_scale_div
    WEIGHTED_MULTILIMB_EXACT = args.weighted_multilimb_exact
    WEIGHTED_LIMB_BITS = args.weighted_limb_bits
    VERBOSE = args.verbose
    if WEIGHT_SCALE_DIV < 1:
        log("Error: --weight_scale_div must be >= 1", "ERROR")
        return False
    if WEIGHTED_LIMB_BITS < 1 or WEIGHTED_LIMB_BITS > 16:
        log("Error: --weighted_limb_bits must be in [1,16]", "ERROR")
        return False
    
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
        log(f"Weight scale divisor: {WEIGHT_SCALE_DIV}")
        if args.weight_scale_div == 0:
            log(
                f"Weight scale mode: auto (set_size={SET_SIZE}, max_weight={MAX_WEIGHT}, "
                f"plain_modulus_bit={SEAL_PLAIN_MODULUS_BIT}, mom_k={MOM_K})"
            )
        if WEIGHTED_MULTILIMB_EXACT:
            log(
                f"Weighted multi-limb OLE mode enabled (limb_bits={WEIGHTED_LIMB_BITS}); "
                "no per-round residue reveal, bucket-level reveal only",
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
