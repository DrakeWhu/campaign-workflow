# Development branch review — 2026-10-20

Repository: `campaign-workflow`. Audited base: `82bbea66c2311cd7e361b2b12196850cbfa4d6c8`.
Remote references observed at `2026-10-20T11:41:16.026347+00:00`.

## Evidence scope

Remote heads were fetched into a temporary bare shared clone. Source repositories
were not switched, merged, rebased, or updated with development code. Inspection
used `git show`, `git diff`, `git log`, `git rev-list --left-right --count`,
and `git merge-base --is-ancestor`. Reference acquisition used `git ls-remote
--heads origin` and `git fetch --no-tags origin
'+refs/heads/*:refs/remotes/origin/*'` in the temporary clone. No branch tests,
simulations, scheduler jobs, analysis pipelines, or cleanup commands were run.
The inventory is a dated remote snapshot; findings below cover selected relevant
implementations, not certification of every changed file.

`BASELINE_2026-10-20.md` is preserved byte-for-byte as historical evidence for
the audited base and its recorded Cloud environment. It does not validate these
development tips, future versions, SUNRISE, LYNX, or WarpX execution. Its SHA-256:
`12287aa4bcc6483001cbef05ad82905ca5f50e9e1daa8f127889700c74c5e879`.

## Remote branch inventory

Counts are commits unique to the audited base / unique to the remote branch.
A zero branch-only count denotes an ancestor or the identical base, not pending
implementation work. Hashes, rather than moving branch names, identify evidence.

| Remote branch | Commit | Base-only / branch-only |
| --- | --- | --- |
| `feat/clpu-baseline-soft50-dual-exit` | `94a93103a365ddb1d3d3893b2a0429dfa1fa754d` | 0 / 35 |
| `feat/clpu-corrected-capillary-ionization` | `31a7c375f14d672d7889716b557b7495c7055bc3` | 0 / 12 |
| `feat/clpu-v4-unfiltered-canary` | `a36e49aeb77c76592bfdb0f1f9451311e60588ae` | 0 / 13 |
| `feat/multichannel-soft100-v2` | `fe683f422fd31e956af548ec11df8ab01514f564` | 0 / 6 |
| `feature/clpu-n2-beam-sobol` | `5ce81f8d6a696b40e5d9ceff3f072ad0ba8f80c5` | 0 / 75 |
| `feature/clpu-n2-gate-b` | `3baf0414c27c10b1e3e816aeb1168fc7599ce519` | 0 / 42 |
| `feature/clpu-n2-launch-gate` | `6ab7d8b1373e4c0e9efa00280afeb6d5b710ff0c` | 0 / 41 |
| `feature/clpu-n2-retain-raw` | `ea8cc7725da4b14b8b12b4b52064cd27a7f2fecc` | 0 / 40 |
| `feature/clpu-n2-uniform` | `83b835deac9a1977f517570ef0514fef2daed301` | 0 / 39 |
| `feature/clpu-n2-validated-cleanup` | `21013af33ff658220a7099f4eef628319b82f1af` | 0 / 64 |
| `fix/clpu-n2-walltime-recovery` | `d4337cfd4ea2b141d522b85d19e4366cf3b57c5a` | 0 / 70 |
| `hardening/sim-runtime-receipt` | `73cc97e3b41839bbd87567e803d6c0fcb1348489` | 0 / 37 |
| `master` | `82bbea66c2311cd7e361b2b12196850cbfa4d6c8` | 0 / 0 |

## Verified implementation findings

- `feature/clpu-n2-retain-raw` adds the `clpu_n2_retain_raw_v1` launcher
  contract. It requires `policy.raw_retention_required=true` and forces
  `CW_RAW_RETENTION_REQUIRED=1` and `CONFIRM_CLEANUP_EXECUTE=0`. The cycle
  script also lets the retention flag override an execution request. The
  cleanup dry run still writes evidence.
