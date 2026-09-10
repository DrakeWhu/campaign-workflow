#!/usr/bin/env bash
set -Eeuo pipefail
trap 'echo "[CLPU-N2-WARPX] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 CASE_DIR" >&2
    exit 2
fi

CASE_DIR="$(readlink -f "$1")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
WORKFLOW_ROOT="${WORKFLOW_ROOT:-$(cd "${SCRIPT_DIR}/../../.." && pwd -P)}"
WORKFLOW_ROOT="$(cd "${WORKFLOW_ROOT}" && pwd -P)"
OPTIMIZATION_ROOT="${CW_OPTIMIZATION_ROOT:-${CLPU_N2_OPTIMIZATION_ROOT:-}}"
GUIDING_ANALYSIS_ROOT="${GUIDING_ANALYSIS_ROOT:-${HOME}/apps/src/guiding_analysis_module-clpu-n2-exact-exit}"
OPTIMIZER_ROOT="${CLPU_N2_OPTIMIZER_ROOT:-${HOME}/apps/src/campaign-optimizer-clpu-n2-gate-a}"
WORKFLOW_PYTHON="${CLPU_N2_WORKFLOW_PYTHON:-${HOME}/apps/venvs/campaign-workflow-py310/bin/python}"

[[ -n "${OPTIMIZATION_ROOT}" ]] || {
    echo "[CLPU-N2-WARPX] ERROR: CW_OPTIMIZATION_ROOT/CLPU_N2_OPTIMIZATION_ROOT is required." >&2
    exit 2
}
OPTIMIZATION_ROOT="$(cd "${OPTIMIZATION_ROOT}" && pwd -P)"
GATE_B_RECEIPT="${CLPU_N2_GATE_B_RECEIPT:-${OPTIMIZATION_ROOT}/provenance/clpu_n2_gate_b.json}"
BASE_INPUT="${WORKFLOW_ROOT}/examples/sunrise/corrected_capillary/input_template.py"
GENERIC_RUNNER="${WORKFLOW_ROOT}/examples/sunrise/run_warpx_case_sunrise.sh"
GATE_B_CLI="${WORKFLOW_ROOT}/examples/sunrise/corrected_capillary/validate_nitrogen_gate_b.py"

for required in \
    "${CASE_DIR}/input.py" \
    "${CASE_DIR}/case.env" \
    "${GATE_B_RECEIPT}" \
    "${BASE_INPUT}" \
    "${GENERIC_RUNNER}" \
    "${GATE_B_CLI}" \
    "${WORKFLOW_PYTHON}"
do
    [[ -e "${required}" ]] || {
        echo "[CLPU-N2-WARPX] ERROR: missing required runtime asset: ${required}" >&2
        exit 1
    }
done

export WFLOW_SRC="${WORKFLOW_ROOT}"
export CAP_CORRECTED_INPUT_TEMPLATE="${BASE_INPUT}"
export GUIDING_ANALYSIS_ROOT
export CLPU_N2_OPTIMIZER_ROOT="${OPTIMIZER_ROOT}"
export CLPU_N2_GATE_B_RECEIPT="${GATE_B_RECEIPT}"

"${WORKFLOW_PYTHON}" "${GATE_B_CLI}" verify-runtime \
    --receipt "${GATE_B_RECEIPT}" \
    --optimization-root "${OPTIMIZATION_ROOT}" \
    --workflow-root "${WORKFLOW_ROOT}" \
    --guiding-analysis-root "${GUIDING_ANALYSIS_ROOT}" \
    --optimizer-root "${OPTIMIZER_ROOT}"

echo "[CLPU-N2-WARPX] Gate B runtime closure revalidated before PICMI/WarpX entry."
exec "${GENERIC_RUNNER}" "${CASE_DIR}"
