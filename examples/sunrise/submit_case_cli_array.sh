#!/usr/bin/env bash
#SBATCH --job-name=cw_case_phase
#SBATCH --partition=T1H
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --array=0-0
#SBATCH --output=array_logs/%x_%A_%a.out
#SBATCH --error=array_logs/%x_%A_%a.err

set -Eeuo pipefail
trap 'echo "[CASE-PHASE] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR
set -x

if [[ -z "${PHASE:-}" ]]; then
    echo "[CASE-PHASE] ERROR: PHASE is not set." >&2
    exit 2
fi

if [[ -z "${SLURM_ARRAY_TASK_ID:-}" ]]; then
    echo "[CASE-PHASE] ERROR: SLURM_ARRAY_TASK_ID is not set." >&2
    exit 2
fi

CAMPAIGN_ROOT="${CAMPAIGN_ROOT:-${SLURM_SUBMIT_DIR}}"
WORKFLOW_ENV="${WORKFLOW_ENV:-${HOME}/apps/env/campaign-workflow.sh}"

cd "${CAMPAIGN_ROOT}"
mkdir -p array_logs

if [[ ! -f campaign.json || ! -f cases.tsv ]]; then
    echo "[CASE-PHASE] ERROR: campaign.json/cases.tsv missing in ${CAMPAIGN_ROOT}" >&2
    exit 1
fi

if [[ ! -f "${WORKFLOW_ENV}" ]]; then
    echo "[CASE-PHASE] ERROR: missing workflow env: ${WORKFLOW_ENV}" >&2
    exit 1
fi

CASE_ID="${SLURM_ARRAY_TASK_ID}"

source "${WORKFLOW_ENV}"

case "${PHASE}" in
    validate_raw)
        python -m campaign_workflow.cli.validate_raw_case \
            --campaign-root "${CAMPAIGN_ROOT}" \
            --case-id "${CASE_ID}" \
            --verbose
        ;;

    analyze)
        python -m campaign_workflow.cli.analyze_case \
            --campaign-root "${CAMPAIGN_ROOT}" \
            --case-id "${CASE_ID}" \
            --verbose
        ;;

    mark_raw_delete_eligible)
        python -m campaign_workflow.cli.mark_raw_delete_eligible \
            --campaign-root "${CAMPAIGN_ROOT}" \
            --case-id "${CASE_ID}" \
            --verbose
        ;;

    cleanup_dry_run)
        python -m campaign_workflow.cli.cleanup_raw_case \
            --campaign-root "${CAMPAIGN_ROOT}" \
            --case-id "${CASE_ID}" \
            --dry-run \
            --verbose
        ;;

    cleanup_execute)
        if [[ "${CONFIRM_CLEANUP_EXECUTE:-0}" != "1" ]]; then
            echo "[CASE-PHASE] ERROR: cleanup_execute requires CONFIRM_CLEANUP_EXECUTE=1." >&2
            exit 2
        fi

        python -m campaign_workflow.cli.cleanup_raw_case \
            --campaign-root "${CAMPAIGN_ROOT}" \
            --case-id "${CASE_ID}" \
            --execute \
            --verbose
        ;;

    *)
        echo "[CASE-PHASE] ERROR: unknown PHASE=${PHASE}" >&2
        exit 2
        ;;
esac