# State machine

## Goal

The state machine provides a compact, auditable description of where each case is in the campaign lifecycle.

The state file is not the only source of truth. Destructive decisions require agreement between:

1. `state.json`;
2. `validation.json`;
3. physical evidence files and outputs on disk.

## Main states

### Created

The case exists in `cases.tsv` and its case directory exists or is expected to exist.

Allowed transitions:
```text
Created -> Submitted
Created -> Quarantined
```

### Submitted

The simulation job has been submitted or the workflow has evidence that submission was attempted.

Allowed transitions:
```
Submitted -> Running
Submitted -> Failed
Submitted -> Retryable
```

### Running

The simulation is currently running or presumed running.

Allowed transitions:
```
Running -> Sim_done
Running -> Failed
Running -> Stale
```

### Sim_done

The simulation finished successfully according to job evidence.

Required evidence should include one of:
- `post/sim_done.json`
- trusted scheduler exit status
- trusted simulation log marker

Allowed transitions:
```
Sim_done -> Raw_validated
Sim_done -> Validation_failed
Sim_done -> Quarantined
```

### Raw_validated

The configured raw diagnostics exist and passed validation.

For WarpX/openPMD/HDF5 this means that HDF5 files exist, are old enough, non-empty, openable, and match the configured raw diagnostic contract.

Allowed transitions:
```
Raw_validated -> Analyzing
Raw_validated -> Reduced_validated
Raw_validated -> Validation_failed
```

The direct transition to `Reduced_validated` is allowed only if reduced outputs already exist and pass validation.

### Analyzing

A case-local analysis job is running or was started.

Allowed transitions:
```
Analyzing -> Reduced_validated
Analyzing -> Analysis_failed
Analyzing -> Retryable
Analyzing -> Stale
```

### Reduced_validated

The configured reduced outputs exist and passed validation.

Examples:

- metrics CSV
- summary JSON
- compact diagnostic outputs

Allowed transitions:
```
Reduced_validated -> Raw_delete_eligible
Reduced_validated -> Quarantined
```

### Raw_delete_eligible

Raw diagnostics are eligible for deletion, but have not necessarily been deleted.

This state is not enough to delete files by itself. Cleanup still requires a fresh validation pass and an explicit manifest.

Allowed transitions:
```
Raw_delete_eligible -> Raw_deleted
Raw_delete_eligible -> Cleanup_failed
Raw_delete_eligible -> Quarantined
```

### Raw_deleted

Raw diagnostics listed in a validated cleanup manifest were deleted.

Required evidence:

- cleanup manifest
- deletion report
- updated `validation.json`
- state history entry

Allowed transitions:
```
Raw_deleted -> Reduced_validated
Raw_deleted -> Quarantined
```
The transition back to `Reduced_validated` means that the case remains scientifically usable through reduced outputs, but raw diagnostics are no longer available.

## Side states

### Failed

A non-retryable failure occurred.

### Retryable

A failure occurred, but retrying may be valid

Examples:

- temporary filesystem issue
- scheduler preemption
- missing dependency
- walltime exceeded but restart/checkpoint is possible

### Walltime-insufficient failures

A case may fail because the requested walltime is insufficient even though the
simulation itself is healthy.

In V1 this does not require a new top-level state. It can be represented as:

```text
Running -> Failed

with explicit failure metadata:

{
  "failure_kind": "walltime_insufficient",
  "retry_recommended": true,
  "recommended_partition": "T12H",
  "current_step": 219396,
  "max_steps": 448000,
  "avg_s_per_step": 0.0742,
  "eta_remaining_hours": 4.71
}

A later phase may introduce a more explicit state such as:

Walltime_insufficient
Rerun_planned
Partial_raw_cleaned

but the first implementation should avoid expanding the state machine unless the
workflow needs those states for safe transitions.

Walltime-insufficient cases are not successful simulations. They must not advance
to:

Sim_done
Raw_validated
Analyzing
Reduced_validated
Raw_delete_eligible
Raw_deleted

unless the simulation is rerun and finishes successfully.

Running orphan / stale scheduler state

If SLURM kills a job by walltime, the case-local script may not have time to call
mark_sim_failed.

In that case, state.json may still say:

Running

even though no corresponding SLURM job exists.

This is a stale state and should be handled as:

Running -> Stale

or manually inspected and then transitioned to Failed with an explicit reason.

A future maintenance tick should detect this by checking:

state.json says Running
scheduler job/task no longer exists
post/sim_done.json does not exist
post/sim_failed.json does not exist
logs indicate interruption or incomplete simulation

Such a case must not be cleaned using the normal success cleanup path.

### Stale

The state claims an operation is in progress, but the associated lock or job appears expired

### Quarantined

The case must not be modified automatically.

Reasons:
- corrupted JSON
- inconsistent state and validation evidence
- path safety violation
- possible partial deletion
- unexpected case directory layout

### Validation_failed

Validation failed

this does not necessarily mean the simulation is useless. It means the current workflow cannot prove that the case may advance

### Analysis_failed

the analysis job failed

### Cleanup_failed

Cleanup was attempted but did not complete safely

### Disk_wait

The case is waiting for disk quota or storage cleanup before proceeding.

## State file schema

Minimal `state.json`:
```json
{
  "schema_version": 1,
  "case_id": 0,
  "case_name": "000_example",
  "state": "Created",
  "created_at": "2026-06-11T00:00:00Z",
  "updated_at": "2026-06-11T00:00:00Z",
  "history": [
    {
      "timestamp": "2026-06-11T00:00:00Z",
      "from": null,
      "to": "Created",
      "operation": "init_case_states",
      "reason": "initialized from cases.tsv",
      "actor": {
        "hostname": "unknown",
        "pid": 0,
        "user": "unknown"
      }
    }
  ]
}
```

## Transition rules

A script may only perform transitions that it owns

Examples:
- simulation submitter may set `Submitted`, `Running`, `Sim_done`, `Failed`
- raw validator may set `Raw_validated` or `Validation_failed`
- analysis script may set `Analyzing`, `Reduced_validated`, `Analysis_failed`
- cleanup script may set `Raw_delete_eligible`, `Raw_deleted`, `Cleanup_failed`

## No destructive action from state alone

`Raw_delete_eligible` is only a candidate state

Deletion requires:
```
state.json says Raw_delete_eligible
validation.json says cleanup_allowed = true
delete manifest exists
fresh pre-delete validation succeeds
case is not running
all paths are safe
```
