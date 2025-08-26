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

# ==================== CONFIGURABLE PARAMETERS ====================
# Test parameters - Small for quick testing
UNIVERSAL_SIZE_BIT = 20  # Small for quick testing
SET_SIZE = 2**12  # Small for quick testing
INTERSECTION_SIZE = SET_SIZE // 2
NUM_CLIENTS_PER_SERVER = 4
OUTPUT_DIR = "./test_fhe_single"
PORT = 22000

# Test parameters
VERBOSE = True
CLEANUP_AFTER_TEST = True
TIMEOUT_SECONDS = 120  # 2 minutes timeout for FHE

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
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid
    )
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

def extract_timing_from_output(processes):
    """Extract timing information from process output"""
    timing_data = {}
    
    for i, process in enumerate(processes):
        if process.poll() is not None:
            stdout = process.stdout.read().decode() if process.stdout else ""
            stderr = process.stderr.read().decode() if process.stderr else ""
            output = stdout + stderr
            
            if i < 3: # Servers
                log(f"Process {i+1} output:")
                log(output[:1000] + "..." if len(output) > 1000 else output)
            
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
            
            # Extract key generation communication size
            comm_match = re.search(r'Key generation Communication: ([\d.]+) MB', output)
            if comm_match:
                timing_data["key_gen_communication_mb"] = float(comm_match.group(1))
            
            # Extract server recovery communication size
            comm_match = re.search(r'Server recovery Communication: ([\d.]+) MB', output)
            if comm_match:
                timing_data["server_recover_communication_mb"] = float(comm_match.group(1))
            
            # Extract PSI size
            psi_match = re.search(r'Final PSI size: (\d+)', output)
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
    cmd = f"./build/bin/gendata --intersection_size={INTERSECTION_SIZE} " \
          f"--universal_size_bit={UNIVERSAL_SIZE_BIT} " \
          f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
          f"--set_size={SET_SIZE} " \
          f"--output_dir={OUTPUT_DIR}"
    
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
    
    # Calculate expected intersection size (simplified)
    expected_intersection_size = INTERSECTION_SIZE
    
    log(f"Set size: {SET_SIZE}")
    log(f"Expected intersection size: {expected_intersection_size}")
    
    return True, server1_files, server2_files, expected_intersection_size

def run_fhe_test(server1_files, server2_files):
    """Run FHE PSI test"""
    log("=" * 50)
    log("RUNNING FHE PSI TEST")
    log("=" * 50)
    
    # FHE parameters
    seed_size = 64
    prg_dd = 6
    network_mode = "lan"
    
    # Start servers with FHE mode
    server1_cmd = f"./build/bin/psi_server -p 1 --port={PORT} --psi_mode=fhe " \
                  f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                  f"--seed_size={seed_size} --prg_dd={prg_dd} " \
                  f"--network_mode={network_mode}"
    server2_cmd = f"./build/bin/psi_server -p 2 --port={PORT} --psi_mode=fhe " \
                  f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                  f"--seed_size={seed_size} --prg_dd={prg_dd} " \
                  f"--network_mode={network_mode}"
    server1_process = start_process(server1_cmd, "Server 1 (FHE)")
    server2_process = start_process(server2_cmd, "Server 2 (FHE)")
    
    # Start clients
    client_processes = []
    
    # Start Server 1 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        data_file = server1_files[i]
        client_cmd = f"./build/bin/psi_client -p {client_id} --port={PORT} " \
                     f"--data_file={data_file} --psi_mode=fhe " \
                     f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                     f"--seed_size={seed_size} --prg_dd={prg_dd} " \
                     f"--network_mode={network_mode}"
        client_process = start_process(client_cmd, f"Client {client_id} (Server 1)")
        client_processes.append(client_process)
    
    # Start Server 2 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        data_file = server2_files[i]
        client_cmd = f"./build/bin/psi_client -p {client_id + NUM_CLIENTS_PER_SERVER} --port={PORT} " \
                     f"--data_file={data_file} --psi_mode=fhe " \
                     f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                     f"--seed_size={seed_size} --prg_dd={prg_dd} " \
                     f"--network_mode={network_mode}"
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
    
    # Extract timing information
    timing_data = extract_timing_from_output(all_processes)
    
    return True, timing_data

def validate_results(expected_size, timing_data):
    """Validate test results"""
    log("=" * 50)
    log("VALIDATING RESULTS")
    log("=" * 50)
    
    if not timing_data:
        log("No timing data extracted", "ERROR")
        return False
    
    # Print timing information
    log("Timing Results:")
    for key, value in timing_data.items():
        if "time" in key or "size" in key or "communication" in key:
            log(f"  {key}: {value}")
    
    # Check PSI size if available
    if "psi_size" in timing_data:
        actual_size = timing_data["psi_size"]
        tolerance = max(1, expected_size // 10)  # 10% tolerance
        
        if abs(actual_size - expected_size) > tolerance:
            log(f"✗ Intersection size mismatch! Expected {expected_size}, got {actual_size}", "ERROR")
            return False
        else:
            log(f"✓ Intersection size is correct (within tolerance)")
            log(f"  Expected: {expected_size}, Actual: {actual_size}")
    else:
        log("Warning: Could not extract PSI size from output", "WARNING")
    
    return True

def cleanup():
    """Clean up test data"""
    if CLEANUP_AFTER_TEST and os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
        log("Cleaned up test data directory")

def test_fhe_psi():
    """Main test function for FHE PSI"""
    log("Starting FHE PSI test...")
    
    # Check if executables exist
    required_files = ["./build/bin/gendata", "./build/bin/psi_server", "./build/bin/psi_client"]
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
        
        # Run FHE test
        success, timing_data = run_fhe_test(server1_files, server2_files)
        if not success:
            return False
        
        # Validate results
        success = validate_results(expected_size, timing_data)
        
        return success
        
    finally:
        cleanup()

# ==================== MAIN FUNCTION ====================

def main():
    """Main function"""
    log("Starting FHE PSI test...")
    
    # Run single correctness test
    success = test_fhe_psi()
    
    if success:
        log("=" * 50)
        log("✓ FHE PSI TEST PASSED")
        log("=" * 50)
    else:
        log("=" * 50)
        log("✗ FHE PSI TEST FAILED")
        log("=" * 50)
    
    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
