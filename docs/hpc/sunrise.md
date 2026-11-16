# SUNRISE runtime and campaign inspection

This profile records verified behavior of the CLPU N2 workflow at
`campaign-workflow@5ce81f8d6a696b40e5d9ceff3f072ad0ba8f80c5`
as audited on 2026-11-16. It is **not** a guarantee for another SUNRISE
image, queue, campaign, or later checkout.

## Select the runtime by role, not by convenience

| Role | Observed modules / environment |
| --- | --- |
| Workflow, optimizer, guiding analysis | `GCC/12.1.0`, `Python/3.10.12`, and the corresponding pre-existing `*-py310` venv |
| WarpX PICMI/runner | `GCC/12.1.0`, `Python/3.14.3`, `OpenBLAS/0.3.31`, `warpx/26.05-gcc12-openmpi413-all-dims`, and `~/apps/venvs/warpx-26.05-py314` |
| Git after a module purge | The N2 Gate B scripts explicitly load `Git/2.41.0` |

These values were read from
`examples/sunrise/corrected_capillary/run_nitrogen_case_analysis_sunrise.sh`,
`run_nitrogen_gate_b_control_sunrise.sh`, and
`examples/sunrise/run_warpx_case_sunrise.sh` at the pinned commit.
The analysis script also loads the WarpX module while using Python 3.10;
do not interpret that as permission to switch the analysis interpreter to 3.14.

In a controlled subshell, load the site modules **before** activating the
chosen venv. Check `python --version`, `sys.executable`, imported
`package.__file__`/`package.__path__`, `git rev-parse HEAD` and
`ldd` unresolved libraries when appropriate. For the Python 3.10 failure
involving `libpython3.10.so.1.0`, confirm the GCC/Python runtime modules
before attempting reinstallations.

The workflow's generated environment file sources a generic optimizer
environment and **then** exports the campaign-specific checkout paths.
Sourcing any environment script can reset `PATH`, `PYTHONPATH`, and
other values. Check effective imports **after the final source**; an editable
installation can take precedence over the path an agent intended to use.
Never repair this by silently reinstalling an environment used by production.

Do not run `set -e` or `module purge` in the user's persistent interactive
shell; use a disposable subshell when reproducing the runtime.

## Read-only inspection checklist

1. Identify the host and campaign root. Read `optimization.json`,
   `optimizer.json` and per-iteration `campaign.json` when present.
   Read receipts, manifests and `cases.tsv` without changing them.
2. Resolve the exact workflow, optimizer and analysis roots and commits from
   the campaign's recorded provenance. Verify each checkout and status;
   report absent or dirty checkouts rather than switching them.
3. Locate the relevant materializer, PICMI validator, launcher, analyzer,
   state initializer and cleanup contract **in those pinned revisions**.
   Historical worktrees need not include today's `AGENTS.md` or skills.
4. Inspect environment scripts in source and the actual post-source import
   origins. Do not trust a `PYTHONPATH` echo alone.
5. Inspect Slurm state and the user's actual quota using the configured
   storage backend. `df`/filesystem free space is not user quota.
6. Record which checks have only static evidence, which require an
   artifact-producing PICMI Gate B, and which require a physical WarpX run.

## Quota and scheduler controls

The pinned `campaign_workflow/guards.py` supports both
`lfs_quota_user` (Lustre) and `mmlsquota_user` (GPFS). The N2 Sobol
documentation uses `mmlsquota_user` and parses `mmlsquota -Y` in KB,
including in-doubt allocations against the lower positive soft/hard limit.
Choose the backend from the real campaign configuration and live site
evidence; do not hard-code `lfs quota` for every SUNRISE dataset.

The existing per-case quota guard is not an aggregate capacity reservation.
For highly concurrent arrays, separately estimate peak raw storage across
simultaneous cases using **measured** per-case peak and an explicit margin;
report unknown estimates rather than asserting capacity.

The core has a nested-`sbatch` protection. Never submit from a compute job.
Inspect the actual finite-chain launcher and its `--execute` boundary
before proposing submissions. No Slurm command belongs to a runtime
inspection script.

## PICMI, state and cleanup boundaries

- At the pinned N2 input, `CAP_DRY_RUN=1 python input.py 2` calls
  `sim.write_input_file()` and returns before `sim.step()`; it **writes**
  a serialized input and associated materialization evidence. It is not
  a read-only preflight.
- Materialization and `init_case_states` precede per-case validation.
  Never weaken a campaign-specific Gate B to accommodate a different
  batch size; create/review an explicitly versioned contract.
- The case-cycle script re-sources `WORKFLOW_ENV` after WarpX before
  registering state and analysis.
- Reduced-output receipts are not, by themselves, proof of scientific
  sufficiency. The historical beam runner allows partial spatial coverage;
  acceptance and raw-retention policy are separate scientific decisions.
- Cleanup `--dry-run` writes a manifest, while `--execute` deletes
  validated manifest-listed files. Never run either against an active
  campaign as part of mere inspection.

## References

- Versioned core contracts: `docs/CAMPAIGN_WORKFLOW.md`,
  `docs/CONFIG_CONTRACT.md`, `docs/STATE_MACHINE.md`,
  `docs/CLEANUP_SAFETY.md`.
- Pinned N2 procedure:
  `examples/sunrise/CLPU_N2_BEAM_SOBOL.md`.
- WarpX 26.05 Python/PICMI:
  https://warpx.readthedocs.io/en/26.05/usage/python.html
- WarpX 26.05 running simulations:
  https://warpx.readthedocs.io/en/26.05/usage/how_to_run.html
