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
fake full workflow integration test from simulation markers to cleanup execute
thin case-local SUNRISE WarpX runner wrapper
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

Latest local suite result after the fake full workflow integration test and
case-local SUNRISE WarpX runner work:

```text
Ran 80 tests
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

## Current execution philosophy

The previous handoff described the next step as a thin simulation wrapper plus a
thin SLURM array wrapper, with raw validation, analysis, reduced validation, and
cleanup left as separate later jobs.

That has been refined.

The current production direction is a managed case-local cycle:

```text
case-local SLURM array task
  -> mark_sim_submitted
  -> mark_sim_running
  -> run external WarpX/PyWarpX simulation
  -> mark_sim_done or mark_sim_failed
  -> validate_raw_case
  -> analyze_case
  -> mark_raw_delete_eligible
  -> cleanup_raw_case --dry-run
  -> optionally cleanup_raw_case --execute
```

The phase boundaries remain explicit and transactional, but they do not have to
map to separate SLURM jobs when the later phases are cheap compared with the
simulation.

The reason is practical:

* WarpX/PyWarpX simulation dominates walltime.
* Raw validation, analysis/reduced validation, cleanup eligibility, and cleanup
  dry-run are expected to be much shorter for the current campaign class.
* Launching a separate SLURM array for every cheap case-local phase adds
  unnecessary scheduler overhead.
* A resident parent orchestrator is explicitly not wanted.

There must still be no parent job sitting idle in a long partition. No job should
live in T48H just to wait, poll, or coordinate. The longest job should normally
be the simulation/case-cycle array task, typically in T6H or T12H depending on
the campaign.

A short launcher that submits a job and exits is acceptable. A resident
controller is not.

## Case-local cycle responsibilities

The case-local cycle wrapper should:

```text
1. select CASE_ID / CASE_NAME from cases.tsv using SLURM_ARRAY_TASK_ID
2. use campaign-workflow-py310 for workflow CLIs
3. call mark_sim_submitted
4. call mark_sim_running
5. delegate simulation to examples/sunrise/run_warpx_case_sunrise.sh
6. if simulation fails, call mark_sim_failed and stop that case
7. if simulation succeeds, call mark_sim_done
8. call validate_raw_case
9. call analyze_case
10. call mark_raw_delete_eligible
11. call cleanup_raw_case --dry-run
12. optionally call cleanup_raw_case --execute only with explicit confirmation
```

The wrapper must keep logs readable and phase-separated.

It must not:

```text
edit WarpX/PyWarpX physics inputs
hard-code capillary/guiding/ionization physics
run BO/MORBO logic
delete anything except through campaign_workflow.cli.cleanup_raw_case --execute
run cleanup execute without explicit confirmation
use rm -rf
delete directories
delete files outside CASE_DIR
```

Simulation completion alone still never authorizes cleanup. Cleanup remains safe
only after:

```text
raw validation
reduced validation
raw delete eligibility
cleanup dry-run manifest
explicit cleanup execute confirmation
```

## Campaign-wide jobs

Some operations remain campaign-wide because they are intrinsically global:

```text
storage_snapshot
optimizer_tick
objective aggregation
candidate proposal
campaign summary reports
```

For future BO/MORBO workflows, the optimizer tick is expected to run globally
after one or more case-local cycles have produced validated reduced outputs.

The optimizer tick may:

```text
read validated reduced outputs
build observations
decide whether enough new data exists
compute objectives/scores
propose new candidates
submit or prepare a new candidate batch
```

The optimizer tick must not become a resident daemon.

## Implemented SUNRISE script status

Already implemented and committed:

```text
examples/sunrise/run_warpx_case_sunrise.sh
```

This is the case-local WarpX/PyWarpX runner based on the real SUNRISE
`submit_top10_particles_array.sh` pattern.

It does:

```text
cd CASE_DIR
source case.env
mkdir -p logs diags checkpoints post
skip if existing HDF5 diagnostics are found
load warpx-26.05-py314 environment
run CAP_DRY_RUN=1 python input.py 2
run srun -n "$SLURM_NTASKS" python input.py 2
write case-local logs/run_info
```

It does not call workflow validation, analysis, cleanup, optimizer logic, or edit
physics inputs.

Also implemented and tested:

```text
fake full workflow integration test from simulation markers to cleanup execute
```

Latest known local result:

```text
Ran 80 tests
OK
```

## Next immediate task

Adapt the SUNRISE SLURM scripts minimally to the new case-local cycle philosophy.

The next main implementation target is:

```text
examples/sunrise/submit_case_cycle_array.sh
```

or an equivalent name.

It should wrap one full case-local cycle:

```text
Created
-> Submitted
-> Running
-> Sim_done / Failed
-> Raw_validated / Validation_failed
-> Reduced_validated / Analysis_failed
-> Raw_delete_eligible
-> cleanup dry-run manifest
-> optional Raw_deleted
```

The first implementation should not include optimizer/MORBO logic.

The first implementation should include static `unittest` coverage checking that
the script:

```text
exists
uses bash strict mode
selects CASE_ID / CASE_NAME from cases.tsv
calls mark_sim_submitted
calls mark_sim_running
calls mark_sim_done
calls mark_sim_failed
delegates simulation to run_warpx_case_sunrise.sh
calls validate_raw_case
calls analyze_case
calls mark_raw_delete_eligible
calls cleanup_raw_case --dry-run
only calls cleanup_raw_case --execute behind explicit confirmation
does not contain rm -rf
does not run optimizer/MORBO
does not edit physics inputs
does not mix guiding-analysis-py310 into the workflow layer
```

## Real campaign direction

The next real campaign should be the campaign Juan actually wants to run, not a
toy campaign created only for testing.

Juan will prepare:

```text
WarpX/PyWarpX input template
case list / cases.tsv
case.env files
campaign.json
diagnostics contract
updated guiding/particle/ionization analysis module if needed
```

The workflow side should ensure:

```text
state initialization
case-local cycle execution
raw validation
external analysis invocation
reduced output validation
safe cleanup eligibility
cleanup dry-run manifest
optional cleanup execute
clear logs and evidence for failures
```

The existing `top10_particles` campaign remains useful as a preserved real raw
HDF5 corpus, but should still not be used for destructive cleanup unless Juan
explicitly decides otherwise.

## Not yet done

Still not implemented:

```text
managed case-local SLURM cycle wrapper
static tests for managed case-local SLURM cycle wrapper
real campaign launch using the managed case-local cycle
retry policy Failed -> Retryable -> Submitted
scheduler polling
optimizer tick
automatic candidate proposal/submission
MORBO/BO integration
ionization campaign
```

## Next chat priority

Start from the current repo and handoff.

First inspect:

```text
docs/SIMULATION_MODULE_CONTRACT.md
docs/NEXT_CHAT_HANDOFF.md
campaign_workflow/cli/mark_sim_submitted.py
campaign_workflow/cli/mark_sim_running.py
campaign_workflow/cli/mark_sim_failed.py
campaign_workflow/cli/mark_sim_done.py
campaign_workflow/cli/validate_raw_case.py
campaign_workflow/cli/analyze_case.py
campaign_workflow/cli/mark_raw_delete_eligible.py
campaign_workflow/cli/cleanup_raw_case.py
examples/sunrise/run_warpx_case_sunrise.sh
tests/test_full_fake_workflow.py
tests/test_sunrise_warpx_runner_script.py
```

Then propose the smallest safe implementation of the managed case-local SLURM
cycle wrapper.

Do not implement a parent orchestrator.

Do not implement a campaign-wide optimizer tick yet.

Do not modify WarpX/PyWarpX physics inputs.

Do not prepare the ionization campaign inside `campaign-workflow`; Juan will
prepare the physical input/template and case definitions separately.

## Latest completed phase — explicit case directory materialization

A new generic workflow phase has been implemented and committed:

```text
campaign_workflow/core/case_dirs.py
campaign_workflow/cli/create_case_dirs.py
tests/test_create_case_dirs.py
```

The new CLI is:

```bash
python -m campaign_workflow.cli.create_case_dirs \
  --campaign-root . \
  --dry-run \
  --verbose
