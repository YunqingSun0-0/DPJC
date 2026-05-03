#!/bin/bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${REPO_DIR}/fhe_phase"

TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')
ROLE=""

SEED_BITS=(10)
SET_SIZE_BIT=5
PRG_DD=6
NUM_CLIENTS_PER_SERVER=1
UNIVERSAL_SIZE_BIT=24
PORT=22000

SERVER1_HOST="127.0.0.1" # change to other ip
SERVER2_HOST="127.0.0.1" # change to other ip
DATA_DIR="${REPO_DIR}/test_fhe_wan"
GENERATE_DATA=0
THROTTLE_INTERFACE="auto"
WAN_BANDWIDTH_MBIT="200"
WAN_LATENCY_MS=40
MAX_WEIGHT=0
WEIGHTED_MULTILIMB_EXACT=0
WEIGHTED_LIMB_BITS=16
WEIGHTED_CHUNK_K=0

FULL_LOG_FILE="${LOG_DIR}/wan_2pc_full_${ROLE:-unknown}_${TIMESTAMP}.log"
ANALYSIS_LOG_FILE="${LOG_DIR}/wan_2pc_analysis_${ROLE:-unknown}_${TIMESTAMP}.log"
WAN_THROTTLE_ACTIVE=0

usage() {
    cat <<EOF_USAGE
Usage:
  $0 --role server1 --server1-host <host> --server2-host <host> [options]
  $0 --role server2 --server1-host <host> --server2-host <host> [options]

Required:
  --role server1|server2

Options:
  --generate-data
  --data-dir <path>
  --port <int>
  --set-size-bit <int>
  --prg-dd <int>
  --num-clients-per-server <int>
  --universal-size-bit <int>
  --seed-bits "10 11 12"
  --throttle-interface <name|auto>
  --wan-bandwidth-mbit <mbit>
  --wan-latency-ms <ms>
  --max-weight <int>                Enable weighted mode when >0
  --weight-scale-div <int>          Deprecated and ignored (weight scaling disabled)
  --weighted-multilimb-exact        Enable limb-decomposed OLE recovery path
  --weighted-limb-bits <1..16>      Limb width (default 16)
  --weighted-chunk-k <int>          Weighted mode: split each tt bucket into chunks in recovery (0=disabled)
  --help

Notes:
  - 2PC metrics are printed by server1 in current binary output.
  - Run one instance on each host with matching args.
EOF_USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
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
        --max-weight)
            MAX_WEIGHT="$2"
            shift 2
            ;;
        --weight-scale-div)
            # Deprecated: accept for compatibility, but ignore.
            shift 2
            ;;
        --weighted-multilimb-exact)
            WEIGHTED_MULTILIMB_EXACT=1
            shift
            ;;
        --weighted-limb-bits)
            WEIGHTED_LIMB_BITS="$2"
            shift 2
            ;;
        --weighted-chunk-k)
            WEIGHTED_CHUNK_K="$2"
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

if [[ "${ROLE}" != "server1" && "${ROLE}" != "server2" ]]; then
    echo "--role server1|server2 is required" >&2
    usage
    exit 1
fi

if ! [[ "${MAX_WEIGHT}" =~ ^[0-9]+$ ]]; then
    echo "--max-weight must be a non-negative integer" >&2
    exit 1
fi
if ! [[ "${WEIGHTED_LIMB_BITS}" =~ ^[0-9]+$ ]] || [[ "${WEIGHTED_LIMB_BITS}" -lt 1 || "${WEIGHTED_LIMB_BITS}" -gt 16 ]]; then
    echo "--weighted-limb-bits must be in [1,16]" >&2
    exit 1
fi
if ! [[ "${WEIGHTED_CHUNK_K}" =~ ^[0-9]+$ ]]; then
    echo "--weighted-chunk-k must be an integer >= 0" >&2
    exit 1
fi

FULL_LOG_FILE="${LOG_DIR}/wan_2pc_full_${ROLE}_${TIMESTAMP}.log"
ANALYSIS_LOG_FILE="${LOG_DIR}/wan_2pc_analysis_${ROLE}_${TIMESTAMP}.log"

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
        log_both "Remove manually: python3 ${REPO_DIR}/throttle.py -i ${THROTTLE_INTERFACE} -d"
    fi
}

