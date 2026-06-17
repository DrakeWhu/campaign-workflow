# Campaign workflow

## Purpose

This workflow manages simulation campaigns where many independent cases are launched, validated, analyzed, summarized, and eventually cleaned up.

The design target is HPC execution with SLURM arrays, heavy raw diagnostics, and reduced analysis products. The first production use case is WarpX/PyWarpX on SUNRISE, but the workflow must remain general enough to support different inputs, diagnostics, and analysis modules.

## Core rule

The workflow core must not know about capillaries, guiding, laser waist, plasma density, or any specific physics quantity.

The workflow core only knows about:

- campaign configuration;
- case manifests;
- case directories;
- states;
- validations;
- locks;
- manifests;
- storage snapshots;
- safe cleanup.

Physics-specific logic belongs in the simulation input and analysis adapters.

## Campaign root

A campaign root is a directory containing:

```text
campaign_root/
├── campaign.json
├── cases.tsv
├── workflow/
├── slurm scripts or submit scripts
└── case directories
```

`campaign.json` is mutable only by deliberate workflow configuration changes.

`cases.tsv` is immutable once the campaign starts.

Case directories are addressed through the `case_id_column` and `case_name_column` defined in `campaign.json`.

## Case directory materialization

Before case states are initialized, campaign case directories are created explicitly from the immutable case manifest.

The intended bootstrap sequence for a new campaign is:

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

`create_case_dirs` is a minimal, generic, non-physical workflow phase.

It reads:

```text
campaign.json
cases.tsv
```

It uses the configured case manifest parser to identify:

```text
CASE_ID
CASE_NAME
```

It validates that:

```text
cases.tsv exists
cases.tsv contains at least one row
CASE_ID values are valid according to the existing manifest parser
CASE_NAME values are not empty
CASE_NAME values are not duplicated
CASE_NAME values are relative paths
CASE_NAME values do not contain ..
resolved case directories stay inside campaign_root
```

In write mode, it creates:

```text
CASE_DIR/
├── logs/
├── post/
├── manifests/
├── locks/
├── diags/
└── checkpoints/
```

The command is idempotent:

```text
existing case directories are accepted
existing subdirectories are accepted
existing files are not overwritten
nothing is deleted
destructive_operations=0
```

This phase deliberately does not create or modify:

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

This phase also deliberately does not implement:

```text
WarpX/PyWarpX input preparation
SLURM submitter generation
case.env generation
cases.tsv -> environment variable mapping
simulation execution
analysis execution
BO/MORBO logic
cleanup
```

Simulation-specific materialization, such as copying `input_template.py`, generating `case.env`, or mapping physical columns from `cases.tsv` into environment variables, belongs in a later campaign-specific preparation layer, not in this generic workflow phase.

## Case directory ownership

Each case owns its own runtime artifacts:

```
CASE_DIR/
├── state.json
├── validation.json
├── locks/
├── manifests/
├── post/
├── logs/
├── raw diagnostics
├── reduced outputs
└── plots or reports
```

No case should write into another case directory.

Global summaries may be produced later by reading case-local validated outputs.

## Execution model: case-local cycle plus campaign-wide ticks

The workflow separates responsibilities by phase, but this does not require one
SLURM job per phase.

The preferred production model for expensive WarpX/PyWarpX campaigns is a
case-local cycle executed by a SLURM array task:

```text
case cycle array task
  -> mark_sim_submitted
  -> mark_sim_running
  -> run external simulation
  -> mark_sim_done or mark_sim_failed
  -> validate_raw_case
  -> analyze_case
  -> mark_raw_delete_eligible
  -> cleanup_raw_case --dry-run
  -> optionally cleanup_raw_case --execute
```

The simulation step dominates walltime. Raw validation, analysis/reduced
validation, cleanup eligibility, and cleanup dry-run are expected to be much
shorter for the current campaign class, so executing them immediately after a
successful simulation avoids unnecessary intermediate scheduling overhead.

The separation remains semantic and transactional:

- every phase writes explicit workflow evidence;
- every phase may fail independently;
- logs must identify the phase that failed;
- cleanup still requires validated raw evidence, validated reduced evidence,
- explicit eligibility, and a validated dry-run manifest;
- cleanup execute remains optional and must require explicit confirmation.

There is no resident parent orchestrator in V1.

Campaign-wide jobs are allowed only when the operation is intrinsically global,
for example:

```
storage snapshot
optimizer tick
ranking / objective aggregation
candidate proposal
campaign summary reports
```

A launcher may submit one or more jobs and exit immediately. Such a launcher is
not a resident orchestrator. It must not occupy a long walltime partition while
waiting or polling.

## Cooperative campaign maintenance ticks

A real SUNRISE production campaign showed that the workflow also needs
lock-protected campaign maintenance operations.

A maintenance tick is a short campaign-wide operation that may be invoked by a
case-local SLURM task after it finishes its own case-local cycle.

The key rule is:

