#!/usr/bin/env python3
"""
Test script for comparing FHE and naive PSI implementations
Tests if the FHE implementation produces the same results as naive implementation
"""

import os
import sys
import subprocess
import time
import signal
import shutil
import re

# ==================== CONFIGURABLE PARAMETERS ====================
# Test parameters - Keep small for FHE testing
UNIVERSAL_SIZE_BIT = 16  # Very small for FHE testing
SET_SIZE = 1000
INTERSECTION_SIZE = 400
NUM_CLIENTS_PER_SERVER = 1
OUTPUT_DIR = "./test_fhe_data"
PORT_NAIVE = 20929
PORT_FHE = 20930

# Test parameters
VERBOSE = True
CLEANUP_AFTER_TEST = True
TIMEOUT_SECONDS = 60  # Longer timeout for FHE

# ==================== HELPER FUNCTIONS ====================

def log(message, level="INFO"):
    """Print log message"""
    if VERBOSE:
        print(f"[{level}] {message}")

def run_command(cmd, description=""):
    """Run a command and return success status"""
    log(f"Running: {description or cmd}")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        if result.returncode == 0:
            log(f"✓ {description or cmd} completed successfully")
            if VERBOSE and result.stdout:
                print(result.stdout)
            return True, result.stdout
        else:
            log(f"✗ {description or cmd} failed with return code {result.returncode}", "ERROR")
            if result.stderr:
                print(f"Error output: {result.stderr}")
            return False, result.stderr
    except subprocess.TimeoutExpired:
        log(f"✗ {description or cmd} timed out", "ERROR")
        return False, "Timeout"
    except Exception as e:
        log(f"✗ {description or cmd} failed with exception: {e}", "ERROR")
        return False, str(e)

def read_set_from_file(filename):
    """Read a set from file"""
    if not os.path.exists(filename):
        return set()
    
    with open(filename, 'r') as f:
        return set(int(line.strip()) for line in f if line.strip())

def calculate_intersection(set1, set2):
    """Calculate intersection of two sets"""
    return set1.intersection(set2)

def kill_process_tree(pid):
    """Kill a process and all its children"""
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        time.sleep(1)
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except:
        pass

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
    time.sleep(2)  # Wait for process to start
    return process

def wait_for_processes(processes, timeout=60):
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
        time.sleep(1)
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

# ==================== TEST FUNCTIONS ====================

def generate_test_data():
    """Generate test data"""
    log("=" * 60)
    log("GENERATING TEST DATA")
    log("=" * 60)
    
    # Clean up previous test data
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    
    # Generate test data
    cmd = f"./build/bin/gendata --intersection_size={INTERSECTION_SIZE} " \
          f"--universal_size_bit={UNIVERSAL_SIZE_BIT} " \
          f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
          f"--set_size={SET_SIZE} " \
          f"--output_dir={OUTPUT_DIR}"
    
    success, output = run_command(cmd, "Data generation")
    if not success:
        return False, None, None, None
    
    # Read expected intersection from generated data
    server1_file = f"{OUTPUT_DIR}/client1_1.txt"
    server2_file = f"{OUTPUT_DIR}/client2_2.txt"
    
    if not os.path.exists(server1_file) or not os.path.exists(server2_file):
        log("Error: Generated data files not found", "ERROR")
        return False, None, None, None
    
    server1_set = read_set_from_file(server1_file)
    server2_set = read_set_from_file(server2_file)
    expected_intersection = calculate_intersection(server1_set, server2_set)
    expected_intersection_size = len(expected_intersection)
    
    log(f"Server 1 set size: {len(server1_set)}")
    log(f"Server 2 set size: {len(server2_set)}")
    log(f"Expected intersection size: {expected_intersection_size}")
    
    return True, server1_file, server2_file, expected_intersection_size

def run_psi_test(server1_file, server2_file, psi_mode, port):
    """Run PSI test with servers and clients"""
    log("=" * 60)
    log(f"RUNNING {psi_mode.upper()} PSI TEST")
    log("=" * 60)
    
    # Start servers
    server1_cmd = f"./build/bin/psi_server -p 1 --port {port} --psi_mode {psi_mode}"
    server2_cmd = f"./build/bin/psi_server -p 2 --port {port} --psi_mode {psi_mode}"
    
    server1_process = start_process(server1_cmd, f"Server 1 ({psi_mode})")
    server2_process = start_process(server2_cmd, f"Server 2 ({psi_mode})")
    
    # Start clients
    client1_cmd = f"./build/bin/psi_client -p 1 --port {port} --data_file={server1_file} --psi_mode {psi_mode}"
    client2_cmd = f"./build/bin/psi_client -p 2 --port {port} --data_file={server2_file} --psi_mode {psi_mode}"
    
    client1_process = start_process(client1_cmd, f"Client 1 ({psi_mode})")
    client2_process = start_process(client2_cmd, f"Client 2 ({psi_mode})")
    
    # Wait for all processes to complete
    all_processes = [server1_process, server2_process, client1_process, client2_process]
    success = wait_for_processes(all_processes, TIMEOUT_SECONDS)
    
    if not success:
        log(f"Some {psi_mode} processes did not complete within timeout", "ERROR")
        return False, None
    
    # Check if all processes completed successfully
    for i, process in enumerate(all_processes):
        if process.returncode != 0:
            log(f"{psi_mode} Process {i+1} failed with return code {process.returncode}", "ERROR")
            return False, None
    
    # Extract PSI size from server output
    actual_intersection_size = extract_psi_size_from_output([server1_process, server2_process])
    
    return True, actual_intersection_size