trap cleanup_wan_throttle EXIT

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
        log_both "Failed to apply WAN throttle"
        exit 1
    fi
}

role_id=2
role_suffix=2
if [[ "${ROLE}" == "server1" ]]; then
    role_id=1
    role_suffix=1
fi

log_both "=========================================================="
log_both "WAN 2PC Phase Test (${ROLE})"
log_both "=========================================================="
log_both "Seed sizes: ${SEED_BITS[*]}"
log_both "Set size: 2^${SET_SIZE_BIT} = $((2 ** SET_SIZE_BIT))"
log_both "PRG DD: ${PRG_DD}"
log_both "Port: ${PORT}"
log_both "Server 1 host: ${SERVER1_HOST}"
log_both "Server 2 host: ${SERVER2_HOST}"
log_both "Data dir: ${DATA_DIR}"
log_both "Max weight: ${MAX_WEIGHT} ($( [[ "${MAX_WEIGHT}" -gt 0 ]] && echo weighted || echo unweighted ))"
if [[ "${MAX_WEIGHT}" -gt 0 ]]; then
    log_both "Weighted flags: --weighted_mode$( [[ "${WEIGHTED_MULTILIMB_EXACT}" -eq 1 ]] && echo " --weighted_multilimb_exact --weighted_limb_bits=${WEIGHTED_LIMB_BITS}" )$( [[ "${WEIGHTED_CHUNK_K}" -gt 0 ]] && echo " --weighted_chunk_k=${WEIGHTED_CHUNK_K}" )"
fi
log_both "Logs:"
log_both "  - Full log: ${FULL_LOG_FILE}"
log_both "  - Analysis log: ${ANALYSIS_LOG_FILE}"
log_both "=========================================================="

log_analysis "=========================================================="
log_analysis "WAN 2PC Phase Analysis (${ROLE})"
log_analysis "=========================================================="
if [[ "${ROLE}" == "server1" ]]; then
    log_analysis "Format: Seed Size | Status | server_2pc_time(s) | 2PC communication(bytes) | 2PC communication(MB) | server_recovery_comm(MB)"
else
    log_analysis "Format: Seed Size | Status | note"
fi
log_analysis "=========================================================="

if [[ "${GENERATE_DATA}" -eq 1 ]]; then
    if [[ "${MAX_WEIGHT}" -gt 0 ]]; then
        log_both "Error: --generate-data in WAN mode is non-weighted in current gendata build."
        log_both "Prepare weighted client files in ${DATA_DIR} first, then rerun without --generate-data."
        exit 1
    fi
    generate_wan_data
fi

if [[ "${role_id}" -eq 1 && ! -f "${DATA_DIR}/client1_1.txt" ]]; then
    log_both "Server1 WAN data not found: ${DATA_DIR}"
    exit 1
fi
if [[ "${role_id}" -eq 2 && ! -f "${DATA_DIR}/client1_2.txt" ]]; then
    log_both "Server2 WAN data not found: ${DATA_DIR}"
    exit 1
fi

apply_wan_throttle

