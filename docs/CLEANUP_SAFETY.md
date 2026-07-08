# Cleanup safety contract

Raw cleanup is intentionally conservative because WarpX/openPMD diagnostics can dominate campaign storage and may be expensive or impossible to regenerate.

## Invariant

No workflow command may delete raw data unless all of the following are true:

1. the case is cleanup-compatible;
2. required raw evidence is valid;
3. required reduced evidence is valid;
4. the case has been marked `Raw_delete_eligible`;
5. a dry-run cleanup manifest exists;
6. execute mode is explicitly requested;
7. every target path is a regular file inside the case directory;
8. directory deletion is disabled.

## Cleanup configuration

```json
"cleanup": {
  "raw_delete_globs": ["diags/**/*.h5", "diags/**/*.hdf5"],
  "require_raw_validated": true,
  "require_reduced_validated": true,
  "require_delete_manifest": true,
  "allow_directory_delete": false
}
```

`allow_directory_delete` must remain `false` for the supported workflow.

## Two-step operation

Dry-run:

```bash
python -m campaign_workflow.cli.cleanup_raw_case --campaign-root . --dry-run --verbose
```

Dry-run writes a manifest of exact files that would be deleted. It deletes nothing.

Execute:

```bash
python -m campaign_workflow.cli.cleanup_raw_case --campaign-root . --execute --verbose
```

Execute deletes only files listed in an existing validated manifest.

## Allowed targets

Cleanup targets are selected from `cleanup.raw_delete_globs`, case-relative, and constrained to the case directory.

For the production openPMD/HDF5 path, allowed suffixes are:

```text
.h5
.hdf5
```

The workflow must not delete:

- directories;
- files outside the case directory;
- state files;
- validation files;
- logs;
- manifests;
- reduced CSV outputs;
- optimizer state;
- campaign manifests.

## Reduced-only adopted campaigns

`validate_reduced_case --legacy-reduced-only` can recover scientific reduced outputs for old campaigns, but it does not authorize raw cleanup. Cleanup requires explicit raw validation evidence unless the code is deliberately changed and retested.

## Failure behavior

If any required condition fails, cleanup must fail closed. Partial deletion failures must be recorded in cleanup evidence and must not be silently ignored.
