# 3D multichannel honeycomb campaign on SUNRISE

This example connects the generic campaign lifecycle to the multichannel
optimizer and `multichannel-lfmetrics`:

```text
WarpX final frame
-> raw HDF5 validation
-> LFMetrics beam summary and plots
-> reduced-output validation
-> manifest-driven HDF5 cleanup
-> Sobol or MORBO proposal
```

The physical target begins at `z=40 um`; the laser antenna remains at `z=37
um`. The campaign scans 11 native parameters, including periodic honeycomb and
linear-polarization angles plus an ellipticity angle. Periodic angles are
encoded as sine/cosine pairs by the optimizer.

## Required repositories and environments

Expected SUNRISE checkouts:

```bash
export WORKFLOW_ROOT="${HOME}/src/campaign-workflow"
export OPTIMIZER_ROOT="${HOME}/src/campaign-optimizer"
```

The case-cycle environment must expose `campaign_workflow`. The optimizer
environment must expose `campaign_optimizer`, NumPy, pandas, SciPy and, for the
model phase, Torch/BoTorch. LFMetrics can be loaded with either
`LFMETRICS_ENV_SCRIPT` or this default venv:

```bash
export LFMETRICS_VENV="${HOME}/apps/venvs/multichannel-lfmetrics-py310"
```

Verify the optimizer environment before the first bootstrap. This prevents a
later model iteration from silently depending on an incomplete environment:

```bash
source "${HOME}/apps/env/campaign-optimizer.sh"

python - <<'PY'
import botorch
import numpy
import pandas
import scipy
import torch

print("numpy", numpy.__version__)
print("pandas", pandas.__version__)
print("scipy", scipy.__version__)
print("torch", torch.__version__)
print("botorch", botorch.__version__)
PY
```

## Bootstrap iteration 0

Create one optimization root on SUNRISE:

```bash
export OPT_ROOT="${HOME}/warpx_runs/multichannel_honeycomb_3d"
mkdir -p "${OPT_ROOT}/iterations" "${OPT_ROOT}/optimizer_runs" "${OPT_ROOT}/loop_logs"

cp "${WORKFLOW_ROOT}/examples/sunrise/multichannel/optimization.json" \
   "${OPT_ROOT}/optimization.json"
cp "${OPTIMIZER_ROOT}/examples/optimizer_multichannel_sunrise.json" \
   "${OPT_ROOT}/optimizer.json"
```

Build the reference plus Sobol indices 0 through 7:

```bash
source "${HOME}/apps/env/campaign-optimizer.sh"
cd "${OPT_ROOT}"

python -m campaign_optimizer.cli.run_iteration \
    --config "${OPT_ROOT}/optimizer.json" \
    --iteration 0 \
    --build-candidate-batch
```

Prepare and materialize the first campaign without submitting anything:

```bash
source "${HOME}/apps/env/campaign-workflow.sh"

python -m campaign_workflow.cli.prepare_batch_campaign \
    --candidate-batch "${OPT_ROOT}/optimizer_runs/iter_000/outputs/candidate_batch.tsv" \
    --batch-plan "${OPT_ROOT}/optimizer_runs/iter_000/outputs/batch_campaign_plan.json" \
    --template-campaign-root "${WORKFLOW_ROOT}/examples/sunrise/multichannel" \
    --output-campaign-root "${OPT_ROOT}/iterations/iter_000" \
    --campaign-name multichannel_honeycomb_3d_iter_000 \
    --dry-run

python -m campaign_workflow.cli.prepare_batch_campaign \
    --candidate-batch "${OPT_ROOT}/optimizer_runs/iter_000/outputs/candidate_batch.tsv" \
    --batch-plan "${OPT_ROOT}/optimizer_runs/iter_000/outputs/batch_campaign_plan.json" \
    --template-campaign-root "${WORKFLOW_ROOT}/examples/sunrise/multichannel" \
    --output-campaign-root "${OPT_ROOT}/iterations/iter_000" \
    --campaign-name multichannel_honeycomb_3d_iter_000 \
    --execute

python -m campaign_workflow.cli.materialize_cases \
    --campaign-root "${OPT_ROOT}/iterations/iter_000" \
    --dry-run \
    --verbose

python -m campaign_workflow.cli.materialize_cases \
    --campaign-root "${OPT_ROOT}/iterations/iter_000"

python -m campaign_workflow.cli.init_case_states \
    --campaign-root "${OPT_ROOT}/iterations/iter_000" \
    --dry-run \
    --verbose

python -m campaign_workflow.cli.init_case_states \
    --campaign-root "${OPT_ROOT}/iterations/iter_000"

python -m campaign_workflow.cli.init_case_states \
    --campaign-root "${OPT_ROOT}/iterations/iter_000" \
    --check

python -m campaign_workflow.cli.optimizer_tick \
    --optimization-root "${OPT_ROOT}" \
    --init-state
```

## PICMI 26.05 preflight

Run the reference input serially inside the WarpX environment. This writes
`inputs_3d_picmi` and `resolved_parameters.json`, but never calls `sim.step`:

