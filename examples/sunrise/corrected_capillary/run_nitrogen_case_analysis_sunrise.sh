#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 CASE_DIR" >&2
    exit 2
fi

CASE_DIR="$(readlink -f "$1")"
WORKFLOW_ROOT="${WORKFLOW_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
GUIDING_ANALYSIS_ROOT="${GUIDING_ANALYSIS_ROOT:-${HOME}/apps/src/guiding_analysis_module-clpu-n2-exact-exit}"
GUIDING_ANALYSIS_VENV="${GUIDING_ANALYSIS_VENV:-${HOME}/apps/venvs/guiding-analysis-py310}"
GUIDING_ANALYSIS_REQUIRED_COMMIT="${GUIDING_ANALYSIS_REQUIRED_COMMIT:-e809b43d2071e5fa5cb39de2613f3e9d170bea84}"

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

[[ -d "${GUIDING_ANALYSIS_ROOT}/.git" || -f "${GUIDING_ANALYSIS_ROOT}/.git" ]] || {
    echo "ERROR: GUIDING_ANALYSIS_ROOT is not a git checkout: ${GUIDING_ANALYSIS_ROOT}" >&2
    exit 1
}

git -C "${GUIDING_ANALYSIS_ROOT}" cat-file -e "${GUIDING_ANALYSIS_REQUIRED_COMMIT}^{commit}" 2>/dev/null || {
    echo "ERROR: guiding analysis checkout does not contain required commit ${GUIDING_ANALYSIS_REQUIRED_COMMIT}" >&2
    exit 1
}

git -C "${GUIDING_ANALYSIS_ROOT}" merge-base --is-ancestor \
    "${GUIDING_ANALYSIS_REQUIRED_COMMIT}" HEAD || {
    echo "ERROR: guiding analysis HEAD does not include required exact-exit/multispecies commit ${GUIDING_ANALYSIS_REQUIRED_COMMIT}" >&2
    exit 1
}

FIELD_DIAG_DIR="$(python \
    "${WORKFLOW_ROOT}/examples/capillary_guiding/resolve_field_diag_dir.py" \
    "${CASE_DIR}")"
PARTICLE_DIAG_DIR="${CASE_DIR}/diags/plasma_electrons"
RESOLVED="${CASE_DIR}/resolved_parameters.json"

[[ -s "${RESOLVED}" ]] || {
    echo "ERROR: missing resolved parameters: ${RESOLVED}" >&2
    exit 1
}
[[ -d "${PARTICLE_DIAG_DIR}" ]] || {
    echo "ERROR: missing particle diagnostic directory: ${PARTICLE_DIAG_DIR}" >&2
    exit 1
}

cd "${GUIDING_ANALYSIS_ROOT}"
python scripts/analyze_case.py \
    --diag "${FIELD_DIAG_DIR}" \
    --outdir "${CASE_DIR}" \
    --overwrite

PARTICLE_SPECIES="preionized_background_electrons,nitrogen_ionized_electrons"
PLATEAU_OUT="${CASE_DIR}/particle_analysis/plateau_exit"
CAPILLARY_OUT="${CASE_DIR}/particle_analysis/capillary_exit"

run_exit_analysis() {
    local exit_kind="$1"
    local outdir="$2"

    python scripts/analyze_particle_case.py \
        --diag "${PARTICLE_DIAG_DIR}" \
        --outdir "${outdir}" \
        --species "${PARTICLE_SPECIES}" \
        --which exit \
        --exit-kind "${exit_kind}" \
        --resolved-parameters "${RESOLVED}" \
        --guiding-metrics "${CASE_DIR}/guiding_metrics.csv" \
        --overwrite \
        --spectrum-emin-mev 5 \
        --spectrum-log-y
}

run_exit_analysis plateau "${PLATEAU_OUT}"
run_exit_analysis capillary "${CAPILLARY_OUT}"

python "${WORKFLOW_ROOT}/examples/sunrise/corrected_capillary/validate_nitrogen_particle_outputs.py" \
    --particle-analysis-root "${CASE_DIR}/particle_analysis" \
    --resolved-parameters "${RESOLVED}" \
    --output "${CASE_DIR}/particle_analysis/validation.json"

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
    "${CASE_DIR}/particle_analysis/validation.json" \
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

echo "[CLPU-N2-ANALYSIS] guiding, exact dual particle exits, multispecies scopes and animations are complete"
