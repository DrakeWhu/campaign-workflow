#!/usr/bin/env bash
set -Eeuo pipefail

trap 'echo "[WARPX-LYNX-RUNNER] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 CASE_DIR" >&2
    exit 2
fi

CASE_DIR="$1"
WARPX_INPUT_ARG="${WARPX_INPUT_ARG:-2}"

: "${WARPX_LYNX_MODULE:?missing WARPX_LYNX_MODULE, e.g. 26.03_lynx_cpu_rz_yee_openpmd_py311}"

if [[ ! -d "${CASE_DIR}" ]]; then
    echo "[WARPX-LYNX-RUNNER] ERROR: case directory not found: ${CASE_DIR}" >&2
    exit 1
fi

cd "${CASE_DIR}"

if [[ ! -f case.env ]]; then
    echo "[WARPX-LYNX-RUNNER] ERROR: missing case.env in ${CASE_DIR}" >&2
    exit 1
fi

if [[ ! -f input.py ]]; then
    echo "[WARPX-LYNX-RUNNER] ERROR: missing input.py in ${CASE_DIR}" >&2
    exit 1
fi

EXPECTED_SLURM_PARTITION="${EXPECTED_SLURM_PARTITION:-novas}"

if [[ -z "${SLURM_JOB_ID:-}" ]]; then
    echo "[WARPX-LYNX-RUNNER] ERROR: SLURM_JOB_ID is not set. Refusing to run WarpX outside SLURM." >&2
    exit 1
fi

if [[ -z "${SLURM_NTASKS:-}" ]]; then
    echo "[WARPX-LYNX-RUNNER] ERROR: SLURM_NTASKS is not set. Refusing to run WarpX outside a valid SLURM allocation." >&2
    exit 1
fi

if [[ "${SLURM_JOB_PARTITION:-}" != "${EXPECTED_SLURM_PARTITION}" ]]; then
    echo "[WARPX-LYNX-RUNNER] ERROR: refusing to run on partition '${SLURM_JOB_PARTITION:-unset}'." >&2
    echo "[WARPX-LYNX-RUNNER] Expected partition: ${EXPECTED_SLURM_PARTITION}" >&2
    exit 1
fi

source ./case.env

mkdir -p logs diags checkpoints post

JOB_NAME="${SLURM_JOB_NAME:-local_warpx_lynx}"
ARRAY_JOB_ID="${SLURM_ARRAY_JOB_ID:-no_array_job}"
ARRAY_TASK_ID="${SLURM_ARRAY_TASK_ID:-no_array_task}"
CASE_ID_FOR_LOG="${CASE_ID:-unknown_case_id}"

CASE_LOG_PREFIX="logs/${JOB_NAME}_${ARRAY_JOB_ID}_${ARRAY_TASK_ID}_${CASE_ID_FOR_LOG}"

exec > >(tee -a "${CASE_LOG_PREFIX}.out")
exec 2> >(tee -a "${CASE_LOG_PREFIX}.err" >&2)

set -x

echo "[WARPX-LYNX-RUNNER] Case directory: $(pwd)"
echo "[WARPX-LYNX-RUNNER] Per-case stdout log: ${CASE_LOG_PREFIX}.out"
echo "[WARPX-LYNX-RUNNER] Per-case stderr log: ${CASE_LOG_PREFIX}.err"

shopt -s nullglob globstar
existing_h5=(diags/**/*.h5 diags/**/*.hdf5)

if (( ${#existing_h5[@]} > 0 )); then
    echo "[WARPX-LYNX-RUNNER] Existing HDF5 diagnostics found in $(pwd)" >&2
    printf '  %s\n' "${existing_h5[@]}" >&2

    if [[ "${WARPX_SKIP_IF_H5_EXISTS:-0}" == "1" ]]; then
        echo "[WARPX-LYNX-RUNNER] SKIP explicitly allowed by WARPX_SKIP_IF_H5_EXISTS=1"
        exit 0
    fi

    echo "[WARPX-LYNX-RUNNER] ERROR: refusing to treat pre-existing HDF5 files as a successful simulation." >&2
    echo "[WARPX-LYNX-RUNNER] This may be a previous partial/failed run. Inspect the case before rerunning." >&2
    echo "[WARPX-LYNX-RUNNER] To intentionally adopt existing HDF5 diagnostics, use workflow validation/backfill tools, not this runner." >&2
    exit 1
fi

source "${HOME}/apps/env/lynx_clean_base.sh"
module load "WarpX/${WARPX_LYNX_MODULE}"

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"

{
    echo "=== RUN INFO ==="
    date --iso-8601=seconds
    echo "CASE_DIR=$(pwd)"
    echo "CASE_ID=${CASE_ID:-}"
    echo "CASE_NAME=${CASE_NAME:-}"
    echo "SLURM_JOB_ID=${SLURM_JOB_ID:-}"
    echo "SLURM_ARRAY_JOB_ID=${SLURM_ARRAY_JOB_ID:-}"
    echo "SLURM_ARRAY_TASK_ID=${SLURM_ARRAY_TASK_ID:-}"
    echo "SLURM_JOB_NAME=${SLURM_JOB_NAME:-}"
    echo "SLURM_NTASKS=${SLURM_NTASKS:-}"
    echo "SLURM_CPUS_PER_TASK=${SLURM_CPUS_PER_TASK:-}"
    echo "WARPX_INPUT_ARG=${WARPX_INPUT_ARG}"
    echo "WARPX_LYNX_MODULE=${WARPX_LYNX_MODULE}"
    echo "WARPX_HOME=${WARPX_HOME:-}"
    echo "WARPX_DIM=${WARPX_DIM:-}"
    echo "WARPX_LYNX_BUILD_TAG=${WARPX_LYNX_BUILD_TAG:-}"
    echo "OMP_NUM_THREADS=${OMP_NUM_THREADS}"
    echo "OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS}"
    echo "MKL_NUM_THREADS=${MKL_NUM_THREADS}"
    echo "python=$(which python)"
    python --version
    echo "mpirun=$(command -v mpirun || true)"
    mpirun --version 2>&1 | sed -n '1,4p' || true
    echo
    echo "=== CAP ENVIRONMENT ==="
    env | grep '^CAP_' | sort || true
    echo "======================="
} | tee run_info.txt

echo "[WARPX-LYNX-RUNNER] PICMI preflight: CAP_DRY_RUN=1 python input.py ${WARPX_INPUT_ARG}"
CAP_DRY_RUN=1 python input.py "${WARPX_INPUT_ARG}"
echo "[WARPX-LYNX-RUNNER] PICMI preflight passed."

if [[ -z "${SLURM_NTASKS:-}" ]]; then
    echo "[WARPX-LYNX-RUNNER] ERROR: SLURM_NTASKS is not set. This runner is intended to run inside a SLURM allocation." >&2
    exit 1
fi

echo "[WARPX-LYNX-RUNNER] Running: srun -n ${SLURM_NTASKS} python input.py ${WARPX_INPUT_ARG}"
srun -n "${SLURM_NTASKS}" python input.py "${WARPX_INPUT_ARG}"

date --iso-8601=seconds
echo "[WARPX-LYNX-RUNNER] DONE"