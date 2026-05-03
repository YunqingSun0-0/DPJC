#!/bin/bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${REPO_DIR}/fhe_phase"

TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')
MODE="local"
ROLE=""

# Default test parameters
SEED_BITS=(10)
SET_SIZE_BIT=5
PRG_DD=6
NUM_CLIENTS_PER_SERVER=1
UNIVERSAL_SIZE_BIT=24
PORT=22000

# WAN parameters
SERVER1_HOST="127.0.0.1"
SERVER2_HOST="127.0.0.1"
DATA_DIR="${REPO_DIR}/test_fhe_wan"
GENERATE_DATA=0
THROTTLE_INTERFACE="auto"
WAN_BANDWIDTH_MBIT="200"
WAN_LATENCY_MS=40

FULL_LOG_FILE="${LOG_DIR}/key_gen_full_${TIMESTAMP}.log"
ANALYSIS_LOG_FILE="${LOG_DIR}/key_gen_analysis_${TIMESTAMP}.log"
WAN_THROTTLE_ACTIVE=0

usage() {
    cat <<EOF
Usage:
  $0
  $0 --mode local
  $0 --mode wan --role server1 --server1-host <host> --server2-host <host> --generate-data
  $0 --mode wan --role server2 --server1-host <host> --server2-host <host> --generate-data

Modes:
  local
    Preserves the original single-machine seed test flow by calling fhe_test_single.py.

  wan
    Runs only the local server and its local clients for a two-instance deployment.
    Start one instance with --role server1 and another with --role server2.

Notes:
  - Key generation timing is printed only by Server 1, so the analysis log is meaningful on the server1 side.
  - In WAN mode, use --generate-data on both sides.
  - Start server1 with --generate-data first so it can generate Set 1 and Set 2 and wait for server2.
  - Then start server2 with --generate-data so it can receive Set 2 and write its local client files.
  - After WAN data generation finishes, the same command continues into the seed test on each side.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            MODE="$2"
            shift 2
            ;;
        --role)
            ROLE="$2"
            shift 2
            ;;
        --server1-host)
            SERVER1_HOST="$2"
            shift 2
            ;;
        --server2-host)
            SERVER2_HOST="$2"
            shift 2
            ;;
        --data-dir)
            DATA_DIR="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --set-size-bit)
            SET_SIZE_BIT="$2"
            shift 2
            ;;
        --prg-dd)
            PRG_DD="$2"
            shift 2
            ;;
        --num-clients-per-server)
            NUM_CLIENTS_PER_SERVER="$2"
            shift 2
            ;;
        --universal-size-bit)
            UNIVERSAL_SIZE_BIT="$2"
            shift 2
            ;;
        --seed-bits)
            read -r -a SEED_BITS <<< "$2"
            shift 2
            ;;
        --throttle-interface)
            THROTTLE_INTERFACE="$2"
            shift 2
            ;;
        --wan-bandwidth-mbit)
            WAN_BANDWIDTH_MBIT="$2"
            shift 2
            ;;
        --wan-latency-ms)
            WAN_LATENCY_MS="$2"
            shift 2
            ;;
        --generate-data)
            GENERATE_DATA=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage
            exit 1
            ;;
    esac
done

mkdir -p "${LOG_DIR}"
> "${FULL_LOG_FILE}"
> "${ANALYSIS_LOG_FILE}"

log_full() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] $*" >> "${FULL_LOG_FILE}"
}

log_both() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] $*" | tee -a "${FULL_LOG_FILE}"
}

log_analysis() {
    echo "$*" >> "${ANALYSIS_LOG_FILE}"
}

cleanup_wan_throttle() {
    if [[ "${WAN_THROTTLE_ACTIVE}" -eq 1 ]]; then
        log_both "WAN throttle is still active on ${THROTTLE_INTERFACE}"
        log_both "Remove it manually with: python3 ${REPO_DIR}/throttle.py -i ${THROTTLE_INTERFACE} -d"
    fi
}

trap cleanup_wan_throttle EXIT

