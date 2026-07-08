#!/usr/bin/env bash
#SBATCH --job-name=cw_lynx_optimizer_tick
#SBATCH --partition=novas
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --output=loop_logs/%x_%j.out
#SBATCH --error=loop_logs/%x_%j.err

set -Eeuo pipefail
trap 'echo "[LYNX-MORBO-TICK] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR
set -x

EXPECTED_SLURM_PARTITION="${EXPECTED_SLURM_PARTITION:-novas}"

: "${CW_OPTIMIZATION_ROOT:?missing CW_OPTIMIZATION_ROOT}"
: "${CW_ITERATION:?missing CW_ITERATION}"
: "${CW_NEXT_ITERATION:?missing CW_NEXT_ITERATION}"
: "${CW_ARRAY_SPEC:?missing CW_ARRAY_SPEC}"
: "${CW_WORKFLOW_ROOT:?missing CW_WORKFLOW_ROOT}"
: "${CW_WORKFLOW_ENV:?missing CW_WORKFLOW_ENV}"
: "${CW_JOB_NAME_PREFIX:?missing CW_JOB_NAME_PREFIX}"
: "${SLURM_JOB_ID:?missing SLURM_JOB_ID}"

if [[ "${SLURM_JOB_PARTITION:-}" != "${EXPECTED_SLURM_PARTITION}" ]]; then
    echo "[LYNX-MORBO-TICK] ERROR: refusing to run on partition '${SLURM_JOB_PARTITION:-unset}'." >&2
    echo "[LYNX-MORBO-TICK] Expected partition: ${EXPECTED_SLURM_PARTITION}" >&2
    exit 2
fi

OPTIMIZATION_ROOT="$(cd "${CW_OPTIMIZATION_ROOT}" && pwd -P)"
WORKFLOW_ROOT="$(cd "${CW_WORKFLOW_ROOT}" && pwd -P)"
WORKFLOW_ENV="${CW_WORKFLOW_ENV}"

cd "${OPTIMIZATION_ROOT}"
mkdir -p loop_logs

if [[ ! -f "${WORKFLOW_ENV}" ]]; then
    echo "[LYNX-MORBO-TICK] ERROR: missing workflow env: ${WORKFLOW_ENV}" >&2
    exit 1
fi

source "${WORKFLOW_ENV}"

COMMAND=(
    python -m campaign_workflow.cli.optimizer_tick
    --optimization-root "${OPTIMIZATION_ROOT}"
    --action run_loop_once
    --iteration "${CW_ITERATION}"
    --next-iteration "${CW_NEXT_ITERATION}"
    --array-spec "${CW_ARRAY_SPEC}"
    --workflow-root "${WORKFLOW_ROOT}"
    --workflow-env "${WORKFLOW_ENV}"
    --job-name-prefix "${CW_JOB_NAME_PREFIX}"
    --stop-after-materialization
    --execute
)

if [[ -n "${CW_OPTIMIZATION_CONFIG:-}" ]]; then
    COMMAND+=(--optimization-config "${CW_OPTIMIZATION_CONFIG}")
fi

"${COMMAND[@]}"