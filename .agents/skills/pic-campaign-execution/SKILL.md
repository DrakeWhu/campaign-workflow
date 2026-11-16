---
name: pic-campaign-execution
description: Plan an explicitly authorized PIC/WarpX campaign submission, Slurm array monitoring, case recovery or reduction lifecycle using the pinned campaign-workflow launcher and state machine. Do not activate for environment troubleshooting or read-only campaign auditing; never infer permission for sbatch, WarpX, or raw cleanup.
---

# PIC campaign execution

Use only after a contract-aware preflight has identified an exact campaign
root, site, pinned scripts/commits, and sufficient validation evidence.
This skill is a controlled operational procedure, not a new scheduler
implementation. The default is to **plan**, not execute.

## Execution boundary

1. Record the exact authorized scope: campaign root, iteration/cases,
   launcher, Slurm array/concurrency specification, and whether the request
   permits submission, physical simulation, recovery, or cleanup. These are
   distinct permissions. If any permission is missing, report a plan and
   stop before that effect.
2. Verify the actual checkout and imported modules again in the final runtime
   context; verify materialization, initialized states, Gate B/runtime
   receipts, resolved physics contracts, guard configuration and current user
   quota. Re-evaluate high-concurrency peak storage risk.
3. Select the **existing** launcher/transaction at the pinned revision.
   Inspect command help/source and the difference between planning and
   --execute. Avoid composing a new sbatch path if an existing contract owns
   submission. In particular, do not run nested sbatch from a Slurm compute
   job. Inspect dependencies, case ID mapping, --array specification,
   job-resource requests, and environment exports.
4. Present a concrete command and its expected writes/side effects, then
   execute only the operation explicitly authorized for that exact root.
   Do not treat an earlier successful Gate B, an unrelated job ID, or an
   approval to *inspect* as authorization to submit.
5. Monitor using the scheduler and case evidence. Preserve the native state
   order: Created -> submission/running -> raw validation -> analysis ->
   reduced validation -> cleanup eligibility. The pinned workflow controls
   actual transitions; do not edit state.json by hand.
6. On failure, collect logs, receipts and partial-output evidence before
   proposing recovery. Do not silently resubmit cases with existing HDF5,
   reset states, overwrite materializations, or promote unknown results
   to success. Use recovery tools only if they exist in the selected commit
   and are explicitly approved.

## Raw-data retention and cleanup

- Reduced-output validation, scientific adequacy and future raw-data needs
  are distinct decisions. Raw HDF5 removal must not be inferred from a
  successful scheduler state, a reduced CSV or a beam GIF.
- Inspect policy.raw_retention_required versus
  policy.cleanup_after_validation_required and the effective exported
  CW_RAW_RETENTION_REQUIRED and CONFIRM_CLEANUP_EXECUTE settings.
  Never silently change their semantics.
- Dry-run cleanup writes a deletion manifest; execute cleanup removes only
  validated manifest-listed regular files. These require separate,
  explicit authorizations. Do not execute either on active campaigns
  by default. Follow docs/CLEANUP_SAFETY.md and the pinned code.

## Handoff evidence

Report command/launcher and revision, jobs (only those actually submitted),
array settings, quota/guard results, case state, receipts, output coverage and
cleanup status. Distinguish a proposed action from one actually executed.

## References

- docs/STATE_MACHINE.md, docs/CLEANUP_SAFETY.md,
  docs/CAMPAIGN_WORKFLOW.md, docs/CONFIG_CONTRACT.md.
- docs/hpc/README.md and site profile when available (PR #4).
- The actual pinned version of examples/sunrise/submit_case_cycle_array.sh
  and examples/sunrise/submit_morbo_chain.py; CLPU N2-specific launchers
  exist only in historical development commits.
