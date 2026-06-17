# Phase history — campaign-workflow

This document records the historical development of `campaign-workflow`.

It is not the active handoff. Current state and next steps belong in:

```text
docs/NEXT_CHAT_HANDOFF.md
```

Stable contracts belong in dedicated documents such as:

```text
docs/CONFIG_CONTRACT.md
docs/STATE_MACHINE.md
docs/CLEANUP_SAFETY.md
docs/ANALYSIS_MODULE_CONTRACT.md
```

## Project goal

`campaign-workflow` is a generic coordination layer for simulation campaigns:

```text
simulation -> raw validation -> analysis -> reduced validation -> storage snapshot -> cleanup eligibility -> cleanup dry-run manifest -> cleanup execute
```

The workflow is designed to remain generic. Capillary guiding is the first production example, not the architecture.

Development is done locally under Git and deployed to SUNRISE through Git checkout/pull. OpenCode is not used for this workflow.

## Persistent design constraints

The following constraints have guided all phases:

* Do not edit WarpX/PyWarpX physics inputs unless explicitly requested.
* Do not center the core architecture on capillaries or guiding.
* `cases.tsv` is immutable after campaign start.
* No SQL database in V1.
* No resident parent orchestrator daemon in V1.
* Simulation, raw validation, analysis, reduced validation, storage snapshots, cleanup, and future optimization are separate jobs.
* Case directories are the concurrency boundary.
* Destructive operations require redundant validation.
* Cleanup is two-phase: dry-run manifest, then execute.
* Cleanup must never delete directories.
* Cleanup must never use `rm -rf` on campaign data.
* Cleanup must never delete paths outside `CASE_DIR`.
* State alone is never sufficient permission to delete raw data.
* Scripts must be visible, auditable, and runnable directly on SUNRISE.
* Deployment to SUNRISE should happen through Git.
* For WarpX/PyWarpX and SLURM submitters, do not invent scripts from scratch if working scripts already exist; ask for the current files unless explicitly told to build from zero.
* Use `unittest`, not `pytest`.

Standard test command:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

## Core abstraction

The reusable workflow concepts are:

```text
campaign.json
cases.tsv
CASE_DIR
state.json
validation.json
locks/
manifests/
post/
logs/
raw diagnostics
reduced outputs
storage snapshots
safe cleanup
```

The workflow core knows about:

