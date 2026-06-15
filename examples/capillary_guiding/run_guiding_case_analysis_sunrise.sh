#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 CASE_DIR" >&2
    exit 2
fi

CASE_DIR="$1"

GUIDING_ANALYSIS_ROOT="${GUIDING_ANALYSIS_ROOT:-$HOME/apps/src/guiding_analysis_module}"
GUIDING_ANALYSIS_VENV="${GUIDING_ANALYSIS_VENV:-$HOME/apps/venvs/guiding-analysis-py310}"

if ! type module >/dev/null 2>&1; then
    source /etc/profile.d/modules.sh 2>/dev/null || true
fi

module purge
module load Git/2.41.0
module load GCC/12.1.0
module load Python/3.10.12
module load OpenBLAS/0.3.31
module load warpx/26.05-gcc12-openmpi413-all-dims

export MPLBACKEND=Agg

source "${GUIDING_ANALYSIS_VENV}/bin/activate"

cd "${GUIDING_ANALYSIS_ROOT}"

python scripts/analyze_case.py \
    --diag "${CASE_DIR}/diags/diag1" \
    --outdir "${CASE_DIR}" \
    --overwrite \
    --no-plots