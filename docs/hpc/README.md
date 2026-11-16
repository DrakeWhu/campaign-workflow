# HPC operating profiles for campaign-workflow

These are operational references, not replacements for the versioned workflow,
campaign contracts, or the actual site configuration. They are deliberately
separate from `AGENTS.md`: agents must load only the profile of the selected
site and the procedure relevant to the requested task.

## Sources of truth (in order)

1. The campaign's materialized configuration, manifests, state and validation
   evidence; use absolute resolved paths and recorded hashes when available.
2. The exact Git commits and runners referenced by that campaign, including
   worktrees of `campaign-workflow`, `campaign-optimizer`, and
   `guiding_analysis_module`.
3. Versioned contracts in `docs/` and at the selected commits.
4. The appropriate site profile, verified against the host before use.

Do not substitute the current default-branch implementation for a historical
production commit. In particular, the current repository instructions and
skills were not present in the October 2026 N2 production worktrees.

## Operation classes

| Class | Examples | Permission boundary |
| --- | --- | --- |
| Inspect | `git rev-parse`, `git status --porcelain`, inspecting JSON/TSV, interpreter/module-path queries, reading quota or Slurm state | Default; do not write campaign outputs |
| Prepare / validate with writes | Temporary test fixtures, materialization, initializing case states, PICMI `write_input_file()` with `CAP_DRY_RUN=1`, Gate B receipts, cleanup `--dry-run` manifests | Require explicit approval and a dedicated output location |
| Execute | `sbatch`, `srun`, physical WarpX evolution, execution of raw-data cleanup, recovery that changes scheduler or campaign state | Require explicit task-specific approval; cleanup needs separate authorization |

This classification is based on effects, not command names. A `--dry-run`
flag is not proof of read-only behavior. Keep the inspection tools from
creating cache files, `__pycache__`, temporary receipts or state updates
inside campaign roots.

## Site selection

- [SUNRISE](sunrise.md): known Python 3.10 workflow/analysis and Python 3.14
  WarpX environments, `module` behavior, provenance and storage checks.
- LYNX: no profile approved here yet; inspect the actual LYNX scripts in
  `examples/lynx/` and obtain site/runtime evidence before authoring one.
- ELI workstations: no profile approved here yet; avoid assuming the HPC
  module stack exists on Windows or ELI hosts.

## Historical N2 evidence

The October 2026 production snapshot used
`campaign-workflow@5ce81f8d6a696b40e5d9ceff3f072ad0ba8f80c5`,
`campaign-optimizer@73dab76305f547581c57b70a900706374c929141`,
and `guiding_analysis_module@990ada876ac21a0ab77a0a94ec23f3fc98a27563`.
These are a provenance example, **not** defaults for new campaigns.

On 2026-11-16 an N2 Sobol extension used a distinct campaign root and
reused the physical per-case contracts while expanding to 96 cases. Do not
generalize the original N2 Gate B assumption of eight candidates and four
iterations. The production branch Gate B validator is campaign-specific:
check its applicability and schema before reuse.

For details, inspect the corresponding commit of
`examples/sunrise/CLPU_N2_BEAM_SOBOL.md`, `campaign_workflow/clpu_n2_gate_b.py`
and `examples/sunrise/corrected_capillary/run_nitrogen_gate_b_control_sunrise.sh`.
