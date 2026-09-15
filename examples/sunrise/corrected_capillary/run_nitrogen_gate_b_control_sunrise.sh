#!/usr/bin/env bash
#SBATCH --job-name=clpu_n2_gate_b
#SBATCH --partition=T1H
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=01:00:00
set -Eeuo pipefail
trap 'echo "[CLPU-N2-GATE-B-CONTROL] ERROR at line \${LINENO}: \${BASH_COMMAND}" >&2' ERR
: "\${CW_OPTIMIZATION_ROOT:?missing CW_OPTIMIZATION_ROOT}"
: "\${CW_GATE_ITERATION:?missing CW_GATE_ITERATION}"
: "\${CW_WORKFLOW_ROOT:?missing CW_WORKFLOW_ROOT}"
if [[ -z "\${SLURM_JOB_ID:-}" ]]; then echo "gate must run as Slurm job" >&2; exit 2; fi
ROOT="$(cd "\${CW_OPTIMIZATION_ROOT}" && pwd -P)"
WF="$(cd "\${CW_WORKFLOW_ROOT}" && pwd -P)"
GA="\${GUIDING_ANALYSIS_ROOT:-\${HOME}/apps/src/guiding_analysis_module-clpu-n2-exact-exit}"
OPT="\${CLPU_N2_OPTIMIZER_ROOT:-\${HOME}/apps/src/campaign-optimizer-clpu-n2-gate-a}"
GA="$(cd "\${GA}" && pwd -P)"; OPT="$(cd "\${OPT}" && pwd -P)"
ITERATION="$((10#\${CW_GATE_ITERATION}))"
ITER="$(printf '%s/iterations/iter_%03d' "\${ROOT}" "\${ITERATION}")"
GATE_B="$(printf '%s/provenance/clpu_n2_gate_b_iter_%03d.json' "\${ROOT}" "\${ITERATION}")"
AUDIT="$(printf '%s/provenance/gate_b_iter_%03d_%s' "\${ROOT}" "\${ITERATION}" "$(date -u +%Y%m%dT%H%M%SZ)")"
for required in "\${ITER}/cases.tsv" "\${WF}/examples/sunrise/corrected_capillary/input_template.py" "\${WF}/examples/sunrise/corrected_capillary/validate_nitrogen_gate_b.py"; do
  [[ -e "\${required}" ]] || { echo "missing \${required}" >&2; exit 1; }
done
[[ ! -e "\${GATE_B}" ]] || { echo "receipt already exists: \${GATE_B}" >&2; exit 2; }
if find "\${ITER}" -type f \( -name '*.h5' -o -name '*.hdf5' \) -print -quit | grep -q .; then echo "HDF5 exists before PICMI" >&2; exit 1; fi
if find "\${ITER}" -path '*/post/sim_submitted.json' -type f -print -quit | grep -q .; then echo "case already submitted" >&2; exit 1; fi
if ! type module >/dev/null 2>&1; then source /etc/profile.d/modules.sh; fi
module purge; module use "\${HOME}/apps/modules"
module load Git/2.41.0 GCC/12.1.0 Python/3.14.3 OpenBLAS/0.3.31 warpx/26.05-gcc12-openmpi413-all-dims
[[ "$(git -C "\${GA}" rev-parse HEAD)" == "e809b43d2071e5fa5cb39de2613f3e9d170bea84" ]]
[[ "$(git -C "\${OPT}" rev-parse HEAD)" == "73dab76305f547581c57b70a900706374c929141" ]]
for repo in "\${WF}" "\${GA}" "\${OPT}"; do [[ -z "$(git -C "\${repo}" status --porcelain)" ]] || { echo "dirty checkout: \${repo}" >&2; exit 1; }; done
export PYTHON314_ROOT=/APPS/centos7/centos79/software/Compiler/GCC-12.1/Python/3.14.3
export LD_LIBRARY_PATH="\${PYTHON314_ROOT}/lib:\${LD_LIBRARY_PATH:-}"
source "\${HOME}/apps/venvs/warpx-26.05-py314/bin/activate"
export PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export PYTHONPATH="\${WF}" WFLOW_SRC="\${WF}" CAP_CORRECTED_INPUT_TEMPLATE="\${WF}/examples/sunrise/corrected_capillary/input_template.py"
export GUIDING_ANALYSIS_ROOT="\${GA}" CLPU_N2_OPTIMIZER_ROOT="\${OPT}"
mkdir -p "\${AUDIT}"; exec > >(tee "\${AUDIT}/gate_b.log") 2>&1
{ echo "SLURM_JOB_ID=\${SLURM_JOB_ID}"; echo "ITERATION=\${ITERATION}"; python --version; python - <<'PY'
import pywarpx
print("pywarpx=" + str(pywarpx.__file__))
PY
} > "\${AUDIT}/runtime.txt"
while IFS=$'\t' read -r case_id case_name; do
  case_dir="\${ITER}/\${case_name}"; log="\${AUDIT}/picmi_case_\${case_id}.log"
  ( cd "\${case_dir}"; source ./case.env; CAP_DRY_RUN=1 python ./input.py 2 ) > "\${log}" 2>&1
  grep -F "[CLPU] nitrogen PICMI preflight completed; simulation not started" "\${log}" >/dev/null
  echo "PICMI_PREFLIGHT_OK case_id=\${case_id} case_name=\${case_name}"
done < <(python - "\${ITER}/cases.tsv" <<'PY'
import csv, sys
from pathlib import Path
with Path(sys.argv[1]).open(newline="", encoding="utf-8-sig") as stream:
    for row in csv.DictReader(stream, delimiter="\t"):
        print(f"{row['CASE_ID']}\t{row['CASE_NAME']}")
PY
)
python "\${WF}/examples/sunrise/corrected_capillary/validate_nitrogen_gate_b.py" validate \
 --optimization-root "\${ROOT}" --campaign-root "\${ITER}" --workflow-root "\${WF}" \
 --guiding-analysis-root "\${GA}" --optimizer-root "\${OPT}" --output "\${GATE_B}"
if find "\${ITER}" -type f \( -name '*.h5' -o -name '*.hdf5' \) -print -quit | grep -q .; then echo "PICMI produced HDF5" >&2; exit 1; fi
echo "GATE_B_STATUS=pass"
echo "GATE_B_RECEIPT=\${GATE_B}"
echo "NO_WARPX_EVOLUTION=1"
echo "NO_SBATCH_FROM_COMPUTE=1"