```bash
cd "${OPT_ROOT}/iterations/iter_000/mc_i000_c000_reference"
source case.env

module purge
module use "${HOME}/apps/modules"
module load GCC/12.1.0 Python/3.14.3 OpenBLAS/0.3.31
module load warpx/26.05-gcc12-openmpi413-all-dims
source "${HOME}/apps/venvs/warpx-26.05-py314/bin/activate"

MC_DRY_RUN=1 python -u input.py
```

Before any production array, inspect that a nonzero ellipticity produces two
entries in `resolved_parameters.json` and two laser names in
`inputs_3d_picmi`.

## Reference pilot

Plan the reference-only array through the workflow so its case ID and SLURM job
ID are recorded in `optimization_state.json`:

```bash
python -m campaign_workflow.cli.optimizer_tick \
    --optimization-root "${OPT_ROOT}" \
    --iteration 0 \
    --action submit_iteration \
    --array-spec 0 \
    --workflow-root "${WORKFLOW_ROOT}" \
    --workflow-env "${HOME}/apps/env/campaign-workflow.sh" \
    --case-runner "${WORKFLOW_ROOT}/examples/sunrise/multichannel/run_warpx_multichannel_case_sunrise.sh" \
    --confirm-cleanup-execute \
    --job-name mc3d_ref \
    --optimization-config "${OPT_ROOT}/optimization.json" \
    --dry-run
```

After reviewing the plan, repeat the same command with `--execute` in place of
`--dry-run`. That is the first command in this guide that calls `sbatch`.

Measure `Elapsed`, `MaxRSS`, seconds per step and WarpX timers for this pilot
before releasing Sobol cases 1 through 8. Once the reference is reduced and its
raw HDF5 has been cleaned, use the same reviewed resource request for the
remaining initial design:

```bash
sacct -j JOB_ID \
    --format=JobID,JobName,State,Elapsed,AllocCPUS,MaxRSS,ExitCode
```

The static case-cycle request deliberately keeps the established
one-node/24-rank SUNRISE layout. Change ranks, memory or array concurrency only
from the measured pilot; the 3D input has 64 AMReX boxes at
`max_grid_size=64`, so rank scaling is not assumed from the previous 2D
campaign. The generic walltime guard remains disabled until the pilot provides
an empirical 3D seconds-per-step model; the hard free-space/quota guard is
active from the start.

Submit the remaining initial design as an explicit disjoint extension. Start
with `--dry-run`, then replace it with `--execute` after review:

```bash
python -m campaign_workflow.cli.optimizer_tick \
    --optimization-root "${OPT_ROOT}" \
    --iteration 0 \
    --action submit_iteration \
    --array-spec '1-8%2' \
    --allow-additional-cases \
    --workflow-root "${WORKFLOW_ROOT}" \
    --workflow-env "${HOME}/apps/env/campaign-workflow.sh" \
    --case-runner "${WORKFLOW_ROOT}/examples/sunrise/multichannel/run_warpx_multichannel_case_sunrise.sh" \
    --confirm-cleanup-execute \
    --job-name mc3d_sobol_a \
    --optimization-config "${OPT_ROOT}/optimization.json" \
    --dry-run
```

The workflow rejects overlap with case 0 and accumulates both SLURM job IDs and
all submitted case IDs for reconciliation.

After all iteration-0 jobs finish, reconcile it and materialize iteration 1
(Sobol indices 8 through 15) without submitting anything from inside the tick:

```bash
source "${HOME}/apps/env/campaign-workflow.sh"

python -m campaign_workflow.cli.optimizer_tick \
    --optimization-root "${OPT_ROOT}" \
    --action run_loop_once \
    --iteration 0 \
    --next-iteration 1 \
    --array-spec 0-8 \
    --workflow-root "${WORKFLOW_ROOT}" \
    --workflow-env "${HOME}/apps/env/campaign-workflow.sh" \
    --job-name-prefix mc3d \
    --optimization-config "${OPT_ROOT}/optimization.json" \
    --stop-after-materialization \
    --execute
```

## Finite chained iterations

After iteration 0 is complete and reconciled, the chain can use a nine-task
array. Iterations with only four MORBO candidates leave higher task IDs as
safe no-ops:

```bash
python "${WORKFLOW_ROOT}/examples/sunrise/submit_morbo_chain.py" \
    --optimization-root "${OPT_ROOT}" \
    --start-iteration 1 \
    --num-additional-iterations 4 \
    --array-spec '0-8%2' \
    --workflow-root "${WORKFLOW_ROOT}" \
    --workflow-env "${HOME}/apps/env/campaign-workflow.sh" \
    --optimization-config "${OPT_ROOT}/optimization.json" \
    --case-runner "${WORKFLOW_ROOT}/examples/sunrise/multichannel/run_warpx_multichannel_case_sunrise.sh" \
    --partition T6H \
    --time 06:00:00 \
    --nodes 1 \
    --ntasks 24 \
    --mem 64G \
    --tick-partition T6H \
    --tick-time 00:30:00 \
    --tick-nodes 1 \
    --tick-ntasks 1 \
    --tick-mem 8G
```

Without `--execute`, the submitter prints the complete finite chain and does
not call `sbatch`.
