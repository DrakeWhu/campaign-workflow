#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "[PIPELINE] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

CAMPAIGN_ROOT="${CAMPAIGN_ROOT:-${PWD}}"
WORKFLOW_ROOT="${WORKFLOW_ROOT:-${HOME}/apps/src/campaign-workflow}"
WORKFLOW_ENV="${WORKFLOW_ENV:-${HOME}/apps/env/campaign-workflow.sh}"
JOB_PREFIX="${JOB_PREFIX:-cw_campaign}"
INIT_STATES="${INIT_STATES:-1}"
RUN_CLEANUP_EXECUTE="${RUN_CLEANUP_EXECUTE:-0}"

CAMPAIGN_ROOT="$(cd "${CAMPAIGN_ROOT}" && pwd -P)"
WORKFLOW_ROOT="$(cd "${WORKFLOW_ROOT}" && pwd -P)"

SIM_SCRIPT="${WORKFLOW_ROOT}/examples/sunrise/submit_simulation_array_managed.sh"
CASE_PHASE_SCRIPT="${WORKFLOW_ROOT}/examples/sunrise/submit_case_cli_array.sh"
SNAPSHOT_SCRIPT="${WORKFLOW_ROOT}/examples/sunrise/submit_storage_snapshot_job.sh"

cd "${CAMPAIGN_ROOT}"
mkdir -p array_logs snapshots

if [[ ! -f campaign.json || ! -f cases.tsv ]]; then
    echo "[PIPELINE] ERROR: campaign.json/cases.tsv missing in ${CAMPAIGN_ROOT}" >&2
    exit 1
fi

if [[ ! -f "${WORKFLOW_ENV}" ]]; then
    echo "[PIPELINE] ERROR: missing workflow env: ${WORKFLOW_ENV}" >&2
    exit 1
fi

for script in "${SIM_SCRIPT}" "${CASE_PHASE_SCRIPT}" "${SNAPSHOT_SCRIPT}"; do
    if [[ ! -x "${script}" ]]; then
        echo "[PIPELINE] ERROR: missing/non-executable script: ${script}" >&2
        exit 1
    fi
done

LAST_CASE_ID="$(
    awk 'BEGIN{FS="\t"; found=0; max=-1}
         NR>1 {
             id=$1+0
             if (id > max) max=id
             found=1
         }
         END {
             if (!found) exit 1
             print max
         }' cases.tsv
)"

CASE_COUNT="$(
    awk 'BEGIN{FS="\t"; n=0}
         NR>1 {n++}
         END{print n}' cases.tsv
)"

ARRAY_SPEC="${ARRAY_SPEC:-0-${LAST_CASE_ID}}"

echo "[PIPELINE] campaign_root=${CAMPAIGN_ROOT}"
echo "[PIPELINE] workflow_root=${WORKFLOW_ROOT}"
echo "[PIPELINE] case_count=${CASE_COUNT}"
echo "[PIPELINE] last_case_id=${LAST_CASE_ID}"
echo "[PIPELINE] array_spec=${ARRAY_SPEC}"
echo "[PIPELINE] run_cleanup_execute=${RUN_CLEANUP_EXECUTE}"

source "${WORKFLOW_ENV}"

if [[ "${INIT_STATES}" == "1" ]]; then
    python -m campaign_workflow.cli.init_case_states \
        --campaign-root "${CAMPAIGN_ROOT}" \
        --create-missing-case-dirs \
        --verbose
fi

python -m campaign_workflow.cli.init_case_states \
    --campaign-root "${CAMPAIGN_ROOT}" \
    --check \
    --verbose

submit_job() {
    local output
    output="$("$@")"
    echo "${output}" >&2
    echo "${output}" | tail -n 1 | cut -d';' -f1
}

SIM_JOB="$(
    submit_job sbatch --parsable \
        --array="${ARRAY_SPEC}" \
        --job-name="${JOB_PREFIX}_sim" \
        --export=ALL,CAMPAIGN_ROOT="${CAMPAIGN_ROOT}",WORKFLOW_ROOT="${WORKFLOW_ROOT}",WORKFLOW_ENV="${WORKFLOW_ENV}" \
        "${SIM_SCRIPT}"
)"

RAW_JOB="$(
    submit_job sbatch --parsable \
        --dependency=afterany:${SIM_JOB} \
        --array="${ARRAY_SPEC}" \
        --job-name="${JOB_PREFIX}_raw" \
        --export=ALL,CAMPAIGN_ROOT="${CAMPAIGN_ROOT}",WORKFLOW_ENV="${WORKFLOW_ENV}",PHASE=validate_raw \
        "${CASE_PHASE_SCRIPT}"
)"

