# Analysis module contract

This document defines the boundary between `campaign-workflow` and an external analysis module.

## Core rule

The analysis module owns scientific interpretation of raw diagnostics. The workflow owns invocation, logs, state transitions, and reduced-output validation.

The workflow must not import campaign-specific analysis packages directly. The production interface is a command adapter.

## Command adapter

A compatible analysis command is declared in `campaign.json`:

```json
"analysis": {
  "name": "guiding",
  "kind": "command",
  "adapter": "command",
  "command": ["bash", "path/to/run_analysis.sh", "{case_dir}"],
  "outputs": [...]
}
```

Supported placeholders include:

```text
{campaign_root}
{case_dir}
{case_id}
{case_name}
```

The adapter also provides useful environment variables such as:

```text
CAMPAIGN_ROOT
CAMPAIGN_NAME
CASE_DIR
CASE_ID
CASE_NAME
```

## Input contract

The analysis command may assume:

- the case directory exists;
- required raw diagnostics have been validated unless an explicit legacy/reanalysis path is used;
- raw diagnostic locations are those declared in `campaign.json`;
- the command is invoked for one case at a time.

The analysis command should not hard-code campaign roots. Use wrapper scripts for site-specific module/venv loading.

## Output contract

The command must write the reduced outputs declared under `analysis.outputs`.

Supported output kind:

```text
csv
```

CSV validation checks:

- case-relative safe path;
- readable CSV;
- at least `min_rows` data rows;
- all configured `required_columns` present;
- required outputs must pass;
- optional outputs may be absent without failing the case.

Example:

```json
{
  "name": "guiding_metrics",
  "kind": "csv",
  "path": "guiding_metrics.csv",
  "min_rows": 1,
  "required_columns": ["iteration", "time_fs"],
  "required": true
}
```

## Exit-code contract

- exit code `0`: command execution succeeded, then workflow validates outputs;
- non-zero exit code: analysis failed.

A zero exit code alone is not enough for success. Reduced outputs must validate.

## Logs

The workflow captures stdout and stderr under the case-local logs directory. The analysis module may print progress freely; it does not need to create workflow log files itself.

## What the analysis module must not do

The analysis module must not:

- delete raw diagnostics;
- write cleanup manifests;
- edit `state.json` or `validation.json` directly;
- submit new simulations;
- update optimization state.

## Current guiding example

The current capillary guiding example calls an external guiding-analysis environment through:

```text
examples/capillary_guiding/run_guiding_case_analysis_sunrise.sh
```

That wrapper resolves WarpX/openPMD field diagnostics for one case and writes `guiding_metrics.csv` as the official reduced output.
