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
NUM_CLIENTS_PER_SERVER = 4
PORT_BASE = 21000  # Base port for tests

# Parameter ranges
PRG_DD_VALUES = [4, 5, 6, 7, 8]
MOM_K_VALUES = [20, 40, 60, 80, 100]
MOM_T = 80  # Fixed mom_t value
NUM_RUNS_PER_POINT = 100  # Number of runs per parameter combination

# Test parameters
TIMEOUT_SECONDS = 30
VERBOSE = False

# Output directories
RESULTS_DIR = "./batch_test_results_08120040"
DATA_DIR = "./batch_test_data"
PLOTS_DIR = "./batch_test_plots_08120040"

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
    cmd = f"./build/bin/gendata --intersection_size={INTERSECTION_SIZE} " \
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

def run_single_test(test_id, prg_dd, mom_k, test_data_info):
    """Run a single PSI test"""
    test_data_dir = test_data_info['test_data_dir']
    expected_intersection_size = test_data_info['expected_intersection_size']
    server1_files = test_data_info['server1_files']
    server2_files = test_data_info['server2_files']
    
    # Calculate port for this test
    port = PORT_BASE + test_id % 1000  # Use modulo to avoid port conflicts
    
    # Start servers
    server1_cmd = f"./build/bin/psi_server -p 1 --port {port} --psi_mode naive --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={mom_k} --mom_t={MOM_T} --prg_dd={prg_dd}"
    server2_cmd = f"./build/bin/psi_server -p 2 --port {port} --psi_mode naive --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={mom_k} --mom_t={MOM_T} --prg_dd={prg_dd}"
    
    server1_process = start_process(server1_cmd, f"Server 1 (test {test_id})")
    server2_process = start_process(server2_cmd, f"Server 2 (test {test_id})")
    
    # Start all clients
    client_processes = []
    
    # Start Server 1 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        data_file = server1_files[i]
        client_cmd = f"./build/bin/psi_client -p {client_id} --port {port} --data_file={data_file} --psi_mode naive --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={mom_k} --mom_t={MOM_T} --prg_dd={prg_dd}"
        client_process = start_process(client_cmd, f"Client {client_id} (Server 1, test {test_id})")
        client_processes.append(client_process)
    
    # Start Server 2 clients
    for i in range(NUM_CLIENTS_PER_SERVER):
        client_id = i + 1
        data_file = server2_files[i]
        client_cmd = f"./build/bin/psi_client -p {client_id + NUM_CLIENTS_PER_SERVER} --port {port} --data_file={data_file} --psi_mode naive --num_clients_per_server={NUM_CLIENTS_PER_SERVER} --test_mode --mom_k={mom_k} --mom_t={MOM_T} --prg_dd={prg_dd}"
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
    error_ratio = actual_intersection_size / expected_intersection_size if expected_intersection_size > 0 else float('inf')
    
    return True, {
        'test_id': test_id,
        'prg_dd': prg_dd,
        'mom_k': mom_k,
        'expected_intersection_size': expected_intersection_size,
        'actual_intersection_size': actual_intersection_size,
        'error_ratio': error_ratio,
        'timestamp': datetime.now().isoformat()
    }

# ==================== BATCH TESTING ====================

def get_test_key(prg_dd, mom_k):
    """Get unique key for a parameter combination"""
    return f"prg_dd_{prg_dd}_mom_k_{mom_k}"

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
    
    # Load existing results
    results = load_existing_results()
    
    # Check if executables exist
    required_files = ["./build/bin/gendata", "./build/bin/psi_server", "./build/bin/psi_client"]
    for file in required_files:
        if not os.path.exists(file):
            log(f"Error: {file} not found. Please build the project first.", "ERROR")
            return False
    
    # Kill any existing PSI processes before starting
    kill_existing_processes()
    
    test_id = 0
    total_tests = len(PRG_DD_VALUES) * len(MOM_K_VALUES) * NUM_RUNS_PER_POINT
    completed_tests = 0
    
    for prg_dd in PRG_DD_VALUES:
        for mom_k in MOM_K_VALUES:
            test_key = get_test_key(prg_dd, mom_k)
            
            # Check if this parameter combination is already completed
            if test_key in results and len(results[test_key]) >= NUM_RUNS_PER_POINT:
                log(f"Skipping completed parameter combination: prg_dd={prg_dd}, mom_k={mom_k}")
                completed_tests += NUM_RUNS_PER_POINT
                continue
            
            # Initialize results for this parameter combination
            if test_key not in results:
                results[test_key] = []
            
            # Run tests for this parameter combination
            log(f"Testing prg_dd={prg_dd}, mom_k={mom_k} (completed: {len(results[test_key])}/{NUM_RUNS_PER_POINT})")
            
            for run in range(len(results[test_key]), NUM_RUNS_PER_POINT):
                test_id += 1
                completed_tests += 1
                
                log(f"Running test {test_id}/{total_tests} (run {run+1}/{NUM_RUNS_PER_POINT})")
                
                # Generate test data
                success, test_data_info = generate_test_data(test_id, prg_dd, mom_k)
                if not success:
                    log(f"Failed to generate test data for test {test_id}", "ERROR")
                    continue
                
                # Run the test
                success, test_result = run_single_test(test_id, prg_dd, mom_k, test_data_info)
                if success and test_result:
                    results[test_key].append(test_result)
                    save_results(results)  # Save after each successful test
                    log(f"Test {test_id} completed successfully. Error ratio: {test_result['error_ratio']:.3f}")
                else:
                    log(f"Test {test_id} failed", "ERROR")
                
                # Clean up test data
                if os.path.exists(test_data_info['test_data_dir']):
                    shutil.rmtree(test_data_info['test_data_dir'])
                
                # Progress update
                progress = (completed_tests / total_tests) * 100
                log(f"Progress: {progress:.1f}% ({completed_tests}/{total_tests})")
    
    log("Batch tests completed!")
    return True

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
        if len(test_results) < NUM_RUNS_PER_POINT:
            log(f"Warning: {test_key} has only {len(test_results)} results, expected {NUM_RUNS_PER_POINT}")
            continue
        
        # Extract parameters from test key
        parts = test_key.split('_')
        prg_dd = int(parts[2])
        mom_k = int(parts[5])
        
        # Extract error ratios
        error_ratios = [result['error_ratio'] for result in test_results]
        
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
        
        # Calculate statistics
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
            'prg_dd': prg_dd,
            'mom_k': mom_k,
            'mean_error': mean_error,
            'median_error': median_error,
            'std_error': std_error,
            'min_error': min_error,
            'max_error': max_error,
            'filtered_ratios': filtered_ratios,
            'total_runs': len(test_results),
            'filtered_runs': len(filtered_ratios)
        }
    
    # Save analysis results
    analysis_file = f"{RESULTS_DIR}/analysis_results.json"
    with open(analysis_file, 'w') as f:
        json.dump(analysis_results, f, indent=2)
    
    log(f"Analysis results saved to {analysis_file}")
    return analysis_results

