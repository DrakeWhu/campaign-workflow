#!/usr/bin/env bash
#SBATCH --job-name=hex_ei_base
#SBATCH --partition=novas
#SBATCH --nodes=2
#SBATCH --ntasks=48
#SBATCH --mem=100G
#SBATCH --time=12:00:00
#SBATCH --hint=nomultithread
#SBATCH --output=hex_ei_base_%j.out
#SBATCH --error=hex_ei_base_%j.err

set -Eeuo pipefail

# Resolve repository path robustly under sbatch.
# Do not rely on BASH_SOURCE[0], because SLURM may execute a spool copy of
# the submit script under /var/spool/slurm.
DEFAULT_REPO="$HOME/apps/src/campaign-workflow"
REPO_DIR="${CAMPAIGN_WORKFLOW_REPO:-$DEFAULT_REPO}"

# If submitted from a checkout, prefer that checkout.
if [[ -n "${SLURM_SUBMIT_DIR:-}" ]] && [[ -f "$SLURM_SUBMIT_DIR/examples/lynx/multichannel/hexagonal_electrons_ions_baseline/input.py" ]]; then
    REPO_DIR="$SLURM_SUBMIT_DIR"
fi

INPUT_SRC="$REPO_DIR/examples/lynx/multichannel/hexagonal_electrons_ions_baseline/input.py"

if [[ ! -f "$INPUT_SRC" ]]; then
    echo "[hex_ei_base] missing input: $INPUT_SRC" >&2
    echo "[hex_ei_base] REPO_DIR=$REPO_DIR" >&2
    echo "[hex_ei_base] SLURM_SUBMIT_DIR=${SLURM_SUBMIT_DIR:-}" >&2
    exit 2
fi

source "$HOME/apps/env/lynx_clean_base.sh"

module load WarpX/26.03_lynx_cpu_3d_yee_openpmd_py311

export OMP_NUM_THREADS=1
export PYTHONNOUSERSITE=1

RUN_ROOT="${RUN_ROOT:-$HOME/warpx_runs/multichannel}"
RUN_DIR="$RUN_ROOT/hexagonal_electrons_ions_baseline_${SLURM_JOB_ID}"

mkdir -p "$RUN_DIR/post"
cp "$INPUT_SRC" "$RUN_DIR/input.py"

{
    echo "job_id=$SLURM_JOB_ID"
    echo "host=$(/usr/bin/hostname 2>/dev/null || echo unknown)"
    echo "date_start=$(date -Is)"
    echo "run_dir=$RUN_DIR"
    echo "input_src=$INPUT_SRC"
    echo "partition=${SLURM_JOB_PARTITION:-}"
    echo "ntasks=${SLURM_NTASKS:-}"
    echo "warpx_module=WarpX/26.03_lynx_cpu_3d_yee_openpmd_py311"
    echo "nodes=${SLURM_JOB_NUM_NODES:-}"
    echo "ntasks_per_node=${SLURM_NTASKS_PER_NODE:-}"
    echo "cpus_on_node=${SLURM_CPUS_ON_NODE:-}"
    echo "omp_num_threads=${OMP_NUM_THREADS:-}"
} | tee "$RUN_DIR/post/context.txt"

cd "$RUN_DIR"

python -m py_compile input.py

echo "[hex_ei_base] starting WarpX at $(date -Is)"
srun --nodes="$SLURM_JOB_NUM_NODES" --ntasks="$SLURM_NTASKS" python input.py
echo "[hex_ei_base] WarpX finished at $(date -Is)"

cat > "$RUN_DIR/post/sim_done.json" <<EOF_DONE
{
  "status": "done",
  "job_id": "${SLURM_JOB_ID}",
  "date_done": "$(date -Is)",
  "run_dir": "${RUN_DIR}"
}
EOF_DONE

echo "[hex_ei_base] running LFMetrics"
source "$HOME/apps/env/multichannel_lfmetrics_lynx.sh"

lfmetrics analyze-case \
    "$RUN_DIR" \
    --diagnostics-dir "3D" \
    --species electrons \
    --energy-threshold-MeV 5 \
    --output "$RUN_DIR/post/particle_summary.csv"

echo "[hex_ei_base] LFMetrics output:"
cat "$RUN_DIR/post/particle_summary.csv"

echo "[hex_ei_base] complete: $RUN_DIR"