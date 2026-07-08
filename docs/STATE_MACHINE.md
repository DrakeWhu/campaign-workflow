# State machine

The state machine records the lifecycle of one campaign case.

`state.json` is important but not sufficient for destructive decisions. Cleanup also requires `validation.json`, manifests, and physical file checks.

## Main states

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

## Failure and side states

```text
Failed
Retryable
Validation_failed
Analysis_failed
Cleanup_failed
Stale
Quarantined
```

## Normal successful path

```text
Created
-> Submitted
-> Running
-> Sim_done
-> Raw_validated
-> Analyzing
-> Reduced_validated
-> Raw_delete_eligible
-> Raw_deleted
```

Some adopted campaigns may enter the middle of this path through explicit backfill or validation commands. Such transitions must still write evidence.

## Simulation states

`Created`: the case exists in the manifest and has workflow state files.

`Submitted`: submission evidence exists.

`Running`: the simulation command started.

`Sim_done`: the simulation command finished successfully or trusted completion evidence was deliberately backfilled.

`Failed`: execution failed.

`Retryable`: a failed case has been explicitly marked as safe to retry.

`Stale`: state says running/analyzing, but external evidence indicates the job is no longer active or cannot complete without inspection.

## Validation and analysis states

`Raw_validated`: configured required raw diagnostics passed validation.

`Validation_failed`: raw or reduced validation failed.

`Analyzing`: external analysis was started.

`Analysis_failed`: external analysis failed or produced invalid required outputs.

`Reduced_validated`: configured required reduced outputs passed validation.

## Cleanup states

`Raw_delete_eligible`: the case may enter cleanup planning. This state alone does not delete files.

`Raw_deleted`: files listed in a validated cleanup manifest were deleted.

`Cleanup_failed`: cleanup planning or execution failed.

## Quarantine

`Quarantined` is for cases that should not advance automatically. Manual inspection is required.

## Destructive-action rule

`Raw_deleted` must only be reached through cleanup execute after manifest validation. No other transition may delete raw files.
