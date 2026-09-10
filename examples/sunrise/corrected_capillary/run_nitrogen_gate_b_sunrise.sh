#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "[CLPU-N2-GATE-B] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

if [[ -n "${SLURM_JOB_ID:-}" ]]; then
    echo "[CLPU-N2-GATE-B] ERROR: run Gate B from a SUNRISE login node, not inside a Slurm job." >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
WF="${CLPU_N2_WORKFLOW_ROOT:-$(cd "${SCRIPT_DIR}/../../.." && pwd -P)}"
ROOT="${CLPU_N2_OPTIMIZATION_ROOT:-${HOME}/warpx_runs/clpu_n2_gate_a_staging_20260910}"
GA="${GUIDING_ANALYSIS_ROOT:-${HOME}/apps/src/guiding_analysis_module-clpu-n2-exact-exit}"
OPT="${CLPU_N2_OPTIMIZER_ROOT:-${HOME}/apps/src/campaign-optimizer-clpu-n2-gate-a}"
WORKFLOW_PYTHON="${HOME}/apps/venvs/campaign-workflow-py310/bin/python"

ROOT="$(cd "${ROOT}" && pwd -P)"
WF="$(cd "${WF}" && pwd -P)"
GA="$(cd "${GA}" && pwd -P)"
OPT="$(cd "${OPT}" && pwd -P)"

TEMPLATE="${ROOT}/template_campaign"
ITER="${ROOT}/iterations/iter_000"
CANDIDATE_BATCH="${ROOT}/optimizer_runs/iter_000/outputs/candidate_batch.tsv"
BATCH_PLAN="${ROOT}/optimizer_runs/iter_000/outputs/batch_campaign_plan.json"
GATE_A="${ROOT}/optimizer_runs/iter_000/reports/clpu_n2_gate_a.json"
GATE_B="${ROOT}/provenance/clpu_n2_gate_b.json"
AUDIT="${ROOT}/provenance/gate_b_$(date -u +%Y%m%dT%H%M%SZ)"
SOURCE_CAMPAIGN="${WF}/examples/sunrise/corrected_capillary/campaign_nitrogen_uniform_soft50.json"
SOURCE_WRAPPER="${WF}/examples/sunrise/corrected_capillary/nitrogen_input_template.py"
CAMPAIGN_NAME="clpu_capillary_guiding_n2_uniform_soft50_template"

for required in \
    "${CANDIDATE_BATCH}" \
    "${BATCH_PLAN}" \
    "${GATE_A}" \
    "${SOURCE_CAMPAIGN}" \
    "${SOURCE_WRAPPER}" \
    "${WF}/examples/sunrise/corrected_capillary/input_template.py" \
    "${WF}/examples/sunrise/corrected_capillary/run_nitrogen_warpx_case_sunrise.sh" \
    "${WF}/examples/sunrise/corrected_capillary/run_nitrogen_case_analysis_sunrise.sh" \
    "${WF}/examples/sunrise/corrected_capillary/validate_nitrogen_particle_outputs.py" \
    "${WORKFLOW_PYTHON}"
do
    [[ -e "${required}" ]] || {
        echo "[CLPU-N2-GATE-B] ERROR: missing required asset: ${required}" >&2
        exit 1
    }
done

if [[ -e "${TEMPLATE}" || -e "${ITER}" || -e "${GATE_B}" ]]; then
    echo "[CLPU-N2-GATE-B] ERROR: refusing to reuse an existing Gate B materialization." >&2
    echo "  template=${TEMPLATE}" >&2
    echo "  iteration=${ITER}" >&2
    echo "  receipt=${GATE_B}" >&2
    echo "Preserve the existing root for audit; use a dedicated resume path after inspection." >&2
    exit 2
fi

if ! type module >/dev/null 2>&1; then
    source /etc/profile.d/modules.sh
fi
module load Git/2.41.0

EXPECTED_GA="e809b43d2071e5fa5cb39de2613f3e9d170bea84"
EXPECTED_OPT="73dab76305f547581c57b70a900706374c929141"
[[ "$(git -C "${GA}" rev-parse HEAD)" == "${EXPECTED_GA}" ]] || {
    echo "[CLPU-N2-GATE-B] ERROR: guiding-analysis HEAD is not ${EXPECTED_GA}" >&2
    exit 1
}
[[ "$(git -C "${OPT}" rev-parse HEAD)" == "${EXPECTED_OPT}" ]] || {
    echo "[CLPU-N2-GATE-B] ERROR: campaign-optimizer HEAD is not ${EXPECTED_OPT}" >&2
    exit 1
}
for repo in "${WF}" "${GA}" "${OPT}"; do
    [[ -z "$(git -C "${repo}" status --porcelain)" ]] || {
        echo "[CLPU-N2-GATE-B] ERROR: dirty checkout: ${repo}" >&2
        exit 1
    }
done

mkdir -p "${AUDIT}" "${TEMPLATE}" "${ROOT}/iterations"
exec > >(tee "${AUDIT}/gate_b.log") 2>&1

cp "${SOURCE_CAMPAIGN}" "${TEMPLATE}/campaign.json"
cp "${SOURCE_WRAPPER}" "${TEMPLATE}/input_template.py"

echo "===== GATE B: PREPARE REVIEWED CANDIDATE CAMPAIGN ====="
PYTHONPATH="${WF}" "${WORKFLOW_PYTHON}" -m campaign_workflow.cli.prepare_batch_campaign \
    --candidate-batch "${CANDIDATE_BATCH}" \
    --batch-plan "${BATCH_PLAN}" \
    --template-campaign-root "${TEMPLATE}" \
    --output-campaign-root "${ITER}" \
    --campaign-name "${CAMPAIGN_NAME}" \
    --dry-run

