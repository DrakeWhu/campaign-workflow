---
name: pic-campaign-preflight
description: Audit a PIC campaign before materialization, extension, restart, Gate B PICMI validation, or Slurm submission. Resolve exact campaign manifests, historical checkout revisions, physics contracts, batch limits and quotas; default to non-mutating inspection. Do not use as a replacement for scientific or native Gate B validation.
---

# PIC campaign preflight

Use for WarpX/PIC campaign preparation, batch extension and recovery planning.
This skill does not replace campaign-workflow's guards, validators, or Slurm
transaction code. It applies to the selected site only after its profile and
pinned runner have been inspected.

## Phase A: read-only inventory (default)

1. Resolve the actual campaign/optimization root and site. Identify whether
   the task is a new campaign, extension, historical reanalysis, or recovery.
2. Read the existing optimization.json, optimizer.json, campaign.json,
   candidate_batch.tsv, batch_campaign_plan.json, cases.tsv and provenance
   manifests/receipts **where present**. Do not assume all apply to a given
   workflow. Preserve the distinction between distinct historical roots.
3. Derive exact workflow/optimizer/guiding paths and commits from the campaign
   and its launcher; inspect worktree HEAD, cleanliness and imported package
   origins. Historical branches may not contain current documentation or skills.
4. Identify the **actual** input template, materializer, state initializer,
   Gate B validator, guard, launcher, analyzer and cleanup policy from the
   pinned code. Do not infer code availability from the current default branch.
5. Check candidate identity, IDs, seed, iteration boundaries, expected counts,
   array indices, and configured campaign-size limits against the **selected
   campaign contract**. Original CLPU N2 Gate B limits (8 candidates,
   4 iterations, 32 simulations) do not apply automatically to a 96-case
   Sobol extension; never drop physical per-case checks to resolve conflicts.
6. Inspect current user quota via the existing backend-specific guard:
   Lustre lfs_quota_user or GPFS mmlsquota_user according to site configuration.
   Filesystem free space is not a personal quota. For large arrays, report
   aggregate peak-storage risk and the measured or unknown per-case estimate;
   the per-case guard is not a reservation.
7. Inspect Slurm availability/state if available but do not submit jobs.
   Read-only inventory must not produce PICMI inputs, receipts, case states,
   manifests, caches in campaign roots, or cleanup plans.

## Phase B: authorized artifact-producing validation

After **explicit** approval of the exact output root and requested operation,
use the selected checkout's native materialization and init_case_states
procedure. Execute its native PICMI Gate B only after confirming it applies
to the case population and scientific contract. PICMI CAP_DRY_RUN writes
serialized inputs even though it returns before physical evolution. Gate B
writes receipts. A cleanup --dry-run writes a manifest and is **not**
part of read-only inspection.

Verify requested -> resolved -> serialized values, input-template hashes,
case environment, state, expected species/diagnostics, and actual validator
receipts through the existing code. Report unsupported/unknown checks rather
than fabricating a successful receipt or retrofitting a validator by weakening
its invariants.

## Phase C: review handoff

Produce a concise structured report with:
- resolved paths and pinned commits, including import provenance;
- target campaign identity and exact counts;
- physical contracts and validators selected by **path and revision**;
- each check's evidence class (static, synthetic, PICMI, actual WarpX);
- quota/scheduler observations and storage uncertainty;
- missing artifacts, blockers, and actions requiring approval.

Do not claim that PICMI validates the physical output, that passing reduced
CSV structural checks establishes scientific sufficiency, or that any cleanup
is authorized by reduced-output validation alone.

Never run sbatch, srun, WarpX evolution, optimizer proposals that write
state, or deletion under this skill. Use pic-campaign-execution only for a
separately authorized operation.

## References

- docs/CAMPAIGN_WORKFLOW.md, docs/CANDIDATE_BATCH_CONTRACT.md,
  docs/CONFIG_CONTRACT.md, docs/CLEANUP_SAFETY.md.
- docs/hpc/README.md and docs/hpc/sunrise.md when available
  (documentation PR #4).
- At production commit 5ce81f8d6a696b40e5d9ceff3f072ad0ba8f80c5:
  campaign_workflow/clpu_n2_gate_b.py and
  examples/sunrise/CLPU_N2_BEAM_SOBOL.md.