# ==================== PLOTTING ====================

def create_plots(analysis_results):
    """Create plots from analysis results"""
    log("Creating plots...")
    
    if not analysis_results:
        log("No analysis results to plot", "ERROR")
        return False
    
    # Create figure
    plt.figure(figsize=(12, 8))
    
    # Colors for different mom_k values
    colors = ['red', 'blue', 'green', 'orange', 'purple']
    color_map = {mom_k: colors[i] for i, mom_k in enumerate(MOM_K_VALUES)}
    
    # Plot data for each mom_k
    for mom_k in MOM_K_VALUES:
        prg_dd_values = []
        mean_errors = []
        std_errors = []
        
        for test_key, result in analysis_results.items():
            if result['mom_k'] == mom_k:
                prg_dd_values.append(result['prg_dd'])
                mean_errors.append(result['mean_error'])
                std_errors.append(result['std_error'])
        
        if prg_dd_values:
            # Sort by prg_dd
            sorted_data = sorted(zip(prg_dd_values, mean_errors, std_errors))
            prg_dd_values, mean_errors, std_errors = zip(*sorted_data)
            
            # Plot with error bars
            plt.errorbar(prg_dd_values, mean_errors, yerr=std_errors, 
                        label=f'mom_k={mom_k}', color=color_map[mom_k], 
                        marker='o', capsize=5, capthick=2, linewidth=2, markersize=8)
    
    plt.xlabel('prg_dd', fontsize=14)
    plt.ylabel('Error Ratio (actual/expected intersection size)', fontsize=14)
    plt.title('PSI Error Analysis: Error Ratio vs prg_dd for Different mom_k Values', fontsize=16)
    plt.legend(fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.xticks(PRG_DD_VALUES)
    
    # Save plot
    plot_file = f"{PLOTS_DIR}/error_analysis.png"
    plt.tight_layout()
    plt.savefig(plot_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    log(f"Plot saved to {plot_file}")
    
    # Create box plot
    plt.figure(figsize=(15, 10))
    
    # Prepare data for box plot
    box_data = []
    box_labels = []
    
    for mom_k in MOM_K_VALUES:
        for prg_dd in PRG_DD_VALUES:
            test_key = get_test_key(prg_dd, mom_k)
            if test_key in analysis_results:
                filtered_ratios = analysis_results[test_key]['filtered_ratios']
                if len(filtered_ratios) > 0:  # Only add non-empty data
                    box_data.append(filtered_ratios)
                    box_labels.append(f'prg_dd={prg_dd}\nmom_k={mom_k}')
    
    if box_data:
        plt.boxplot(box_data, labels=box_labels)
        plt.xlabel('Parameter Combinations', fontsize=14)
        plt.ylabel('Error Ratio (actual/expected intersection size)', fontsize=14)
        plt.title('PSI Error Distribution: Box Plot of Error Ratios', fontsize=16)
        plt.xticks(rotation=45)
        plt.grid(True, alpha=0.3)
        
        # Save box plot
        box_plot_file = f"{PLOTS_DIR}/error_boxplot.png"
        plt.tight_layout()
        plt.savefig(box_plot_file, dpi=300, bbox_inches='tight')
        plt.close()
        
        log(f"Box plot saved to {box_plot_file}")
    else:
        log("No valid data for box plot", "WARNING")
    
    return True

# ==================== MAIN FUNCTIONS ====================

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Batch PSI testing with parameter scanning')
    parser.add_argument('--run-tests', action='store_true', help='Run the batch tests')
    parser.add_argument('--analyze', action='store_true', help='Analyze existing results')
    parser.add_argument('--plot', action='store_true', help='Create plots from analysis')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    
    args = parser.parse_args()
    
    global VERBOSE
    VERBOSE = args.verbose
    
    # Ensure directories exist
    ensure_directories()
    
    if args.run_tests:
        log("Starting batch tests...")
        success = run_batch_tests()
        if not success:
            log("Batch tests failed", "ERROR")
            return 1
    
    if args.analyze or args.plot:
        log("Analyzing results...")
        analysis_results = analyze_results()
        if not analysis_results:
            log("Analysis failed", "ERROR")
            return 1
    
    if args.plot:
        log("Creating plots...")
        success = create_plots(analysis_results)
        if not success:
            log("Plotting failed", "ERROR")
            return 1
    
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
        
        success = create_plots(analysis_results)
        if not success:
            log("Plotting failed", "ERROR")
            return 1
    
    log("Batch test pipeline completed successfully!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