```text
case manifests
case directories
states
validation documents
locks
manifests
storage accounting
safe path checks
atomic JSON writes
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

Campaign-specific behavior belongs in config, wrappers, and external adapters/modules.

## State machine

Main generic states:

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

Important naming decision:

Use generic states such as:

```text
Raw_validated
Reduced_validated
Raw_delete_eligible
Raw_deleted
```

not format-specific states such as:

```text
H5_validated
CSV_validated
```

The concrete meaning of raw/reduced is defined by `campaign.json`.

## Phase 0 — Initial documentation and example config

Phase 0 created the first project documentation and example campaign config.

Implemented files:

```text
README.md
.gitignore
docs/CAMPAIGN_WORKFLOW.md
docs/STATE_MACHINE.md
docs/CONFIG_CONTRACT.md
docs/CLEANUP_SAFETY.md
docs/NEXT_CHAT_HANDOFF.md
examples/capillary_guiding/campaign.json
examples/capillary_guiding/README.md
```

Initial commit:

```text
b6eb066 Initialize campaign workflow documentation and example config
```

Correction made during this phase:

```text
docs/CONFIG_CONTRAST.md
```

was renamed/fixed to:

```text
docs/CONFIG_CONTRACT.md
```

## Phase 1 — Generic case state initialization

Phase 1 implemented generic initialization of per-case workflow state.

Implemented files:

```text
campaign_workflow/__init__.py
campaign_workflow/core/__init__.py
campaign_workflow/core/atomic_io.py
campaign_workflow/core/tsv_cases.py
campaign_workflow/core/state.py
campaign_workflow/cli/__init__.py
campaign_workflow/cli/init_case_states.py
campaign_workflow/diagnostics/__init__.py
campaign_workflow/analysis/__init__.py
tests/fake_campaign/campaign.json
tests/fake_campaign/cases.tsv
tests/fake_campaign/000_fake_case/.gitkeep
tests/fake_campaign/001_fake_case/.gitkeep
tests/fake_campaign/002_fake_case/.gitkeep
```

Commit:

```text
d548040 Add generic case state initialization
```

Validated commands:

```powershell
python -m campaign_workflow.cli.init_case_states --campaign-root tests\fake_campaign --dry-run --verbose
python -m campaign_workflow.cli.init_case_states --campaign-root tests\fake_campaign --verbose
python -m campaign_workflow.cli.init_case_states --campaign-root tests\fake_campaign --check --verbose
python -m campaign_workflow.cli.init_case_states --campaign-root tests\fake_campaign --case-id 1 --check --verbose
```

Observed result:

```text
cases_processed=3
cases_with_errors=0
errors=0
destructive_operations=0
```

Example initialized `state.json`:

```json
{
  "schema_version": 1,
  "case_id": 0,
  "case_name": "000_fake_case",
  "state": "Created",
  "history": [
    {
      "from": null,
      "to": "Created",
      "operation": "init_case_states",
      "reason": "initialized from case manifest"
    }
  ]
}
```

Example initialized `validation.json`:

```json
{
  "schema_version": 1,
  "case_id": 0,
  "case_name": "000_fake_case",
  "raw": {},
  "reduced": {},
  "cleanup": {
    "cleanup_allowed": false,
    "reason": "No raw/reduced validation has been performed yet."
  }
}
```

Implementation notes:

* JSON loaders were changed to use `utf-8-sig`, because PowerShell may write UTF-8 files with BOM.
* `.gitattributes` was added to force LF line endings for Python, shell, JSON, Markdown, TSV, YAML, etc.
* LF normalization matters because future SLURM/bash scripts must run correctly on Linux/SUNRISE.

## Phase 2a — Local generic raw validation against fake raw files

Phase 2a implemented generic raw diagnostic validation.

Purpose:

```text
Validate raw diagnostics according to campaign.json.
Implement generic raw diagnostic validation.
Implement first adapter stack with fake files and openpmd_hdf5 support prepared.
Write validation evidence into validation.json.
Write raw manifests atomically.
Transition Sim_done -> Raw_validated on success.
Transition Sim_done -> Validation_failed on failure.
Do not delete anything.
Do not analyze guiding metrics.
Do not depend on capillary-specific physics.
```

Implemented files:

```text
campaign_workflow/core/path_safety.py
campaign_workflow/core/manifests.py
campaign_workflow/core/transitions.py
campaign_workflow/diagnostics/openpmd_hdf5.py
campaign_workflow/cli/validate_raw_case.py
tests/test_raw_validation.py
```

Main command shape:

```powershell
python -m campaign_workflow.cli.validate_raw_case --campaign-root tests\fake_campaign --case-id 0 --dry-run --verbose
python -m campaign_workflow.cli.validate_raw_case --campaign-root tests\fake_campaign --case-id 0 --verbose
```

The raw validator supports diagnostic kinds:

```text
fake
openpmd_hdf5
```

For `fake`, allowed suffix defaults to:

```text
.fake
```

For `openpmd_hdf5`, allowed suffixes default to:

```text
.h5
.hdf5
```

The `openpmd_hdf5` validation is intentionally minimal:

```text
path safety
glob resolution
file count
suffix check
regular file check
inside CASE_DIR check
symlink escape rejection
non-empty file check
minimum age check
HDF5 open/readability check using h5py
```

Strict openPMD semantic validation is not implemented yet. It can be added later using openPMD-specific tooling without changing the core contract.

Raw validator rejects:

```text
absolute globs
globs containing ..
absolute config paths
paths containing ..
files outside CASE_DIR
symlink escapes outside CASE_DIR
directories instead of files
empty files
suffixes not allowed by diagnostic config
files younger than min_age_seconds
diagnostics with fewer files than min_files
unsupported diagnostic kinds
state-incompatible validation attempts
```

Raw validator does not:

```text
delete files
delete directories
run rm -rf
modify WarpX/PyWarpX inputs
run analysis
compute guiding metrics
mark cleanup as allowed
```

Important invariant:

```text
cleanup.cleanup_allowed = false
```

after raw validation, even if raw validation succeeds.

Reason:

```text
Raw validation alone does not authorize cleanup. Reduced validation is still required.
```

Raw validation state policy:

```text
Sim_done          -> Raw_validated / Validation_failed
Validation_failed -> Raw_validated / Validation_failed
Raw_validated     -> Raw_validated / Validation_failed
Created           -> rejected
```

Local test result:

```text
Ran 5 tests in 0.230s
OK (skipped=1)
```

The skipped test was the symlink escape test on Windows. This is acceptable locally when symlink creation is restricted. On Linux/SUNRISE, that test should ideally run and pass.

## Phase 2b — Local HDF5/openPMD smoke tests

Phase 2b added minimal HDF5 tests for the `openpmd_hdf5` branch.

Purpose:

```text
Verify that openpmd_hdf5 validation works syntactically with h5py.
Use tiny artificial HDF5 files.
Do not require strict openPMD metadata yet.
Do not use real WarpX data yet.
```

Implemented file:

```text
tests/test_raw_validation_hdf5.py
```

Checks:

```text
valid .h5 file opens with h5py -> Raw_validated
valid .hdf5 file opens with h5py -> Raw_validated
invalid .h5 payload -> Validation_failed
non-HDF5 suffix -> Validation_failed
skip cleanly if h5py is not installed
```

## Phase 1b — SUNRISE deployment and real state initialization

A private GitHub repository was created and cloned on SUNRISE under:

```text
~/apps/src/campaign-workflow
```

The workflow was tested against the real campaign:

```text
/gpfs/home/jrodriguez/warpx_runs/capillaries_bo_top10_particles
```

This campaign had:

```text
cases.tsv
10 case directories
raw HDF5 files under diags/diag1/*.h5
existing reduced CSVs such as guiding_metrics.csv and particle_summary.csv
```

A campaign-local runtime config was created:

```text
/gpfs/home/jrodriguez/warpx_runs/capillaries_bo_top10_particles/campaign.json
```

Important:

```text
campaign.json is campaign-local runtime/config state.
It is not a WarpX/PyWarpX input.
It does not modify physics parameters.
```

For this campaign, the raw diagnostic contract was:

```text
name: fields_openpmd
kind: openpmd_hdf5
glob: diags/diag1/*.h5
min_files: 1
min_age_seconds: 600
allowed_suffixes: .h5, .hdf5
```

State initialization was run on the 10 real cases:

```bash
python -m campaign_workflow.cli.init_case_states --campaign-root . --dry-run --verbose
python -m campaign_workflow.cli.init_case_states --campaign-root . --verbose
python -m campaign_workflow.cli.init_case_states --campaign-root . --check --verbose
```

Observed result:

```text
cases_processed=10
cases_with_errors=0
errors=0
destructive_operations=0
```

After this phase, all cases had `state.json` and `validation.json` initialized and were in:

```text
Created
```

## Phase 1c — Simulation completion backfill

Implemented CLI:

```text
campaign_workflow.cli.mark_sim_done
```

Purpose:

```text
Backfill Created -> Sim_done for campaigns that already exist and have evidence that simulation output was produced.
```

This command does not:

```text
open HDF5 files
validate openPMD/HDF5
run analysis
authorize cleanup
delete anything
modify WarpX/PyWarpX inputs
modify physics parameters
```

Completion evidence can be:

```text
simulation.completion_marker exists
OR
all required raw diagnostics have at least min_files matching configured globs
```

For the SUNRISE campaign, `post/sim_done.json` did not exist, so completion was inferred from the presence of required raw diagnostic files:

```text
diags/diag1/*.h5
```

Commands used on SUNRISE:

```bash
python -m campaign_workflow.cli.mark_sim_done --campaign-root . --case-id 0 --dry-run --verbose
python -m campaign_workflow.cli.mark_sim_done --campaign-root . --case-id 0 --verbose
```

The command was then used for the remaining 9 cases.

After this phase, all 10 cases were marked:

```text
Sim_done
```

## Phase 2c — Real SUNRISE raw validation against WarpX/openPMD HDF5

Real raw validation was run on:

```text
/gpfs/home/jrodriguez/warpx_runs/capillaries_bo_top10_particles
```

First dry-run on case 0:

```bash
python -m campaign_workflow.cli.validate_raw_case \
  --campaign-root . \
  --case-id 0 \
  --dry-run \
  --verbose
```

Observed result:

```text
case_ok=True
target_state=Raw_validated
DIAG: fields_openpmd kind=openpmd_hdf5 ok=True files=65 required=True
errors=0
destructive_operations=0
```

Then case 0 was validated in write mode.

Resulting state:

```text
Raw_validated
```

Raw validation evidence included:

```text
diagnostic_name: fields_openpmd
diagnostic_kind: openpmd_hdf5
ok: True
glob: diags/diag1/*.h5
file_count: 65
total_size_bytes: 2864856800
errors: []
warnings: []
manifest_path: manifests/raw_fields_openpmd.json
cleanup.cleanup_allowed: false
cleanup.reason: Raw validation alone does not authorize cleanup. Reduced validation is still required.
```

Raw manifest included:

```text
diagnostic: fields_openpmd
kind: openpmd_hdf5
files: 65
destructive_operations: 0
```

The remaining 9 cases were raw-validated.

Final campaign status after this phase:

```text
10/10 cases Raw_validated
raw manifests exist under each case's manifests/raw_fields_openpmd.json
cleanup still not allowed
0 raw files deleted
0 directories deleted
0 WarpX/PyWarpX input modifications
0 physics parameter modifications
```

## Phase 4 — Generic reduced CSV validation

Phase 4 implemented generic reduced-output validation independent of analysis execution.

Purpose:

```text
Validate configured reduced outputs independently of analysis execution.
Allow existing externally-generated reduced CSV outputs to be validated by the workflow.
Keep the core generic and avoid baking guiding/capillary physics into the workflow.
```

Implemented files:

```text
campaign_workflow/analysis/csv_contract.py
campaign_workflow/cli/validate_reduced_case.py
tests/test_reduced_validation.py
```

Updated file:

```text
campaign_workflow/core/transitions.py
```

Main command:

```bash
python -m campaign_workflow.cli.validate_reduced_case \
  --campaign-root . \
  --case-id 0 \
  --dry-run \
  --verbose
```

Normal reduced validation requires previous raw validation evidence. In the normal path, reduced validation is allowed only when raw diagnostics have already been validated and recorded in `validation.json`.

Normal state flow:

```text
Raw_validated -> Reduced_validated
```

Failure state flow:

```text
Raw_validated -> Validation_failed
```

Reduced validation checks:

```text
campaign.json analysis.outputs is present and non-empty
output kind is supported; currently csv
configured path is relative
output file exists
output path resolves inside CASE_DIR
symlink escapes are rejected
directories are rejected
file is non-empty
suffix matches allowed suffixes; default .csv
CSV is readable with Python stdlib csv
CSV has at least min_rows data rows
CSV contains required_columns if configured
```

Reduced validation writes evidence under:

```text
validation.json["reduced"][output_name]
```

Successful reduced validation does not authorize cleanup.

Important invariant:

```text
Reduced_validated != raw safely deletable
```

Cleanup remains blocked after reduced validation:

```json
{
  "cleanup_allowed": false,
  "reason": "Reduced validation succeeded, but cleanup requires a later explicit eligibility phase."
}
```

The first real normal-path validation was run on:

```text
/gpfs/home/jrodriguez/warpx_runs/capillaries_bo_top10_particles
```

Result:

```text
states: {'Reduced_validated': 10}
guiding_metrics reduced ok: 10
cleanup_allowed: 0
```

No raw files were deleted.

## Phase 4b — Legacy reduced-only validation mode

A legacy reduced-only mode was implemented for old campaigns where reduced CSV outputs exist but raw diagnostics are no longer available.

Main flag:

```bash
--legacy-reduced-only
```

Example command:

```bash
python -m campaign_workflow.cli.validate_reduced_case \
  --campaign-root . \
  --legacy-reduced-only \
  --dry-run
```

Purpose:

```text
Adopt legacy campaigns into the workflow without falsifying raw validation evidence.
Validate existing reduced CSV outputs even when raw WarpX/openPMD diagnostics have already been deleted or were not preserved.
Keep cleanup permanently blocked for these legacy cases.
```

Legacy state flow:

```text
Created -> Reduced_validated
```

Failure state flow:

```text
Created -> Validation_failed
```

Allowed legacy revalidation states:

```text
Created
Validation_failed
Reduced_validated
```

Legacy mode explicitly does not:

```text
validate raw diagnostics
create raw manifests
populate validation.json["raw"]
mark raw_evidence_ok=true
authorize cleanup
delete anything
modify WarpX/PyWarpX inputs
modify physics parameters
```

Legacy mode writes explicit evidence:

```json
{
  "legacy": {
    "schema_version": 1,
    "legacy_reduced_only": true,
    "raw_evidence_mode": "legacy_reduced_only",
    "raw_evidence_ok": false,
    "cleanup_allowed": false,
    "operation": "validate_reduced_case",
    "reason": "Reduced outputs were validated for a legacy campaign without available raw diagnostic evidence. No raw manifests were created and cleanup must remain disabled."
  }
}
```

Each reduced output summary also records:

```json
{
  "legacy_reduced_only": true
}
```

Cleanup remains blocked:

```json
{
  "cleanup_allowed": false,
  "reason": "Legacy reduced-only validation succeeded without raw diagnostic evidence. Cleanup is not allowed."
}
```

The first real legacy campaign validated with this mode was:

```text
/gpfs/home/jrodriguez/warpx_runs/capillaries_bo_full_campaign
```

This campaign was legacy because no raw diagnostics remained:

```text
diags/**/*.h5   -> 0 files
diags/**/*.hdf5 -> 0 files
diags/**/*.bp   -> 0 files
diags/**/*.bp4  -> 0 files
diags/**/*.bp5  -> 0 files
```

but reduced CSV outputs existed:

```text
guiding_metrics.csv
```

The campaign had:

```text
cases.tsv lines: 352
header lines: 1
real cases: 351
CASE_ID range: 0..350
```

Auxiliary directories existed and were not part of `cases.tsv`:

```text
analysis_outputs
array_logs
dryrun_logs
```

The workflow processed only the cases listed in `cases.tsv`.

Final result:

```text
states: {'Reduced_validated': 351}
guiding_metrics reduced ok: 351
legacy reduced-only: 351
raw nonempty: 0
cleanup_allowed: 0
```

Meaning:

```text
The reduced CSVs are valid.
The raw diagnostics are not validated.
The campaign is adopted as legacy reduced-only.
Cleanup remains impossible from this evidence.
```

Important invariant:

```text
Never convert a legacy reduced-only campaign into raw-validated state unless real raw diagnostics are available and pass raw validation.
```

## Phase 5 — Storage snapshot

Phase 5 implemented campaign-level and case-level storage accounting without modifying raw data.

Implemented files:

```text
campaign_workflow/core/storage.py
campaign_workflow/cli/storage_snapshot.py
tests/test_storage_snapshot.py
```

Commands run on SUNRISE:

```bash
python -m campaign_workflow.cli.storage_snapshot \
  --campaign-root . \
  --dry-run \
  --verbose

