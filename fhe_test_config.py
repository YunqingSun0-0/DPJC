#!/usr/bin/env python3
"""
FHE Test Configuration
This file contains all configurable parameters for FHE performance testing
"""

# ==================== TEST PARAMETERS ====================

# Fixed parameters
SET_SIZE = 1 << 12  # 2^18 = 262,144 elements
INTERSECTION_SIZE = SET_SIZE // 2  # Half of set size
UNIVERSAL_SIZE_BIT = 24
NUM_CLIENTS_PER_SERVER = 4
NUM_RUNS_PER_POINT = 1  # Number of runs per parameter combination
PORT_BASE = 22000  # Base port for tests

# Parameter ranges for different test types
SEED_SIZE_VALUES = [6, 7]  # seed_size values
SET_SIZE_VALUES = [1 << 8, 1 << 10, 1 << 12, 1 << 14]  # set sizes for client computation
# SET_SIZE_VALUES = [1 << 6, 1 << 8, 1 << 10, 1 << 12, 1 << 14, 1 << 16, 1 << 18]  # set sizes for client computation
PRG_DD_VALUES = [4, 5, 6, 7, 8]  # d values for client computation
NETWORK_MODES = ['lan', 'wan']  # Network simulation modes

# Test parameters
TIMEOUT_SECONDS = 300  # 5 minutes timeout for FHE operations
VERBOSE = True
CLEANUP_AFTER_TEST = True

# Output directories
RESULTS_DIR = "./fhe_performance_results"
DATA_DIR = "./fhe_performance_data"
PLOTS_DIR = "./fhe_performance_plots"

# ==================== FHE SPECIFIC PARAMETERS ====================

# Default FHE parameters
DEFAULT_SEED_SIZE = 6
DEFAULT_PRG_DD = 6
DEFAULT_NETWORK_MODE = "lan"

# FHE security parameters (if needed)
FHE_SECURITY_LEVEL = 128  # Security level in bits
FHE_POLY_MODULUS_DEGREE = 8192  # Polynomial modulus degree
FHE_PLAIN_MODULUS_BITS = 20  # Plain modulus bits

# ==================== PERFORMANCE TEST CONFIGURATIONS ====================

# Test 1: Key Generation and Transmission
KEY_GEN_CONFIG = {
    "name": "Key Generation and Transmission",
    "description": "Test server key generation and transmission to client",
    "parameters": {
        "seed_size": SEED_SIZE_VALUES,
        "set_size": [SET_SIZE],
        "network_mode": NETWORK_MODES,
        "prg_dd": [DEFAULT_PRG_DD],  # Fixed value for key gen tests
    },
    "output_format": "table",
    "metrics": ["time", "communication_size"]
}

# Test 2: Client Computation
CLIENT_COMPUTE_CONFIG = {
    "name": "Client Computation",
    "description": "Test client computation time with different parameters",
    "parameters": {
        "set_size": SET_SIZE_VALUES,
        "prg_dd": PRG_DD_VALUES,
        "seed_size": [DEFAULT_SEED_SIZE],  # Fixed value for client compute tests
        "network_mode": ["lan"],  # Fixed network mode
    },
    "output_format": "line_chart",
    "metrics": ["time"]
}

# Test 3: Server Recovery
SERVER_RECOVER_CONFIG = {
    "name": "Server Recovery",
    "description": "Test server recovery time after receiving client results",
    "parameters": {
        "seed_size": SEED_SIZE_VALUES,
        "set_size": [SET_SIZE],
        "network_mode": NETWORK_MODES,
        "prg_dd": [6],  # Fixed value for server recovery tests
    },
    "output_format": "table",
    "metrics": ["time", "communication_size"]
}

# ==================== PLOTTING CONFIGURATIONS ====================

# Plot configurations
PLOT_CONFIGS = {
    "key_generation_table": {
        "title": "Key Generation and Transmission Performance",
        "xlabel": "Set Size × Network Mode",
        "ylabel": "Seed Size",
        "color_map": "YlOrRd",
        "aspect": "auto",
        "figsize": (12, 10)
    },
    "client_computation_chart": {
        "title": "Client Computation Time vs Set Size",
        "xlabel": "Set Size",
        "ylabel": "Computation Time (seconds)",
        "xscale": "log",
        "yscale": "log",
        "base": 2,
        "figsize": (10, 6),
        "grid": True
    },
    "server_recovery_table": {
        "title": "Server Recovery Performance",
        "xlabel": "Set Size × Network Mode",
        "ylabel": "Seed Size",
        "color_map": "YlOrRd",
        "aspect": "auto",
        "figsize": (12, 10)
    }
}

# ==================== NETWORK SIMULATION ====================

# Network simulation parameters
NETWORK_CONFIGS = {
    "lan": {
        "name": "Local Area Network",
        "bandwidth": "1 Gbps",
        "latency": "1 ms",
        "description": "High-speed local network"
    },
    "wan": {
        "name": "Wide Area Network",
        "bandwidth": "100 Mbps",
        "latency": "50 ms",
        "description": "Slower wide area network"
    }
}

# ==================== VALIDATION PARAMETERS ====================

# Result validation parameters
VALIDATION_CONFIG = {
    "intersection_tolerance": 0.1,  # 10% tolerance for intersection size
    "min_successful_runs": 3,  # Minimum successful runs required
    "timeout_multiplier": 1.5,  # Timeout multiplier for slow systems
}

# ==================== DEBUG AND LOGGING ====================

