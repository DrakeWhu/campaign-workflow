# Simulation module contract

## Purpose

`campaign-workflow` can coordinate simulation execution without owning the
physics input script or importing simulation-specific code.

The workflow owns:

* case state transitions;
* simulation lifecycle evidence;
* stdout/stderr log locations when it directly runs a command;
* marker files under `CASE_DIR/post/`;
* validation preconditions for later raw validation;
* cleanup blocking;
* generic scheduler metadata;
* generic command metadata.

The external simulation module / wrapper owns:

* the WarpX/PyWarpX input script;
* physical parameters;
* case-specific environment variables;
* MPI/Scheduler execution details;
* generation of raw diagnostics;
* checkpoint/restart behavior internal to the simulation code;
* any code-specific preflight checks.

This separation is mandatory. The workflow core must remain generic.

Capillary guiding, ionization, particle diagnostics, and future non-WarpX codes
must be expressible through configuration and wrappers, not hard-coded in the
workflow core.

## Minimal simulation architecture

A compatible simulation backend should provide a case-local command-line entrypoint:

```bash
simulation_command CASE_DIR
```

or an equivalent command declared in `campaign.json` using placeholders:

```json
"simulation": {
  "name": "warpx_capillary_rz",
  "kind": "command",
  "adapter": "command",
  "scheduler": "slurm",
  "environment_name": "warpx-26.05-py314",
  "command": [
    "bash",
    "/path/to/run_simulation_case.sh",
    "{case_dir}"
  ]
}
```

The command must be able to run or manage one case directory independently.

The command may itself call `srun`, `mpiexec`, `python input.py`, a compiled
WarpX executable, or another simulation code. Those details belong to the
external wrapper, not to the workflow core.

## Required input contract

The simulation command may assume that:

- `CASE_DIR` exists;
- `CASE_DIR/state.json` exists;
- `CASE_DIR/validation.json` exists;
- any case-local configuration files required by the simulation exist;
- the case is in a simulation-compatible state;
- the workflow will pass explicit paths through arguments or environment variables.

For command-based simulation adapters, `campaign-workflow` should provide these
environment variables:

```
CAMPAIGN_ROOT
CAMPAIGN_NAME
CASE_DIR
CASE_ID
CASE_NAME
```

The wrapper may also use case-local files such as:

```
CASE_DIR/case.env
CASE_DIR/input.py
```

The workflow must not edit the WarpX/PyWarpX physics input unless explicitly
requested.

## Required output contract

A successful simulation must produce the raw diagnostics declared in
`campaign.json`:

```json
"raw_diagnostics": [
  {
    "name": "fields_openpmd",
    "kind": "openpmd_hdf5",
    "glob": "diags/diag1/*.h5",
    "min_files": 1,
    "required": true
  }
]
```

Raw diagnostics are not considered valid merely because the simulation returned
success. They must later pass the explicit raw validation phase.

Normal downstream path:
```
Sim_done -> Raw_validated -> Analyzing -> Reduced_validated
```

A simulation command may write additional diagnostics, but only diagnostics
declared in `campaign.json` are validated by the workflow.

## State semantics

The simulation lifecycle states are:

```
Created
Submitted
Running
Sim_done
Failed
Retryable
```

Normal managed path:

```
Created -> Submitted -> Running -> Sim_done
```

Failure path:
```
Created -> Submitted -> Running -> Failed
```

Retry path:
```
Failed -> Retryable -> Submitted -> Running -> Sim_done
```

Backfill/adoption path for already-existing campaigns:
```
Created -> Sim_done
```

The backfill path exists only to adopt campaigns that were launched before the
workflow owned simulation lifecycle evidence.

`Submitted` means that the workflow has evidence that the job was handed to an
execution backend.

`Running` means that the workflow has evidence that the simulation command
actually started for this case.

`Sim_done` means that the simulation command finished successfully or that
sufficient completion evidence exists for an already-existing campaign.

`Failed` means that execution failed or produced explicit failure evidence.

`Retryable` means that a failed case has been deliberately marked as safe to
submit again. This must be explicit; the workflow should not silently retry.

## Marker files

The workflow-owned marker files live under:

```
CASE_DIR/post/
```

Recommended markers:

```
post/sim_submitted.json
post/sim_running.json
post/sim_done.json
post/sim_failed.json
```

The wrapper may create these markers directly only if it follows this contract.
Otherwise, the workflow should create them before/after invoking the external
command.

`post/sim_submitted.json`

Minimum fields:

```json
{
  "schema_version": 1,
  "ok": true,
  "operation": "mark_sim_submitted",
  "submitted_at": "UTC timestamp",
  "scheduler": "slurm",
  "scheduler_job_id": "optional scheduler id",
  "scheduler_array_task_id": "optional array task id",
  "submit_command": ["sbatch", "submit_array.sh"],
  "environment_name": "campaign-workflow-py310"
}
```

`post/sim_running.json`

Minumum fields:

