# not usable

#!/usr/bin/env python3
"""
Test script for data generation validation
Tests if the generated data has the correct intersection size and distribution
"""

import os
import sys
import subprocess
import tempfile
import shutil
from pathlib import Path

# ==================== CONFIGURABLE PARAMETERS ====================
# Data generation parameters
UNIVERSAL_SIZE_BIT = 20
UNIVERSAL_SIZE = 1 << UNIVERSAL_SIZE_BIT
SET_SIZE = 65536
INTERSECTION_SIZE = 10000
NUM_CLIENTS_PER_SERVER = 24
OUTPUT_DIR = "./test_data"

# Test parameters
VERBOSE = True
CLEANUP_AFTER_TEST = True  

# ==================== HELPER FUNCTIONS ====================

def log(message, level="INFO"):
    """Print log message with timestamp"""
    if VERBOSE:
        print(f"[{level}] {message}")

def run_command(cmd, description=""):
    """Run a command and return success status"""
    log(f"Running: {description or cmd}")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
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

def validate_data_generation():
    """Test data generation correctness"""
    log("=" * 60)
    log("TESTING DATA GENERATION CORRECTNESS")
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
        return False
    
    # Read generated data
    log("Reading generated data files...")
    
    # Read all client files
    client_files = []
    for server_id in [1, 2]:
        for client_id in range(1, NUM_CLIENTS_PER_SERVER + 1):
            if server_id == 1:
                filename = f"{OUTPUT_DIR}/client{client_id}_1.txt"
            else:
                filename = f"{OUTPUT_DIR}/client{client_id}_2.txt"
            client_files.append((filename, server_id, client_id))
    
    # Read sets for each server
    server1_sets = []
    server2_sets = []
    
    for filename, server_id, client_id in client_files:
        if os.path.exists(filename):
            client_set = read_set_from_file(filename)
            log(f"Server {server_id} Client {client_id}: {len(client_set)} elements")
            
            if server_id == 1:
                server1_sets.append(client_set)
            else:
                server2_sets.append(client_set)
        else:
            log(f"Warning: File {filename} not found", "WARNING")
    
    # Combine sets for each server
    if server1_sets:
        server1_combined = set().union(*server1_sets)
        log(f"Server 1 combined set size: {len(server1_combined)}")
    else:
        server1_combined = set()
        log("Warning: No Server 1 sets found", "WARNING")
    
    if server2_sets:
        server2_combined = set().union(*server2_sets)
        log(f"Server 2 combined set size: {len(server2_combined)}")
    else:
        server2_combined = set()
        log("Warning: No Server 2 sets found", "WARNING")
    
    # Calculate actual intersection
    actual_intersection = calculate_intersection(server1_combined, server2_combined)
    actual_intersection_size = len(actual_intersection)
    
    log(f"Expected intersection size: {INTERSECTION_SIZE}")
    log(f"Actual intersection size: {actual_intersection_size}")
    
    # Validate results
    success = True
    
    # Check if intersection size is correct (allow small tolerance due to randomness)
    tolerance = max(1, INTERSECTION_SIZE // 20)  # 5% tolerance
    if abs(actual_intersection_size - INTERSECTION_SIZE) > tolerance:
        log(f"✗ Intersection size mismatch! Expected {INTERSECTION_SIZE}, got {actual_intersection_size}", "ERROR")
        success = False
    else:
        log(f"✓ Intersection size is correct (within tolerance)")
    
    # Check if set sizes are correct
    expected_set_size = SET_SIZE
    if abs(len(server1_combined) - expected_set_size) > tolerance:
        log(f"✗ Server 1 set size mismatch! Expected {expected_set_size}, got {len(server1_combined)}", "ERROR")
        success = False
    else:
        log(f"✓ Server 1 set size is correct")
    
    if abs(len(server2_combined) - expected_set_size) > tolerance:
        log(f"✗ Server 2 set size mismatch! Expected {expected_set_size}, got {len(server2_combined)}", "ERROR")
        success = False
    else:
        log(f"✓ Server 2 set size is correct")
    
    # Check if all elements are within universal set
    max_element = max(max(server1_combined) if server1_combined else 0, 
                     max(server2_combined) if server2_combined else 0)
    if max_element >= UNIVERSAL_SIZE:
        log(f"✗ Element {max_element} exceeds universal set size {UNIVERSAL_SIZE}", "ERROR")
        success = False
    else:
        log(f"✓ All elements are within universal set range [0, {UNIVERSAL_SIZE})")
    
    # Check distribution across clients
    if len(server1_sets) == NUM_CLIENTS_PER_SERVER:
        avg_size = sum(len(s) for s in server1_sets) / len(server1_sets)
        expected_avg = SET_SIZE / NUM_CLIENTS_PER_SERVER
        if abs(avg_size - expected_avg) > tolerance:
            log(f"✗ Server 1 client distribution uneven! Expected avg {expected_avg}, got {avg_size:.1f}", "ERROR")
            success = False
        else:
            log(f"✓ Server 1 client distribution is even")
    
    if len(server2_sets) == NUM_CLIENTS_PER_SERVER:
        avg_size = sum(len(s) for s in server2_sets) / len(server2_sets)
        expected_avg = SET_SIZE / NUM_CLIENTS_PER_SERVER
        if abs(avg_size - expected_avg) > tolerance:
            log(f"✗ Server 2 client distribution uneven! Expected avg {expected_avg}, got {avg_size:.1f}", "ERROR")
            success = False
        else:
            log(f"✓ Server 2 client distribution is even")
    
    # Cleanup
    if CLEANUP_AFTER_TEST and os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
        log("Cleaned up test data directory")
    
    return success

def main():
    """Main test function"""
    log("Starting data generation tests...")
    
    # Check if gendata executable exists
    if not os.path.exists("./build/bin/gendata"):
        log("Error: gendata executable not found. Please build the project first.", "ERROR")
        return False
    
    # Run tests
    success = validate_data_generation()
    
    if success:
        log("=" * 60)
        log("✓ ALL DATA GENERATION TESTS PASSED")
        log("=" * 60)
    else:
        log("=" * 60)
        log("✗ SOME DATA GENERATION TESTS FAILED")
        log("=" * 60)
    
    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 