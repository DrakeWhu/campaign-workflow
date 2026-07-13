#!/usr/bin/env bash
set -Eeuo pipefail

trap 'echo "[MULTICHANNEL-WARPX] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 CASE_DIR" >&2
    exit 2
fi

CASE_DIR="$(cd "$1" && pwd -P)"
cd "${CASE_DIR}"

if [[ ! -f case.env || ! -f input.py ]]; then
    echo "[MULTICHANNEL-WARPX] missing case.env or input.py in ${CASE_DIR}" >&2
    exit 1
fi

source ./case.env
mkdir -p logs post

JOB_NAME="${SLURM_JOB_NAME:-local_multichannel_warpx}"
ARRAY_JOB_ID="${SLURM_ARRAY_JOB_ID:-no_array_job}"
ARRAY_TASK_ID="${SLURM_ARRAY_TASK_ID:-no_array_task}"
CASE_ID_FOR_LOG="${CASE_ID:-unknown_case_id}"
CASE_LOG_PREFIX="logs/${JOB_NAME}_${ARRAY_JOB_ID}_${ARRAY_TASK_ID}_${CASE_ID_FOR_LOG}"

exec > >(tee -a "${CASE_LOG_PREFIX}.out")
exec 2> >(tee -a "${CASE_LOG_PREFIX}.err" >&2)

set -x

shopt -s nullglob
existing_h5=(3D/*.h5 3D/*.hdf5 fields3D/*.h5 fields3D/*.hdf5)
if (( ${#existing_h5[@]} > 0 )); then
    echo "[MULTICHANNEL-WARPX] existing HDF5 diagnostics found:" >&2
    printf '  %s\n' "${existing_h5[@]}" >&2
    echo "[MULTICHANNEL-WARPX] refusing to mix a new run with previous output" >&2
    exit 1
fi

if ! type module >/dev/null 2>&1; then
    source /etc/profile.d/modules.sh 2>/dev/null || true
fi

module purge
module use "${HOME}/apps/modules"
module load GCC/12.1.0
module load Python/3.14.3
module load OpenBLAS/0.3.31
module load warpx/26.05-gcc12-openmpi413-all-dims

export PYTHON314_ROOT=/APPS/centos7/centos79/software/Compiler/GCC-12.1/Python/3.14.3
export LD_LIBRARY_PATH="${PYTHON314_ROOT}/lib:${LD_LIBRARY_PATH:-}"
source "${HOME}/apps/venvs/warpx-26.05-py314/bin/activate"

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

{
    echo "=== MULTICHANNEL RUN INFO ==="
    date --iso-8601=seconds
    echo "CASE_DIR=${CASE_DIR}"
    echo "SLURM_JOB_ID=${SLURM_JOB_ID:-}"
    echo "SLURM_ARRAY_JOB_ID=${SLURM_ARRAY_JOB_ID:-}"
    echo "SLURM_ARRAY_TASK_ID=${SLURM_ARRAY_TASK_ID:-}"
    echo "SLURM_NTASKS=${SLURM_NTASKS:-}"
    echo "python=$(which python)"
    python --version
    echo
    echo "=== MC ENVIRONMENT ==="
    env | grep '^MC_' | sort
    echo "======================"
} | tee run_info.txt

echo "[MULTICHANNEL-WARPX] PICMI preflight"
MC_DRY_RUN=1 python -u input.py
test -s inputs_3d_picmi
test -s resolved_parameters.json

python - <<'PY'
import json
from pathlib import Path

resolved = json.loads(Path("resolved_parameters.json").read_text(encoding="utf-8"))
inputs_text = Path("inputs_3d_picmi").read_text(encoding="utf-8")
components = resolved.get("laser_components", [])
if not components:
    raise SystemExit("preflight produced no laser components")
if abs(float(resolved.get("ellipticity_angle_deg", 0.0))) > 1.0e-12 and len(components) != 2:
    raise SystemExit("elliptical polarization did not produce two laser components")
names = [str(component.get("name", "")) for component in components]
if len(set(names)) != len(names) or any(not name for name in names):
    raise SystemExit(f"preflight produced invalid laser names: {names}")
missing_names = [name for name in names if name not in inputs_text]
if missing_names:
    raise SystemExit(f"generated inputs file is missing lasers: {missing_names}")
print(
    "[MULTICHANNEL-WARPX] preflight resolved "
    f"max_steps={resolved['max_steps']} lasers={','.join(names)}"
)
PY

if [[ -z "${SLURM_NTASKS:-}" ]]; then
    echo "[MULTICHANNEL-WARPX] SLURM_NTASKS is required for the production step" >&2
    exit 1
fi

echo "[MULTICHANNEL-WARPX] srun -n ${SLURM_NTASKS} python -u input.py"
srun -n "${SLURM_NTASKS}" python -u input.py

date --iso-8601=seconds
echo "[MULTICHANNEL-WARPX] DONE"
