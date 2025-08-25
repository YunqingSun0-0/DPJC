# FHE Performance Testing Guide

This guide provides comprehensive instructions for testing the performance of the FHE (Fully Homomorphic Encryption) PSI implementation.

## Overview

The FHE testing system consists of several components designed to thoroughly test and benchmark the FHE PSI implementation:

1. **Single Test Script** (`fhe_test_single.py`) - Quick functionality verification
2. **Batch Test Script** (`fhe_performance_test_batch.py`) - Comprehensive performance testing
3. **Configuration System** (`fhe_test_config.py`) - Centralized parameter management
4. **Runner Script** (`run_fhe_tests.sh`) - Easy-to-use command-line interface
5. **Documentation** - Complete usage instructions

## Quick Start

### 1. Prerequisites

Ensure you have the following installed:
- Python 3.6+ with numpy, matplotlib, pandas
- Built PSI project with FHE support
- Required executables: `gendata`, `psi_server`, `psi_client`

### 2. Verify Setup

```bash
# Check if executables exist
ls -la build/bin/gendata build/bin/psi_server build/bin/psi_client

# Check Python dependencies
python3 -c "import numpy, matplotlib, pandas; print('Dependencies OK')"

# View test configuration
python3 fhe_test_config.py
```

### 3. Run Tests

```bash
# Quick functionality test
./run_fhe_tests.sh single

# Full performance test suite
./run_fhe_tests.sh batch

# Generate plots from existing results
./run_fhe_tests.sh plots
```

## Test Structure

### Test Categories

The system performs three main types of tests:

#### 1. Key Generation and Transmission
- **Purpose**: Measure server key generation and transmission time
- **Parameters**: seed_size (64, 128), set_size (2^6 to 2^18), network_mode (lan, wan)
- **Output**: Heatmap table showing time and communication size

#### 2. Client Computation
- **Purpose**: Measure client computation time with different parameters
- **Parameters**: set_size (2^6 to 2^18), prg_dd (4-8)
- **Output**: Line chart showing computation time vs set size

#### 3. Server Recovery
- **Purpose**: Measure server recovery time after receiving client results
- **Parameters**: seed_size (64, 128), set_size (2^6 to 2^18), network_mode (lan, wan)
- **Output**: Heatmap table showing time and communication size

### Test Parameters

| Parameter | Values | Description |
|-----------|--------|-------------|
| Set Size | 2^6, 2^8, 2^10, 2^12, 2^14, 2^16, 2^18 | Number of elements in each set |
| Intersection Size | Set Size / 2 | Number of common elements |
| Seed Size | 64, 128 | Cryptographic seed size |
| PRG_DD | 4, 5, 6, 7, 8 | PRG parameter d |
| Network Mode | lan, wan | Network simulation mode |
| Runs per Point | 5 | Number of test runs for averaging |

## Configuration

### Customizing Test Parameters

Edit `fhe_test_config.py` to modify test parameters:

```python
# Example: Reduce test scope for faster testing
SET_SIZE_VALUES = [1 << 6, 1 << 8, 1 << 10]  # Fewer set sizes
NUM_RUNS_PER_POINT = 3  # Fewer runs per point
TIMEOUT_SECONDS = 180  # Shorter timeout
```

### Configuration Validation

The configuration system includes validation to ensure parameters are reasonable:

```bash
python3 fhe_test_config.py
```

This will show a summary and validate all parameters.

## Running Tests

### Single Test

For quick functionality verification:

```bash
./run_fhe_tests.sh single
```

This runs a small test (1000 elements) to verify FHE functionality works correctly.

### Batch Tests

For comprehensive performance testing:

```bash
# Standard batch test
./run_fhe_tests.sh batch

# Verbose output
./run_fhe_tests.sh batch --verbose
```

The batch test will:
- Run 91 test combinations (configurable)
- Each combination runs 5 times for averaging
- Save progress and results to files
- Generate performance charts

### Interruption and Recovery

The batch test supports interruption and recovery:

1. **Progress Tracking**: Automatically saves progress to `test_progress.json`
2. **Resume**: Restart the script to continue from where it left off
3. **Skip Completed**: Already completed tests are automatically skipped

### Plot Generation

Generate charts from existing results:

```bash
./run_fhe_tests.sh plots
```

## Output and Results

### Generated Files

```
fhe_performance_results/
├── test_progress.json          # Progress tracking
├── performance_results.json    # Test results
└── analysis_results.json       # Analysis data

fhe_performance_plots/
├── key_generation_table.png    # Table 1: Key generation performance
├── client_computation_chart.png # Chart 2: Client computation time
└── server_recovery_table.png   # Table 3: Server recovery performance
```

### Result Format

Each test result contains:

