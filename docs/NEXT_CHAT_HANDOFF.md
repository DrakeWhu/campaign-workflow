# Next chat handoff — campaign-workflow

## Purpose

`campaign-workflow` is a generic campaign coordination layer for simulation campaigns:

```text
simulation -> raw validation -> analysis -> reduced validation -> storage snapshot -> cleanup eligibility -> cleanup dry-run manifest -> cleanup execute
```

The workflow is generic. Capillary guiding is the first real production example, not the architecture.

Development is done locally under Git and deployed to SUNRISE through Git checkout/pull. OpenCode is not used for this workflow.

## Non-negotiable rules

* Do not edit WarpX/PyWarpX physics inputs unless explicitly requested.
* Do not invent SLURM/PyWarpX submitters from scratch if working scripts exist; ask for the current files first.
* Do not generate opaque patches, ZIPs, or downloadable code artifacts.
* All operational scripts/commands must be visible, auditable, and copyable.
* Use `unittest`, not `pytest`.
* Standard local test command:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

* Keep virtual environments separate:

```text
campaign-workflow-py310    -> workflow orchestration, states, validation, manifests, storage, cleanup
guiding-analysis-py310     -> guiding / particle analysis
warpx-26.05-py314          -> PyWarpX / WarpX simulation execution
```

* `cases.tsv` is immutable after campaign start.
* State alone is never sufficient permission to delete raw data.
* Cleanup must never delete directories.
* Cleanup must never use `rm -rf` on campaign data.
* Cleanup must never delete files outside `CASE_DIR`.
* Cleanup execute must delete only files listed in a validated dry-run manifest.

## Repository / deployment

Local development path:

```text
C:\Users\juan.rodriguez-perez\OneDrive - ELI ERIC\Desktop\campaign-workflow
```

GitHub remote:

```text
git@github.com:DrakeWhu/campaign-workflow.git
```

SUNRISE checkout:

```text
~/apps/src/campaign-workflow
```

SUNRISE checkout should track `master`. SUNRISE has an old Git, so prefer:

```bash
cd ~/apps/src/campaign-workflow
git fetch origin
git checkout master
git merge --ff-only origin/master
```

or, if supported:

```bash
git pull --ff-only origin master
```

## SUNRISE environment

System Python is too old:

```text
/usr/bin/python3 -> Python 3.6.8
```

Working workflow environment:

```bash
source ~/apps/env/campaign-workflow.sh
```

Equivalent stack:

```text
GCC/12.1.0 + Python/3.10.12 + ~/apps/venvs/campaign-workflow-py310
```

Important `h5py` note:

```bash
python -m pip uninstall -y h5py numpy
python -m pip install --only-binary=:all: "numpy<2" "h5py==3.10.0"
```

This was needed because a plain `pip install h5py` tried to build a newer h5py against an incompatible system HDF5.

## Core workflow concepts

Per campaign:

```text
campaign.json
cases.tsv
snapshots/
```

Per case:

```text
CASE_DIR/
  state.json
  validation.json
  locks/
  manifests/
  post/
  logs/
  raw diagnostics
  reduced outputs
```

The workflow core knows about:

```text
case manifests
case directories
states
validation documents
locks
safe path checks
atomic JSON writes
storage accounting
safe transition rules
cleanup manifests
```

The workflow core must not know about:

```text
capillary radius
laser case
plasma density
guiding physics
particle physics details
WarpX-specific physical parameters
```

Campaign-specific behavior belongs in `campaign.json`, wrappers, and external analysis/simulation modules.

## Current state machine

Main states:

```text
Created
Submitted
Running
Sim_done
Raw_validated
Analyzing
Reduced_validated
Raw_delete_eligible
Raw_deleted
```

Side/failure states:

```text
Failed
Retryable
Stale
Quarantined
Validation_failed
Analysis_failed
Cleanup_failed
Disk_wait
```

Important naming decision: use generic states like `Raw_validated` and `Reduced_validated`, not format-specific states like `H5_validated` or `CSV_validated`.

## Completed capabilities

Implemented and tested:

