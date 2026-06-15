#!/usr/bin/env bash
set -Eeuo pipefail

trap 'echo "[WARPX-RUNNER] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 CASE_DIR" >&2
    exit 2
fi

CASE_DIR="$1"
WARPX_INPUT_ARG="${WARPX_INPUT_ARG:-2}"

if [[ ! -d "${CASE_DIR}" ]]; then
    echo "[WARPX-RUNNER] ERROR: case directory not found: ${CASE_DIR}" >&2
    exit 1
fi

cd "${CASE_DIR}"

if [[ ! -f case.env ]]; then
    echo "[WARPX-RUNNER] ERROR: missing case.env in ${CASE_DIR}" >&2
    exit 1
fi

if [[ ! -f input.py ]]; then
    echo "[WARPX-RUNNER] ERROR: missing input.py in ${CASE_DIR}" >&2
    exit 1
fi

source ./case.env

mkdir -p logs diags checkpoints post

JOB_NAME="${SLURM_JOB_NAME:-local_warpx}"
ARRAY_JOB_ID="${SLURM_ARRAY_JOB_ID:-no_array_job}"
ARRAY_TASK_ID="${SLURM_ARRAY_TASK_ID:-no_array_task}"
CASE_ID_FOR_LOG="${CASE_ID:-unknown_case_id}"

CASE_LOG_PREFIX="logs/${JOB_NAME}_${ARRAY_JOB_ID}_${ARRAY_TASK_ID}_${CASE_ID_FOR_LOG}"

exec > >(tee -a "${CASE_LOG_PREFIX}.out")
exec 2> >(tee -a "${CASE_LOG_PREFIX}.err" >&2)

set -x

echo "[WARPX-RUNNER] Case directory: $(pwd)"
echo "[WARPX-RUNNER] Per-case stdout log: ${CASE_LOG_PREFIX}.out"
echo "[WARPX-RUNNER] Per-case stderr log: ${CASE_LOG_PREFIX}.err"

shopt -s nullglob globstar
existing_h5=(diags/**/*.h5 diags/**/*.hdf5)

if (( ${#existing_h5[@]} > 0 )); then
    echo "[WARPX-RUNNER] SKIP: existing HDF5 diagnostics found in $(pwd)"
    printf '  %s\n' "${existing_h5[@]}"
    exit 0
fi

if ! type module >/dev/null 2>&1; then
    source /etc/profile.d/modules.sh 2>/dev/null || true
fi

module purge
module use ~/apps/modules

module load GCC/12.1.0
module load Python/3.14.3
module load OpenBLAS/0.3.31
module load warpx/26.05-gcc12-openmpi413-all-dims

export PYTHON314_ROOT=/APPS/centos7/centos79/software/Compiler/GCC-12.1/Python/3.14.3
export LD_LIBRARY_PATH="${PYTHON314_ROOT}/lib:${LD_LIBRARY_PATH:-}"

source ~/apps/venvs/warpx-26.05-py314/bin/activate

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

{
    echo "=== RUN INFO ==="
    date --iso-8601=seconds
    echo "CASE_DIR=$(pwd)"
    echo "CASE_ID=${CASE_ID:-}"
    echo "CASE_NAME=${CASE_NAME:-}"
    echo "LASER_CASE=${LASER_CASE:-}"
    echo "PLASMA_KIND=${PLASMA_KIND:-}"
    echo "N0_CM3=${N0_CM3:-}"
    echo "PLATEAU_LENGTH_MM=${PLATEAU_LENGTH_MM:-}"
    echo "DIAMETER_UM=${DIAMETER_UM:-}"
    echo "RADIUS_UM=${RADIUS_UM:-}"
    echo "FOCUS_OFFSET_FROM_PLATEAU_START_MM=${FOCUS_OFFSET_FROM_PLATEAU_START_MM:-}"
    echo "CAP_RMAX_UM=${CAP_RMAX_UM:-}"
    echo "CAP_NR=${CAP_NR:-}"
    echo "SLURM_JOB_ID=${SLURM_JOB_ID:-}"
    echo "SLURM_ARRAY_JOB_ID=${SLURM_ARRAY_JOB_ID:-}"
    echo "SLURM_ARRAY_TASK_ID=${SLURM_ARRAY_TASK_ID:-}"
    echo "SLURM_JOB_NAME=${SLURM_JOB_NAME:-}"
    echo "SLURM_NTASKS=${SLURM_NTASKS:-}"
    echo "SLURM_CPUS_PER_TASK=${SLURM_CPUS_PER_TASK:-}"
    echo "OMP_NUM_THREADS=${OMP_NUM_THREADS}"
    echo "OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS}"
    echo "MKL_NUM_THREADS=${MKL_NUM_THREADS}"
    echo "WARPX_INPUT_ARG=${WARPX_INPUT_ARG}"
    echo "python=$(which python)"
    python --version
    ldd "$(which python)" | grep -E "libpython|not found" || true
    echo
    echo "=== CAP ENVIRONMENT ==="
    env | grep '^CAP_' | sort || true
    echo "======================="
} | tee run_info.txt

echo "[WARPX-RUNNER] PICMI preflight: CAP_DRY_RUN=1 python input.py ${WARPX_INPUT_ARG}"
CAP_DRY_RUN=1 python input.py "${WARPX_INPUT_ARG}"
echo "[WARPX-RUNNER] PICMI preflight passed."

if [[ -z "${SLURM_NTASKS:-}" ]]; then
    echo "[WARPX-RUNNER] ERROR: SLURM_NTASKS is not set. This runner is intended to run inside a SLURM allocation." >&2
    exit 1
fi

echo "[WARPX-RUNNER] Running: srun -n ${SLURM_NTASKS} python input.py ${WARPX_INPUT_ARG}"
srun -n "${SLURM_NTASKS}" python input.py "${WARPX_INPUT_ARG}"

date --iso-8601=seconds
echo "[WARPX-RUNNER] DONE"