ANALYSIS_JOB="$(
    submit_job sbatch --parsable \
        --dependency=afterany:${RAW_JOB} \
        --array="${ARRAY_SPEC}" \
        --job-name="${JOB_PREFIX}_ana" \
        --export=ALL,CAMPAIGN_ROOT="${CAMPAIGN_ROOT}",WORKFLOW_ENV="${WORKFLOW_ENV}",PHASE=analyze \
        "${CASE_PHASE_SCRIPT}"
)"

SNAPSHOT_AFTER_ANALYSIS_JOB="$(
    submit_job sbatch --parsable \
        --dependency=afterany:${ANALYSIS_JOB} \
        --job-name="${JOB_PREFIX}_snap_a" \
        --export=ALL,CAMPAIGN_ROOT="${CAMPAIGN_ROOT}",WORKFLOW_ENV="${WORKFLOW_ENV}",SNAPSHOT_OUTPUT=snapshots/storage_snapshot_after_analysis.json \
        "${SNAPSHOT_SCRIPT}"
)"

ELIGIBILITY_JOB="$(
    submit_job sbatch --parsable \
        --dependency=afterany:${SNAPSHOT_AFTER_ANALYSIS_JOB} \
        --array="${ARRAY_SPEC}" \
        --job-name="${JOB_PREFIX}_elig" \
        --export=ALL,CAMPAIGN_ROOT="${CAMPAIGN_ROOT}",WORKFLOW_ENV="${WORKFLOW_ENV}",PHASE=mark_raw_delete_eligible \
        "${CASE_PHASE_SCRIPT}"
)"

CLEANUP_DRY_RUN_JOB="$(
    submit_job sbatch --parsable \
        --dependency=afterany:${ELIGIBILITY_JOB} \
        --array="${ARRAY_SPEC}" \
        --job-name="${JOB_PREFIX}_dry" \
        --export=ALL,CAMPAIGN_ROOT="${CAMPAIGN_ROOT}",WORKFLOW_ENV="${WORKFLOW_ENV}",PHASE=cleanup_dry_run \
        "${CASE_PHASE_SCRIPT}"
)"

if [[ "${RUN_CLEANUP_EXECUTE}" == "1" ]]; then
    CLEANUP_EXECUTE_JOB="$(
        submit_job sbatch --parsable \
            --dependency=afterany:${CLEANUP_DRY_RUN_JOB} \
            --array="${ARRAY_SPEC}" \
            --job-name="${JOB_PREFIX}_clean" \
            --export=ALL,CAMPAIGN_ROOT="${CAMPAIGN_ROOT}",WORKFLOW_ENV="${WORKFLOW_ENV}",PHASE=cleanup_execute,CONFIRM_CLEANUP_EXECUTE=1 \
            "${CASE_PHASE_SCRIPT}"
    )"

    SNAPSHOT_AFTER_CLEANUP_JOB="$(
        submit_job sbatch --parsable \
            --dependency=afterany:${CLEANUP_EXECUTE_JOB} \
            --job-name="${JOB_PREFIX}_snap_c" \
            --export=ALL,CAMPAIGN_ROOT="${CAMPAIGN_ROOT}",WORKFLOW_ENV="${WORKFLOW_ENV}",SNAPSHOT_OUTPUT=snapshots/storage_snapshot_after_cleanup.json \
            "${SNAPSHOT_SCRIPT}"
    )"
else
    CLEANUP_EXECUTE_JOB="not_submitted"
    SNAPSHOT_AFTER_CLEANUP_JOB="not_submitted"
fi

cat <<EOF
[PIPELINE] Submitted campaign pipeline

SIM_JOB=${SIM_JOB}
RAW_JOB=${RAW_JOB}
ANALYSIS_JOB=${ANALYSIS_JOB}
SNAPSHOT_AFTER_ANALYSIS_JOB=${SNAPSHOT_AFTER_ANALYSIS_JOB}
ELIGIBILITY_JOB=${ELIGIBILITY_JOB}
CLEANUP_DRY_RUN_JOB=${CLEANUP_DRY_RUN_JOB}
CLEANUP_EXECUTE_JOB=${CLEANUP_EXECUTE_JOB}
SNAPSHOT_AFTER_CLEANUP_JOB=${SNAPSHOT_AFTER_CLEANUP_JOB}

Monitor:
  squeue -u "$USER"

Inspect states after jobs:
  source "${WORKFLOW_ENV}"
  python - <<'PY'
import json
from pathlib import Path
from collections import Counter

root = Path("${CAMPAIGN_ROOT}")
states = Counter()
for row in (root / "cases.tsv").read_text(encoding="utf-8-sig").splitlines()[1:]:
    if not row.strip():
        continue
    case_id, case_name, *_ = row.split("\t")
    state_path = root / case_name / "state.json"
    if state_path.exists():
        states[json.loads(state_path.read_text(encoding="utf-8-sig"))["state"]] += 1
    else:
        states["<missing state.json>"] += 1
print(dict(states))
PY

EOF