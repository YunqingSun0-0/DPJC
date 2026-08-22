#!/bin/bash

# FHE Client Computation Time Test Script (Multi-thread)
# Same sweep as fhe_run_client_compute_tests.sh, but enables multi-thread
# client compute via --client_threads.
#
# Peer (Server-2) clients default to 1 thread so the measured Server-1 client
# is not bandwidth-contended by a second N-thread worker. Single-thread tests
# (fhe_run_client_compute_tests.sh / default fhe_test_single.py) are unchanged.
#
# Usage:
#   ./fhe_run_client_compute_multithread_tests.sh [RUNS] [THREADS] [PEER_THREADS]
#
# Examples:
#   ./fhe_run_client_compute_multithread_tests.sh           # 1 run, 4 threads, peer=1
#   ./fhe_run_client_compute_multithread_tests.sh 5 32      # 5 runs, 32 threads, peer=1
#   ./fhe_run_client_compute_multithread_tests.sh 1 32 32   # dual heavy peers (old behavior)
#
# Creates two logs under fhe_phase/:
#   1. client compute mt (<timestamp>).log
#   2. client compute mt (<timestamp>)_analysis.log

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${REPO_DIR}/fhe_phase"

TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')
FULL_LOG_FILE="${LOG_DIR}/client compute mt (${TIMESTAMP}).log"
ANALYSIS_LOG_FILE="${LOG_DIR}/client compute mt (${TIMESTAMP})_analysis.log"

# Test parameters
CLIENT_SIZES_BITS=(12)  # 2^0=1, 2^4=16, 2^8=256; add 12 for 4096 if needed
PRG_DD_VALUES=(8)
RUNS_TO_AVERAGE="${1:-1}"
CLIENT_THREADS="${2:-4}"
# Default peer to 1 thread: protocol still needs Server-2 client, but it
# should not burn N threads and steal DRAM bandwidth from the measured side.
PEER_CLIENT_THREADS="${3:-1}"

if ! [[ "${RUNS_TO_AVERAGE}" =~ ^[0-9]+$ ]] || [ "${RUNS_TO_AVERAGE}" -lt 1 ]; then
    echo "Error: RUNS_TO_AVERAGE must be a positive integer (got '${RUNS_TO_AVERAGE}')"
    exit 1
fi

if ! [[ "${CLIENT_THREADS}" =~ ^[0-9]+$ ]] || [ "${CLIENT_THREADS}" -lt 1 ]; then
    echo "Error: CLIENT_THREADS must be a positive integer (got '${CLIENT_THREADS}')"
    exit 1
fi

if ! [[ "${PEER_CLIENT_THREADS}" =~ ^[0-9]+$ ]] || [ "${PEER_CLIENT_THREADS}" -lt 1 ]; then
    echo "Error: PEER_CLIENT_THREADS must be a positive integer (got '${PEER_CLIENT_THREADS}')"
    exit 1
fi

mkdir -p "${LOG_DIR}"

> "${FULL_LOG_FILE}"
> "${ANALYSIS_LOG_FILE}"

log_full() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] $*" >> "${FULL_LOG_FILE}"
}

log_both() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] $*" | tee -a "${FULL_LOG_FILE}"
}

log_analysis() {
    echo "$*" >> "${ANALYSIS_LOG_FILE}"
}

log_both "=========================================================="
log_both "FHE Client Compute Time Test (Multi-thread)"
log_both "=========================================================="
log_both "Client sizes bits: ${CLIENT_SIZES_BITS[*]}"
log_both "PRG DD values: ${PRG_DD_VALUES[*]}"
log_both "Measured client_threads: ${CLIENT_THREADS}"
log_both "Peer (Server-2) client_threads: ${PEER_CLIENT_THREADS}"
log_both "Runs to average per test: ${RUNS_TO_AVERAGE}"
log_both "Logs:"
log_both "  - Full log: ${FULL_LOG_FILE}"
log_both "  - Analysis log: ${ANALYSIS_LOG_FILE}"
log_both "=========================================================="

log_analysis "=========================================================="
log_analysis "FHE Client Compute Time Analysis (Multi-thread)"
log_analysis "=========================================================="
log_analysis "client_threads=${CLIENT_THREADS}  peer_client_threads=${PEER_CLIENT_THREADS}"
log_analysis "Format: client_size (2^N) | PRG_DD | Avg Client Compute Time (s, N runs) | Avg Client Compute Time (ms/element)"
log_analysis "=========================================================="

