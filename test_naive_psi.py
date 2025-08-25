#!/usr/bin/env python3
"""
Ultra-fast test script for naive PSI implementation validation
Uses global data manager - clients automatically get their data
"""

import os
import sys
import subprocess
import time
import signal
import shutil
import re
import threading
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

# ==================== CONFIGURABLE PARAMETERS ====================
# Test parameters - Optimized for maximum speed
UNIVERSAL_SIZE_BIT = 18  # Further reduced for ultra-fast testing
SET_SIZE = 5000  # Further reduced for ultra-fast testing
INTERSECTION_SIZE = SET_SIZE // 2  # Further reduced for ultra-fast testing
NUM_CLIENTS_PER_SERVER = 2  # Reduced for faster testing
PORT = 20929  # Different port to avoid conflicts
MOM_K = 100  # Further reduced for speed
MOM_T = 80  # Further reduced for speed

# Test parameters
VERBOSE = True
CLEANUP_AFTER_TEST = True
TIMEOUT_SECONDS = 10  # Further reduced timeout
USE_MULTIPROCESSING = True  # Use multiprocessing for parallel tests

# ==================== HELPER FUNCTIONS ====================

def log(message, level="INFO"):
    """Print log message"""
    if VERBOSE:
        print(f"[{level}] {message}")

def kill_existing_processes():
    """Kill any existing PSI processes"""
    try:
        subprocess.run("pkill -f psi_server", shell=True, capture_output=True)
        subprocess.run("pkill -f psi_client", shell=True, capture_output=True)
        time.sleep(0.05)  # Minimal wait
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
    time.sleep(0.1)  # Minimal wait
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
        time.sleep(0.02)  # Ultra-fast polling
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

# ==================== GLOBAL DATA MANAGER TEST ====================

def run_psi_test_global_data():
    """Run PSI test with servers and clients using global data manager"""
    log("=" * 50)
    log("RUNNING PSI TEST WITH GLOBAL DATA MANAGER")
    log("=" * 50)
    
    # Start servers
    server1_cmd = f"./build/bin/psi_server -p 1 --port {PORT} --psi_mode naive --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={MOM_K} --mom_t={MOM_T}"
    server2_cmd = f"./build/bin/psi_server -p 2 --port {PORT} --psi_mode naive --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={MOM_K} --mom_t={MOM_T}"
    
    server1_process = start_process(server1_cmd, "Server 1")
    server2_process = start_process(server2_cmd, "Server 2")
    
    # Start all clients - they will automatically get their data from global data manager
    client_processes = []
    
    # Start Server 1 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        client_cmd = f"./build/bin/psi_client -p {client_id} --port {PORT} --psi_mode naive --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={MOM_K} --mom_t={MOM_T}"
        client_process = start_process(client_cmd, f"Client {client_id} (Server 1)")
        client_processes.append(client_process)
    
    # Start Server 2 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        client_cmd = f"./build/bin/psi_client -p {client_id + NUM_CLIENTS_PER_SERVER} --port {PORT} --psi_mode naive --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={MOM_K} --mom_t={MOM_T}"
        client_process = start_process(client_cmd, f"Client {client_id} (Server 2)")
        client_processes.append(client_process)

    # Wait for all processes to complete
    all_processes = [server1_process, server2_process] + client_processes
    success = wait_for_processes(all_processes, TIMEOUT_SECONDS)
    
    if not success:
        log("Some processes did not complete within timeout", "ERROR")
        return False, None
    
    # Check if all processes completed successfully
    for i, process in enumerate(all_processes):
        if process.returncode != 0:
            log(f"Process {i+1} failed with return code {process.returncode}", "ERROR")
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
        log(f"✗ Intersection size mismatch! Expected {expected_size}, got {actual_size}", "ERROR")
        return False
    else:
        log(f"✓ Intersection size is correct (within tolerance)")
        log(f"  Expected: {expected_size}, Actual: {actual_size}")
        return True

# ==================== BATCH TESTING ====================

def run_single_test_global_data(test_id):
    """Run a single PSI test with global data manager"""
    log(f"Starting global data test {test_id}")
    
    # Run PSI test - data will be automatically generated and distributed
    success, actual_size = run_psi_test_global_data()
    if not success:
        return False
    
    # Get expected intersection size from global data manager
    expected_size = INTERSECTION_SIZE
    
    # Validate results
    success = validate_results(expected_size, actual_size)
    
    log(f"Global data test {test_id} {'PASSED' if success else 'FAILED'}")
    return success

def run_batch_tests_global_data(num_tests=10):
    """Run multiple tests in parallel with global data manager"""
    log("=" * 50)
    log(f"RUNNING GLOBAL DATA BATCH TESTS ({num_tests} tests)")
    log("=" * 50)
    
    if USE_MULTIPROCESSING:
        # Use multiprocessing for true parallelism
        with ProcessPoolExecutor(max_workers=min(num_tests, multiprocessing.cpu_count())) as executor:
            futures = [executor.submit(run_single_test_global_data, i) for i in range(num_tests)]
            results = [future.result() for future in futures]
    else:
        # Use threading for I/O bound operations
        with ThreadPoolExecutor(max_workers=num_tests) as executor:
            futures = [executor.submit(run_single_test_global_data, i) for i in range(num_tests)]
            results = [future.result() for future in futures]
    
    passed = sum(results)
    failed = len(results) - passed
    
    log(f"Global data batch test results: {passed}/{len(results)} passed, {failed} failed")
    return passed == len(results)

# ==================== MAIN FUNCTIONS ====================

def test_naive_psi_global_data():
    """Main test function for naive PSI with global data manager"""
    log("Starting global data manager naive PSI test...")
    
    # Check if executables exist
    required_files = ["./build/bin/psi_server", "./build/bin/psi_client"]
    for file in required_files:
        if not os.path.exists(file):
            log(f"Error: {file} not found. Please build the project first.", "ERROR")
            return False
    
    # Kill any existing PSI processes before starting
    kill_existing_processes()
    
    try:
        # Run PSI test - data will be automatically generated and distributed
        success, actual_size = run_psi_test_global_data()
        if not success:
            return False
        
        # Get expected intersection size from global data manager
        expected_size = INTERSECTION_SIZE
        
        # Validate results
        success = validate_results(expected_size, actual_size)
        
        return success
        
    except Exception as e:
        log(f"Test failed with exception: {e}", "ERROR")
        return False

def main():
    """Main function"""
    log("Starting global data manager naive PSI test...")
    
    # Run single correctness test
    success = test_naive_psi_global_data()
    
    if success:
        log("=" * 50)
        log("✓ GLOBAL DATA MANAGER NAIVE PSI TEST PASSED")
        log("=" * 50)
        
        # Optionally run batch tests
        if len(sys.argv) > 1 and sys.argv[1] == "--batch":
            num_tests = int(sys.argv[2]) if len(sys.argv) > 2 else 5
            batch_success = run_batch_tests_global_data(num_tests)
            if batch_success:
                log("✓ GLOBAL DATA BATCH TESTS PASSED")
            else:
                log("✗ GLOBAL DATA BATCH TESTS FAILED")
                success = False
    else:
        log("=" * 50)
        log("✗ GLOBAL DATA MANAGER NAIVE PSI TEST FAILED")
        log("=" * 50)
    
    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
