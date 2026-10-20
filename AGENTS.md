# Repository guidance: campaign-workflow

## Scope

- The core owns file-based case/iteration orchestration, materialization,
  lifecycle evidence, command adapters, validation, and manifest-driven cleanup.
  Scientific analysis and optimizer algorithms belong to external repositories.
- The core does not implement WarpX physics. `examples/` does contain
  campaign-specific PICMI inputs and SUNRISE/LYNX runners; changes there may
  change physics or site behavior and need corresponding review.
- Read the relevant contracts in `docs/` before changing on-disk formats or
  commands. Start with `CAMPAIGN_WORKFLOW.md`, `ANALYSIS_MODULE_CONTRACT.md`,
  `CANDIDATE_BATCH_CONTRACT.md`, and `CLEANUP_SAFETY.md`.

## Compatibility and scientific evidence

- Preserve legacy `CAP_*` materialization defaults, explicit environment-column
  conversions, case identity, and versioned custom candidate-batch contracts.
- Preserve `csv` and `file` reduced-output validation and required/optional
  evidence semantics. Structural validation is not scientific validation.
- Never weaken cleanup evidence or path checks. Cleanup must remain explicit,
  manifest-driven, file-only, and separate from analysis. A cleanup dry run
  writes evidence and is not a read-only inspection.
- Preserve finite, daemon-free scheduler orchestration. Do not add WarpX or
  optimizer dependencies to the core.
- The pending scientific decisions in `docs/INTEGRATION_REVIEW.md` are review
  questions, not permission to change retention, species, or timing policies.
- Consult `docs/DEVELOPMENT_BRANCH_REVIEW_2026-10-20.md` before overlapping
  integration work. Its pinned branch findings are not incorporated behavior.

## Verification

- Use `python -B -m unittest discover -s tests -p 'test_*.py' -v`, not pytest.
  Set `PYTHONPATH` to this repository only for isolated tests. HDF5 tests need
  `h5py`; missing optional coverage must be reported.
- Prefer isolated Git exports and temporary directories for the full suite:
  tests create fixtures and exercise deletion of those fixtures.
- Read `docs/BASELINE_2026-10-20.md` for the recorded baseline and environment.
  Do not treat its results as validation of later revisions or SUNRISE/LYNX.
- Distinguish static checks, unit/synthetic tests, real-diagnostic analysis, and
  actual scheduler/WarpX execution. Do not run simulations, submit jobs, or
  clean campaign data without task authorization.
- Write code, comments, tests, documentation, commits, and PR text in English.
