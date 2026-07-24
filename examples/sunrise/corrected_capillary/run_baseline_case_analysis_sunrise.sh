#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 CASE_DIR" >&2
    exit 2
fi

CASE_DIR="$(readlink -f "$1")"
WORKFLOW_ROOT="${WORKFLOW_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
GUIDING_ANALYSIS_ROOT="${GUIDING_ANALYSIS_ROOT:-${HOME}/apps/src/guiding_analysis_module}"
GUIDING_ANALYSIS_VENV="${GUIDING_ANALYSIS_VENV:-${HOME}/apps/venvs/guiding-analysis-py310}"

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
export MPLCONFIGDIR="${CASE_DIR}/post/matplotlib-cache"
mkdir -p "${MPLCONFIGDIR}"
source "${GUIDING_ANALYSIS_VENV}/bin/activate"

FIELD_DIAG_DIR="$(python \
    "${WORKFLOW_ROOT}/examples/capillary_guiding/resolve_field_diag_dir.py" \
    "${CASE_DIR}")"
PARTICLE_DIAG_DIR="${CASE_DIR}/diags/plasma_electrons"
RESOLVED="${CASE_DIR}/resolved_parameters.json"

cd "${GUIDING_ANALYSIS_ROOT}"
python scripts/analyze_case.py \
    --diag "${FIELD_DIAG_DIR}" \
    --outdir "${CASE_DIR}" \
    --overwrite

readarray -t TARGET_MM < <(
    python - "${RESOLVED}" <<'PY'
import json
import sys
from pathlib import Path

resolved = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
targets = resolved["particle_diagnostic_targets"]
print(float(targets["plateau_exit"]["target_distance_m"]) * 1.0e3)
print(float(targets["capillary_exit"]["target_distance_m"]) * 1.0e3)
PY
)

PLATEAU_OUT="${CASE_DIR}/particle_analysis/plateau_exit"
CAPILLARY_OUT="${CASE_DIR}/particle_analysis/capillary_exit"

python scripts/analyze_particle_case.py \
    --diag "${PARTICLE_DIAG_DIR}" \
    --outdir "${PLATEAU_OUT}" \
    --species preionized_background_electrons \
    --which exit \
    --exit-kind plateau \
    --target-propagation-mm "${TARGET_MM[0]}" \
    --guiding-metrics "${CASE_DIR}/guiding_metrics.csv" \
    --overwrite \
    --spectrum-emin-mev 5 \
    --spectrum-log-y

python scripts/analyze_particle_case.py \
    --diag "${PARTICLE_DIAG_DIR}" \
    --outdir "${CAPILLARY_OUT}" \
    --species preionized_background_electrons \
    --which exit \
    --exit-kind capillary \
    --target-propagation-mm "${TARGET_MM[1]}" \
    --guiding-metrics "${CASE_DIR}/guiding_metrics.csv" \
    --overwrite \
    --spectrum-emin-mev 5 \
    --spectrum-log-y

python -c 'import imageio.v2, imageio_ffmpeg; print("[ANIMATION] ffmpeg=" + imageio_ffmpeg.get_ffmpeg_exe())'

python "${WORKFLOW_ROOT}/examples/sunrise/corrected_capillary/animate_guiding_fields.py" \
    --diag "${FIELD_DIAG_DIR}" \
    --outdir "${CASE_DIR}/animations" \
    --stride 1 \
    --fps 8

python "${WORKFLOW_ROOT}/examples/sunrise/corrected_capillary/validate_animations.py" \
    --output "${CASE_DIR}/animations/validation.json" \
    --minimum-frames 2 \
    "${CASE_DIR}/animations/eperp2.mp4" \
    "${CASE_DIR}/animations/ez_wake.mp4"

for required in \
    "${CASE_DIR}/guiding_metrics.csv" \
    "${CASE_DIR}/guiding_singlecase_score.csv" \
    "${CASE_DIR}/plots/laser_waist_rms.png" \
    "${CASE_DIR}/plots/laser_a0_peak.png" \
    "${CASE_DIR}/plots/guiding_summary_multipanel.png" \
    "${PLATEAU_OUT}/particle_summary.csv" \
    "${PLATEAU_OUT}/particle_soft50_curves.csv" \
    "${PLATEAU_OUT}/particle_acceptance_curves.csv" \
    "${CAPILLARY_OUT}/particle_summary.csv" \
    "${CAPILLARY_OUT}/particle_soft50_curves.csv" \
    "${CAPILLARY_OUT}/particle_acceptance_curves.csv" \
    "${CASE_DIR}/animations/eperp2.mp4" \
    "${CASE_DIR}/animations/ez_wake.mp4" \
    "${CASE_DIR}/animations/animation_frame_metrics.csv" \
    "${CASE_DIR}/animations/validation.json"
do
    [[ -s "${required}" ]] || {
        echo "ERROR: missing or empty reduced output: ${required}" >&2
        exit 1
    }
done

echo "[CLPU-BASELINE-ANALYSIS] guiding, dual particle exits and animations are complete"