- Its descendant `feature/clpu-n2-validated-cleanup` changes the launcher to
  `clpu_n2_cleanup_after_validation_v1`, requires
  `policy.cleanup_after_validation_required=true`, and forces
  `CW_RAW_RETENTION_REQUIRED=0` and `CONFIRM_CLEANUP_EXECUTE=1`. This is a
  material policy reversal, not just additional metadata. The launch gate,
  per-iteration Gate B, repository/asset hashes, and runtime receipts add
  provenance controls; they do not by themselves establish scientific
  sufficiency of all reduced products.
- `fix/clpu-n2-walltime-recovery` descends from validated cleanup;
  `feature/clpu-n2-beam-sobol` descends from walltime recovery. These tips
  contain cumulative changes, not independent patches to combine blindly.
- The beam branch adds `campaign_nitrogen_beam_evolution.json`, requires
  beam tables, a frame manifest, two GIFs, and validation evidence, and calls
  `validate_beam_products.py` from `run_nitrogen_case_analysis_sunrise.sh`.
  The validator checks every scheduled iteration for the two electron species
  and `all_electrons`, hard 50 MeV/forward selection, readable GIF frame counts,
  species provenance, and agreement of combined and component charges.
- That runner explicitly passes `--allow-partial-coverage`. The validator
  records `integration_status` and `coverage_fraction` but does not require
  complete spatial integration or a minimum coverage fraction. Its exit check
  rejects a named set of bad statuses, rather than requiring one specific
  successful status. Therefore a passing product receipt is not proof that a
  full-plasma integral or every intended exit measurement is scientifically
  sufficient. This is a static finding, not an observed campaign failure.

Evidence: `examples/sunrise/submit_clpu_n2_morbo_chain.py`,
`examples/sunrise/submit_case_cycle_array.sh`,
`campaign_workflow/clpu_n2_gate_b.py`, `campaign_workflow/clpu_n2_submission.py`,
and the campaign/runner/validator paths above at their pinned tips.
The inventory also includes input-physics, launch-gate, and runtime-receipt
branches; their existence is not approval to adopt their physical parameters.

## Cross-repository consequences and possible conflicts

The optimizer's N2 observation branch already enforces ordered population
scopes, exact resolved exits, a common particle iteration, and summary/curve
agreement for the explicit capillary contract. Guiding's exact-exit branch is
already an ancestor of its audited main. New guiding beam branches add spatial
coverage and combined-population products. LFMetrics publication branches add
fixed final-bunch tracking and field/trajectory analyses with raw-history needs.
See each repository's dated development-branch review for exact commits.

The reviewed workflow branches do not overlap the documentation paths changed
in this documentation stage. This is a path comparison, not a guarantee of a
conflict-free future merge. The main risk is semantic: retaining raw and
explicitly enabling cleanup are different operational contracts. A future
integration must select a policy and migrate configuration deliberately.
The beam branch also spans input generation, scheduling, analysis, and
validation; adopting only its validator would not supply its expected schedule,
producer outputs, or provenance.

`docs/INTEGRATION_REVIEW.md` describes behavior at the audited default revision.
Its generic structural-validation limitations remain relevant; campaign-specific
branch validators must not be mistaken for a global core guarantee. Optimizer
multichannel first-row selection and required/optional evidence disagreement
remain unresolved in the inspected consumer implementations.

## Decisions required before functional integration

1. Specify retained products per raw diagnostic family, including future
   trajectory, field, and alternative-selection analyses.
2. Decide whether partial spatial coverage is acceptable for each metric and
   for cleanup; define exact exit-status and coverage acceptance criteria.
3. Choose retention versus validated-cleanup launch policy and its explicit
   authorization/migration boundary. Existing branch defaults are not approval.
4. Agree species, timing, optional-output eligibility, and cross-repository
   version compatibility before running branch acceptance tests.

## Integration boundary

The current stage is documentation only. No branch implementation, species
selection, objective, temporal policy, scientific-validation rule, or raw
cleanup behavior is adopted. Scientific decisions above remain pending.
Any later integration requires its own version-specific tests and site evidence.
