#!/usr/bin/env python3
"""
Fast test script for naive PSI implementation validation
Optimized for speed - runs only essential tests
"""

from multiprocessing import process
import os
import sys
import subprocess
import time
import signal
import shutil
import re
import threading
import argparse
from pathlib import Path

# ==================== CONFIGURABLE PARAMETERS ====================
# Test parameters - Optimized for speed
UNIVERSAL_SIZE_BIT = 24  # Reduced for faster testing
SET_SIZE = 2**10  # Reduced for faster testing
INTERSECTION_SIZE = SET_SIZE // 2  # Reduced for faster testing
NUM_CLIENTS_PER_SERVER = 2  # Reduced for faster testing
GENDATA_INPUT_FILE = "./uci_words/docword.nips.txt"  # e.g. "./uci_words/docword.kos.txt"; empty means random generation
OUTPUT_DIR = "./test_naive_data_fast"
PORT = 20930  # Different port to avoid conflicts
MOM_K = 2500
MOM_T = 11

# Test parameters
VERBOSE = True
CLEANUP_AFTER_TEST = True
TIMEOUT_SECONDS = 10  # Reduced timeout
STREAM_PROCESS_OUTPUT = False  # Show server/client output in terminal for debugging

SCRIPT_DIR = Path(__file__).resolve().parent
BIN_DIR = SCRIPT_DIR / "bin"

def repo_path(path_str):
    """Resolve a repo-relative path while preserving absolute paths."""
    if not path_str:
        return ""
    path = Path(path_str)
    return str(path if path.is_absolute() else SCRIPT_DIR / path)

# ==================== HELPER FUNCTIONS ====================

import subprocess
import threading
import os
import signal


def stream_output(pipe, prefix, collector, level="INFO"):
    try:
        for line in iter(pipe.readline, ''):
            if not line:
                break
            line = line.rstrip()
            collector.append(line)
            log(f"[{prefix}] {line}", level)
    finally:
        pipe.close()

def log(message, level="INFO"):
    """Print log message"""
    if VERBOSE:
        print(f"[{level}] {message}")

def kill_existing_processes():
    """Kill any existing PSI processes"""
    try:
        subprocess.run("pkill -f psi_server", shell=True, capture_output=True)
        subprocess.run("pkill -f psi_client", shell=True, capture_output=True)
        time.sleep(0.2)  # Minimal wait
    except Exception as e:
        log(f"Warning: Could not check for existing processes: {e}", "WARNING")

def run_command(cmd, description=""):
    """Run a command and return success status"""
    log(f"Running: {description or cmd}")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=SCRIPT_DIR,
        )
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

def read_weighted_set_from_file(filename):
    """Read a weighted set from file as value -> weight."""
    if not os.path.exists(filename):
        return {}
    
    with open(filename, 'r') as f:
        values = {}
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            value = int(parts[0])
            weight = int(parts[1]) if len(parts) > 1 else 1
            values[value] = weight
        return values

def merge_weighted_sets(weighted_sets):
    """Merge multiple client sets into one value -> summed weight map."""
    merged = {}
    for weighted_set in weighted_sets:
        for value, weight in weighted_set.items():
            merged[value] = merged.get(value, 0) + weight
    return merged

def compute_weighted_intersection_sum(server1_weights, server2_weights):
    """Compute sum of weight products over the shared values."""
    total = 0
    for value, weight1 in server1_weights.items():
        weight2 = server2_weights.get(value)
        if weight2 is not None:
            total += weight1 * weight2
    return total

# def start_process(cmd, description):
#     """Start a process in background"""
#     log(f"Starting {description}")
#     process = subprocess.Popen(
#         cmd, 
#         shell=True, 
#         stdout=subprocess.PIPE, 
#         stderr=subprocess.PIPE,
#         preexec_fn=os.setsid
#     )
#     time.sleep(0.3)  # Minimal wait
#     return process