run_local_seed_suite() {
    log_both "=========================================================="
    log_both "FHE Seed Bits Test (Local Mode)"
    log_both "=========================================================="
    log_both "Seed sizes: ${SEED_BITS[*]}"
    log_both "Set size: 2^${SET_SIZE_BIT} = $((2 ** SET_SIZE_BIT))"
    log_both "PRG DD: ${PRG_DD}"
    log_both "Logs:"
    log_both "  - Full log: ${FULL_LOG_FILE}"
    log_both "  - Analysis log: ${ANALYSIS_LOG_FILE}"
    log_both "=========================================================="

    log_analysis "=========================================================="
    log_analysis "FHE Seed Bits Analysis (Local Mode)"
    log_analysis "=========================================================="
    log_analysis "Format: Seed Size (2^exp = bits) | Status | Key Gen Server Time (s) | key_gen_comm (MB) | broadcast_comm (MB)"
    log_analysis "=========================================================="

    for seed_bits in "${SEED_BITS[@]}"; do
        local seed_size
        local temp_log
        local key_gen_time
        local key_gen_comm
        local broadcast_comm
        local test_status
        local kgt_str
        local kgc_str
        local bcc_str

        seed_size=$((2 ** seed_bits))
        temp_log="${LOG_DIR}/.temp_seed_test_local_${seed_bits}.log"

        log_both ""
        log_both "--- [LOCAL] Testing with Seed Size=2^${seed_bits} = ${seed_size} bits ---"

        local test_status="OK"
        if ! { cd "${REPO_DIR}" && python3 fhe_test_single.py \
            --set_size_bit "${SET_SIZE_BIT}" \
            --prg_dd "${PRG_DD}" \
            --seed_size_bit "${seed_bits}" \
            --output_log "${temp_log}" \
            >> "${FULL_LOG_FILE}" 2>&1; }; then
            test_status="FAIL"
        fi

        # Extract whatever metrics landed in the log, even if the test failed mid-run.
        key_gen_time=""
        key_gen_comm=""
        broadcast_comm=""
        if [[ -f "${temp_log}" ]]; then
            key_gen_time=$(grep -oP 'key_gen_server_time_1: \K[\d.]+' "${temp_log}" | head -1 || true)
            key_gen_comm=$(grep -oP 'key_gen_comm_mb: \K[\d.]+' "${temp_log}" | head -1 || true)
            broadcast_comm=$(grep -oP 'broadcast_comm_mb: \K[\d.]+' "${temp_log}" | head -1 || true)
        fi

        local kgt_str="${key_gen_time:-N/A}"
        local kgc_str="${key_gen_comm:-N/A}"
        local bcc_str="${broadcast_comm:-N/A}"

        if [[ "${test_status}" == "OK" ]]; then
            log_both "✓ [LOCAL] Seed 2^${seed_bits} (${seed_size} bits): Key Gen Server ${kgt_str}s, key_gen_comm ${kgc_str} MB, broadcast_comm ${bcc_str} MB"
        else
            log_both "✗ [LOCAL] Seed 2^${seed_bits} (${seed_size} bits) FAILED — partial metrics: Key Gen Server ${kgt_str}s, key_gen_comm ${kgc_str} MB, broadcast_comm ${bcc_str} MB"
        fi
        log_analysis "2^${seed_bits} (${seed_size}): ${test_status} | ${kgt_str}s | ${kgc_str} MB | ${bcc_str} MB"

        rm -f "${temp_log}"
    done
}

generate_wan_data() {
    local set_size
    local intersection_size
    local role_id

    set_size=$((2 ** SET_SIZE_BIT))
    intersection_size=$((set_size / 2))

    mkdir -p "${DATA_DIR}"
    if [[ "${ROLE}" == "server1" ]]; then
        role_id=1
    else
        role_id=2
    fi

    log_both "Generating WAN test data in ${DATA_DIR} for ${ROLE}"

    cd "${REPO_DIR}"
    ./bin/gendata \
        --network_mode=wan \
        -p "${role_id}" \
        --intersection_size="${intersection_size}" \
        --universal_size_bit="${UNIVERSAL_SIZE_BIT}" \
        --num_clients_per_server="${NUM_CLIENTS_PER_SERVER}" \
        --set_size="${set_size}" \
        --port="${PORT}" \
        --server1_host="${SERVER1_HOST}" \
        --output_dir="${DATA_DIR}" \
        >> "${FULL_LOG_FILE}" 2>&1
}

