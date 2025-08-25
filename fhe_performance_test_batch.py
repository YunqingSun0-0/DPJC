#!/usr/bin/env python3
"""
FHE Performance Batch Test Script
Tests FHE PSI performance with different parameters and generates performance charts
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
from datetime import datetime
import argparse
import threading
from pathlib import Path

# ==================== CONFIGURABLE PARAMETERS ====================
# Import configuration
try:
    from fhe_test_config import *
    print("✓ Successfully imported configuration from fhe_test_config.py")
    # Configuration successfully imported, no fallback needed
except ImportError:
    print("Warning: fhe_test_config.py not found, using default configuration")
    # Default parameters if config file is not available
    SET_SIZE = 1 << 18  # 2^18
    INTERSECTION_SIZE = SET_SIZE // 2  # Half of set size
    UNIVERSAL_SIZE_BIT = 24
    NUM_CLIENTS_PER_SERVER = 1
    NUM_RUNS_PER_POINT = 5  # Number of runs per parameter combination
    PORT_BASE = 22000  # Base port for tests
    SEED_SIZE_VALUES = [64, 128]  # seed_size values
    SET_SIZE_VALUES = [1 << 6, 1 << 8, 1 << 10, 1 << 12, 1 << 14, 1 << 16, 1 << 18]  # set sizes for client computation
    PRG_DD_VALUES = [4, 5, 6, 7, 8]  # d values for client computation
    NETWORK_MODES = ['lan', 'wan']  # Network simulation modes
    TIMEOUT_SECONDS = 300  # 5 minutes timeout for FHE operations
    VERBOSE = True
    CLEANUP_AFTER_TEST = True
    RESULTS_DIR = "./fhe_performance_results"
    DATA_DIR = "./fhe_performance_data"
    PLOTS_DIR = "./fhe_performance_plots"
    # Default values for tests
    DEFAULT_SEED_SIZE = 64
    DEFAULT_PRG_DD = 6
    DEFAULT_NETWORK_MODE = "lan"

# File paths
PROGRESS_FILE = os.path.join(RESULTS_DIR, "test_progress.json")
RESULTS_FILE = os.path.join(RESULTS_DIR, "performance_results.json")

# ==================== HELPER FUNCTIONS ====================

def log(message, level="INFO"):
    """Print log message with timestamp"""
    if VERBOSE or level in ["ERROR", "WARNING"]:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}")

def ensure_directories():
    """Ensure all required directories exist"""
    for directory in [RESULTS_DIR, DATA_DIR, PLOTS_DIR]:
        os.makedirs(directory, exist_ok=True)

def kill_existing_processes():
    """Kill any existing PSI processes"""
    try:
        subprocess.run("pkill -f psi_server", shell=True, capture_output=True)
        subprocess.run("pkill -f psi_client", shell=True, capture_output=True)
        time.sleep(0.2)
    except Exception as e:
        log(f"Warning: Could not check for existing processes: {e}", "WARNING")

def run_command(cmd, description="", timeout=None):
    """Run a command and return success status"""
    if timeout is None:
        timeout = TIMEOUT_SECONDS
    
    log(f"Running: {description or cmd}")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
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
    time.sleep(0.5)  # Wait for process to start
    return process

def wait_for_processes(processes, timeout=None):
    """Wait for multiple processes to complete"""
    if timeout is None:
        timeout = TIMEOUT_SECONDS
    
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

def extract_timing_from_output(processes, timing_type):
    """Extract timing information from process output"""
    timing_data = {}
    psi_size = None  # Initialize psi_size variable
    
    for i, process in enumerate(processes):
        if process.poll() is not None:
            # stdout = process.stdout.read().decode() if process.stdout else ""
            # stderr = process.stderr.read().decode() if process.stderr else ""
            # output = stdout + stderr
            stdout, stderr = process.communicate()
            output = stdout.decode() + stderr.decode()
            # log(f"Process {i+1} output:")
            # log(output[:1000] + "..." if len(output) > 1000 else output)
            
            # Extract timing information based on type
            if timing_type == "key_gen":
                # Look for key generation timing
                match = re.search(r'Key generation time: ([\d.]+)s', output)
                if match:
                    timing_data[f"server_{i+1}"] = float(match.group(1))
                
                # Extract key generation communication size
                comm_match = re.search(r'Key generation Communication: ([\d.]+) MB', output)
                if comm_match:
                    timing_data["key_gen_communication_mb"] = float(comm_match.group(1))
            
            elif timing_type == "client_compute":
                # Look for client computation timing
                match = re.search(r'Client computation time: ([\d.]+)s', output)
                if match:
                    timing_data["client"] = float(match.group(1))
            
            elif timing_type == "server_recover":
                # Look for server recovery timing
                match = re.search(r'Server recovery time: ([\d.]+)s', output)
                if match:
                    timing_data[f"server_{i+1}"] = float(match.group(1))
                
                # Extract server recovery communication size
                comm_match = re.search(r'Server recovery Communication: ([\d.]+) MB', output)
                if comm_match:
                    timing_data["server_recover_communication_mb"] = float(comm_match.group(1))
            
            # Extract PSI size if available (but don't add it to timing_data)
            if psi_size is None:
                psi_match = re.search(r'Final PSI size: (\d+)', output)
                if psi_match:
                    psi_size = int(psi_match.group(1))
    
    # Return both timing data and psi_size separately
    return timing_data, psi_size

def load_progress():
    """Load test progress from file"""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            log(f"Error loading progress file: {e}", "WARNING")
    return {"completed_tests": [], "current_test": None}

def save_progress(progress):
    """Save test progress to file"""
    try:
        with open(PROGRESS_FILE, 'w') as f:
            json.dump(progress, f, indent=2)
    except Exception as e:
        log(f"Error saving progress file: {e}", "ERROR")

def load_results():
    """Load existing test results"""
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            log(f"Error loading results file: {e}", "WARNING")
    return {}

def save_results(results):
    """Save test results to file"""
    try:
        with open(RESULTS_FILE, 'w') as f:
            json.dump(results, f, indent=2)
    except Exception as e:
        log(f"Error saving results file: {e}", "ERROR")

# ==================== TEST FUNCTIONS ====================

def generate_test_data(set_size, intersection_size, output_dir):
    """Generate test data for given parameters"""
    log(f"Generating test data: set_size={set_size}, intersection_size={intersection_size}")
    
    # Clean up previous test data
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    
    # Generate test data
    cmd = f"./build/bin/gendata --intersection_size={intersection_size} " \
          f"--universal_size_bit={UNIVERSAL_SIZE_BIT} " \
          f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
          f"--set_size={set_size} " \
          f"--output_dir={output_dir}"
    
    success, output = run_command(cmd, "Data generation", timeout=60)
    if not success:
        return False, None, None
    
    # Get file paths
    server1_files = [f"{output_dir}/client{i}_1.txt" for i in range(1, NUM_CLIENTS_PER_SERVER + 1)]
    server2_files = [f"{output_dir}/client{i}_2.txt" for i in range(1, NUM_CLIENTS_PER_SERVER + 1)]
    
    # Check if all files exist
    all_files = server1_files + server2_files
    for file in all_files:
        if not os.path.exists(file):
            log(f"Error: Generated data file not found: {file}", "ERROR")
            return False, None, None
    
    return True, server1_files, server2_files

def run_fhe_test(server1_files, server2_files, seed_size, prg_dd, network_mode, port):
    """Run FHE PSI test with given parameters"""
    log(f"Running FHE test: seed_size={seed_size}, prg_dd={prg_dd}, network={network_mode}")
    
    # Start servers with FHE mode
    server1_cmd = f"./build/bin/psi_server -p 1 --port={port} --psi_mode=fhe " \
                  f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                  f"--seed_size={seed_size} --prg_dd={prg_dd} " \
                  f"--network_mode={network_mode} --test_mode"
    
    server2_cmd = f"./build/bin/psi_server -p 2 --port={port} --psi_mode=fhe " \
                  f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                  f"--seed_size={seed_size} --prg_dd={prg_dd} " \
                  f"--network_mode={network_mode} --test_mode"
    
    server1_process = start_process(server1_cmd, "Server 1 (FHE)")
    server2_process = start_process(server2_cmd, "Server 2 (FHE)")
    
    # Start clients
    client_processes = []
    
    # Start Server 1 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        data_file = server1_files[i]
        client_cmd = f"./build/bin/psi_client -p {client_id} --port={port} " \
                     f"--data_file={data_file} --psi_mode=fhe " \
                     f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                     f"--seed_size={seed_size} --prg_dd={prg_dd} " \
                     f"--network_mode={network_mode} --test_mode"
        client_process = start_process(client_cmd, f"Client {client_id} (Server 1)")
        client_processes.append(client_process)
    
    # Start Server 2 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        data_file = server2_files[i]
        client_cmd = f"./build/bin/psi_client -p {client_id + NUM_CLIENTS_PER_SERVER} --port={port} " \
                     f"--data_file={data_file} --psi_mode=fhe " \
                     f"--num_clients_per_server={NUM_CLIENTS_PER_SERVER} " \
                     f"--seed_size={seed_size} --prg_dd={prg_dd} " \
                     f"--network_mode={network_mode} --test_mode"
        client_process = start_process(client_cmd, f"Client {client_id} (Server 2)")
        client_processes.append(client_process)
    
    # Wait for all processes to complete
    all_processes = [server1_process, server2_process] + client_processes
    success = wait_for_processes(all_processes, TIMEOUT_SECONDS)
    
    if not success:
        log("Some processes did not complete within timeout", "ERROR")
        return False, None, None, None
    
    # Check if all processes completed successfully
    for i, process in enumerate(all_processes):
        if process.returncode != 0:
            log(f"Process {i+1} failed with return code {process.returncode}", "ERROR")
            return False, None, None, None
    
    # Extract timing information
    key_gen_timing, psi_size = extract_timing_from_output([server1_process, server2_process], "key_gen")
    client_compute_timing, _ = extract_timing_from_output(client_processes, "client_compute")
    server_recover_timing, _ = extract_timing_from_output([server1_process, server2_process], "server_recover")
    
    return True, key_gen_timing, client_compute_timing, server_recover_timing, psi_size

def run_single_test_point(seed_size, set_size, prg_dd, network_mode, test_id, test_type):
    """Run a single test point with multiple runs"""
    log(f"Running test point {test_id}: seed_size={seed_size}, set_size={set_size}, prg_dd={prg_dd}, network={network_mode}")
    
    # Generate unique test directory
    test_dir = f"{DATA_DIR}/test_{test_id}"
    port = PORT_BASE + test_id
    
    # Generate test data
    success, server1_files, server2_files = generate_test_data(set_size, set_size // 2, test_dir)
    if not success:
        return None
    
    # Run multiple times
    key_gen_times = []
    client_compute_times = []
    server_recover_times = []
    communication_sizes = []
    
    for run in range(NUM_RUNS_PER_POINT):
        log(f"Run {run + 1}/{NUM_RUNS_PER_POINT}")
        
        # Kill any existing processes
        kill_existing_processes()
        
        # Run test
        success, key_gen_timing, client_compute_timing, server_recover_timing, psi_size = run_fhe_test(
            server1_files, server2_files, seed_size, prg_dd, network_mode, port
        )
        
        if success:
            # Collect timing data
            if key_gen_timing:
                key_gen_times.append(key_gen_timing)
            if client_compute_timing:
                client_compute_times.append(client_compute_timing)
            if server_recover_timing:
                server_recover_times.append(server_recover_timing)
            
            # Collect communication sizes based on test type
            if test_type == "key_gen" and key_gen_timing and "key_gen_communication_mb" in key_gen_timing:
                communication_sizes.append(key_gen_timing["key_gen_communication_mb"])
            elif test_type == "server_recover" and server_recover_timing and "server_recover_communication_mb" in server_recover_timing:
                communication_sizes.append(server_recover_timing["server_recover_communication_mb"])
            
            # PSI size is now directly returned from run_fhe_test
        
        # Clean up
        if CLEANUP_AFTER_TEST and os.path.exists(test_dir):
            shutil.rmtree(test_dir)
    
    # Calculate averages
    result = {
        "test_id": test_id,
        "type": test_type,  # Add test type for plotting
        "seed_size": seed_size,
        "set_size": set_size,
        "prg_dd": prg_dd,
        "network_mode": network_mode,
        "runs": NUM_RUNS_PER_POINT,
        "key_gen_times": key_gen_times,
        "client_compute_times": client_compute_times,
        "server_recover_times": server_recover_times,
        "communication_sizes": communication_sizes,
        "psi_size": psi_size,  # PSI size at the same level as other data
        "timestamp": datetime.now().isoformat()
    }
    
    return result

# ==================== BATCH TEST FUNCTIONS ====================

def run_batch_tests():
    """Run all batch tests"""
    log("=" * 60)
    log("STARTING FHE PERFORMANCE BATCH TESTS")
    log("=" * 60)
    
    # Check if executables exist
    required_files = ["./build/bin/gendata", "./build/bin/psi_server", "./build/bin/psi_client"]
    for file in required_files:
        if not os.path.exists(file):
            log(f"Error: {file} not found. Please build the project first.", "ERROR")
            return False
    
    # Load existing progress and results
    progress = load_progress()
    results = load_results()
    
    # Generate all test combinations
    try:
        from fhe_test_config import get_test_combinations
        test_combinations = get_test_combinations()
    except ImportError:
        # Fallback to manual generation if config is not available
        test_combinations = []
        test_id = 1
        
        # Test 1: Key generation and transmission (Table 1)
        for seed_size in SEED_SIZE_VALUES:
            for set_size in SET_SIZE_VALUES:
                for network_mode in NETWORK_MODES:
                    test_combinations.append({
                        "test_id": test_id,
                        "type": "key_gen",
                        "seed_size": seed_size,
                        "set_size": set_size,
                        "prg_dd": DEFAULT_PRG_DD,  # Default value for key gen tests
                        "network_mode": network_mode
                    })
                    test_id += 1
        
        # Test 2: Client computation (Line chart)
        for set_size in SET_SIZE_VALUES:
            for prg_dd in PRG_DD_VALUES:
                test_combinations.append({
                    "test_id": test_id,
                    "type": "client_compute",
                    "seed_size": DEFAULT_SEED_SIZE,  # Default value for client compute tests
                    "set_size": set_size,
                    "prg_dd": prg_dd,
                    "network_mode": "lan"  # Default network mode
                })
                test_id += 1
        
        # Test 3: Server recovery (Table 3)
        for seed_size in SEED_SIZE_VALUES:
            for set_size in SET_SIZE_VALUES:
                for network_mode in NETWORK_MODES:
                    test_combinations.append({
                        "test_id": test_id,
                        "type": "server_recover",
                        "seed_size": seed_size,
                        "set_size": set_size,
                        "prg_dd": DEFAULT_PRG_DD,  # Default value for server recovery tests
                        "network_mode": network_mode
                    })
                    test_id += 1
    
    # Run tests
    completed_count = 0
    total_count = len(test_combinations)
    
    for test_config in test_combinations:
        test_id = test_config["test_id"]
        
        # Check if already completed
        if test_id in progress["completed_tests"]:
            log(f"Test {test_id} already completed, skipping...")
            completed_count += 1
            continue
        
        # Update progress
        progress["current_test"] = test_config
        save_progress(progress)
        
        log(f"Running test {test_id}/{total_count}: {test_config['type']}")
        
        # Run test
        result = run_single_test_point(
            test_config["seed_size"],
            test_config["set_size"],
            test_config["prg_dd"],
            test_config["network_mode"],
            test_id,
            test_config["type"]
        )
        
        if result:
            # Save result
            results[f"test_{test_id}"] = result
            save_results(results)
            
            # Update progress
            progress["completed_tests"].append(test_id)
            progress["current_test"] = None
            save_progress(progress)
            
            completed_count += 1
            log(f"✓ Test {test_id} completed successfully ({completed_count}/{total_count})")
        else:
            log(f"✗ Test {test_id} failed", "ERROR")
    
    log(f"Batch tests completed: {completed_count}/{total_count} tests passed")
    return completed_count == total_count

# ==================== PLOTTING FUNCTIONS ====================

def create_key_gen_table(results):
    """Create table for key generation and transmission times"""
    log("Creating key generation table...")
    
    # Extract key generation test results
    key_gen_results = []
    for test_key, test_data in results.items():
        if test_data.get("type") == "key_gen" or "key_gen" in test_key:
            key_gen_results.append(test_data)
    
    if not key_gen_results:
        log("No key generation results found", "WARNING")
        return
    
    # Create DataFrame
    data = []
    for result in key_gen_results:
        if result["key_gen_times"]:
            # Calculate average timing
            avg_time = np.mean([t.get("server_1", 0) + t.get("server_2", 0) for t in result["key_gen_times"]])
            comm_size = np.mean(result["communication_sizes"]) if result["communication_sizes"] else 0
            
            data.append({
                "seed_size": result["seed_size"],
                "set_size": result["set_size"],
                "network_mode": result["network_mode"],
                "time": avg_time,
                "communication_size": comm_size
            })
    
    if not data:
        log("No valid key generation data found", "WARNING")
        return
    
    df = pd.DataFrame(data)
    
    # 导出的表格，行是 seedsize setsize lantime wantime communication
    df.to_csv(os.path.join(PLOTS_DIR, "key_generation_table.csv"), float_format='%.3f')


    # Create pivot table
    # pivot_time = df.pivot_table(
    #     values="time", 
    #     index="seed_size", 
    #     columns=["set_size", "network_mode"], 
    #     aggfunc="mean"
    # )
    
    # pivot_comm = df.pivot_table(
    #     values="communication_size", 
    #     index="seed_size", 
    #     columns=["set_size", "network_mode"], 
    #     aggfunc="mean"
    # )
    
    # # Save to csv
    # pivot_time.to_csv(os.path.join(PLOTS_DIR, "key_generation_time.csv"), float_format='%.3f')
    # pivot_comm.to_csv(os.path.join(PLOTS_DIR, "key_generation_communication.csv"), float_format='%.2f')
    
    log(f"✓ Key generation table saved to csv")
    
    print(df.to_string(float_format='%.3f'))
    # Also create simple text table
    # log("Key Generation Time Table (seconds):")
    # print(pivot_time.to_string(float_format='%.3f'))
    
    # log("Key Generation Communication Table (MB):")
    # print(pivot_comm.to_string(float_format='%.2f'))

def create_client_compute_chart(results):
    """Create line chart for client computation times"""
    log("Creating client computation chart...")
    
    # Extract client computation test results
    client_results = []
    for test_key, test_data in results.items():
        if test_data.get("type") == "client_compute" or "client_compute" in test_key:
            client_results.append(test_data)
    
    if not client_results:
        log("No client computation results found", "WARNING")
        return
    
    # Group by prg_dd
    data_by_d = {}
    for result in client_results:
        prg_dd = result["prg_dd"]
        if prg_dd not in data_by_d:
            data_by_d[prg_dd] = []
        
        if result["client_compute_times"]:
            avg_time = np.mean([t.get("client", 0) for t in result["client_compute_times"]])
            data_by_d[prg_dd].append({
                "set_size": result["set_size"],
                "time": avg_time
            })
    
    # Create plot
    plt.figure(figsize=(10, 6))
    
    colors = plt.cm.Set1(np.linspace(0, 1, len(data_by_d)))
    for i, (prg_dd, data) in enumerate(sorted(data_by_d.items())):
        if data:
            set_sizes = [d["set_size"] for d in data]
            times = [d["time"] for d in data]
            
            plt.plot(set_sizes, times, 'o-', label=f'd={prg_dd}', color=colors[i], linewidth=2, markersize=6)
    
    plt.xlabel('Set Size')
    plt.ylabel('Client Computation Time (seconds)')
    plt.title('Client Computation Time vs Set Size')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.xscale('log', base=2)
    plt.yscale('log')
    
    plt.tight_layout()
    plt.savefig(os.path.join(PLOTS_DIR, "client_computation_chart.png"), dpi=300, bbox_inches='tight')
    plt.close()
    
    log("✓ Client computation chart saved")

def create_server_recover_table(results):
    """Create table for server recovery times"""
    log("Creating server recovery table...")
    
    # Extract server recovery test results
    server_results = []
    for test_key, test_data in results.items():
        if test_data.get("type") == "server_recover" or "server_recover" in test_key:
            server_results.append(test_data)
    
    if not server_results:
        log("No server recovery results found", "WARNING")
        return
    
    # Create DataFrame
    data = []
    for result in server_results:
        if result["server_recover_times"]:
            # Calculate average timing
            avg_time = np.mean([t.get("server_1", 0) + t.get("server_2", 0) for t in result["server_recover_times"]])
            comm_size = np.mean(result["communication_sizes"]) if result["communication_sizes"] else 0
            
            data.append({
                "seed_size": result["seed_size"],
                "set_size": result["set_size"],
                "network_mode": result["network_mode"],
                "time": avg_time,
                "communication_size": comm_size
            })
    
    if not data:
        log("No valid server recovery data found", "WARNING")
        return
    
    df = pd.DataFrame(data)
    df.to_csv(os.path.join(PLOTS_DIR, "server_recovery_table.csv"), float_format='%.3f')
    
    # Create pivot table
    # pivot_time = df.pivot_table(
    #     values="time", 
    #     index="seed_size", 
    #     columns=["set_size", "network_mode"], 
    #     aggfunc="mean"
    # )
    
    # pivot_comm = df.pivot_table(
    #     values="communication_size", 
    #     index="seed_size", 
    #     columns=["set_size", "network_mode"], 
    #     aggfunc="mean"
    # )
    
    # # Save to csv
    # pivot_time.to_csv(os.path.join(PLOTS_DIR, "server_recovery_time.csv"), float_format='%.3f')
    # pivot_comm.to_csv(os.path.join(PLOTS_DIR, "server_recovery_communication.csv"), float_format='%.2f')
    
    log(f"✓ Server recovery table saved to csv")
    
    # Also create simple text table
    # log("Server Recovery Time Table (seconds):")
    print(df.to_string(float_format='%.3f'))
    
    # log("Server Recovery Communication Table (MB):")
    # print(df.to_string(float_format='%.2f'))

def create_all_plots():
    """Create all performance plots"""
    log("=" * 60)
    log("CREATING PERFORMANCE PLOTS")
    log("=" * 60)
    
    # Load results
    results = load_results()
    
    if not results:
        log("No results found. Please run tests first.", "ERROR")
        return False
    
    # Create plots
    create_key_gen_table(results)
    create_client_compute_chart(results)
    create_server_recover_table(results)
    
    log("✓ All plots created successfully")
    return True

# ==================== MAIN FUNCTION ====================

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="FHE Performance Batch Test")
    parser.add_argument("--plot-only", action="store_true", help="Only create plots from existing results")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    
    args = parser.parse_args()
    
    global VERBOSE
    if args.verbose:
        VERBOSE = True
    
    # Ensure directories exist
    ensure_directories()
    
    if args.plot_only:
        # Only create plots
        success = create_all_plots()
    else:
        # Run tests and create plots
        log("Starting FHE performance batch tests...")
        success = run_batch_tests()
        
        if success:
            log("Creating performance plots...")
            create_all_plots()
    
    if success:
        log("=" * 60)
        log("✓ FHE PERFORMANCE BATCH TESTS COMPLETED SUCCESSFULLY")
        log("=" * 60)
    else:
        log("=" * 60)
        log("✗ FHE PERFORMANCE BATCH TESTS FAILED")
        log("=" * 60)
    
    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