def start_process(cmd, name):
    log(f"Starting {name}: {cmd}")
    process = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid,
        cwd=SCRIPT_DIR,
    )

    process._stdout_lines = []
    process._stderr_lines = []

    process._stdout_thread = threading.Thread(
        target=stream_output,
        args=(process.stdout, name, process._stdout_lines, "INFO"),
        daemon=True
    )
    process._stderr_thread = threading.Thread(
        target=stream_output,
        args=(process.stderr, name, process._stderr_lines, "ERROR"),
        daemon=True
    )

    process._stdout_thread.start()
    process._stderr_thread.start()

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

# def extract_psi_size_from_output(processes):
#     """Extract PSI size from server output"""
#     for process in processes:
#         if process.poll() is not None:
#             stderr = process.stderr.read().decode() if process.stderr else ""
#             lines = stderr.split('\n')
#             for line in lines:
#                 if 'Final PSI size' in line:
#                     numbers = re.findall(r'\d+', line)
#                     if numbers:
#                         return int(numbers[-1])
#     return None

def extract_psi_size_from_output(processes):
    """Extract PSI size from server output"""
    for process in processes:
        lines = getattr(process, "_stderr_lines", []) + getattr(process, "_stdout_lines", [])
        for line in lines:
            if 'Final PSI size' in line:
                numbers = re.findall(r'\d+', line)
                if numbers:
                    return int(numbers[-1])
    return None

# ==================== TEST FUNCTIONS ====================

def generate_test_data():
    """Generate test data"""
    log("=" * 50)
    log("GENERATING TEST DATA")
    log("=" * 50)
    
    # Clean up previous test data
    output_dir = repo_path(OUTPUT_DIR)
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    
    # Generate test data
    cmd = f"{BIN_DIR / 'gendata'} --intersection_size={INTERSECTION_SIZE} " \
          f"--universal_size_bit={UNIVERSAL_SIZE_BIT} " \
          f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
          f"--set_size={SET_SIZE} " \
          f"--output_dir={output_dir}"
    if GENDATA_INPUT_FILE:
        cmd += f" --uci_data_file={repo_path(GENDATA_INPUT_FILE)}"
    
    success, output = run_command(cmd, "Data generation")
    if not success:
        return False, None, None, None
    
    # Read expected intersection from generated data
    server1_files = [str(Path(output_dir) / f"client{i}_1.txt") for i in range(1, NUM_CLIENTS_PER_SERVER + 1)]
    server2_files = [str(Path(output_dir) / f"client{i}_2.txt") for i in range(1, NUM_CLIENTS_PER_SERVER + 1)]
    
    # Check if all files exist
    all_files = server1_files + server2_files
    for file in all_files:
        if not os.path.exists(file):
            log(f"Error: Generated data file not found: {file}", "ERROR")
            return False, None, None, None
    
    # Read all sets and calculate expected weighted intersection sum
    server1_sets = [read_weighted_set_from_file(f) for f in server1_files]
    server2_sets = [read_weighted_set_from_file(f) for f in server2_files]

    all_server1_weights = merge_weighted_sets(server1_sets)
    all_server2_weights = merge_weighted_sets(server2_sets)
    expected_intersection_sum = compute_weighted_intersection_sum(all_server1_weights, all_server2_weights)

    log(f"Server 1 total elements: {len(all_server1_weights)}")
    log(f"Server 2 total elements: {len(all_server2_weights)}")
    log(f"Expected weighted intersection sum: {expected_intersection_sum}")

    return True, server1_files, server2_files, expected_intersection_sum

