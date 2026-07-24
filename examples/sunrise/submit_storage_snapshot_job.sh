#!/usr/bin/env bash
#SBATCH --job-name=cw_snapshot
#SBATCH --partition=T1H
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --output=array_logs/%x_%j.out
#SBATCH --error=array_logs/%x_%j.err

set -Eeuo pipefail
trap 'echo "[SNAPSHOT] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR
set -x

CAMPAIGN_ROOT="${CAMPAIGN_ROOT:-${SLURM_SUBMIT_DIR}}"
WORKFLOW_ENV="${WORKFLOW_ENV:-${HOME}/apps/env/campaign-workflow.sh}"

cd "${CAMPAIGN_ROOT}"
mkdir -p array_logs snapshots

if [[ ! -f "${WORKFLOW_ENV}" ]]; then
    echo "[SNAPSHOT] ERROR: missing workflow env: ${WORKFLOW_ENV}" >&2
    exit 1
fi

source "${WORKFLOW_ENV}"

ARGS=(
    --campaign-root "${CAMPAIGN_ROOT}"
    --verbose
)

if [[ -n "${SNAPSHOT_OUTPUT:-}" ]]; then
    ARGS+=(--output "${SNAPSHOT_OUTPUT}")
fi

python -m campaign_workflow.cli.storage_snapshot "${ARGS[@]}"