apply_wan_throttle() {
    local throttle_cmd

    throttle_cmd=(python3 throttle.py -i "${THROTTLE_INTERFACE}")
    if [[ -n "${WAN_BANDWIDTH_MBIT}" ]]; then
        throttle_cmd+=(-b "${WAN_BANDWIDTH_MBIT}")
    fi
    if [[ -n "${WAN_LATENCY_MS}" ]]; then
        throttle_cmd+=(-l "${WAN_LATENCY_MS}")
    fi

    log_both "Applying WAN throttle on ${THROTTLE_INTERFACE} (bandwidth=${WAN_BANDWIDTH_MBIT:-unchanged} mbit, latency=${WAN_LATENCY_MS} ms)"
    if cd "${REPO_DIR}" && "${throttle_cmd[@]}" >> "${FULL_LOG_FILE}" 2>&1; then
        WAN_THROTTLE_ACTIVE=1
    else
        log_both "✗ Failed to apply WAN throttle"
        exit 1
    fi
}

run_wan_seed_for_role() {
    local role_id
    local role_suffix
    local seed_bits

    if [[ "${ROLE}" != "server1" && "${ROLE}" != "server2" ]]; then
        echo "--mode wan requires --role server1 or --role server2" >&2
        exit 1
    fi

    if [[ "${ROLE}" == "server1" ]]; then
        role_id=1
        role_suffix=1
    else
        role_id=2
        role_suffix=2
    fi

    log_both "=========================================================="
    log_both "FHE Seed Bits Test (WAN Mode ${ROLE})"
    log_both "=========================================================="
    log_both "Seed sizes: ${SEED_BITS[*]}"
    log_both "Set size: 2^${SET_SIZE_BIT} = $((2 ** SET_SIZE_BIT))"
    log_both "PRG DD: ${PRG_DD}"
    log_both "Port: ${PORT}"
    log_both "Server 1 host: ${SERVER1_HOST}"
    log_both "Server 2 host: ${SERVER2_HOST}"
    log_both "Data dir: ${DATA_DIR}"
    log_both "Role: ${ROLE}"
    log_both "Logs:"
    log_both "  - Full log: ${FULL_LOG_FILE}"
    log_both "  - Analysis log: ${ANALYSIS_LOG_FILE}"
    log_both "=========================================================="

    log_analysis "=========================================================="
    log_analysis "FHE Seed Bits Analysis (WAN Mode ${ROLE})"
    log_analysis "=========================================================="
    if [[ "${ROLE}" == "server1" ]]; then
        log_analysis "Format: Seed Size (2^exp = bits) | Status | Key Gen Server Time (s) | key_gen_comm (MB) | broadcast_comm (MB)"
    else
        log_analysis "Format: Seed Size (2^exp = bits) | Status | server2 note"
    fi
    log_analysis "=========================================================="

    if [[ "${GENERATE_DATA}" -eq 1 ]]; then
        generate_wan_data
    fi

    if [[ "${role_id}" -eq 1 && ! -f "${DATA_DIR}/client1_1.txt" ]]; then
        log_both "✗ Server 1 WAN data files not found in ${DATA_DIR}"
        log_both "  Run server1 with --generate-data to generate and send WAN data."
        exit 1
    fi

    if [[ "${role_id}" -eq 2 && ! -f "${DATA_DIR}/client1_2.txt" ]]; then
        log_both "✗ Server 2 WAN data files not found in ${DATA_DIR}"
        log_both "  Run server2 with --generate-data so it can receive Set 2 from server1."
        exit 1
    fi

    apply_wan_throttle

    for seed_bits in "${SEED_BITS[@]}"; do
        local seed_size
        local server_log
        local client_log
        local server_pid
        local client_pids=()
        local local_client_id
        local global_client_id
        local data_file
        local key_gen_time
        local key_gen_comm
        local broadcast_comm
        local wan_status
        local kgt_str
        local kgc_str
        local bcc_str

        seed_size=$((2 ** seed_bits))
        server_log="${LOG_DIR}/.wan_${ROLE}_server_seed_${seed_bits}_${TIMESTAMP}.log"

        log_both ""
        log_both "--- [WAN ${ROLE}] Testing with Seed Size=2^${seed_bits} = ${seed_size} bits ---"

        cd "${REPO_DIR}"
        ./bin/psi_server -p "${role_id}" \
            --port="${PORT}" \
            --psi_mode=fhe \
            --num_clients_per_server="${NUM_CLIENTS_PER_SERVER}" \
            --seed_size_bit="${seed_bits}" \
            --prg_dd="${PRG_DD}" \
            --server1_host="${SERVER1_HOST}" \
            --server2_host="${SERVER2_HOST}" \
            > "${server_log}" 2>&1 &
        server_pid=$!

        sleep 1

        for ((local_client_id=1; local_client_id<=NUM_CLIENTS_PER_SERVER; local_client_id++)); do
            if [[ "${role_id}" -eq 1 ]]; then
                global_client_id=${local_client_id}
            else
                global_client_id=$((NUM_CLIENTS_PER_SERVER + local_client_id))
            fi

            data_file="${DATA_DIR}/client${local_client_id}_${role_suffix}.txt"
            client_log="${LOG_DIR}/.wan_${ROLE}_client_${global_client_id}_seed_${seed_bits}_${TIMESTAMP}.log"

            ./bin/psi_client -p "${global_client_id}" \
                --port="${PORT}" \
                --data_file="${data_file}" \
                --psi_mode=fhe \
                --num_clients_per_server="${NUM_CLIENTS_PER_SERVER}" \
                --seed_size_bit="${seed_bits}" \
                --prg_dd="${PRG_DD}" \
                --server1_host="${SERVER1_HOST}" \
                --server2_host="${SERVER2_HOST}" \
                > "${client_log}" 2>&1 &
            client_pids+=($!)
        done

        local wan_status="OK"
        if ! wait "${server_pid}"; then
            wan_status="SERVER_FAIL"
            sed -n '1,120p' "${server_log}" >> "${FULL_LOG_FILE}"
        fi

        for client_pid in "${client_pids[@]}"; do
            if ! wait "${client_pid}"; then
                if [[ "${wan_status}" == "OK" ]]; then
                    wan_status="CLIENT_FAIL"
                    sed -n '1,120p' "${server_log}" >> "${FULL_LOG_FILE}"
                fi
            fi
        done

        cat "${server_log}" >> "${FULL_LOG_FILE}"

        if [[ "${ROLE}" == "server1" ]]; then
            # Extract whatever metrics landed in the server log, even on failure.
            key_gen_time=$(grep -oP 'Key generation time: \K[\d.]+' "${server_log}" | head -1 || true)
            key_gen_comm=$(grep -oP 'key_gen_comm_mb: \K[\d.]+' "${server_log}" | head -1 || true)
            broadcast_comm=$(grep -oP 'broadcast_comm_mb: \K[\d.]+' "${server_log}" | head -1 || true)

            local kgt_str="${key_gen_time:-N/A}"
            local kgc_str="${key_gen_comm:-N/A}"
            local bcc_str="${broadcast_comm:-N/A}"

            if [[ "${wan_status}" == "OK" ]]; then
                log_both "✓ [WAN ${ROLE}] Seed 2^${seed_bits} (${seed_size} bits): Key Gen Server ${kgt_str}s, key_gen_comm ${kgc_str} MB, broadcast_comm ${bcc_str} MB"
            else
                log_both "✗ [WAN ${ROLE}] Seed 2^${seed_bits} (${seed_size} bits) ${wan_status} — partial metrics: Key Gen Server ${kgt_str}s, key_gen_comm ${kgc_str} MB, broadcast_comm ${bcc_str} MB"
            fi
            log_analysis "2^${seed_bits} (${seed_size}): ${wan_status} | ${kgt_str}s | ${kgc_str} MB | ${bcc_str} MB"
        else
            if [[ "${wan_status}" == "OK" ]]; then
                log_both "✓ [WAN ${ROLE}] Seed 2^${seed_bits} (${seed_size} bits): Completed"
                log_analysis "2^${seed_bits} (${seed_size}): OK | completed on server2"
            else
                log_both "✗ [WAN ${ROLE}] Seed 2^${seed_bits} (${seed_size} bits) ${wan_status}"
                log_analysis "2^${seed_bits} (${seed_size}): ${wan_status} | server2"
            fi
        fi
    done
}

case "${MODE}" in
    local)
        run_local_seed_suite
        ;;
    wan)
        run_wan_seed_for_role
        ;;
    *)
        echo "Unsupported mode: ${MODE}" >&2
        usage
        exit 1
        ;;
esac

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

# refresh-marker: 20260503T033330Z