def run_psi_test(server1_files, server2_files, psi_mode="naive"):
    """Run PSI test with servers and clients"""
    log("=" * 50)
    log(f"RUNNING PSI TEST ({psi_mode.upper()})")
    log("=" * 50)
    
    # Start servers
    server1_cmd = f"{BIN_DIR / 'psi_server'} -p 1 --port={PORT} --psi_mode={psi_mode}  --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --universal_set_size_bit={UNIVERSAL_SIZE_BIT} --test_mode --mom_k={MOM_K} --mom_t={MOM_T}"
    server2_cmd = f"{BIN_DIR / 'psi_server'} -p 2 --port={PORT} --psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --universal_set_size_bit={UNIVERSAL_SIZE_BIT} --test_mode --mom_k={MOM_K} --mom_t={MOM_T}"
    
    server1_process = start_process(server1_cmd, "Server 1")
    server2_process = start_process(server2_cmd, "Server 2")
    
    # Start all clients
    client_processes = []
    
    # Start Server 1 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        data_file = server1_files[i]
        client_cmd = f"{BIN_DIR / 'psi_client'} -p {client_id} --port={PORT} --data_file={data_file} --psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --universal_set_size_bit={UNIVERSAL_SIZE_BIT} --test_mode --mom_k={MOM_K} --mom_t={MOM_T}"
        client_process = start_process(client_cmd, f"Client {client_id} (Server 1)")
        client_processes.append(client_process)
    
    # Start Server 2 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        data_file = server2_files[i]
        client_cmd = f"{BIN_DIR / 'psi_client'} -p {client_id + NUM_CLIENTS_PER_SERVER} --port={PORT} --data_file={data_file} --psi_mode={psi_mode} --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --universal_set_size_bit={UNIVERSAL_SIZE_BIT} --test_mode --mom_k={MOM_K} --mom_t={MOM_T}"
        client_process = start_process(client_cmd, f"Client {client_id} (Server 2)")
        client_processes.append(client_process)

    # Wait for all processes to complete
    all_processes = [server1_process, server2_process] + client_processes
    success = wait_for_processes(all_processes, TIMEOUT_SECONDS)

    for process in all_processes:
        if hasattr(process, "_stdout_thread"):
            process._stdout_thread.join(timeout=1)
        if hasattr(process, "_stderr_thread"):
            process._stderr_thread.join(timeout=1)
    
    if not success:
        log("Some processes did not complete within timeout", "ERROR")
        for i, process in enumerate(all_processes):
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except Exception:
                pass

            log(f"\n--- Process {i+1} OUTPUT ---", "ERROR")
            log(f"Return code: {process.returncode}", "ERROR")
            log("STDOUT:\n" + "\n".join(getattr(process, "_stdout_lines", [])), "ERROR")
            log("STDERR:\n" + "\n".join(getattr(process, "_stderr_lines", [])), "ERROR")
        return False, None
    
    # Check if all processes completed successfully
    for i, process in enumerate(all_processes):
        if process.returncode != 0:
            log(f"Process {i+1} failed with return code {process.returncode}", "ERROR")
            log("STDOUT:\n" + "\n".join(getattr(process, "_stdout_lines", [])), "ERROR")
            log("STDERR:\n" + "\n".join(getattr(process, "_stderr_lines", [])), "ERROR")
            return False, None
    
    # Extract PSI size from server output
    actual_intersection_size = extract_psi_size_from_output([server1_process, server2_process])
    
    return True, actual_intersection_size