```json
{
  "schema_version": 1,
  "ok": true,
  "operation": "mark_sim_running",
  "started_at": "UTC timestamp",
  "scheduler": "slurm",
  "scheduler_job_id": "optional scheduler id",
  "scheduler_array_task_id": "optional array task id",
  "run_command": ["srun", "-n", "24", "python", "input.py", "2"],
  "environment_name": "warpx-26.05-py314",
  "stdout_log": "logs/simulation_stdout.txt",
  "stderr_log": "logs/simulation_stderr.txt"
}
```

`post/sim_done.json`

Minimum fields:

```json
{
  "schema_version": 1,
  "ok": true,
  "operation": "mark_sim_done",
  "started_at": "UTC timestamp or null",
  "finished_at": "UTC timestamp",
  "return_code": 0,
  "scheduler": "slurm",
  "scheduler_job_id": "optional scheduler id",
  "scheduler_array_task_id": "optional array task id",
  "run_command": ["srun", "-n", "24", "python", "input.py", "2"],
  "environment_name": "warpx-26.05-py314",
  "stdout_log": "logs/simulation_stdout.txt",
  "stderr_log": "logs/simulation_stderr.txt",
  "raw_outputs_expected": [
    {
      "name": "fields_openpmd",
      "glob": "diags/diag1/*.h5"
    }
  ]
}
```

`post/sim_failed.json`

Minimum fields:

```json
{
  "schema_version": 1,
  "ok": false,
  "operation": "mark_sim_failed",
  "started_at": "UTC timestamp or null",
  "finished_at": "UTC timestamp",
  "return_code": 1,
  "scheduler": "slurm",
  "scheduler_job_id": "optional scheduler id",
  "scheduler_array_task_id": "optional array task id",
  "run_command": ["srun", "-n", "24", "python", "input.py", "2"],
  "environment_name": "warpx-26.05-py314",
  "stdout_log": "logs/simulation_stdout.txt",
  "stderr_log": "logs/simulation_stderr.txt",
  "errors": [
    "human-readable failure reason"
  ]
}
```

## Logging contract

For case-local execution, simulation logs should live under:
`CASE_DIR/logs/`
Recommended generic names:
```
logs/simulation_stdout.txt
logs/simulation_stderr.txt
```
Scheduler-level logs may also exist at campaign level, for example:
`CAMPAIGN_ROOT/array_logs/`
but case-level logs are preferred for workflow evidence and debugging.

For SLURM arrays, a thin scheduler wrapper may write both:
```
CAMPAIGN_ROOT/array_logs/<job>_<array>_<task>.out
CASE_DIR/logs/<job>_<array>_<task>_<case_id>.out
```

The case-local log is the one that should be referenced in simulation evidence.

## Environment contract

Simulation execution may require a separate environment from the workflow.

For SUNRISE, the expected separation is:
```
campaign-workflow-py310    -> workflow orchestration
guiding-analysis-py310     -> guiding/particle analysis
warpx-26.05-py314          -> PyWarpX/WarpX simulation execution
```
A simulation wrapper may load the WarpX/PyWarpX stack, for example:
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

The workflow must not assume that the simulation environment can import
`campaign_workflow`.

If a wrapper needs to write workflow markers, it should either:

- write plain JSON markers directly according to this contract; or
- call a thin `campaign_workflow` CLI from the workflow environment before/after
changing to the simulation environment.

## Scheduler contract

SLURM is an execution backend, not the architecture.

A SLURM script should be a thin wrapper around one case or one case-local cycle.

For a simulation-only wrapper, the script should:

- select one case;
- enter the case directory;
- prepare logs;
- load the simulation environment;
- optionally run a preflight;
- run the simulation command;
- record success or failure evidence.

For a managed case-cycle wrapper, the script may also call workflow CLIs for
later case-local phases after the simulation succeeds:

```text
mark_sim_done
validate_raw_case
analyze_case
mark_raw_delete_eligible
cleanup_raw_case --dry-run
optional cleanup_raw_case --execute
```

This is allowed only if each phase remains explicit, logged, and transactional.

Core physics logic must not live inside SLURM scripts. Campaign-wide optimizer
logic must not live inside case-local SLURM scripts.

The workflow should support non-SLURM execution later, for example local shell,
direct subprocess execution, or experimental-control computers.

## Preflight contract

A simulation wrapper may perform a preflight before the expensive run.

For PyWarpX/PICMI this may be:

```bash
CAP_DRY_RUN=1 python input.py 2
```

A successful preflight is not simulation completion.

If preflight fails, the case should end in:
```
Failed
```

with `post/sim_failed.json` explaining that failure happened during preflight.

## Exit-code contract

The simulation command must return:

- `0` on successful execution;
- non-zero on failure.

A zero exit code is sufficient to mark the simulation execution as complete, but
not sufficient to validate raw diagnostics.

After `Sim_done`, the raw validation phase must still check configured raw
diagnostics.

## Walltime-aware simulation execution

A simulation wrapper may be walltime-aware.

The workflow should eventually support a walltime guard that estimates whether a
running simulation can finish before the scheduler kills the job.

For WarpX/PyWarpX this can be estimated from simulation logs containing values
such as:

