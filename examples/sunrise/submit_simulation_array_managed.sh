#!/usr/bin/env bash
#SBATCH --job-name=warpx_sim
#SBATCH --partition=T6H
#SBATCH --nodes=1
#SBATCH --ntasks=24
#SBATCH --ntasks-per-node=24
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH --array=0-0
#SBATCH --output=array_logs/%x_%A_%a.out
#SBATCH --error=array_logs/%x_%A_%a.err

set -Eeuo pipefail
trap 'echo "[SIM-ARRAY] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR
set -x

if [[ -z "${SLURM_ARRAY_TASK_ID:-}" ]]; then
    echo "[SIM-ARRAY] ERROR: SLURM_ARRAY_TASK_ID is not set." >&2
    exit 2
fi

CAMPAIGN_ROOT="${CAMPAIGN_ROOT:-${SLURM_SUBMIT_DIR}}"
WORKFLOW_ROOT="${WORKFLOW_ROOT:-${HOME}/apps/src/campaign-workflow}"
WORKFLOW_ENV="${WORKFLOW_ENV:-${HOME}/apps/env/campaign-workflow.sh}"
CASE_RUNNER="${CASE_RUNNER:-${WORKFLOW_ROOT}/examples/sunrise/run_warpx_case_sunrise.sh}"

cd "${CAMPAIGN_ROOT}"
mkdir -p array_logs

if [[ ! -f campaign.json || ! -f cases.tsv ]]; then
    echo "[SIM-ARRAY] ERROR: campaign.json/cases.tsv missing in ${CAMPAIGN_ROOT}" >&2
    exit 1
fi

ROW="$(awk -v id="${SLURM_ARRAY_TASK_ID}" 'BEGIN{FS="\t"} NR>1 && ($1+0)==(id+0) {print; exit}' cases.tsv)"
if [[ -z "${ROW}" ]]; then
    echo "[SIM-ARRAY] ERROR: no row found for SLURM_ARRAY_TASK_ID=${SLURM_ARRAY_TASK_ID}" >&2
    exit 1
fi

IFS=$'\t' read -r CASE_ID CASE_NAME _REST <<< "${ROW}"
CASE_DIR="${CAMPAIGN_ROOT}/${CASE_NAME}"

if [[ ! -d "${CASE_DIR}" ]]; then
    echo "[SIM-ARRAY] ERROR: missing case directory: ${CASE_DIR}" >&2
    exit 1
fi

if [[ ! -f "${WORKFLOW_ENV}" ]]; then
    echo "[SIM-ARRAY] ERROR: missing workflow env: ${WORKFLOW_ENV}" >&2
    exit 1
fi

if [[ ! -x "${CASE_RUNNER}" ]]; then
    echo "[SIM-ARRAY] ERROR: missing/non-executable case runner: ${CASE_RUNNER}" >&2
    exit 1
fi

CASE_LOG_PREFIX="logs/${SLURM_JOB_NAME}_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}_${CASE_ID}"
STDOUT_LOG="${CASE_LOG_PREFIX}.out"
STDERR_LOG="${CASE_LOG_PREFIX}.err"

source "${WORKFLOW_ENV}"

python -m campaign_workflow.cli.mark_sim_submitted \
    --campaign-root "${CAMPAIGN_ROOT}" \
    --case-id "${CASE_ID}" \
    --scheduler slurm \
    --scheduler-job-id "${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID}}" \
    --scheduler-array-task-id "${SLURM_ARRAY_TASK_ID}" \
    --submit-command "sbatch ${BASH_SOURCE[0]}" \
    --environment-name "campaign-workflow-py310" \
    --verbose

python -m campaign_workflow.cli.mark_sim_running \
    --campaign-root "${CAMPAIGN_ROOT}" \
    --case-id "${CASE_ID}" \
    --scheduler slurm \
    --scheduler-job-id "${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID}}" \
    --scheduler-array-task-id "${SLURM_ARRAY_TASK_ID}" \
    --run-command "bash ${CASE_RUNNER} ${CASE_DIR}" \
    --environment-name "warpx-26.05-py314" \
    --stdout-log "${STDOUT_LOG}" \
    --stderr-log "${STDERR_LOG}" \
    --verbose

set +e
bash "${CASE_RUNNER}" "${CASE_DIR}"
RUN_RC=$?
set -e

if [[ "${RUN_RC}" -eq 0 ]]; then
    python -m campaign_workflow.cli.mark_sim_done \
        --campaign-root "${CAMPAIGN_ROOT}" \
        --case-id "${CASE_ID}" \
        --verbose
    echo "[SIM-ARRAY] DONE: case ${CASE_ID} ${CASE_NAME}"
    exit 0
fi

python -m campaign_workflow.cli.mark_sim_failed \
    --campaign-root "${CAMPAIGN_ROOT}" \
    --case-id "${CASE_ID}" \
    --scheduler slurm \
    --scheduler-job-id "${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID}}" \
    --scheduler-array-task-id "${SLURM_ARRAY_TASK_ID}" \
    --run-command "bash ${CASE_RUNNER} ${CASE_DIR}" \
    --environment-name "warpx-26.05-py314" \
    --stdout-log "${STDOUT_LOG}" \
    --stderr-log "${STDERR_LOG}" \
    --return-code "${RUN_RC}" \
    --error "case-local WarpX runner failed" \
    --verbose || true

exit "${RUN_RC}"