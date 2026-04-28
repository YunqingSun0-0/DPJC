#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/lyd8051/psi-code-test"
PYTHON_BIN="python"
SCRIPT="$ROOT/accuracy_test_batch_clear.py"
RESUME_SCRIPT="$ROOT/resume_maxsetsupport_unfinished.py"
RUN1_DIR="$ROOT/experiments/run_20260414_185720"
LOG_DIR="$ROOT/experiments"
TS="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/overnight_maxsetsupport_${TS}.log"

mkdir -p "$LOG_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "Overnight maxsetsupport job started"
log "Log file: $LOG_FILE"

# ----------------------------
# Task 1: Resume existing run
# ----------------------------
#log "Task 1: resume run_20260414_185720 with cap setexp<=20, target-cores=24"
"$PYTHON_BIN" "$RESUME_SCRIPT" \
  --run-dir "$RUN1_DIR" \
  --target-cores 32 \
  --skip-baseline-above 20 \
  --execute \
  --verbose 2>&1 | tee -a "$LOG_FILE"

log "Task 1 completed"

# -----------------------------------------------------
# Task 2: switch maxsetsupport mom_k_values to [10000]
# and run accuracy_test_batch_clear directly
# -----------------------------------------------------
log "Task 2: patch maxsetsupport mom_k_values -> [10000]"

"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import re

path = Path('/home/lyd8051/psi-code-test/accuracy_test_batch_clear.py')
text = path.read_text(encoding='utf-8')

pattern = re.compile(
    r'("maxsetsupport"\s*:\s*\{\s*"description"\s*:\s*"[^"]*",\s*"prg_dd_values"\s*:\s*\[[^\]]*\],\s*)"mom_k_values"\s*:\s*\[[^\]]*\]',
    re.S,
)

new_text, n = pattern.subn(r'\1"mom_k_values": [10000]', text, count=1)
if n != 1:
    raise SystemExit('Failed to patch mom_k_values for maxsetsupport mode')

path.write_text(new_text, encoding='utf-8')
print('Patched maxsetsupport mom_k_values to [10000]')
PY

log "Task 2: run maxsetsupport parallel (skip baseline>20), target-cores=24"
"$PYTHON_BIN" "$SCRIPT" \
  --param-mode maxsetsupport \
  --maxsetsupport-parallel \
  --target-cores 24 \
  --skip-baseline-above 20 \
  --verbose 2>&1 | tee -a "$LOG_FILE"

log "Task 2 completed"
log "All tasks finished successfully"