# Debug and logging parameters
DEBUG_CONFIG = {
    "save_intermediate_results": True,
    "log_level": "INFO",  # DEBUG, INFO, WARNING, ERROR
    "save_process_output": True,
    "max_output_length": 1000,  # Maximum output length to save
}

# ==================== SYSTEM REQUIREMENTS ====================

# System requirements and recommendations
SYSTEM_REQUIREMENTS = {
    "min_memory_gb": 8,
    "recommended_memory_gb": 16,
    "min_cpu_cores": 4,
    "recommended_cpu_cores": 8,
    "disk_space_gb": 10,
    "network_bandwidth": "100 Mbps",
}

# ==================== HELPER FUNCTIONS ====================

def get_test_combinations():
    """Get all test combinations for batch testing"""
    combinations = []
    test_id = 1
    
    # Test 1: Key generation
    for seed_size in KEY_GEN_CONFIG["parameters"]["seed_size"]:
        for set_size in KEY_GEN_CONFIG["parameters"]["set_size"]:
            for network_mode in KEY_GEN_CONFIG["parameters"]["network_mode"]:
                combinations.append({
                    "test_id": test_id,
                    "type": "key_gen",
                    "seed_size": seed_size,
                    "set_size": set_size,
                    "prg_dd": KEY_GEN_CONFIG["parameters"]["prg_dd"][0],
                    "network_mode": network_mode
                })
                test_id += 1
    
    # Test 2: Client computation
    for set_size in CLIENT_COMPUTE_CONFIG["parameters"]["set_size"]:
        for prg_dd in CLIENT_COMPUTE_CONFIG["parameters"]["prg_dd"]:
            combinations.append({
                "test_id": test_id,
                "type": "client_compute",
                "seed_size": CLIENT_COMPUTE_CONFIG["parameters"]["seed_size"][0],
                "set_size": set_size,
                "prg_dd": prg_dd,
                "network_mode": CLIENT_COMPUTE_CONFIG["parameters"]["network_mode"][0]
            })
            test_id += 1
    
    # Test 3: Server recovery
    for seed_size in SERVER_RECOVER_CONFIG["parameters"]["seed_size"]:
        for set_size in SERVER_RECOVER_CONFIG["parameters"]["set_size"]:
            for network_mode in SERVER_RECOVER_CONFIG["parameters"]["network_mode"]:
                combinations.append({
                    "test_id": test_id,
                    "type": "server_recover",
                    "seed_size": seed_size,
                    "set_size": set_size,
                    "prg_dd": SERVER_RECOVER_CONFIG["parameters"]["prg_dd"][0],
                    "network_mode": network_mode
                })
                test_id += 1
    
    return combinations

def validate_config():
    """Validate configuration parameters"""
    errors = []
    
    # Check parameter ranges
    if SET_SIZE <= 0:
        errors.append("SET_SIZE must be positive")
    
    if INTERSECTION_SIZE <= 0 or INTERSECTION_SIZE >= SET_SIZE:
        errors.append("INTERSECTION_SIZE must be between 0 and SET_SIZE")
    
    if NUM_RUNS_PER_POINT <= 0:
        errors.append("NUM_RUNS_PER_POINT must be positive")
    
    if TIMEOUT_SECONDS <= 0:
        errors.append("TIMEOUT_SECONDS must be positive")
    
    # Check parameter lists
    if not SEED_SIZE_VALUES:
        errors.append("SEED_SIZE_VALUES cannot be empty")
    
    if not SET_SIZE_VALUES:
        errors.append("SET_SIZE_VALUES cannot be empty")
    
    if not PRG_DD_VALUES:
        errors.append("PRG_DD_VALUES cannot be empty")
    
    if not NETWORK_MODES:
        errors.append("NETWORK_MODES cannot be empty")
    
    return errors

def print_config_summary():
    """Print a summary of the current configuration"""
    print("=" * 60)
    print("FHE TEST CONFIGURATION SUMMARY")
    print("=" * 60)
    print(f"Fixed Parameters:")
    print(f"  Set Size: {SET_SIZE:,} elements")
    print(f"  Intersection Size: {INTERSECTION_SIZE:,} elements")
    print(f"  Universal Size: 2^{UNIVERSAL_SIZE_BIT}")
    print(f"  Clients per Server: {NUM_CLIENTS_PER_SERVER}")
    print(f"  Runs per Point: {NUM_RUNS_PER_POINT}")
    print(f"  Timeout: {TIMEOUT_SECONDS} seconds")
    print()
    
    print(f"Variable Parameters:")
    print(f"  Seed Sizes: {SEED_SIZE_VALUES}")
    print(f"  Set Sizes: {[f'2^{i.bit_length()-1}' for i in SET_SIZE_VALUES]}")
    print(f"  PRG_DD Values: {PRG_DD_VALUES}")
    print(f"  Network Modes: {NETWORK_MODES}")
    print()
    
    combinations = get_test_combinations()
    print(f"Total Test Combinations: {len(combinations)}")
    print()
    
    print(f"Output Directories:")
    print(f"  Results: {RESULTS_DIR}")
    print(f"  Data: {DATA_DIR}")
    print(f"  Plots: {PLOTS_DIR}")
    print("=" * 60)

if __name__ == "__main__":
    # Validate configuration
    errors = validate_config()
    if errors:
        print("Configuration errors:")
        for error in errors:
            print(f"  - {error}")
        exit(1)
    
    # Print configuration summary
    print_config_summary()






