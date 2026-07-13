#!/usr/bin/env bash
set -Eeuo pipefail

trap 'echo "[LFMETRICS-RUNNER] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 CASE_DIR" >&2
    exit 2
fi

CASE_DIR="$(cd "$1" && pwd -P)"

if ! type module >/dev/null 2>&1; then
    source /etc/profile.d/modules.sh 2>/dev/null || true
fi

if [[ -n "${LFMETRICS_ENV_SCRIPT:-}" ]]; then
    if [[ ! -f "${LFMETRICS_ENV_SCRIPT}" ]]; then
        echo "[LFMETRICS-RUNNER] missing LFMETRICS_ENV_SCRIPT=${LFMETRICS_ENV_SCRIPT}" >&2
        exit 1
    fi
    source "${LFMETRICS_ENV_SCRIPT}"
else
    module purge
    module load GCC/12.1.0
    module load Python/3.10.12
    LFMETRICS_VENV="${LFMETRICS_VENV:-${HOME}/apps/venvs/multichannel-lfmetrics-py310}"
    if [[ ! -f "${LFMETRICS_VENV}/bin/activate" ]]; then
        echo "[LFMETRICS-RUNNER] missing LFMetrics venv: ${LFMETRICS_VENV}" >&2
        exit 1
    fi
    source "${LFMETRICS_VENV}/bin/activate"
fi

mkdir -p "${CASE_DIR}/post"

python -m lfmetrics.cli analyze-case "${CASE_DIR}" \
    --diagnostics-dir 3D \
    --species electrons \
    --energy-threshold-MeV 5 \
    --spectrum-min-energy-MeV 5 \
    --output "${CASE_DIR}/post/particle_summary.csv" \
    --plots-dir "${CASE_DIR}/post/plots"

echo "[LFMETRICS-RUNNER] DONE: ${CASE_DIR}/post/particle_summary.csv"