```text
state initialization
simulation completion backfill
simulation lifecycle contract documentation
simulation lifecycle transition helpers
simulation marker CLIs:
  mark_sim_submitted
  mark_sim_running
  mark_sim_failed
simulation done marker writing through mark_sim_done
Created -> Submitted -> Running -> Sim_done lifecycle tests
Running -> Failed lifecycle tests
Created -> Sim_done legacy/backfill path preserved
raw validation
minimal openpmd_hdf5/HDF5 readability validation
reduced CSV validation
legacy reduced-only validation
external analysis command adapter
analysis stdout/stderr capture
explicit analysis rerun from Raw_delete_eligible
explicit analysis rerun from Reduced_validated
storage snapshot
raw delete eligibility
cleanup dry-run manifest
cleanup execute local/tests
```

Cleanup execute exists in code/tests but has intentionally not been run on the real `top10_particles` campaign.

## Simulation lifecycle status

A stable simulation contract document now exists:

```text
docs/SIMULATION_MODULE_CONTRACT.md
```

The current simulation lifecycle is intentionally marker-based and does not yet launch WarpX directly.

Implemented marker CLIs:

```bash
python -m campaign_workflow.cli.mark_sim_submitted
python -m campaign_workflow.cli.mark_sim_running
python -m campaign_workflow.cli.mark_sim_failed
python -m campaign_workflow.cli.mark_sim_done
```

Expected state paths:

```text
Created   -> Submitted
Submitted -> Running
Running   -> Sim_done
Running   -> Failed
Created   -> Sim_done     # legacy/backfill adoption path
```

Marker files:

```text
CASE_DIR/post/sim_submitted.json
CASE_DIR/post/sim_running.json
CASE_DIR/post/sim_done.json
CASE_DIR/post/sim_failed.json
```

`mark_sim_done` now writes `post/sim_done.json` in addition to updating state and simulation evidence in `validation.json`.

Simulation lifecycle evidence alone never authorizes cleanup. After any simulation marker command:

```text
validation.cleanup.cleanup_allowed = false
```

The simulation marker layer does not:

```text
run WarpX
run PyWarpX
submit SLURM jobs
validate raw diagnostics
run analysis
validate reduced outputs
delete anything
edit physics inputs
```

The current tests cover marker creation, state transitions, validation evidence, dry-run behavior, invalid transitions, and no-cleanup invariants.

Standard test command:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

Latest local suite result after simulation marker work:

```text
Ran 75 tests
OK
```

Some symlink-related tests may be skipped on Windows depending on local permissions.

## Real SUNRISE simulation context collected

Current real campaign:

```text
/gpfs/home/jrodriguez/warpx_runs/capillaries_bo_top10_particles
```

Current campaign-local simulation config includes:

```json
"simulation": {
  "backend": "warpx_picmi",
  "scheduler": "slurm",
  "input_script": "input.py",
  "completion_marker": "post/sim_done.json",
  "failure_marker": "post/sim_failed.json"
}
```

Existing working SLURM script for the top10 particle campaign:

```text
submit_top10_particles_array.sh
```

It is a real working script, not architecture doctrine. Do not invent replacements blindly.

Current execution pattern:

```bash
sbatch submit_top10_particles_array.sh
```

Inside the SLURM array task, the script:

```text
selects a case row from cases.tsv
enters CASE_DIR
sources CASE_DIR/case.env
creates logs/ diags/ checkpoints/ post/
checks for existing HDF5 diagnostics and skips if present
loads the WarpX/PyWarpX environment
runs CAP_DRY_RUN=1 python input.py 2 as preflight
runs srun -n "${SLURM_NTASKS}" python input.py 2
```

The real simulation command is:

```bash
srun -n "${SLURM_NTASKS}" python input.py 2
```

The real WarpX/PyWarpX environment is approximately:

```bash
module purge
module use ~/apps/modules

module load GCC/12.1.0
module load Python/3.14.3
module load OpenBLAS/0.3.31
module load warpx/26.05-gcc12-openmpi413-all-dims

export PYTHON314_ROOT=/APPS/centos7/centos79/software/Compiler/GCC-12.1/Python/3.14.3
export LD_LIBRARY_PATH="${PYTHON314_ROOT}/lib:${LD_LIBRARY_PATH:-}"

source ~/apps/venvs/warpx-26.05-py314/bin/activate
```

