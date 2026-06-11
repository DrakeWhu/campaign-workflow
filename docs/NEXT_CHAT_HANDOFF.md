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