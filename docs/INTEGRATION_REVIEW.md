# Cross-repository integration review

Status: proposed review agenda, 2026-10-20. No functional changes or new
cleanup authorization are introduced here. The baseline revisions are recorded
in `BASELINE_2026-10-20.md`. The behavior described below is scoped to those
recorded default-branch revisions. The subsequent static inspection in
`DEVELOPMENT_BRANCH_REVIEW_2026-10-20.md` identifies existing branch
implementations, including N2 observation contracts and distinct raw-retention
and validated-cleanup policies. Those implementations are not incorporated.
The unchanged baseline is historical Cloud evidence, not validation of these
branches, future revisions, SUNRISE, or LYNX.

## 1. Reduced-product completeness before HDF5 cleanup

Observed behavior:

- Cleanup requires raw/reduced evidence, eligibility, a prior dry-run manifest,
  safe file targets, and explicit execution.
- CSV validation checks columns and row counts, not metric finiteness, species,
  time coverage, or scientific adequacy. Generic file validation checks paths,
  suffixes, and size, not image/video content.
- `examples/capillary_guiding/campaign.json` requires only
  `guiding_metrics.csv`; its raw cleanup globs include HDF5 throughout `diags/`.
  Its wrapper can produce particle products outside that declared output set.
- Nozzle's current Lynx profile requires its CSVs, PNGs, and MP4. The library's
  optional animation does not make that campaign output optional. The MP4
  requirement is a configuration policy, not an intrinsic physical requirement
  for its numerical objectives.

Scientific decisions required:

1. For each campaign and raw diagnostic family, which reduced products are
   indispensable: guiding scores, particle summaries, acceptance curves,
   species-specific products, temporal series, plots, or animation?
2. Which checks demonstrate scientific usability: finite metrics, units,
   species identity, exact sampling, and sufficient temporal coverage?
3. Which future analyses require retained raw data even after reduction?
4. Should an optional visualization ever block deletion, optimization, both,
   or neither? Distinguish these policies explicitly.

Proposed future tests, after approval:

- Map each deletable diagnostic family to its required retained products.
- Missing, stale, invalid, or mismatched required products must prevent the
  intended cleanup eligibility under the approved policy.
- Exercise partial particle reduction, absent acceptance curves, raw changes,
  and reduced-only adopted cases with disposable synthetic fixtures.
- Keep manifest/path safety tests separate from scientific-completeness tests.

Do not infer that the existing tests establish this completeness policy.

## 2. Explicit species selection in multichannel observations

LFMetrics can emit one row per requested species. The optimizer's current
`multichannel/observations.py` reads the first row. The SUNRISE wrapper requests
only `electrons`, so that example avoids row-order ambiguity.

The scientific owner must choose the intended species per objective and decide
how missing/duplicate species are handled. Combining populations would change
the physical observable and is not a harmless interface fix. Nozzle reuses the
adapter with a carbon summary, not an electron population.

Future tests should cover reversed row order, extra species, missing and
duplicate target species, and the nozzle adapter separately. Do not implement
selection or aggregation before this contract is approved.

## 3. Temporal selection and optional-output evidence

The guiding particle path can require an exact resolved exit iteration.
The capillary optimizer selects the guiding row matching
`target_guiding_iteration` when the target is numeric and the guiding table
has an `iteration` column; an absent matching row in that path is invalid.
Otherwise, it accepts a single row or falls back to the last row.
LFMetrics uses the final diagnostic dump. Nozzle uses a last physical carbon
sample and a separately defined field time window.

These are different observables. The owner must decide when fallback is
allowed, how to express timing provenance, and whether legacy observations
without exact metadata remain fit-eligible.

The workflow allows failed optional outputs while retaining their failure
evidence. Current optimizer ingestion checks all examined evidence entries
for success rather than applying the workflow's required-only policy.
This can exclude a workflow-valid case from fitting.

Future tests should cover exact/missing target iterations, legacy fallback,
optional failures, required failures, and consistency between case validation
and optimizer eligibility. The desired eligibility policy remains undecided.

## Review boundaries

Keep physical definitions with the analysis owner, fit eligibility with the
optimizer, and evidence/cleanup enforcement with the workflow. Cross-reference
the producer and consumer contracts instead of introducing runtime imports.
Any approved semantic change needs a compatibility/migration plan for existing
reduced data and persisted optimizer state.