python -m campaign_workflow.cli.storage_snapshot \
  --campaign-root . \
  --verbose
```

Observed real campaign result on `top10_particles`:

```text
campaign_name=capillaries_bo_top10_particles
case_count=10
validated_cases=10
submitted_cases=10
case_total_GB=39.808848
raw_live_GB=36.532526
safe_cleanup_candidate_GB=36.532526
raw_delete_eligible_GB=0.0
raw_deleted_GB=0.0
avg_raw_per_case_GB=3.653253
cases_by_state={'Reduced_validated': 10}
errors=0
destructive_operations=0
```

Snapshot written to:

```text
snapshots/storage_snapshot_latest.json
```

Interpretation:

```text
All 10 cases had both raw and reduced validation evidence.
The raw HDF5 files were cleanup candidates from an evidence perspective.
No case was yet explicitly eligible for deletion.
No files were deleted.
```

## Phase 5b — Raw delete eligibility

Implemented CLI:

```text
campaign_workflow.cli.mark_raw_delete_eligible
```

Implemented tests:

```text
tests/test_raw_delete_eligibility.py
```

Purpose:

```text
Move cases from Reduced_validated to Raw_delete_eligible only after rechecking raw validation evidence, reduced validation evidence, legacy status, cleanup globs, and candidate raw files.
```

This command does not delete anything and does not create the final delete manifest.

It writes:

```text
post/raw_delete_eligible.json
validation.json cleanup eligibility evidence
state.json transition to Raw_delete_eligible
```

Commands run on SUNRISE:

```bash
python -m campaign_workflow.cli.mark_raw_delete_eligible \
  --campaign-root . \
  --dry-run \
  --verbose