def validate_results(expected_size, actual_size, psi_mode):
    """Validate test results"""
    if actual_size is None:
        log(f"Could not extract {psi_mode} PSI size from output", "ERROR")
        return False
    
    tolerance = max(1, expected_size // 10)  # 10% tolerance
    
    if abs(actual_size - expected_size) > tolerance:
        log(f"✗ {psi_mode.upper()} intersection size mismatch! Expected {expected_size}, got {actual_size}", "ERROR")
        return False
    else:
        log(f"✓ {psi_mode.upper()} intersection size is correct (within tolerance)")
        log(f"  Expected: {expected_size}, Actual: {actual_size}")
        return True

def cleanup():
    """Clean up test data"""
    if CLEANUP_AFTER_TEST and os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
        log("Cleaned up test data directory")

def compare_fhe_vs_naive():
    """Compare FHE and naive PSI implementations"""
    log("=" * 60)
    log("COMPARING FHE vs NAIVE PSI IMPLEMENTATIONS")
    log("=" * 60)
    
    try:
        # Generate test data
        success, server1_file, server2_file, expected_size = generate_test_data()
        if not success:
            return False
        
        # Test naive implementation
        log("Testing NAIVE implementation...")
        naive_success, naive_size = run_psi_test(server1_file, server2_file, "naive", PORT_NAIVE)
        if not naive_success:
            log("Naive PSI test failed", "ERROR")
            return False
        
        # Test FHE implementation
        log("Testing FHE implementation...")
        fhe_success, fhe_size = run_psi_test(server1_file, server2_file, "fhe", PORT_FHE)
        if not fhe_success:
            log("FHE PSI test failed", "ERROR")
            return False
        
        # Validate results
        log("=" * 60)
        log("VALIDATING RESULTS")
        log("=" * 60)
        
        naive_valid = validate_results(expected_size, naive_size, "naive")
        fhe_valid = validate_results(expected_size, fhe_size, "fhe")
        
        if not naive_valid or not fhe_valid:
            return False
        
        # Compare naive vs FHE
        if naive_size == fhe_size:
            log(f"✓ NAIVE and FHE implementations produce identical results: {naive_size}")
            return True
        else:
            log(f"✗ NAIVE and FHE implementations produce different results!", "ERROR")
            log(f"  Naive: {naive_size}, FHE: {fhe_size}")
            return False
        
    finally:
        cleanup()

def test_fhe_consistency():
    """Test FHE implementation consistency across multiple runs"""
    log("=" * 60)
    log("TESTING FHE IMPLEMENTATION CONSISTENCY")
    log("=" * 60)
    
    results = []
    intersection_sizes = []
    num_runs = 3
    
    for run in range(num_runs):
        log(f"FHE Run {run + 1}/{num_runs}")
        
        try:
            # Generate fresh data for each run
            success, server1_file, server2_file, expected_size = generate_test_data()
            if not success:
                results.append(False)
                continue
            
            # Test FHE implementation
            fhe_success, fhe_size = run_psi_test(server1_file, server2_file, "fhe", PORT_FHE)
            
            if fhe_success and fhe_size is not None:
                results.append(True)
                intersection_sizes.append(fhe_size)
                log(f"FHE Run {run + 1} result: {fhe_size}")
            else:
                results.append(False)
            
        finally:
            cleanup()
        
        time.sleep(3)  # Brief pause between runs
    
    success_count = sum(results)
    log(f"FHE consistency test results: {success_count}/{num_runs} runs successful")
    
    if len(intersection_sizes) > 1:
        # Check if all results are consistent
        first_size = intersection_sizes[0]
        consistent = all(size == first_size for size in intersection_sizes)
        
        if consistent:
            log(f"✓ FHE results are consistent across runs: {first_size}")
        else:
            log(f"✗ FHE results are inconsistent across runs: {intersection_sizes}", "ERROR")
            return False
    
    return success_count == num_runs

def test_fhe_implementation():
    """Main test function for FHE PSI"""
    log("Starting FHE PSI test...")
    
    # Check if executables exist
    required_files = ["./build/bin/gendata", "./build/bin/psi_server", "./build/bin/psi_client"]
    for file in required_files:
        if not os.path.exists(file):
            log(f"Error: {file} not found. Please build the project first.", "ERROR")
            return False
    
    try:
        # Generate test data
        success, server1_file, server2_file, expected_size = generate_test_data()
        if not success:
            return False
        
        # Run FHE test
        success, actual_size = run_psi_test(server1_file, server2_file, "fhe", PORT_FHE)
        if not success:
            return False
        
        # Validate results
        success = validate_results(expected_size, actual_size, "fhe")
        
        return success
        
    finally:
        cleanup()

# ==================== MAIN FUNCTION ====================

def main():
    """Main function"""
    log("Starting FHE vs Naive comparison tests...")
    
    # Check if executables exist
    required_files = ["./build/bin/gendata", "./build/bin/psi_server", "./build/bin/psi_client"]
    for file in required_files:
        if not os.path.exists(file):
            log(f"Error: {file} not found. Please build the project first.", "ERROR")
            return False
    
    # Run comparison test
    success1 = compare_fhe_vs_naive()
    
    # Run FHE consistency test
    success2 = test_fhe_consistency()
    
    overall_success = success1 and success2
    
    if overall_success:
        log("=" * 60)
        log("✓ ALL FHE vs NAIVE TESTS PASSED")
        log("=" * 60)
    else:
        log("=" * 60)
        log("✗ SOME FHE vs NAIVE TESTS FAILED")
        log("=" * 60)
    
    return overall_success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 