```text
STEP <N> ends
Avg. per step = <seconds>
max_steps = <M>

A generic walltime estimate is:

remaining_steps = max_steps - current_step
eta_remaining = remaining_steps * avg_s_per_step

The simulation is at risk if:

eta_remaining + safety_margin > walltime_remaining

If this condition is detected, the wrapper or a campaign maintenance tick may
eventually stop the case before scheduler walltime kills it.

The first implementation should not silently retry. It should record explicit
failure evidence.

Recommended post/sim_failed.json fields for this case:

{
  "schema_version": 1,
  "ok": false,
  "operation": "mark_sim_failed",
  "failure_kind": "walltime_insufficient",
  "current_partition": "T6H",
  "recommended_partition": "T12H",
  "current_step": 219396,
  "max_steps": 448000,
  "avg_s_per_step": 0.0742,
  "eta_remaining_hours": 4.71,
  "walltime_remaining_hours": 1.44,
  "safety_margin_hours": 0.35,
  "partial_raw_cleanup_recommended": true
}

The workflow must distinguish:

simulation failed because physics/code crashed
simulation failed because walltime was insufficient
simulation was killed by scheduler before failure evidence could be written
simulation is stale/running orphan

These cases may have different retry policies.

A future retry planner may use failure_kind=walltime_insufficient to recommend
rerunning in a longer partition, for example:

T6H  -> T12H
T12H -> T24H
T24H -> T48H

This is scheduler policy, not physics logic, and must remain configurable.

## Managed case-cycle execution

The simulation lifecycle may be used as the first part of a managed case-local
cycle.

For expensive WarpX/PyWarpX campaigns, the preferred production pattern may be:

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

This does not make simulation completion equivalent to cleanup permission.

The separation between phases remains mandatory:

- each phase must write its own evidence;
- each phase must have inspectable logs;
- a failure must stop the remaining phases for that case;
- raw diagnostics must still pass raw validation;
- reduced outputs must still pass reduced validation;
- cleanup eligibility must still be explicit;
- cleanup execute must still use a validated dry-run manifest;
- cleanup execute must require explicit confirmation.

The reason for allowing a case-local cycle is practical: simulation usually
dominates walltime, while raw validation, analysis/reduced validation, cleanup
eligibility, and cleanup dry-run are expected to be much shorter for the current
campaign class.

There must still be no resident parent orchestrator. A case-local array task does
work and exits. It must not sit idle waiting for other jobs.

Campaign-wide operations remain separate because they are intrinsically global:

```text
storage_snapshot
optimizer_tick
objective aggregation
candidate proposal
campaign summary reports
```

For future BO/MORBO workflows, the optimizer tick is expected to run globally
after one or more case-local cycles have produced validated reduced outputs. It
must not become a resident daemon.

## Cleanup safety

Simulation completion must never authorize cleanup.

After simulation completion, `validation.json` must keep:
```json
"cleanup": {
  "cleanup_allowed": false
}
```
Raw cleanup requires later explicit phases:
```
raw validation
reduced validation
raw delete eligibility
cleanup dry-run manifest
cleanup execute
```

A simulation wrapper must not:

- delete raw diagnostics;
- update cleanup manifests;
- transition raw/reduced validation states;
- mark raw data as cleanup eligible;
- delete files outside `CASE_DIR`;
- delete case directories.

## SUNRISE top10 particles example

The current real campaign launches simulations through SLURM:
```
sbatch submit_top10_particles_array.sh
```
The SLURM job selects a row from:
```
cases.tsv
```
enters:
```
CASE_DIR
```
load the WarpX/PyWarpX stack, sources:
```
CASE_DIR/case.env
```
then runs:
```bash
CAP_DRY_RUN=1 python input.py 2
srun -n "${SLURM_NTASKS}" python input.py 2
```
The real case-local outputs include:
```
CASE_DIR/diags/diag1/*.h5
CASE_DIR/diags/electron_particles/openpmd/*.h5
CASE_DIR/logs/*.out
CASE_DIR/logs/*.err
CASE_DIR/run_info.txt
```
For this campaign, `fields_openpmd`is the oficial raw diagnostic currently validated by `campaign-workflow`:
`diags/diag1/*.h5`

Particle diagnostics exist physically, but should only become workflow-validated
raw diagnostics when they are explicitly declared in `campaign.json`.

## Non-goals for V1

Do not implement yet:

- a resident daemon;
- automatic retry policy;
- optimizer-driven submission;
- full MORBO orchestration;
- campaign creation for ionization;
- physics input editing;
- SLURM-specific logic in the workflow core;
- Cleanup authorized by simulation completion alone.

## First implementation target

The first code implementation should be small:
```
1. Add generic simulation transition helpers.
2. Allow mark_sim_done to support Running -> Sim_done.
3. Preserve Created -> Sim_done backfill for legacy/adopted campaigns.
4. Add unittest coverage for Created -> Submitted -> Running -> Sim_done.
5. Add unittest coverage for Running -> Failed.
6. Do not launch real simulations yet.
```
