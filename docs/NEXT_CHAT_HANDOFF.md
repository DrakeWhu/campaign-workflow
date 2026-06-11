# Next chat handoff

## Project

General campaign workflow for simulation -> raw validation -> analysis -> reduced validation -> safe cleanup.

Development is done locally under Git. SUNRISE receives the workflow through Git checkout/pull/tag, not rsync.

## Current phase

Fase 0: repository documentation and configuration contract.

No operational scripts have been implemented yet.

## Current design decisions

- Work directly with ChatGPT in small auditable pieces.
- Do not use OpenCode for this workflow.
- Use Git for local development and deployment to SUNRISE.
- Do not center the architecture on the capillary guiding campaign.
- Treat capillary guiding as the first production example.
- Core workflow must be general.
- WarpX/PyWarpX is the first simulation backend.
- openPMD/HDF5 is the first raw diagnostic adapter.
- guiding analysis is the first analysis adapter.
- No SQL database in V1.
- No resident parent orchestrator in V1.
- Simulations, validation, analysis, cleanup, and future optimization are separate jobs.
- Case directories are the concurrency boundary.
- `cases.tsv` is immutable after campaign start.
- Cleanup must be two-phase: dry-run manifest, then execute.
- Cleanup must never delete directories or paths outside a case directory.
- State alone is never sufficient permission to delete raw data.

## Files expected after Fase 0

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
campaign_workflow/
campaign_workflow/core/
campaign_workflow/diagnostics/
campaign_workflow/analysis/
campaign_workflow/cli/
slurm/
tests/
```

## Next phase

Fase 1: implement generic state initialization.

Expected files:
```
campaign_workflow/__init__.py
campaign_workflow/core/__init__.py
campaign_workflow/core/atomic_io.py
campaign_workflow/core/tsv_cases.py
campaign_workflow/core/state.py
campaign_workflow/cli/__init__.py
campaign_workflow/cli/init_case_states.py
```

Expected command shape:

```
python -m campaign_workflow.cli.init_case_states --campaign-root . --dry-run
python -m campaign_workflow.cli.init_case_states --campaign-root .
python -m campaign_workflow.cli.init_case_states --campaign-root . --check
```

## Definition of done for Fase 1

On a fake local campaign:
```
N cases found
N case directories found or created according to mode
N state.json valid
N validation.json valid
0 destructive operations
```

On SUNRISE real campaign dry-run:

```
351 cases found
351 case directories found
no raw data modified
no cleanup performed
```

## Reminder

Never edit the physics input unless explicitly requested.

Never propose destructive commands without explicit path-level safety and confirmation.

Never use `rm -rf` on user data or campaign roots.

---

## Fase 1 local status

Fase 1 has been implemented and tested locally on `tests/fake_campaign`.

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

Validated commands:

```
python -m campaign_workflow.cli.init_case_states --campaign-root tests\fake_campaign --dry-run --verbose
python -m campaign_workflow.cli.init_case_states --campaign-root tests\fake_campaign --verbose
python -m campaign_workflow.cli.init_case_states --campaign-root tests\fake_campaign --check --verbose
python -m campaign_workflow.cli.init_case_states --campaign-root tests\fake_campaign --case-id 1 --check --verbose
```

Observed result:

```
cases_processed=3
cases_with_errors=0
errors=0
destructive_operations=0
```

Important implementation note:

PowerShell may write UTF-8 files with BOM. The JSON loaders were updated to use `utf-8-sig` so `campaign.json`, `state.json`, and `validation.json` are tolerant to BOM on Windows.

Current state semantics:

- `state.json` initializes cases as `Created`.
- `validation.json` initializes empty raw/reduced validation sections.
- `cleanup.cleanup_allowed` is initialized as `false`.
- No HDF5, openPMD, SLURM, analysis, or cleanup logic exists yet.

Next recommended step:

Run Fase 1 as a SUNRISE dry-run/check against a real campaign root after deploying the repo through Git. Then start Fase 2: generic raw validation plus the first `openpmd_hdf5` diagnostic adapter.