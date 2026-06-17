# Cleanup safety contract

## Purpose

Cleanup is the most dangerous part of the workflow.

This document defines non-negotiable rules for deleting heavy raw simulation outputs.

## Absolute prohibitions

The workflow must never:

```text
delete a case directory
delete a campaign directory
run rm -rf on a case directory
delete files outside the case directory
delete files not listed in an explicit manifest
delete raw diagnostics before reduced outputs are validated
delete raw diagnostics while the case is running
delete raw diagnostics from a quarantined case
follow symlinks outside the case directory
treat state.json alone as permission to delete
```

## Allowed deletion target in V1

Only explicit raw files matching configured cleanup globs may be deleted.

Example:
```
CASE_DIR/diags/**/*.h5
```

Each deleted file must appear in a delete manifest generated before execution.

## Required cleanup evidence

Before deletion:
```
state.json
validation.json
delete manifest
raw validation report
reduced validation report
job-not-running evidence
```

After deletion:
```
post/raw_deleted.json
updated validation.json
updated state.json
cleanup log
```

## Two-phase cleanup

Cleanup has two modes.

## Partial raw cleanup for failed/rerun cases

The normal cleanup path is only for successful cases:

```text
Sim_done
-> Raw_validated
-> Reduced_validated
-> Raw_delete_eligible
-> cleanup dry-run manifest
-> cleanup execute
-> Raw_deleted

A different cleanup mode may be needed for failed or aborted simulations that
left partial raw diagnostics on disk.

This must be treated as a separate operation from normal success cleanup.

Suggested name:

partial_raw_cleanup

Partial raw cleanup may only be allowed when all are true:

the case is not running
there is scheduler evidence that the job is gone or was canceled
the case is in Failed, Retryable, Stale, or an explicit walltime/rerun state
failure_kind is compatible with deleting partial raw
the cleanup target is explicitly configured
a partial-raw cleanup manifest is written before deletion
every path is inside CASE_DIR
every path is a regular file
no directory deletion is attempted
no symlink escape is possible

Partial raw cleanup must not pretend that the case was successfully reduced.

It must not write:

post/raw_deleted.json

because raw_deleted.json is reserved for successful raw cleanup after validated
reduced outputs.

Instead, it should write a distinct marker such as:

post/partial_raw_deleted.json

and a distinct manifest such as:

manifests/partial_raw_delete_manifest.json

The marker should record:

{
  "schema_version": 1,
  "operation": "partial_raw_cleanup",
  "reason": "walltime_insufficient",
  "files_deleted": 123,
  "total_size_bytes": 123456789,
  "safe_to_rerun": true
}

Partial raw cleanup does not make the case scientifically complete. Its purpose is
only to free quota and make a clean rerun possible.

Absolute prohibitions still apply:

do not delete directories
do not delete outside CASE_DIR
do not delete undeclared files
do not use rm -rf
do not delete while scheduler says the job is running
do not delete from Quarantined cases

### Dry-run

Dry-run:

- validates cleanup eligibility
- resolves candidate files
- checks path safety
- writes a delete manifest
- writes a cleanup validation report
- does not delete anything

### Execute

Execute:

- reads an existing manifest
- revalidates the case
- revalidates every path
- deletes only files in the manifest
- records exactly what happened

Execute must fail if the manifest is missing.

## Manifest rules

A delete manifest must contain:
```json
{
  "schema_version": 1,
  "case_id": 0,
  "case_name": "000_example",
  "created_at": "...",
  "operation": "cleanup_raw_dry_run",
  "files": [
    {
      "relative_path": "diags/diag1/openpmd_000000.h5",
      "size_bytes": 123456,
      "mtime": "..."
    }
  ],
  "total_size_bytes": 123456
}
```
Manifest paths must be relative to the case directory.

Absolute paths may be recorded as diagnostic information, but deletion should be driven by normalized case-relative paths.

## Path safety

A file is safe to delete only if all are true:
```
path resolves inside CASE_DIR
path is a regular file
path is not a symlink escape
path matches one configured cleanup glob
path suffix is allowed for the raw diagnostic kind
path appears in the manifest
```
For openPMD/HDF5 V1, allowed suffixes are:
```
.h5
.hdf5
```

## Idempotency

If cleanup execute is repeated after successful deletion, it must not delete anything else.

It may report:
```
already deleted according to manifest and post/raw_deleted.json
```

But it must not reinterpret cleanup globs to find new files.

## Quarantine triggers

Move to `Quarantined` or stop automatic cleanup if:
```
state.json is corrupted
validation.json is corrupted
manifest references paths outside CASE_DIR
manifest references directories
manifest references symlinks
expected reduced output is missing
case appears to be running
raw files changed after manifest creation
```

## Human override

Human override is allowed only by creating an explicit manual note in the case directory.

Example:
```
post/manual_cleanup_override_YYYYMMDD_HHMMSS.md
```

The V1 scripts should not implement automatic override.