```text
case-local work remains case-local;
campaign-wide maintenance must acquire a campaign-wide lock.

A case job may attempt to launch a maintenance tick, but the tick is not owned by
that case. It is a campaign operation executed under a campaign lock.

This avoids a resident parent daemon while still allowing the campaign to
self-maintain.

A maintenance tick may eventually inspect:

quota usage
live raw HDF5 size
running jobs
stale Running cases
walltime risk
rerun candidates
SLURM array throttles
validated reduced outputs
optimizer readiness

It may eventually perform safe campaign-wide actions such as:

lowering or raising array throttles
marking cases as walltime_insufficient
canceling jobs that cannot finish within walltime
planning reruns in a longer partition
submitting rerun arrays
creating quota/walltime reports

A maintenance tick must not:

act without a campaign-wide lock
edit WarpX/PyWarpX physics inputs
modify cases.tsv in place
delete directories
delete files outside CASE_DIR
delete raw files without an explicit manifest
reinterpret cleanup globs at execute time
silently retry cases
become a resident daemon
run BO/MORBO as part of a case-local cycle

The maintenance tick is allowed to write into multiple case directories only
because it is a campaign-wide locked operation, not because one case owns another
case's files.

This is the intended future pattern:

case-local cycle finishes
  -> attempts maintenance_tick
  -> if campaign lock is available:
       inspect campaign
       apply safe maintenance actions
       release lock
     else:
       another job is maintaining the campaign; exit OK
Walltime guard

The first real production campaign showed that walltime cannot always be chosen
statically.

For example, a 25 mm WarpX case with:

max_steps = 448000
avg_s_per_step ≈ 0.07

requires roughly:

448000 * 0.07 s ≈ 8.7 h

before analysis and cleanup. Such a case does not fit in a T6H partition even if
shorter cases in the same campaign do.

A future walltime guard should estimate, for every Running case:

current_step
max_steps
avg_s_per_step
elapsed_walltime
remaining_walltime
safety_margin
eta_remaining

and classify the case as:

OK
TIGHT
TIMEOUT_RISK
UNKNOWN

If a case is clearly unable to finish within the current walltime, a future active
guard may:

cancel the SLURM array task
mark the case as failed with failure_kind=walltime_insufficient
record the current progress estimate
clean partial raw files using a special partial-raw cleanup manifest
mark the case as rerun-planned
submit the case again in a longer partition

This must be explicit and auditable. The workflow must not silently retry.

Rerun planning

Rerun planning is a campaign-wide operation.

The workflow should eventually support generating rerun batches from cases whose
failure evidence indicates that rerun is safe and useful.

A rerun plan should record:

source campaign
case IDs
previous partition/time limit
recommended partition/time limit
failure_kind
reason for rerun
whether partial raw was cleaned
submission command or sbatch job id

Rerun planning must not modify the original cases.tsv in place. If new
candidate cases are created by an optimizer, they must be written into a new
batch manifest.

Quota guard

A quota guard is another campaign maintenance operation.

It should inspect real user quota, not only filesystem capacity. On SUNRISE the
working command for the current filesystem was:

lfs quota -h -u "$USER" .

The guard may eventually:

report used/quota/limit
report live raw HDF5 size
delay new submissions if quota is high
lower SLURM array throttles
prioritize cleanup of already validated cases
stop launching reruns if quota is unsafe

Quota pressure alone must not justify unsafe deletion. Cleanup rules still apply.

## Data clases

The workflow distinguishes three levels of data.

### Raw diagnostics

Large outputs produced by the simulation backend.

Examples:
- WarpX openPMD/HDF5 files
- ADIOS2 outputs
- SDF files
- large image stacks

Raw diagnostics are expensive to store and may become cleanup-eligible after validation and reduction.

### Reduced diagnostics

Small persistent analysis outputs.

Examples:
- Metrics CSV
- Scalar summaries
- Compact Parquet tables
- Analysis JSON
- Diagnostic plots

Reduced outputs are the persistent scientific products of V1.

### Evidence files

Small JSON or text files proving that a transition happened safely.

Examples:
- `post/sim_done.json`
- `post/analysis_done.json`
- raw validation manidests
- cleanup manifests
- validation.json

Evidence files are never optional for destructive operations

## Generic state flow

```
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

Side states:

```
Failed
Retryable
Stale
Quarantined
Validation_failed
Analysis_failed
Cleanup_failed
Disk_wait
```

The exact meaning of raw and reduced data is defined by `campaign.json`

### Idempotency

Every operation must be safe to run more than once.

Examples:
- initializing states twice must not corrupt histories
- validating raw outputs twice must produce the same result or a newer validation report
- dry-run cleanup may be repeated
- execute cleanup may be rpeated and report that files are already gone only if prior deletion evidence exists

### Atomicity

State and validation files must be written atomically:
1. write temporary file in the same directory
2. fsync if practical
3. rename into place

A partially written JSON file is treated as corruption and should move the case to `Quarantined` or require manual inspection

### Locking

Locks are directory-based where possible:
```
CASE_DIR/locks/<operation>.lock/
```

Creating a lock directory is atomic on POSIX filesystems

A lock directory contains metadata:
```
{
  "operation": "validate_raw",
  "owner_job_id": "...",
  "array_task_id": "...",
  "hostname": "...",
  "pid": "...",
  "created_at": "...",
  "expires_at": "..."
}
```
Stale locks may be recovered only after explicit expiration checks

### Cleanup philosophy

Cleanup is not an afterthought. Cleanup is a separately validated workflow phase.

Raw files may be deleted only when:
- raw validation succeeded;
- reduced validation succeeded;
- the case is not running;
- an explicit delete manifest exists;
- every path in the manifest is inside the case directory;
- every path in the manifest matches the configured raw-delete rules;
- validation is re-run immediately before deletion.

### Extension points

The core workflow should support replacing:

- simulation backend
- input template
- raw diagnostic kind
- analysis module
- reduced output contract
- cleanup globs
- scheduler backend

The first implemented adapters are expected to be:
```
simulation backend: warpx_picmi
raw diagnostic: openpmd_hdf5
analysis: guiding
scheduler: slurm
```
These adapters must not leak campaign-specific assumptions into the core