def validate_results(expected_size, actual_size):
    """Validate test results"""
    log("=" * 50)
    log("VALIDATING RESULTS")
    log("=" * 50)
    
    if actual_size is None:
        log("Could not extract PSI size from output", "ERROR")
        return False
    
    tolerance = max(1, expected_size // 10)  # 10% tolerance
    
    if abs(actual_size - expected_size) > tolerance:
        log(f"✗ Weighted intersection sum mismatch! Expected {expected_size}, got {actual_size}", "ERROR")
        return False
    else:
        log(f"✓ Weighted intersection sum is correct (within tolerance)")
        log(f"  Expected: {expected_size}, Actual: {actual_size}")
        return True

def cleanup():
    """Clean up test data"""
    if CLEANUP_AFTER_TEST and os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
        log("Cleaned up test data directory")

def test_naive_psi():
    """Main test function for naive PSI"""
    log("Starting fast naive PSI test...")
    
    # Check if executables exist
    required_files = [str(BIN_DIR / "gendata"), str(BIN_DIR / "psi_server"), str(BIN_DIR / "psi_client")]
    for file in required_files:
        if not os.path.exists(file):
            log(f"Error: {file} not found. Please build the project first.", "ERROR")
            return False
    
    # Kill any existing PSI processes before starting
    kill_existing_processes()
    
    try:
        # Generate test data
        success, server1_files, server2_files, expected_size = generate_test_data()
        if not success:
            return False
        
        # Run PSI test
        success, actual_size = run_psi_test(server1_files, server2_files)
        if not success:
            return False
        
        # Validate results
        success = validate_results(expected_size, actual_size)
        
        return success
        
    finally:
        cleanup()

def test_naive_uniform_psi():
    """Test function for naive uniform PSI"""
    log("Starting fast naive_uniform PSI test...")
    
    # Check if executables exist
    required_files = [str(BIN_DIR / "gendata"), str(BIN_DIR / "psi_server"), str(BIN_DIR / "psi_client")]
    for file in required_files:
        if not os.path.exists(file):
            log(f"Error: {file} not found. Please build the project first.", "ERROR")
            return False
    
    # Kill any existing PSI processes before starting
    kill_existing_processes()
    
    try:
        # Generate test data
        success, server1_files, server2_files, expected_size = generate_test_data()
        if not success:
            return False
        
        # Run PSI test
        success, actual_size = run_psi_test(server1_files, server2_files, "naive_uniform")
        if not success:
            return False
        
        # Validate results
        success = validate_results(expected_size, actual_size)
        
        return success
        
    finally:
        cleanup()

def test_naive_fourwise_psi():
    """Test function for naive uniform PSI"""
    log("Starting fast naive_uniform PSI test...")
    
    # Check if executables exist
    required_files = [str(BIN_DIR / "gendata"), str(BIN_DIR / "psi_server"), str(BIN_DIR / "psi_client")]
    for file in required_files:
        if not os.path.exists(file):
            log(f"Error: {file} not found. Please build the project first.", "ERROR")
            return False
    
    # Kill any existing PSI processes before starting
    kill_existing_processes()
    
    try:
        # Generate test data
        success, server1_files, server2_files, expected_size = generate_test_data()
        if not success:
            return False
        
        # Run PSI test
        success, actual_size = run_psi_test(server1_files, server2_files, "naive_fourwise")
        if not success:
            return False
        
        # Validate results
        success = validate_results(expected_size, actual_size)
        
        return success
        
    finally:
        cleanup()

# ==================== MAIN FUNCTION ====================

def main():
    """Main function"""
    log("Starting fast PSI tests...")
    
    all_passed = True
    # Run naive PSI test
    log("\n" + "=" * 50)
    log("TEST 1: NAIVE PSI")
    log("=" * 50)
    naive_success = test_naive_psi()
    if naive_success:
        log("=" * 50)
        log("✓ NAIVE PSI TEST PASSED")
        log("=" * 50)
    else:
        log("=" * 50)
        log("✗ NAIVE PSI TEST FAILED")
        log("=" * 50)

    all_passed = all_passed and naive_success

    # Run naive_uniform PSI test
    log("\n" + "=" * 50)
    log("TEST 2: NAIVE UNIFORM PSI")
    log("=" * 50)
    uniform_success = test_naive_uniform_psi()
    
    if uniform_success:
        log("=" * 50)
        log("✓ NAIVE UNIFORM PSI TEST PASSED")
        log("=" * 50)
    else:
        log("=" * 50)
        log("✗ NAIVE UNIFORM PSI TEST FAILED")
        log("=" * 50)

    all_passed = all_passed and uniform_success
    # Run naive_uniform PSI test
    log("\n" + "=" * 50)
    log("TEST 3: NAIVE fourwise PSI")
    log("=" * 50)
    fourwise_success = test_naive_fourwise_psi()
    if fourwise_success:
        log("=" * 50)
        log("✓ NAIVE fourwise PSI TEST PASSED")
        log("=" * 50)
    else:
        log("=" * 50)
        log("✗ NAIVE fourwise PSI TEST FAILED")
        log("=" * 50)

    all_passed = all_passed and fourwise_success

    if all_passed:
        log("\n✓ ALL TESTS PASSED")
    else:
        log("\n✗ SOME TESTS FAILED")
    
    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 
