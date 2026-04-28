#!/bin/bash

# FHE Client Computation Time Test Script
# Tests different client sizes and prg_dd values.
# Each (size, prg_dd) cell runs RUNS_TO_AVERAGE times; the reported value is the mean.
# Pass the run count as the first positional arg (default 1).
#
# Examples:
#   ./fhe_run_client_compute_tests.sh         # 1 run per cell (single data point)
#   ./fhe_run_client_compute_tests.sh 20      # 20 runs per cell, averaged
#
# Creates two logs:
#   1. client compute (<timestamp>).log          - Full log with every run
#   2. client compute (<timestamp>)_analysis.log - Per-cell averages only

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${REPO_DIR}/fhe_phase"

# Generate timestamp for log filename
TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')
FULL_LOG_FILE="${LOG_DIR}/client compute (${TIMESTAMP}).log"
ANALYSIS_LOG_FILE="${LOG_DIR}/client compute (${TIMESTAMP})_analysis.log"

# Test parameters
CLIENT_SIZES_BITS=(0)  # 2^0=1, 2^4=16, 2^8=256, 2^12=4096
PRG_DD_VALUES=(4 5 6 7 8)
RUNS_TO_AVERAGE="${1:-1}"

if ! [[ "${RUNS_TO_AVERAGE}" =~ ^[0-9]+$ ]] || [ "${RUNS_TO_AVERAGE}" -lt 1 ]; then
    echo "Error: RUNS_TO_AVERAGE must be a positive integer (got '${RUNS_TO_AVERAGE}')"
    exit 1
fi

# Create log directory
mkdir -p "${LOG_DIR}"

# Clear previous logs
> "${FULL_LOG_FILE}"
> "${ANALYSIS_LOG_FILE}"

# Helper function to log to full log only
log_full() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] $*" >> "${FULL_LOG_FILE}"
}

# Helper function to log to both logs
log_both() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] $*" | tee -a "${FULL_LOG_FILE}"
}

# Helper function to log analysis results
log_analysis() {
    echo "$*" >> "${ANALYSIS_LOG_FILE}"
}

log_both "=========================================================="
log_both "FHE Client Compute Time Test"
log_both "=========================================================="
log_both "Client sizes: 1, 16, 256, 4096"
log_both "PRG DD values: 4, 5, 6, 7, 8"
log_both "Runs to average per test: ${RUNS_TO_AVERAGE}"
log_both "Logs:"
log_both "  - Full log: ${FULL_LOG_FILE}"
log_both "  - Analysis log: ${ANALYSIS_LOG_FILE}"
log_both "=========================================================="

# Write analysis log header
log_analysis "=========================================================="
log_analysis "FHE Client Compute Time Analysis"
log_analysis "=========================================================="
log_analysis "Format: client_size (2^N) | PRG_DD | Avg Client Compute Time (s, N runs) | Avg Client Compute Time (ms/element)"
log_analysis "=========================================================="

# Main test loop
for prg_dd in "${PRG_DD_VALUES[@]}"; do
    log_both ""
    log_both "--- Testing with PRG_DD=${prg_dd} ---"
    log_analysis ""
    log_analysis "PRG_DD=${prg_dd}:"
    
    for size_bit in "${CLIENT_SIZES_BITS[@]}"; do
        client_size=$((2 ** size_bit))
        
        log_both "Running test: client_size=2^${size_bit}=${client_size}, prg_dd=${prg_dd}, runs=${RUNS_TO_AVERAGE}"

        sum_client_compute_time=0
        successful_runs=0
        failed_runs=0

        for run_idx in $(seq 1 "${RUNS_TO_AVERAGE}"); do
            temp_log="${LOG_DIR}/.temp_test_${size_bit}_${prg_dd}_${run_idx}.log"
            log_full "  Run ${run_idx}/${RUNS_TO_AVERAGE}: starting"

            # Run the test and capture output to temp log
            if cd "${REPO_DIR}" && python3 fhe_test_single.py \
                --set_size_bit "${size_bit}" \
                --prg_dd "${prg_dd}" \
                --output_log "${temp_log}" \
                >> "${FULL_LOG_FILE}" 2>&1; then

                # Extract client compute time from the temp log
                if [ -f "${temp_log}" ]; then
                    client_compute_time=$(grep -oP 'Client computation time: \K[\d.]+(?=s)' "${temp_log}" | head -1)

                    if [ -n "${client_compute_time}" ]; then
                        sum_client_compute_time=$(awk -v s="${sum_client_compute_time}" -v t="${client_compute_time}" 'BEGIN { printf "%.12f", s + t }')
                        successful_runs=$((successful_runs + 1))
                        log_full "  Run ${run_idx}/${RUNS_TO_AVERAGE}: client_compute_time=${client_compute_time}s"
                    else
                        failed_runs=$((failed_runs + 1))
                        log_full "  Run ${run_idx}/${RUNS_TO_AVERAGE}: missing client compute time"
                    fi
                else
                    failed_runs=$((failed_runs + 1))
                    log_full "  Run ${run_idx}/${RUNS_TO_AVERAGE}: temp log not found"
                fi

                # Clean up temp log
                rm -f "${temp_log}"
            else
                failed_runs=$((failed_runs + 1))
                log_full "  Run ${run_idx}/${RUNS_TO_AVERAGE}: test command failed"
                rm -f "${temp_log}"
            fi
        done

        if [ "${successful_runs}" -gt 0 ]; then
            avg_client_compute_time=$(awk -v s="${sum_client_compute_time}" -v n="${successful_runs}" 'BEGIN { printf "%.6f", s / n }')
            avg_client_compute_ms_per_element=$(awk -v t="${avg_client_compute_time}" -v n="${client_size}" 'BEGIN { printf "%.6f", (t * 1000.0) / n }')
            log_both "✓ Test passed - 2^${size_bit}: avg=${avg_client_compute_time}s (successful_runs=${successful_runs}, failed_runs=${failed_runs})"
            log_analysis "  2^${size_bit} (size=${client_size}): avg=${avg_client_compute_time}s over ${successful_runs}/${RUNS_TO_AVERAGE} runs (${avg_client_compute_ms_per_element} ms/element)"
        else
            log_both "✗ Test failed for 2^${size_bit}: no successful runs (failed_runs=${failed_runs})"
            log_analysis "  2^${size_bit} (size=${client_size}): FAILED (0/${RUNS_TO_AVERAGE} successful runs)"
        fi
    done
done

log_both ""
log_both "=========================================================="
log_both "Test suite completed"
log_both "=========================================================="

log_analysis ""
log_analysis "=========================================================="
log_analysis "Test suite completed"
log_analysis "=========================================================="

echo ""
echo "✓ Test completed!"
echo "Full log: ${FULL_LOG_FILE}"
echo "Analysis log: ${ANALYSIS_LOG_FILE}"
