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