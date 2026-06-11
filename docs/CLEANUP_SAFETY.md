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