PYTHONPATH="${WF}" "${WORKFLOW_PYTHON}" -m campaign_workflow.cli.prepare_batch_campaign \
    --candidate-batch "${CANDIDATE_BATCH}" \
    --batch-plan "${BATCH_PLAN}" \
    --template-campaign-root "${TEMPLATE}" \
    --output-campaign-root "${ITER}" \
    --campaign-name "${CAMPAIGN_NAME}" \
    --execute

# prepare_batch_campaign serializes the reviewed JSON after applying its allowed
# campaign-name/manifest fields. For Gate B staging the chosen name already equals
# the template, so restore the reviewed bytes before materialization and hash them.
cp "${SOURCE_CAMPAIGN}" "${ITER}/campaign.json"

echo "===== GATE B: MATERIALIZE ALL 8 ACTUAL CASE INPUTS ====="
PYTHONPATH="${WF}" "${WORKFLOW_PYTHON}" -m campaign_workflow.cli.materialize_cases \
    --campaign-root "${ITER}" \
    --verbose

PYTHONPATH="${WF}" "${WORKFLOW_PYTHON}" -m campaign_workflow.cli.init_case_states \
    --campaign-root "${ITER}" \
    --verbose

if find "${ITER}" -type f \( -name '*.h5' -o -name '*.hdf5' \) -print -quit | grep -q .; then
    echo "[CLPU-N2-GATE-B] ERROR: HDF5 exists before PICMI preflight." >&2
    exit 1
fi
if find "${ITER}" -path '*/post/sim_submitted.json' -type f -print -quit | grep -q .; then
    echo "[CLPU-N2-GATE-B] ERROR: a case is already marked submitted." >&2
    exit 1
fi

echo "===== GATE B: LOAD THE EXACT WARPX 26.05 RUNNER STACK ====="
module purge
module use "${HOME}/apps/modules"
module load Git/2.41.0
module load GCC/12.1.0
module load Python/3.14.3
module load OpenBLAS/0.3.31
module load warpx/26.05-gcc12-openmpi413-all-dims

export PYTHON314_ROOT=/APPS/centos7/centos79/software/Compiler/GCC-12.1/Python/3.14.3
export LD_LIBRARY_PATH="${PYTHON314_ROOT}/lib:${LD_LIBRARY_PATH:-}"
source "${HOME}/apps/venvs/warpx-26.05-py314/bin/activate"
export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export PYTHONPATH="${WF}"
export WFLOW_SRC="${WF}"
export CAP_CORRECTED_INPUT_TEMPLATE="${WF}/examples/sunrise/corrected_capillary/input_template.py"
export GUIDING_ANALYSIS_ROOT="${GA}"
export CLPU_N2_OPTIMIZER_ROOT="${OPT}"

{
    echo "python=$(command -v python)"
    python --version
    echo "git=$(command -v git)"
    git --version
    module list 2>&1
    python - <<'PY'
import pywarpx
print("pywarpx=" + str(pywarpx.__file__))
PY
} | tee "${AUDIT}/runtime.txt"

echo "===== GATE B: PICMI WRITE_INPUT_FILE FOR ALL 8 MATERIALIZED INPUTS ====="
while IFS=$'\t' read -r case_id case_name; do
    case_dir="${ITER}/${case_name}"
    log="${AUDIT}/picmi_case_${case_id}.log"
    (
        cd "${case_dir}"
        source ./case.env
        CAP_DRY_RUN=1 python ./input.py 2
    ) > "${log}" 2>&1
    grep -F "[CLPU] nitrogen PICMI preflight completed; simulation not started" "${log}" >/dev/null
    echo "PICMI_PREFLIGHT_OK case_id=${case_id} case_name=${case_name} log=${log}"
done < <(
    python - "${ITER}/cases.tsv" <<'PY'
import csv
import sys
from pathlib import Path
with Path(sys.argv[1]).open(newline="", encoding="utf-8-sig") as stream:
    for row in csv.DictReader(stream, delimiter="\t"):
        print(f"{row['CASE_ID']}\t{row['CASE_NAME']}")
PY
)

echo "===== GATE B: VALIDATE REQUESTED -> RESOLVED -> SERIALIZED -> CLOSURE ====="
python "${WF}/examples/sunrise/corrected_capillary/validate_nitrogen_gate_b.py" validate \
    --optimization-root "${ROOT}" \
    --campaign-root "${ITER}" \
    --workflow-root "${WF}" \
    --guiding-analysis-root "${GA}" \
    --optimizer-root "${OPT}" \
    --output "${GATE_B}"

[[ ! -e "${ROOT}/provenance/clpu_n2_launch_gate.json" ]]
if find "${ITER}" -type f \( -name '*.h5' -o -name '*.hdf5' \) -print -quit | grep -q .; then
    echo "[CLPU-N2-GATE-B] ERROR: PICMI preflight unexpectedly produced HDF5 diagnostics." >&2
    exit 1
fi
if find "${ITER}" -path '*/post/sim_submitted.json' -type f -print -quit | grep -q .; then
    echo "[CLPU-N2-GATE-B] ERROR: Gate B unexpectedly marked a case submitted." >&2
    exit 1
fi

echo "GATE_B_STATUS=pass"
echo "GATE_B_RECEIPT=${GATE_B}"
echo "AUDIT=${AUDIT}"
echo "NO_WARPX_EVOLUTION=1"
echo "NO_SLURM_SUBMISSION=1"