for seed_bits in "${SEED_BITS[@]}"; do
    seed_size=$((2 ** seed_bits))
    server_log="${LOG_DIR}/.wan2pc_${ROLE}_server_seed_${seed_bits}_${TIMESTAMP}.log"

    log_both ""
    log_both "--- [WAN ${ROLE}] Seed Size=2^${seed_bits} = ${seed_size} bits ---"

    cd "${REPO_DIR}"
    weighted_flags=()
    if [[ "${MAX_WEIGHT}" -gt 0 ]]; then
        weighted_flags+=(--weighted_mode)
        if [[ "${WEIGHTED_MULTILIMB_EXACT}" -eq 1 ]]; then
            weighted_flags+=(--weighted_multilimb_exact "--weighted_limb_bits=${WEIGHTED_LIMB_BITS}")
        fi
        if [[ "${WEIGHTED_CHUNK_K}" -gt 0 ]]; then
            weighted_flags+=("--weighted_chunk_k=${WEIGHTED_CHUNK_K}")
        fi
    fi

    ./bin/psi_server -p "${role_id}" \
        --port="${PORT}" \
        --psi_mode=fhe \
        --num_clients_per_server="${NUM_CLIENTS_PER_SERVER}" \
        --seed_size_bit="${seed_bits}" \
        --prg_dd="${PRG_DD}" \
        --server1_host="${SERVER1_HOST}" \
        --server2_host="${SERVER2_HOST}" \
        "${weighted_flags[@]}" \
        > "${server_log}" 2>&1 &
    server_pid=$!

    sleep 1

    client_pids=()
    for ((local_client_id=1; local_client_id<=NUM_CLIENTS_PER_SERVER; local_client_id++)); do
        if [[ "${role_id}" -eq 1 ]]; then
            global_client_id=${local_client_id}
        else
            global_client_id=$((NUM_CLIENTS_PER_SERVER + local_client_id))
        fi

        data_file="${DATA_DIR}/client${local_client_id}_${role_suffix}.txt"
        client_log="${LOG_DIR}/.wan2pc_${ROLE}_client_${global_client_id}_seed_${seed_bits}_${TIMESTAMP}.log"

        ./bin/psi_client -p "${global_client_id}" \
            --port="${PORT}" \
            --data_file="${data_file}" \
            --psi_mode=fhe \
            --num_clients_per_server="${NUM_CLIENTS_PER_SERVER}" \
            --seed_size_bit="${seed_bits}" \
            --prg_dd="${PRG_DD}" \
            --server1_host="${SERVER1_HOST}" \
            --server2_host="${SERVER2_HOST}" \
            "${weighted_flags[@]}" \
            > "${client_log}" 2>&1 &
        client_pids+=("$!")
    done

    status="OK"
    if ! wait "${server_pid}"; then
        status="SERVER_FAIL"
        sed -n '1,120p' "${server_log}" >> "${FULL_LOG_FILE}"
    fi

    for client_pid in "${client_pids[@]}"; do
        if ! wait "${client_pid}"; then
            if [[ "${status}" == "OK" ]]; then
                status="CLIENT_FAIL"
                sed -n '1,120p' "${server_log}" >> "${FULL_LOG_FILE}"
            fi
        fi
    done

    cat "${server_log}" >> "${FULL_LOG_FILE}"

    if [[ "${ROLE}" == "server1" ]]; then
        server_2pc_time=$(grep -oP 'server_2pc_time: \K[\d.]+' "${server_log}" | head -1 || true)
        mpc_comm_bytes=$(grep -oP '2PC communication: \K\d+' "${server_log}" | head -1 || true)
        mpc_comm_mb=$(grep -oP '2PC communication: \d+ bytes \(\K[\d.]+' "${server_log}" | head -1 || true)
        server_recovery_comm_mb=$(grep -oP 'Server recovery Communication: \K[\d.]+' "${server_log}" | head -1 || true)

        t_str="${server_2pc_time:-N/A}"
        b_str="${mpc_comm_bytes:-N/A}"
        m_str="${mpc_comm_mb:-N/A}"
        r_str="${server_recovery_comm_mb:-N/A}"

        if [[ "${status}" == "OK" ]]; then
            log_both "[WAN ${ROLE}] 2PC: time ${t_str}s, comm ${b_str} bytes (${m_str} MB), recovery_comm ${r_str} MB"
        else
            log_both "[WAN ${ROLE}] ${status}: partial 2PC metrics time ${t_str}s, comm ${b_str} bytes (${m_str} MB), recovery_comm ${r_str} MB"
        fi

        log_analysis "2^${seed_bits} (${seed_size}) | ${status} | ${t_str} | ${b_str} | ${m_str} | ${r_str}"
    else
        if [[ "${status}" == "OK" ]]; then
            log_both "[WAN ${ROLE}] completed"
            log_analysis "2^${seed_bits} (${seed_size}) | OK | 2PC metrics are emitted by server1"
        else
            log_both "[WAN ${ROLE}] ${status}"
            log_analysis "2^${seed_bits} (${seed_size}) | ${status} | 2PC metrics are emitted by server1"
        fi
    fi
done

log_both ""
log_both "=========================================================="
log_both "WAN 2PC phase suite completed"
log_both "=========================================================="

log_analysis ""
log_analysis "=========================================================="
log_analysis "WAN 2PC phase suite completed"
log_analysis "=========================================================="

echo ""
echo "Test completed"
echo "Full log: ${FULL_LOG_FILE}"
echo "Analysis log: ${ANALYSIS_LOG_FILE}"

# refresh-marker: 20260503T033330Z