python -m campaign_workflow.cli.mark_raw_delete_eligible \
  --campaign-root . \
  --verbose
```

Observed result:

```text
cases_processed=10
cases_ok=10
cases_with_errors=0
errors=0
mode=write
destructive_operations=0
```

Real campaign state after this phase:

```text
10/10 cases Raw_delete_eligible
cleanup_allowed=True in validation.json for those cases
post/raw_delete_eligible.json exists for those cases
0 raw files deleted
0 directories deleted
```

Important distinction:

```text
Raw_delete_eligible means cleanup dry-run may be created.
It does not mean raw files should be deleted immediately.
Deletion still requires a dry-run manifest and explicit execute phase.
```

## Phase 6 — Cleanup dry-run manifests

Implemented/updated:

```text
campaign_workflow/cli/cleanup_raw_case.py
tests/test_cleanup_raw_dry_run.py
```

Dry-run command:

```bash
python -m campaign_workflow.cli.cleanup_raw_case \
  --campaign-root . \
  --dry-run \
  --verbose
```

Purpose:

```text
Resolve cleanup globs.
Revalidate state/evidence/path safety.
Write exact dry-run manifests.
Do not delete anything.
```

First test on case 0:

```text
case_ok=True
state=Raw_delete_eligible
manifest_type=raw_cleanup_dry_run
file_count=65
total_size_GB=2.668106
destructive_operations=0
first file=diags/diag1/openpmd_000000.h5
last file=diags/diag1/openpmd_256000.h5
```

A direct `find` check confirmed that the 65 HDF5 files still existed after dry-run.

Then the command was run over all 10 cases.

Observed result:

```text
cases_processed=10
cases_ok=10
cases_with_errors=0
errors=0
manifest_files=890
manifest_bytes=39226500800
manifest_GB=36.532526
mode=dry-run-manifest
destructive_operations=0
```

Each case got:

```text
manifests/raw_delete_manifest.json
validation.json cleanup.delete_manifest_ready=True
```

No raw files were deleted.

## Phase 7 — Cleanup execute implemented locally, not executed on SUNRISE

`campaign_workflow.cli.cleanup_raw_case` was extended locally to support:

```bash
python -m campaign_workflow.cli.cleanup_raw_case \
  --campaign-root . \
  --case-id <ID> \
  --execute \
  --verbose
```

Purpose:

```text
Delete only files listed in the validated dry-run manifest.
Do not reinterpret globs.
Do not delete directories.
Do not delete outside CASE_DIR.
Write post/raw_deleted.json.
Update validation.json.
Transition Raw_delete_eligible -> Raw_deleted.
```

A local unittest issue was fixed because argparse's required mutually exclusive mode group raised `SystemExit(2)` before `main()` could return a code.

Fix pattern:

```python
try:
    args = build_parser().parse_args(argv)
except SystemExit as exc:
    if isinstance(exc.code, int):
        return exc.code
    return 2
