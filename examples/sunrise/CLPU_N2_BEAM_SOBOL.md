# Finite CLPU N2 beam diagnostic scan

Base: `fix/clpu-n2-walltime-recovery`, with native validated raw cleanup and transactional Slurm submission. This example configures the existing campaign-optimizer and campaign-workflow CLI commands; it does not replace the optimizer, materializer, case cycle, or submission transaction.

Prepare with `prepare_clpu_n2_beam_sobol.py --root NEW_ROOT --workflow-root WORKFLOW --optimizer-root OPTIMIZER --guiding-analysis-root GUIDING --quota-probe-json QUOTA_PROBE`. It refuses existing roots and dirty checkouts. No sbatch or WarpX evolution is performed during preparation.

The design is 32 deterministic Sobol points, four batches of eight, fixed 30 fs, corrected radial profile, uniform nitrogen fraction 0..0.01. Initial historical observations and reference cases are excluded. Model fitting is disabled throughout this finite scan; the existing Soft50 objective is retained only as an archival optimizer product. A subsequent BO campaign must explicitly choose and ingest a temporal beam objective.

Launch through `submit_clpu_n2_morbo_chain.py` with start iteration 0, four iterations, `--array-spec '0-7%1'`, and the prepared environment. Each array is gated by its own native PICMI Gate B job. Cold launch evidence validates native Sobol materialization and hashes without claiming PICMI has already succeeded. A live user quota probe is required for execution; filesystem free space alone is insufficient. The simulation wrapper evaluates the native quota guard before each case. The generated environment enables STOP_OPTIMIZATION on a failed case.

The GPFS probe is `{"kind":"mmlsquota_user","user":"USERNAME","filesystem":"DEVICE"}`. It parses IBM mmlsquota -Y header fields in KB, adds in-doubt allocations to usage, and reserves against the lower positive soft/hard limit. The command must be tested with the actual SUNRISE filesystem/device; an unknown or failed probe blocks simulation. Native Lustre probes remain supported.

The beam campaign enables unfiltered particles at the existing field cadence, plus the two exact exit steps and max_steps. Field and particle schedules coincide. Laser guiding algorithms and scores, physics, and existing Soft50 exit products remain unchanged. The additional exact field snapshots can change the sampled series, not the metric definitions.

Reduced validation requires beam CSVs, complete animations, and beam validation JSON. A failed analysis prevents manifest cleanup. On success, only compact GIFs, CSVs and validation metadata remain; raw HDF5 and intermediate beam PNGs do not accumulate.

Full plasma coverage is reported explicitly and can be partial because coordinates use the guiding window upper bound. Missing/invalid frames fail even when partial integration is allowed. Low effective particle counts affect quality status rather than being confused with missing data.

Local verification cannot replace SUNRISE Gate B serialization and one real simulation/reduction cycle. Quota thresholds and safe temporary raw budget must be checked against the live user quota before submission.
