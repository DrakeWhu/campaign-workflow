#!/usr/bin/env bash
#SBATCH --job-name=cw_iteration_array
#SBATCH --partition=T6H
#SBATCH --nodes=1
#SBATCH --ntasks=24
#SBATCH --ntasks-per-node=24
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH --array=0-0
#SBATCH --output=loop_logs/%x_%A_%a.out
#SBATCH --error=loop_logs/%x_%A_%a.err

set -Eeuo pipefail
trap 'echo "[MORBO-ARRAY] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR
set -x

: "${CW_OPTIMIZATION_ROOT:?missing CW_OPTIMIZATION_ROOT}"
: "${CW_ITERATION:?missing CW_ITERATION}"
: "${CW_WORKFLOW_ROOT:?missing CW_WORKFLOW_ROOT}"
: "${CW_WORKFLOW_ENV:?missing CW_WORKFLOW_ENV}"
: "${SLURM_ARRAY_TASK_ID:?missing SLURM_ARRAY_TASK_ID}"

OPTIMIZATION_ROOT="$(cd "${CW_OPTIMIZATION_ROOT}" && pwd -P)"
WORKFLOW_ROOT="$(cd "${CW_WORKFLOW_ROOT}" && pwd -P)"
WORKFLOW_ENV="${CW_WORKFLOW_ENV}"
ITER_PADDED="$(printf '%03d' "${CW_ITERATION}")"
CAMPAIGN_ROOT="${OPTIMIZATION_ROOT}/iterations/iter_${ITER_PADDED}"
CASE_CYCLE_SCRIPT="${WORKFLOW_ROOT}/examples/sunrise/submit_case_cycle_array.sh"

if [[ -f "${OPTIMIZATION_ROOT}/STOP_OPTIMIZATION" ]]; then
    echo "[MORBO-ARRAY] STOP_OPTIMIZATION exists; skipping iteration ${CW_ITERATION} task ${SLURM_ARRAY_TASK_ID}."
    exit 0
fi

if [[ -f "${OPTIMIZATION_ROOT}/optimization_state.json" ]]; then
    STATE_STATUS="$(OPTIMIZATION_ROOT="${OPTIMIZATION_ROOT}" python - <<'PY'
import json
import os
from pathlib import Path
p = Path(os.environ['OPTIMIZATION_ROOT']) / 'optimization_state.json'
try:
    print(json.loads(p.read_text(encoding='utf-8')).get('status', ''))
except Exception:
    print('')
PY
)"
    if [[ "${STATE_STATUS}" == "stopped" ]]; then
        echo "[MORBO-ARRAY] optimization_state.json status=stopped; skipping iteration ${CW_ITERATION} task ${SLURM_ARRAY_TASK_ID}."
        exit 0
    fi
fi

if [[ ! -d "${CAMPAIGN_ROOT}" ]]; then
    echo "[MORBO-ARRAY] ERROR: campaign root is not materialized: ${CAMPAIGN_ROOT}" >&2
    exit 1
fi
if [[ ! -f "${CAMPAIGN_ROOT}/campaign.json" || ! -f "${CAMPAIGN_ROOT}/cases.tsv" ]]; then
    echo "[MORBO-ARRAY] ERROR: campaign.json/cases.tsv missing in ${CAMPAIGN_ROOT}" >&2
    exit 1
fi
if [[ ! -f "${WORKFLOW_ENV}" ]]; then
    echo "[MORBO-ARRAY] ERROR: missing workflow env: ${WORKFLOW_ENV}" >&2
    exit 1
fi
if [[ ! -f "${CASE_CYCLE_SCRIPT}" ]]; then
    echo "[MORBO-ARRAY] ERROR: missing case-cycle script: ${CASE_CYCLE_SCRIPT}" >&2
    exit 1
fi

set +e
CASE_ROW="$(
    awk -v task_id="${SLURM_ARRAY_TASK_ID}" '
        BEGIN { FS = "\t"; case_id_col = 0; case_name_col = 0; found = 0 }
        NR == 1 {
            for (i = 1; i <= NF; i++) {
                if ($i == "CASE_ID") case_id_col = i
                if ($i == "CASE_NAME") case_name_col = i
            }
            if (case_id_col == 0 || case_name_col == 0) exit 3
            next
        }
        ($case_id_col + 0) == (task_id + 0) {
            print $case_id_col "\t" $case_name_col
            found = 1
            exit 0
        }
        END { if (case_id_col == 0 || case_name_col == 0 || found == 0) exit 4 }
    ' "${CAMPAIGN_ROOT}/cases.tsv"
)"
CASE_ROW_RC=$?
set -e

if [[ "${CASE_ROW_RC}" -ne 0 || -z "${CASE_ROW}" ]]; then
    echo "[MORBO-ARRAY] No case for SLURM_ARRAY_TASK_ID=${SLURM_ARRAY_TASK_ID}; skipping as no-op."
    exit 0
fi

export CAMPAIGN_ROOT
export WORKFLOW_ROOT
export WORKFLOW_ENV
if [[ -n "${CW_CASE_RUNNER:-}" ]]; then
    CASE_RUNNER="$(readlink -f "${CW_CASE_RUNNER}")"
    if [[ ! -x "${CASE_RUNNER}" ]]; then
        echo "[MORBO-ARRAY] ERROR: missing/non-executable CW_CASE_RUNNER: ${CASE_RUNNER}" >&2
        exit 1
    fi
    export CASE_RUNNER
fi

cd "${CAMPAIGN_ROOT}"
exec bash "${CASE_CYCLE_SCRIPT}"