```

and in write mode:

```bash
python -m campaign_workflow.cli.create_case_dirs \
  --campaign-root . \
  --verbose
```

This phase creates the case directory layout from `cases.tsv`, before `init_case_states`.

The intended bootstrap sequence is now:

```bash
python -m campaign_workflow.cli.create_case_dirs \
  --campaign-root . \
  --dry-run \
  --verbose

python -m campaign_workflow.cli.create_case_dirs \
  --campaign-root . \
  --verbose

python -m campaign_workflow.cli.init_case_states \
  --campaign-root . \
  --verbose
```

`create_case_dirs` is intentionally generic. It only knows about campaign configuration, case manifests, case names, path safety, and standard case directories.

It creates:

```text
CASE_DIR/
├── logs/
├── post/
├── manifests/
├── locks/
├── diags/
└── checkpoints/
```

It does not create or modify:

```text
state.json
validation.json
cases.tsv
campaign.json
input_template.py
input.py
case.env
raw diagnostics
reduced outputs
```

It also does not implement:

```text
simulation execution
analysis execution
SLURM submission
WarpX/PyWarpX input preparation
cases.tsv -> environment variable mapping
BO/MORBO
cleanup
```

The previous manual SUNRISE prototype that materialized cases must only be treated as context for what not to put in the generic core. That script mixed directory creation with copying `input_template.py`, generating `case.env`, and physical/capillary-specific `CAP_*` environment variables. That logic is intentionally excluded from `create_case_dirs`.

Latest known test status after this phase:

```text
python -m unittest discover -s tests -p "test_*.py"
```

Result:

```text
OK
```

## Next immediate task

Deploy or pull the latest committed `campaign-workflow` repo on SUNRISE and test the new explicit case directory materialization phase on a real campaign root.

Expected operational sequence on SUNRISE:

```bash
cd /path/to/CAMPAIGN_ROOT

python -m campaign_workflow.cli.create_case_dirs \
  --campaign-root . \
  --dry-run \
  --verbose

python -m campaign_workflow.cli.create_case_dirs \
  --campaign-root . \
  --verbose

python -m campaign_workflow.cli.init_case_states \
  --campaign-root . \
  --verbose
```

After running this, inspect the resulting campaign layout before touching simulation execution or SLURM.

The next development step after this bootstrap check is likely a separate, explicit, campaign-specific preparation layer if needed. That future layer may eventually handle things like:

```text
copying or linking input_template.py
creating case-local input.py
generating case.env
mapping selected cases.tsv columns into environment variables
```

But that must not be folded into `create_case_dirs`.

Do not implement yet:

```text
simulation execution
analysis/guiding logic
particle reduction
ionization-specific logic
SLURM orchestration
BO/MORBO
cleanup extensions
```

Do not modify WarpX/PyWarpX physics inputs inside `campaign-workflow`.