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
UNIVERSAL_SIZE_BIT = 24  # Small for quick testing
SET_SIZE = 2**7  # Small for quick testing
INTERSECTION_SIZE = SET_SIZE // 2
NUM_CLIENTS_PER_SERVER = 1
OUTPUT_DIR = "./test_fhe_single"
PORT = 22000

# Test parameters
VERBOSE = True
CLEANUP_AFTER_TEST = True
TIMEOUT_SECONDS = 7200  # 5 minutes timeout for FHE

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

def wait_for_processes(processes, timeout=TIMEOUT_SECONDS):
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
    all_process_logs = []

    def _to_metric_key(prefix):
        """Convert log label to stable snake_case metric key."""
        key = prefix.strip().lower()
        key = re.sub(r'[^a-z0-9]+', '_', key).strip('_')
        return f"{key}_communication_mb"

    def _store_communication_metric(metric_key, value):
        """Store communication metric without overwriting duplicates."""
        if metric_key not in timing_data:
            timing_data[metric_key] = value
            return
        idx = 2
        while f"{metric_key}_{idx}" in timing_data:
            idx += 1
        timing_data[f"{metric_key}_{idx}"] = value
    
    for i, process in enumerate(processes):
        if process.poll() is not None:
            stdout = process.stdout.read().decode() if process.stdout else ""
            stderr = process.stderr.read().decode() if process.stderr else ""
            output = stdout + stderr

            # Keep full process logs so newly added psi.cpp debug outputs are shown automatically.
            all_process_logs.append({
                "process": i + 1,
                "stdout": stdout,
                "stderr": stderr,
            })
            
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
            
            # Extract all communication sizes with "... Communication: X MB"
            comm_matches = re.findall(
                r'([A-Za-z][A-Za-z0-9 _-]*?)\s+Communication:\s*([\d.]+)\s*MB',
                output
            )
            for label, value in comm_matches:
                value = float(value)
                metric_key = _to_metric_key(label)
                _store_communication_metric(metric_key, value)

            # Extract special communication logs without "Communication:"
            special_comm_patterns = [
                (r'(testmode-prgsetup)\s*([\d.]+)\s*MB', "testmode_prgsetup_communication_mb"),
                (r'(noise budget and secret key)\s*([\d.]+)\s*MB', "noise_budget_and_secret_key_communication_mb"),
            ]
            for pattern, metric_key in special_comm_patterns:
                for _, value in re.findall(pattern, output):
                    _store_communication_metric(metric_key, float(value))
            
            # Extract PSI size
            psi_match = re.search(r'Final PSI size: (\d+)', output)
            if psi_match:
                timing_data["psi_size"] = int(psi_match.group(1))
            
            # Extract total server time
            total_match = re.search(r'Total server time: ([\d.]+)s', output)
            if total_match:
                timing_data[f"total_server_{i+1}"] = float(total_match.group(1))
    
    # Add a convenient aggregate over all extracted communication metrics
    total_communication_mb = sum(
        value for key, value in timing_data.items()
        if "communication" in key and isinstance(value, (int, float))
    )
    if total_communication_mb > 0:
        timing_data["total_communication_mb"] = total_communication_mb

    if all_process_logs:
        timing_data["all_process_logs"] = all_process_logs

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
    server1_cmd = f"stdbuf -oL -eL ./bin/psi_server -p 1 --port={PORT} --psi_mode=fhe " \
                  f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                  f"--seed_size={seed_size} --prg_dd={prg_dd} " \
                  f"--network_mode={network_mode}"
    server2_cmd = f"stdbuf -oL -eL ./bin/psi_server -p 2 --port={PORT} --psi_mode=fhe " \
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
        client_cmd = f"stdbuf -oL -eL ./bin/psi_client -p {client_id} --port={PORT} " \
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
        client_cmd = f"stdbuf -oL -eL ./bin/psi_client -p {client_id + NUM_CLIENTS_PER_SERVER} --port={PORT} " \
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
        timing_data = extract_timing_from_output(all_processes)
        return False, timing_data
    
    # Check if all processes completed successfully
    for i, process in enumerate(all_processes):
        if process.returncode != 0:
            log(f"Process {i+1} failed with return code {process.returncode}", "ERROR")
            timing_data = extract_timing_from_output(all_processes)
            return False, timing_data
    
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

    # Print complete PSI process logs (no keyword filter).
    all_process_logs = timing_data.get("all_process_logs", [])
    if all_process_logs:
        log("Full PSI Process Logs:")
        for proc in all_process_logs:
            proc_id = proc["process"]
            stdout = proc["stdout"].strip()
            stderr = proc["stderr"].strip()
            log(f"Process {proc_id} stdout:")
            if stdout:
                for line in stdout.splitlines():
                    log(f"  {line}")
            else:
                log("  <empty>")
            log(f"Process {proc_id} stderr:")
            if stderr:
                for line in stderr.splitlines():
                    log(f"  {line}")
            else:
                log("  <empty>")
    
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

        # ==================== NEW: CLIENT AVERAGE TIME ====================
    client_times = [
        value for key, value in timing_data.items()
        if key.startswith("client_compute_time")
    ]
    
    if client_times:
        avg_client_time = sum(client_times) / len(client_times)
        log(f"\nAverage client computation time: {avg_client_time:.6f} s")

        # ==================== PER ELEMENT TIME ====================
        # 每个 client 处理 SET_SIZE 个元素
        avg_time_per_element = avg_client_time / SET_SIZE
        
        log(f"Average time per element: {avg_time_per_element:.9f} s")
        
        # 可选：更直观（微秒）
        log(f"Average time per element: {avg_time_per_element * 1e3:.3f} ms")
    else:
        log("Warning: No client computation time found", "WARNING")
    
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
    required_files = ["./bin/gendata", "./bin/psi_server", "./bin/psi_client"]
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
            # Print whatever logs/metrics we collected to aid debugging.
            if timing_data:
                validate_results(expected_size, timing_data)
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