There is also a helper:

```text
~/apps/env/load_sunrise_warpx_py_stack.sh
```

but it may need verification before being used operationally.

Existing SLURM logs live both at campaign level and case level:

```text
CAMPAIGN_ROOT/array_logs/
CASE_DIR/logs/
```

Case-local logs are preferred for workflow evidence.

Example case tree contains:

```text
CASE_DIR/case.env
CASE_DIR/input.py
CASE_DIR/diags/diag1/*.h5
CASE_DIR/diags/electron_particles/openpmd/*.h5
CASE_DIR/logs/*.out
CASE_DIR/logs/*.err
CASE_DIR/run_info.txt
CASE_DIR/state.json
CASE_DIR/validation.json
CASE_DIR/post/
CASE_DIR/manifests/
```

The current `top10_particles` campaign should not be used for destructive cleanup. It remains the preserved real raw-HDF5 corpus.

## Next immediate task

The next task is to integrate the simulation marker CLIs into a thin SUNRISE execution path without turning SLURM into the workflow core.

Do not start by launching a full production campaign.

Recommended next step:

```text
Design a thin case-local WarpX runner wrapper plus a thin SLURM array wrapper.
```

The intended split is:

```text
SLURM array selector:
  - choose CASE_ID / CASE_NAME from cases.tsv
  - call workflow marker CLIs from campaign-workflow-py310 where appropriate
  - delegate execution to case-local runner

case-local WarpX runner:
  - cd CASE_DIR
  - source case.env
  - load warpx-26.05-py314 environment
  - run CAP_DRY_RUN=1 python input.py 2
  - run srun -n "$SLURM_NTASKS" python input.py 2
  - keep raw outputs and logs inside CASE_DIR
```

The wrappers should be thin. No raw validation, analysis, reduced validation, cleanup, optimizer logic, or physics logic belongs inside the SLURM script.

The first operational target should be a disposable or very small integration campaign, not `top10_particles` cleanup.

## Next workflow goal

The desired complete integration path is:

```text
init_case_states
-> mark_sim_submitted
-> mark_sim_running
-> run WarpX/PyWarpX externally
-> mark_sim_done or mark_sim_failed
-> validate_raw_case
-> analyze_case
-> validate_reduced_case if needed
-> storage_snapshot
-> mark_raw_delete_eligible
-> cleanup_raw_case --dry-run
-> cleanup_raw_case --execute
```

Cleanup execute should only be tested on a disposable/small integration campaign, not on `top10_particles`.

## Not yet done

Still not implemented:

```text
thin SUNRISE simulation wrapper
thin SLURM array wrapper using simulation marker CLIs
fake end-to-end full campaign test from simulation to cleanup
real disposable SUNRISE integration campaign
retry policy Failed -> Retryable -> Submitted
scheduler polling
automatic job submission from optimizer
MORBO/BO integration
ionization campaign
```

## Next chat priority

Start from the current repo and handoff.

First inspect:

```text
docs/SIMULATION_MODULE_CONTRACT.md
campaign_workflow/cli/mark_sim_submitted.py
campaign_workflow/cli/mark_sim_running.py
campaign_workflow/cli/mark_sim_failed.py
campaign_workflow/cli/mark_sim_done.py
campaign_workflow/simulation/
tests/test_simulation_lifecycle.py
tests/test_simulation_marker_clis.py
tests/test_mark_sim_done.py
```

Then propose the smallest safe integration step.

Expected next implementation options:

```text
A. Add a fake full workflow integration test from simulation markers to cleanup.
B. Add a thin case-local SUNRISE WarpX runner wrapper, based on the real submit_top10_particles_array.sh.
C. Add a thin SLURM array wrapper that calls lifecycle marker CLIs.
```

Do not choose B or C without preserving the existing working SUNRISE behavior and keeping wrappers visible/auditable.


## Later integration campaign

Only after analysis and simulation integration are stable, prepare a small ionization integration campaign:

```text
~20 cases
two gas-mixture families
fields diagnostic
plasma_electrons diagnostic
ionized_electrons diagnostic
possibly ion diagnostics if needed
limited dumps
full workflow validation
storage snapshot
cleanup dry-run
```

Goal: integration test, not optimization yet.