for prg_dd in "${PRG_DD_VALUES[@]}"; do
    log_both ""
    log_both "--- Testing with PRG_DD=${prg_dd}, client_threads=${CLIENT_THREADS}, peer_threads=${PEER_CLIENT_THREADS} ---"
    log_analysis ""
    log_analysis "PRG_DD=${prg_dd} (threads=${CLIENT_THREADS}, peer=${PEER_CLIENT_THREADS}):"

    for size_bit in "${CLIENT_SIZES_BITS[@]}"; do
        client_size=$((2 ** size_bit))

        log_both "Running test: client_size=2^${size_bit}=${client_size}, prg_dd=${prg_dd}, threads=${CLIENT_THREADS}, peer=${PEER_CLIENT_THREADS}, runs=${RUNS_TO_AVERAGE}"

        sum_client_compute_time=0
        timed_runs=0
        missing_time_runs=0
        protocol_fail_runs=0

        for run_idx in $(seq 1 "${RUNS_TO_AVERAGE}"); do
            temp_log="${LOG_DIR}/.temp_test_mt_${size_bit}_${prg_dd}_${run_idx}.log"
            log_full "  Run ${run_idx}/${RUNS_TO_AVERAGE}: starting"

            # Microbench cares about client compute time, not protocol pass/fail.
            # Always try to scrape timing even when fhe_test_single.py exits non-zero.
            set +e
            cd "${REPO_DIR}" && python3 fhe_test_single.py \
                --set_size_bit "${size_bit}" \
                --prg_dd "${prg_dd}" \
                --client_threads "${CLIENT_THREADS}" \
                --peer_client_threads "${PEER_CLIENT_THREADS}" \
                --output_log "${temp_log}" \
                >> "${FULL_LOG_FILE}" 2>&1
            test_rc=$?
            set -e

            if [ "${test_rc}" -ne 0 ]; then
                protocol_fail_runs=$((protocol_fail_runs + 1))
            fi

            client_compute_time=""
            if [ -f "${temp_log}" ]; then
                # Prefer Server-1 client timing (first Client computation time in log)
                client_compute_time=$(grep -oP 'Client computation time: \K[\d.]+(?=s)' "${temp_log}" | head -1 || true)
            fi

            if [ -n "${client_compute_time}" ]; then
                sum_client_compute_time=$(awk -v s="${sum_client_compute_time}" -v t="${client_compute_time}" 'BEGIN { printf "%.12f", s + t }')
                timed_runs=$((timed_runs + 1))
                if [ "${test_rc}" -eq 0 ]; then
                    log_full "  Run ${run_idx}/${RUNS_TO_AVERAGE}: client_compute_time=${client_compute_time}s"
                else
                    log_full "  Run ${run_idx}/${RUNS_TO_AVERAGE}: client_compute_time=${client_compute_time}s (protocol exit=${test_rc}, timing kept)"
                fi
            else
                missing_time_runs=$((missing_time_runs + 1))
                log_full "  Run ${run_idx}/${RUNS_TO_AVERAGE}: missing client compute time (protocol exit=${test_rc})"
            fi

            rm -f "${temp_log}"
        done

        if [ "${timed_runs}" -gt 0 ]; then
            avg_client_compute_time=$(awk -v s="${sum_client_compute_time}" -v n="${timed_runs}" 'BEGIN { printf "%.6f", s / n }')
            avg_client_compute_ms_per_element=$(awk -v t="${avg_client_compute_time}" -v n="${client_size}" 'BEGIN { printf "%.6f", (t * 1000.0) / n }')
            log_both "✓ Timing ok - 2^${size_bit}: avg=${avg_client_compute_time}s (timed_runs=${timed_runs}/${RUNS_TO_AVERAGE}, protocol_fail=${protocol_fail_runs}, missing_time=${missing_time_runs})"
            log_analysis "  2^${size_bit} (size=${client_size}): avg=${avg_client_compute_time}s over ${timed_runs}/${RUNS_TO_AVERAGE} timed runs (${avg_client_compute_ms_per_element} ms/element); protocol_fail=${protocol_fail_runs}"
        else
            log_both "✗ No timing for 2^${size_bit}: missing_time=${missing_time_runs}, protocol_fail=${protocol_fail_runs}"
            log_analysis "  2^${size_bit} (size=${client_size}): NO TIMING (0/${RUNS_TO_AVERAGE} timed runs; protocol_fail=${protocol_fail_runs})"
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
echo "✓ Multi-thread client compute test completed!"
echo "Full log: ${FULL_LOG_FILE}"
echo "Analysis log: ${ANALYSIS_LOG_FILE}"
