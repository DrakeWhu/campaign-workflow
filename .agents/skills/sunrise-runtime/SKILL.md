---
name: sunrise-runtime
description: Inspect and troubleshoot an already-loaded SUNRISE HPC Python runtime, shared-library failures, wrong editable imports, or mismatched campaign worktrees before running PICMI, analysis, or a Slurm submission. Use for SUNRISE environment/provenance problems; not for LYNX or ELI workstation setup.
---

# SUNRISE runtime inspection

Use this skill to **inspect**, not to fix by mutation. It is not a scheduler,
materialization, installation, or WarpX-execution workflow.

1. Read the relevant pinned runner and environment script from the intended
   campaign/checkout. If available, consult docs/hpc/sunrise.md (introduced
   by documentation PR #4) and the contracts in docs/.
2. Establish the selected role: workflow, optimizer, guiding, or warpx.
   Resolve absolute checkout paths and full expected Git commit hashes from
   campaign manifests/receipts. Do not infer these from worktree names.
3. Inspect the environment scripts **before** sourcing them. If a shell
   transition is needed, use a disposable subshell; module loads and venv
   activation belong there, not in the inspector. Check again after the final
   source because it may overwrite prior exports.
4. Run the standard-library inspector with the Python interpreter actually
   selected by that environment, for example:

       python -B .agents/skills/sunrise-runtime/scripts/inspect_runtime.py \
         --role optimizer \
         --checkout optimizer=/absolute/optimizer/worktree \
         --expected-sha optimizer=FULL_40_CHARACTER_SHA

   Repeat --checkout and --expected-sha for other repositories whose
   provenance matters. Use --verify-import workflow or
   --verify-import guiding to also test their actual imports when available.
5. Record the JSON output and classify each check. PASS confirms only the
   checked property; WARN and UNKNOWN require review, and BLOCKED
   prohibits proceeding as if the runtime were validated.
6. Never submit Slurm jobs, run PICMI, reinstall a package, switch a checkout,
   change files, or invoke cleanup as an automatic remedy. For permitted
   campaign operations, use the separate preflight/execution procedures
   with their own authorization.

## Expected evidence and limits

- Exact sys.executable/Python version and LOADEDMODULES evidence.
- Direct shared-library resolution with ldd when available.
- Repo path, full HEAD, expected revision, and checkout cleanliness.
- Imported campaign_workflow, campaign_optimizer, or cap_guiding
  package __file__ under the intended checkout (for selected role and
  any explicitly requested additional import checks).
- For WarpX, only pywarpx discoverability is tested. This does **not**
  initialize WarpX, verify a PICMI input, or execute physics.

Exit codes: 0=PASS, 1=WARN/UNKNOWN, 2=BLOCKED. Never treat 0 as
permission to run a campaign. The inspector has no output-file option,
does not call source, and does not write test or campaign artifacts. It uses
GIT_OPTIONAL_LOCKS=0 for Git inspection and disables Python bytecode writes.

When a checker cannot establish an invariant, report the gap rather than
guessing a valid environment. For the original N2 example, the separate
Python 3.10 and 3.14 module stacks are recorded in
examples/sunrise/corrected_capillary/run_nitrogen_case_analysis_sunrise.sh
and examples/sunrise/run_warpx_case_sunrise.sh at commit
5ce81f8d6a696b40e5d9ceff3f072ad0ba8f80c5.