```

After the fix, the full local suite passed:

```text
Ran 54 tests
OK, with expected skipped tests depending on platform symlink permissions
```

Important decision:

```text
Do NOT execute cleanup on top10_particles for now.
```

Reason:

```text
The top10_particles campaign is currently the only small real preserved raw-HDF5 corpus on SUNRISE.
The quota is 1 TB and there is no immediate pressure to free ~36.5 GB.
These raw files are useful for testing real analysis integration, multi-diagnostic contracts, stricter openPMD validation, storage snapshots, and future cleanup execution.
```

Therefore:

```text
cleanup --execute exists in code/tests but has intentionally not been run on SUNRISE.
top10_particles raw HDF5 files should be preserved until a replacement raw corpus exists.
```

## Phase B — Real external analysis integration

The workflow successfully invoked the real guiding analysis module as an external command adapter on SUNRISE.

The integration respects the environment split:

```text
campaign-workflow-py310    -> workflow orchestration, state, validation, manifests, storage, cleanup
guiding-analysis-py310     -> physical guiding analysis
warpx-26.05-py314          -> PyWarpX/WarpX simulation execution
```

No guiding package is imported by `campaign-workflow`.

The production analysis path for the capillary campaign is:

```text
CASE_DIR/diags/diag1/*.h5
  -> guiding_analysis_module/scripts/analyze_case.py
  -> CASE_DIR/guiding_metrics.csv
  -> campaign_workflow reduced CSV validation
```

The SUNRISE wrapper is:

```text
examples/capillary_guiding/run_guiding_case_analysis_sunrise.sh
```

The runtime campaign config for:

```text
/gpfs/home/jrodriguez/warpx_runs/capillaries_bo_top10_particles
```

was updated so that:

```text
analysis.name    = guiding
analysis.kind    = command
analysis.adapter = command
analysis.command = bash <workflow>/examples/capillary_guiding/run_guiding_case_analysis_sunrise.sh {case_dir}
```

The official reduced output is overwritten in place:

```text
CASE_DIR/guiding_metrics.csv
```

No alternate `post/guiding_rerun/` output is used. That artificial branch was explicitly rejected because it does not reflect the real production workflow.

First successful real case:

```text
case_id = 0
000_from_042_f20_chan_n4e18cm3_L10mm_d500um_focm5mm_rz
```

Successful command:

```bash
python -m campaign_workflow.cli.analyze_case \
  --campaign-root . \
  --case-id 0 \
  --allow-rerun-from-raw-delete-eligible \
  --verbose
```

Observed result:

```text
analysis_name=guiding
analysis_adapter=command
case_ok=True
target_state=Reduced_validated
adapter_ok=True
OUTPUT: guiding_metrics kind=csv ok=True rows=65 required=True
destructive_operations=0
```

Analysis stdout log showed all field iterations were read and the final CSV was written:

```text
[READ] iteration 0
...
[READ] iteration 256000
[OK] wrote CASE_DIR/guiding_metrics.csv
```

Stderr log was empty.

Resulting state:

```text
Reduced_validated
```

Resulting validation evidence:

```text
analysis.guiding.ok = True
analysis.guiding.return_code = 0
reduced.guiding_metrics.ok = True
reduced.guiding_metrics.path = guiding_metrics.csv
reduced.guiding_metrics.row_count = 65
cleanup.cleanup_allowed = False
cleanup.delete_manifest_ready = False
cleanup.previous_cleanup_evidence_invalidated = True
```

Note:

```text
validation.json uses row_count for CSV row counts.
The CLI may print rows=..., but scripts should read row_count.
```

## Analysis re-run policy

The workflow supports explicit reanalysis from:

```text
Raw_delete_eligible
Reduced_validated
```

using:

```bash
--allow-rerun-from-raw-delete-eligible
--allow-rerun-from-reduced-validated
```

Reason:

```text
Preserved raw diagnostics may need to be reanalyzed after external analysis-module changes.
After successful analysis, a case naturally ends in Reduced_validated, so future reanalysis must not require manual state edits.
```

Supported successful reanalysis transitions:

```text
Raw_delete_eligible -> Analyzing -> Reduced_validated
Reduced_validated   -> Analyzing -> Reduced_validated
Analysis_failed     -> Analyzing -> Reduced_validated
```

Reanalysis from final-ish states must:

```text
verify raw validation evidence
verify preserved raw files still exist
reject legacy reduced-only cases
reject cases with raw_deleted evidence
write stdout/stderr logs
overwrite/update configured reduced outputs
revalidate reduced outputs
end in Reduced_validated on success
end in Analysis_failed on failure
keep cleanup blocked
invalidate previous cleanup eligibility / delete manifest readiness
```

If a case has been reanalyzed, old cleanup eligibility/manifests must not be used. To clean later, rerun:

```bash
python -m campaign_workflow.cli.mark_raw_delete_eligible ...
python -m campaign_workflow.cli.cleanup_raw_case --dry-run ...
```

## SUNRISE environment history

SUNRISE system Python was too old:

```text
/usr/bin/python3 -> Python 3.6.8
```

Modern Python appears only after loading GCC:

```bash
module purge
module load GCC/12.1.0
module load Python/3.10.12
```

Dedicated workflow venv:

```text
~/apps/venvs/campaign-workflow-py310
```

Activation helper:

```text
~/apps/env/campaign-workflow.sh
```

Expected activation:

```bash
source ~/apps/env/campaign-workflow.sh
```

Working environment:

```text
GCC/12.1.0 + Python/3.10.12 + ~/apps/venvs/campaign-workflow-py310
```

Important h5py installation note:

A plain:

```bash
python -m pip install h5py
```

failed because it attempted to compile `h5py 3.16.0` against system HDF5 1.12.0.

Working install:

```bash
python -m pip uninstall -y h5py numpy
python -m pip install --only-binary=:all: "numpy<2" "h5py==3.10.0"
```

SUNRISE checkout:

```text
~/apps/src/campaign-workflow
```

Old-Git-compatible update command:

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

## Real campaign history summary

### top10_particles

Campaign root:

```text
/gpfs/home/jrodriguez/warpx_runs/capillaries_bo_top10_particles
```

Physical layout:

```text
diags/diag1                  # fields, legacy name
diags/electron_particles     # particle diagnostic
guiding_metrics.csv          # official reduced guiding output
```

This campaign became the first small real preserved-raw corpus for workflow testing.

Historical states reached:

```text
Created
Sim_done
Raw_validated
Reduced_validated
Raw_delete_eligible
Reduced_validated again after external analysis rerun
```

Raw diagnostics were preserved throughout.

Cleanup dry-run manifests were created, but cleanup execute was intentionally not run.

### full_campaign

Legacy campaign root:

```text
/gpfs/home/jrodriguez/warpx_runs/capillaries_bo_full_campaign
```

This campaign had no preserved raw diagnostics but had reduced CSV outputs.

It was adopted with legacy reduced-only validation.

Final result:

```text
states: {'Reduced_validated': 351}
guiding_metrics reduced ok: 351
legacy reduced-only: 351
raw nonempty: 0
cleanup_allowed: 0
```

Meaning:

```text
Reduced CSVs are valid.
Raw diagnostics are not validated.
Cleanup remains impossible from this evidence.
```

## Logical diagnostic naming decision

For future campaigns, use logical names:

```text
fields              -> field diagnostics
plasma_electrons    -> pre-existing / plasma electron particle diagnostics
ionized_electrons   -> electrons created by ionization
```

For the legacy/current `top10_particles` campaign, the physical fields directory remains:

```text
diags/diag1
```

Do not rename existing directories in this campaign. Use logical names in `campaign.json` and keep paths pointing to the real legacy layout.

## Historical cleanup safety summary

Absolute prohibitions:

```text
Never delete campaign root.
Never delete case directories.
Never run rm -rf on user data.
Never delete files outside CASE_DIR.
Never delete files not listed in a manifest.
Never delete raw diagnostics before reduced validation.
Never delete while case is running.
Never use state.json alone as deletion permission.
```

Allowed deletion in V1:

```text
explicit regular files
inside CASE_DIR
matching configured raw_delete_globs
listed in validated manifest
after raw validation
after reduced validation
after delete manifest dry-run
during explicit cleanup execute only
```

The first real cleanup execute on SUNRISE has not yet been performed.

## Phase C — Simulation lifecycle contract and marker CLIs

Phase C defined and implemented the first generic simulation lifecycle layer.

Purpose:

```text
Represent simulation execution lifecycle explicitly before writing SUNRISE/SLURM wrappers.
Keep WarpX/PyWarpX execution external to campaign-workflow.
Avoid making SLURM the core architecture.
Preserve Created -> Sim_done backfill for already-existing campaigns.
Add explicit Submitted / Running / Failed evidence paths.
```

Implemented documentation:

```text
docs/SIMULATION_MODULE_CONTRACT.md
```

This contract defines:

```text
workflow-owned simulation lifecycle evidence
external simulation module/wrapper responsibilities
required marker files
logging expectations
environment separation
scheduler metadata
preflight semantics
exit-code semantics
cleanup safety invariants
SUNRISE top10 particles example
```

The contract keeps the separation:

```text
campaign-workflow-py310    -> workflow orchestration
guiding-analysis-py310     -> guiding/particle analysis
warpx-26.05-py314          -> PyWarpX/WarpX simulation execution
```

Implemented core transition support:

```text
Created   -> Submitted
Submitted -> Running
Running   -> Sim_done
Running   -> Failed
Created   -> Sim_done     # legacy/backfill adoption path
```

Implemented marker CLIs:

```text
campaign_workflow.cli.mark_sim_submitted
campaign_workflow.cli.mark_sim_running
campaign_workflow.cli.mark_sim_failed
campaign_workflow.cli.mark_sim_done
```

Implemented simulation helper package:

```text
campaign_workflow/simulation/
```

Marker files:

```text
CASE_DIR/post/sim_submitted.json
CASE_DIR/post/sim_running.json
CASE_DIR/post/sim_done.json
CASE_DIR/post/sim_failed.json
```

`mark_sim_done` was extended so that it now writes:

```text
CASE_DIR/post/sim_done.json
```

in addition to state transition and `validation.json` simulation evidence.

`mark_sim_done` still supports legacy/backfill adoption:

```text
Created -> Sim_done
```

using either:

```text
simulation.completion_marker exists
OR all required raw diagnostics have at least min_files matching configured globs
```

It now also supports the managed execution path:

```text
Running -> Sim_done
```

This fixed the earlier design mismatch where `Running` was rejected despite being the natural state before successful simulation completion.

Simulation marker evidence updates `validation.json` under:

```text
validation["simulation"]
```

and maintains:

```text
validation["simulation"]["latest"]
```

Simulation lifecycle evidence alone never authorizes cleanup:

```text
validation["cleanup"]["cleanup_allowed"] = false
```

Implemented/updated tests:

```text
tests/test_simulation_lifecycle.py
tests/test_simulation_marker_clis.py
tests/test_mark_sim_done.py
```

Tests cover:

```text
Created -> Submitted
Submitted -> Running
Running -> Sim_done
Running -> Failed
Created -> Sim_done backfill
marker JSON creation
validation.json simulation evidence
validation.json simulation.latest evidence
dry-run writes nothing
invalid transitions fail without writing
cleanup remains blocked
already Sim_done is no-op success
```

Latest local suite after this phase:

```text
Ran 75 tests
OK
```

with expected skipped symlink-related tests on Windows depending on permissions.

Real SUNRISE simulation context collected during this phase:

```text
campaign root: /gpfs/home/jrodriguez/warpx_runs/capillaries_bo_top10_particles
existing script: submit_top10_particles_array.sh
execution: sbatch submit_top10_particles_array.sh
case-local input: CASE_DIR/input.py
case-local environment: CASE_DIR/case.env
preflight: CAP_DRY_RUN=1 python input.py 2
run command: srun -n "${SLURM_NTASKS}" python input.py 2
case-local logs: CASE_DIR/logs/*.out and CASE_DIR/logs/*.err
campaign-level logs: CAMPAIGN_ROOT/array_logs/
```

Real WarpX/PyWarpX environment observed:

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

There is also a helper environment file:

```text
~/apps/env/load_sunrise_warpx_py_stack.sh
```

but it may need operational verification.

Important design decision:

```text
Do not yet implement a large submitter.
Do not yet launch a new campaign.
Do not yet touch WarpX/PyWarpX physics inputs.
Do not put workflow logic inside SLURM.
```

Next intended phase:

```text
Build a thin SUNRISE simulation wrapper around the existing working script pattern.
Split responsibilities into:
  - SLURM array selector
  - case-local WarpX runner
  - workflow marker CLIs
Then test on a disposable/small integration campaign before attempting a full simulation->cleanup workflow.
```


## Future phases recorded historically but not yet completed

These items were discussed as future phases, but are not completed in this history file.

### Thin SLURM wrappers

Expected idea:

```text
slurm/submit_validate_raw_array.sh
slurm/submit_analysis_array.sh
slurm/submit_validate_reduced_array.sh
slurm/submit_cleanup_array.sh
```

SLURM scripts should be thin wrappers:

```text
set strict bash mode
cd campaign root
load environment if needed
export PYTHONPATH
call python -m campaign_workflow.cli.<command>
write logs
```

No core logic belongs in SLURM scripts.

Before writing operational SLURM wrappers, ask for existing working SUNRISE/SLURM files if needed.

### Passive optimizer tick

Purpose:

```text
Read validated reduced outputs.
Build observations.tsv.
Compute objective/score.
Produce rankings and plots.
Do not launch jobs yet.
```

This is explicitly after validation/cleanup base works.

### MORBO / recursive optimization

Future phase.

Purpose:

```text
optimizer_tick reads observations
decides if enough new data exists
proposes new candidates
writes candidate batch manifests
submits simulation jobs if quota allows
```

Not part of V1.

Future optimizer architecture must preserve:

```text
raw metrics in results/reduced outputs
objective/score as derived layer
explicit seeds
checkpoint/restart semantics
safe quota checks
no dependency on a resident daemon in V1
```

### Simulation lifecycle contract

Before writing SLURM simulation wrappers, the generic simulation evidence contract still needs to be designed.

Expected concepts:

```text
Submitted
Running
Sim_done
Failed
Retryable
post/sim_submitted.json
post/sim_running.json or logs/sim_runtime.json
post/sim_done.json
post/sim_failed.json
scheduler_job_id
submit_command
run_command
environment_name
started_at
finished_at
return_code
stdout/stderr log paths
```

The workflow must not edit PyWarpX input templates unless explicitly requested.

The simulation backend should be external-command based at first:

```text
campaign_workflow runs or wraps a configured command
the PyWarpX/simulation module remains separate
SLURM remains an execution backend, not the whole architecture
```

### Later ionization integration campaign

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

Goal:

```text
integration test
not optimization yet
```

## Phase D — Managed case-local cycle execution philosophy

The execution philosophy was refined before implementing the SUNRISE production
wrappers.

Earlier documentation described simulation, raw validation, analysis, reduced
validation, cleanup, and future optimization as separate jobs. This remains true
at the semantic/workflow level, but it is no longer required that each phase maps
to a separate SLURM job.

Reason:

* WarpX/PyWarpX simulation dominates walltime.
* Raw validation, analysis/reduced validation, cleanup eligibility, and cleanup
  dry-run are usually short compared with simulation.
* Submitting a new SLURM array for every cheap case-local phase adds avoidable
  scheduler overhead.
* A resident parent orchestrator is explicitly not wanted.

New preferred production model:

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
  -> optional cleanup_raw_case --execute
```

The phase boundaries remain explicit:

* each phase writes evidence;
* each phase has inspectable logs;
* failures stop the remaining phases for that case;
* raw validation is still required after simulation;
* reduced-output validation is still required after analysis;
* cleanup eligibility remains explicit;
* cleanup dry-run must create a manifest before deletion;
* cleanup execute must require explicit confirmation;
* simulation completion alone never authorizes cleanup.

There is still no parent orchestrator:

* no resident daemon;
* no long-walltime job waiting or polling;
* no global controller sitting idle in T48H.

Campaign-wide operations remain separate jobs because they are intrinsically
global:

```text
storage_snapshot
optimizer_tick
objective aggregation
candidate proposal
campaign summaries
```

For BO/MORBO, the optimizer tick is expected to be campaign-wide because it
operates on validated reduced outputs across many cases. It may propose or submit
new candidates, but it must not become a resident daemon.

The next implementation target after this decision is a managed case-local
SUNRISE SLURM wrapper, tentatively:

```text
examples/sunrise/submit_case_cycle_array.sh
```

This wrapper should execute one full case-local cycle and must not include
optimizer/MORBO logic or edit WarpX/PyWarpX physics inputs.

## Phase E — Explicit case directory materialization from `cases.tsv`

A missing bootstrap phase was identified while preparing real WarpX/SUNRISE campaigns.

Before this phase, `campaign-workflow` already had logic for state initialization, validation, analysis, storage snapshots, and cleanup planning/execution, but it did not yet own the generic creation of case directories from `cases.tsv`.

This caused an architectural problem: the WarpX runner expected case directories to exist, but the workflow did not yet provide a clean generic way to create them.

A previous one-off SUNRISE prototype script had attempted to materialize cases, but it mixed too many responsibilities:

```text
created case directories
copied input_template.py
generated case.env
mapped cases.tsv columns into CAP_* environment variables
included capillary/guiding/ionization-specific assumptions
```

That prototype was useful as operational context, but it was rejected as a workflow-core design.

The correct architectural decision was to first implement a minimal generic phase:

```text
create case directories from cases.tsv
do it safely
do it idempotently
do not touch physics-specific files
do not touch simulation execution
do not touch analysis
do not touch cleanup
```

Implemented files:

```text
campaign_workflow/core/case_dirs.py
campaign_workflow/cli/create_case_dirs.py
tests/test_create_case_dirs.py
```

New CLI:

```bash
python -m campaign_workflow.cli.create_case_dirs \
  --campaign-root . \
  --dry-run \
  --verbose
```

Write mode:

```bash
python -m campaign_workflow.cli.create_case_dirs \
  --campaign-root . \
  --verbose
```

Intended campaign bootstrap sequence:

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

Behavior implemented:

```text
read campaign.json
read cases.tsv using the configured manifest parser
identify CASE_ID and CASE_NAME
reject missing or empty case manifests
reject invalid CASE_ID values through existing manifest checks
reject empty CASE_NAME values
reject duplicate CASE_NAME values
reject absolute CASE_NAME paths
reject CASE_NAME values containing ..
reject any resolved case path outside campaign_root
create CASE_DIR
create standard subdirectories
be idempotent
print a clear summary
report destructive_operations=0
```

Standard subdirectories created:

```text
logs/
post/
manifests/
locks/
diags/
checkpoints/
```

Negative contract:

```text
create_case_dirs does not modify cases.tsv
create_case_dirs does not modify campaign.json
create_case_dirs does not copy input_template.py
create_case_dirs does not create input.py
create_case_dirs does not create case.env
create_case_dirs does not map physical columns into environment variables
create_case_dirs does not write state.json
create_case_dirs does not write validation.json
create_case_dirs does not touch raw diagnostics
create_case_dirs does not touch reduced outputs
create_case_dirs does not run simulation
create_case_dirs does not run analysis
create_case_dirs does not run cleanup
```

Test coverage added:

```text
dry-run does not create directories
write mode creates directories from CASE_NAME
standard subdirectories are created
second execution is idempotent
CASE_NAME with .. is rejected
absolute CASE_NAME is rejected
duplicate CASE_NAME is rejected
empty CASE_NAME is rejected
cases.tsv without rows is rejected
existing files inside CASE_DIR are preserved
new code is checked against rm -rf / shutil.rmtree
summary includes destructive_operations=0
```

Latest known test command:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

Latest known result:

```text
OK
```

Next operational step:

```text
pull the committed repo on SUNRISE
activate the campaign-workflow Python environment
run create_case_dirs --dry-run on a real campaign root
run create_case_dirs in write mode if dry-run is correct
run init_case_states after directories exist
inspect the resulting case layout before simulation execution
```

Simulation-specific materialization remains future work. In particular, copying/linking `input_template.py`, generating `case.env`, or mapping `cases.tsv` columns into environment variables must be implemented later as a separate auditable layer if needed.

## Phase F — Real CLPU particle campaign production run on SUNRISE

A real production campaign was launched on SUNRISE:

```text
/HOME/jrodriguez/warpx_runs/capillaries_bo_particles_campaign

This was the first campaign where the managed case-local cycle was exercised
against a full real WarpX/PyWarpX workload with cleanup execute enabled.

Campaign ingredients:

351 cases in cases.tsv
WarpX/PyWarpX input_template.py
case-local input.py and case.env materialized per case
fields diagnostic under diags/fields
plasma electron diagnostic under diags/plasma_electrons for non-vac cases
guiding_metrics.csv reduced output
particle_analysis/particle_summary.csv reduced output for non-vac cases

The campaign used separated environments:

campaign-workflow-py310    -> workflow orchestration
warpx-26.05-py314          -> PyWarpX/WarpX simulation execution
guiding-analysis-py310     -> guiding/particle analysis

The case-local SLURM cycle executed:

mark_sim_submitted
mark_sim_running
run_warpx_case_sunrise.sh
mark_sim_done / mark_sim_failed
validate_raw_case
analyze_case
mark_raw_delete_eligible
cleanup_raw_case --dry-run
cleanup_raw_case --execute

Cleanup execute was intentionally enabled for this production campaign because
raw HDF5 volume would otherwise approach quota limits overnight.

Observed production behavior:

SLURM arrays launched with non-vac and vac cases separated
non-vac cases used CAMPAIGN_RUN_PARTICLE_ANALYSIS=always
vac cases used CAMPAIGN_RUN_PARTICLE_ANALYSIS=never
cleanup execute produced raw_deleted.json markers
guiding_metrics.csv count matched Raw_deleted total
particle_summary.csv count matched non-vac Raw_deleted count
sim_failed markers initially remained zero
quota remained controlled because raw cleanup executed as cases finished

This demonstrated that the workflow can run:

simulation
-> raw validation
-> guiding analysis
-> particle analysis
-> reduced validation
-> cleanup dry-run
-> cleanup execute

inside a real SUNRISE campaign.

Phase G — Production monitoring scripts and reduced-data candidate discovery

During the campaign, temporary monitoring scripts were created in the campaign
root:

watch_campaign.py
estimate_running_eta.py
rank_combined_candidates.py

These scripts were not committed architecture, but they provided useful evidence
for future formal workflow CLIs.

watch_campaign.py monitored:

SLURM running/pending jobs
workflow states
states by PLASMA_KIND
states by PLATEAU_LENGTH_MM
guiding_metrics.csv count
particle_summary.csv count
raw_delete_manifest.json count
raw_deleted.json count
live HDF5 count and size
real user quota

The working quota command on SUNRISE was:

lfs quota -h -u "$USER" .

rank_combined_candidates.py showed that even early f20 cases produced useful
electron reduced metrics.

A first strong region appeared around:

n0 ≈ 6e18 cm^-3
L ≈ 10 mm
diameter ≈ 500 um
focus ≈ 0 to +5 mm

Example useful metrics from particle_summary.csv:

charge_hot_pC
n_macroparticles_hot
Emax_hot_MeV
E95_hot_MeV
Emean_hot_MeV
q_long_mean_hot_mm
u_long_mean_hot

This changed the role of the workflow: it is no longer only a safe file/state
manager. It now produces persistent reduced data that can support real candidate
selection and future Bayesian optimization.

Phase H — Walltime risk discovered in real production

The real campaign discovered that a static T6H walltime is insufficient for some
long cases.

Some 25 mm cases had:

max_steps = 448000
avg_s_per_step ≈ 0.07

Estimated WarpX runtime:

448000 * 0.07 s ≈ 8.7 h

before analysis and cleanup.

An ad-hoc ETA script classified running cases as:

OK
TIGHT
TIMEOUT_RISK
UNKNOWN

using:

current_step
max_steps
avg_s_per_step
elapsed walltime
remaining walltime
safety margin

This revealed the need for a future formal walltime guard.

The intended future feature is a campaign maintenance tick that can:

acquire a campaign-wide lock
detect cases that cannot finish within the current walltime
cancel those jobs before scheduler kill
mark them as walltime_insufficient
write explicit failure evidence
clean partial raw files through a dedicated partial-raw cleanup manifest
plan reruns in a longer partition
submit rerun arrays if configured

This should be implemented in campaign-workflow, not in guiding_analysis.

The feature must remain generic and scheduler-policy driven. It must not know
about capillary physics.

Phase I — Future cooperative maintenance architecture

The production campaign motivated a new architecture pattern:

case-local jobs perform their own work
then attempt a campaign-wide maintenance tick

The tick is cooperative, short-lived, and lock-protected.

It is not a parent daemon.

It may eventually include:

quota_guard
walltime_guard
orphan_running_guard
rerun_planner
rerun_launcher
array_throttle_adjuster
optimizer_readiness_check

This preserves the no-daemon design while allowing the campaign to self-repair
and self-regulate during long unattended SUNRISE runs.

Important rule:

a case job does not own other cases;
a locked campaign maintenance operation may inspect and modify multiple cases
according to workflow rules.

Future implementation should probably introduce:

campaign_workflow.cli.estimate_walltime_risk
campaign_workflow.cli.maintenance_tick
campaign_workflow.cli.plan_reruns

and tests using unittest.

No OpenCode was used for this workflow.


---

## 9. `README.md`

Add this short section after `## Design principle`.

```markdown
## Production lesson: cooperative maintenance without a daemon

A real SUNRISE WarpX/PyWarpX campaign showed that long campaigns need more than a
static SLURM array.

The workflow direction is now:

```text
case-local cycle
+ short lock-protected campaign maintenance ticks

Case-local tasks run simulation, validation, analysis, and cleanup for one case.

After finishing, a task may attempt a campaign-wide maintenance tick. If it gets
the campaign lock, it may inspect quota, walltime risk, stale running cases, and
rerun plans. If it does not get the lock, it exits normally.

This preserves the no-resident-daemon rule while allowing unattended campaigns to
self-regulate on HPC systems.