```json
{
  "test_id": 1,
  "seed_size": 64,
  "set_size": 262144,
  "prg_dd": 4,
  "network_mode": "lan",
  "runs": 5,
  "key_gen_times": [...],
  "client_compute_times": [...],
  "server_recover_times": [...],
  "communication_sizes": [...],
  "timestamp": "2024-01-01T12:00:00"
}
```

## Charts and Analysis

### 1. Key Generation Table
- **Format**: Heatmap with color intensity representing time
- **Rows**: Seed size (64, 128)
- **Columns**: Set size × Network mode combinations
- **Values**: Time (seconds) and communication size (MB)

### 2. Client Computation Chart
- **Format**: Line chart with logarithmic scales
- **X-axis**: Set size (log scale, base 2)
- **Y-axis**: Computation time (log scale)
- **Lines**: Different PRG_DD values (4-8) in different colors

### 3. Server Recovery Table
- **Format**: Heatmap similar to key generation table
- **Purpose**: Show server recovery performance after client computation

## Troubleshooting

### Common Issues

#### 1. Executables Not Found
```
Error: ./build/bin/psi_server not found. Please build the project first.
```
**Solution**: Build the project first
```bash
cd build && make -j$(nproc)
```

#### 2. Python Dependencies Missing
```
ModuleNotFoundError: No module named 'numpy'
```
**Solution**: Install required packages
```bash
pip install numpy matplotlib pandas
```

#### 3. Port Conflicts
```
Error: Process failed with return code 1
```
**Solution**: Kill existing processes
```bash
pkill -f psi_server
pkill -f psi_client
```

#### 4. Timeout Errors
```
Error: Some processes did not complete within timeout
```
**Solution**: Increase timeout in configuration
```python
TIMEOUT_SECONDS = 600  # 10 minutes
```

#### 5. Memory Issues
**Symptoms**: Processes killed by system
**Solutions**:
- Reduce set sizes in configuration
- Use smaller universal size
- Run fewer concurrent tests

### Debug Mode

Enable verbose output for debugging:

```bash
./run_fhe_tests.sh batch --verbose
```

### Manual Cleanup

If the script is interrupted and processes remain:

```bash
./run_fhe_tests.sh clean
```

Or manually:
```bash
pkill -f psi_server
pkill -f psi_client
rm -rf fhe_performance_data/
```

## Performance Considerations

### System Requirements

- **Memory**: Minimum 8GB, recommended 16GB
- **CPU**: Minimum 4 cores, recommended 8 cores
- **Disk**: At least 10GB free space
- **Network**: 100 Mbps minimum

### Test Duration

- **Single Test**: 2-5 minutes
- **Full Batch Test**: 4-8 hours (depending on system)
- **Individual Test Point**: 15-30 minutes

### Optimization Tips

1. **Reduce Test Scope**: Modify configuration for faster testing
2. **Use Smaller Sets**: Start with smaller set sizes
3. **Fewer Runs**: Reduce `NUM_RUNS_PER_POINT` for quicker results
4. **Parallel Testing**: Run multiple test instances on different machines

## Advanced Usage

### Custom Test Scripts

Create custom test scripts by importing the configuration:

```python
from fhe_test_config import *

# Use configuration parameters
print(f"Testing with set size: {SET_SIZE}")
```

### Extending the System

To add new test types:

1. Add configuration in `fhe_test_config.py`
2. Update test combination generation
3. Add plotting functions
4. Update the main test script

### Integration with CI/CD

The test system can be integrated into continuous integration:

```bash
# Example CI script
./run_fhe_tests.sh single
if [ $? -eq 0 ]; then
    echo "FHE functionality test passed"
else
    echo "FHE functionality test failed"
    exit 1
fi
```

## Expected Output Format

The FHE implementation should output timing information in this format:

```
Key generation time: 1.234s
Client computation time: 2.345s
Server recovery time: 0.567s
Communication size: 123.45 MB
Final PSI size: 131072
```

If the output format differs, modify the regex patterns in the `extract_timing_from_output` function.

## Support and Maintenance

### Log Files

Check log files for detailed information:
- Script output shows progress and errors
- Process output is captured for debugging
- Results are saved in JSON format

### Updating Tests

To update test parameters:
1. Modify `fhe_test_config.py`
2. Validate configuration: `python3 fhe_test_config.py`
3. Run tests: `./run_fhe_tests.sh batch`

### Reporting Issues

When reporting issues, include:
1. Configuration summary: `python3 fhe_test_config.py`
2. Error messages and logs
3. System specifications
4. Steps to reproduce

## Conclusion

This FHE testing system provides comprehensive performance evaluation of the FHE PSI implementation. The modular design allows for easy customization and extension, while the robust error handling and recovery mechanisms ensure reliable testing even for long-running batch tests.

For questions or issues, refer to the troubleshooting section or check the log files for detailed error information.






