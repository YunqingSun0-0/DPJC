#!/bin/bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${REPO_DIR}/fhe_phase"
TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')

# Usage: ./fhe_run_agg_time.sh [CLIENTS_PER_SIDE]
#   $1 (optional)     per-server client count (default 100)
#   SET_SIZE_BIT env  total set size = 2^SET_SIZE_BIT, distributed round-robin across clients.
#                     If unset, auto-picks the smallest value such that 2^SET_SIZE_BIT >= CLIENTS_PER_SIDE
#                     (so every client gets at least 1 element; gendata with empty input segfaults psi_client).
#   PRG_DD env        PRG d parameter (default 6)
#   SEED_BITS env     space-separated list of seed bits (default 6)
CLIENTS_PER_SIDE="${1:-100}"
PRG_DD="${PRG_DD:-6}"
read -r -a SEED_BITS <<< "${SEED_BITS:-6}"

# Auto-pick SET_SIZE_BIT = ceil(log2(CLIENTS_PER_SIDE)) when not provided.
if [[ -z "${SET_SIZE_BIT:-}" ]]; then
  SET_SIZE_BIT=0
  v=1
  while (( v < CLIENTS_PER_SIDE )); do
    SET_SIZE_BIT=$((SET_SIZE_BIT + 1))
    v=$((v * 2))
  done
  echo "[INFO] Auto-set SET_SIZE_BIT=${SET_SIZE_BIT} (set_size=${v}) from CLIENTS_PER_SIDE=${CLIENTS_PER_SIDE}"
fi

set_size=$((1 << SET_SIZE_BIT))
if (( set_size < CLIENTS_PER_SIDE )); then
  echo "[ERROR] SET_SIZE_BIT=${SET_SIZE_BIT} (set_size=${set_size}) is too small for CLIENTS_PER_SIDE=${CLIENTS_PER_SIDE}." >&2
  echo "[ERROR] Need 2^SET_SIZE_BIT >= CLIENTS_PER_SIDE; otherwise some clients get empty input and psi_client segfaults." >&2
  exit 1
fi

FULL_LOG="${LOG_DIR}/agg_time_${CLIENTS_PER_SIDE}cps_full_${TIMESTAMP}.log"
SUMMARY_LOG="${LOG_DIR}/agg_time_${CLIENTS_PER_SIDE}cps_summary_${TIMESTAMP}.log"

mkdir -p "${LOG_DIR}"
: > "${FULL_LOG}"
: > "${SUMMARY_LOG}"

echo "[INFO] Full log: ${FULL_LOG}" | tee -a "${SUMMARY_LOG}"
echo "[INFO] Summary log: ${SUMMARY_LOG}" | tee -a "${SUMMARY_LOG}"
echo "[INFO] Config: clients_per_side=${CLIENTS_PER_SIDE}, total_clients=$((2 * CLIENTS_PER_SIDE)), set_size=2^${SET_SIZE_BIT}, prg_dd=${PRG_DD}" | tee -a "${SUMMARY_LOG}"
echo "[INFO] Seed bits: ${SEED_BITS[*]}" | tee -a "${SUMMARY_LOG}"
echo | tee -a "${SUMMARY_LOG}"

cd "${REPO_DIR}"

for seed_bit in "${SEED_BITS[@]}"; do
  RUN_LOG="${LOG_DIR}/.temp_agg_${CLIENTS_PER_SIDE}cps_seed_${seed_bit}_${TIMESTAMP}.log"
  echo "[RUN] seed_size=2^${seed_bit}" | tee -a "${SUMMARY_LOG}"

  if python3 fhe_test_single.py \
      --set_size_bit "${SET_SIZE_BIT}" \
      --prg_dd "${PRG_DD}" \
      --seed_size_bit "${seed_bit}" \
      --num_clients_per_server "${CLIENTS_PER_SIDE}" \
      --output_log "${RUN_LOG}" \
      >> "${FULL_LOG}" 2>&1; then

    # Extract aggregation times from the new RESULT format: aggregation_time_ms_N: X.XXX ms
    agg_time_1=$(grep -oP 'aggregation_time_ms_1:\s*\K[\d.]+' "${RUN_LOG}" | head -1 | xargs)
    agg_time_2=$(grep -oP 'aggregation_time_ms_2:\s*\K[\d.]+' "${RUN_LOG}" | head -1 | xargs)

    if [[ -n "${agg_time_1}" ]] && [[ -n "${agg_time_2}" ]]; then
      total_agg_ms=$(echo "${agg_time_1} + ${agg_time_2}" | bc)
      echo "[OK] seed=2^${seed_bit} aggregation_time: server1=${agg_time_1}ms, server2=${agg_time_2}ms, total=${total_agg_ms}ms" | tee -a "${SUMMARY_LOG}"
    else
      echo "[WARN] seed=2^${seed_bit} aggregation times not found in output (s1=${agg_time_1:-N/A}, s2=${agg_time_2:-N/A})" | tee -a "${SUMMARY_LOG}"
    fi
  else
    echo "[FAIL] seed=2^${seed_bit} test failed" | tee -a "${SUMMARY_LOG}"
  fi

  rm -f "${RUN_LOG}"
  echo | tee -a "${SUMMARY_LOG}"
done

echo "[DONE] Finished. See summary: ${SUMMARY_LOG}" | tee -a "${SUMMARY_LOG}"
