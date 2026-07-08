# Optimization module contract

This document defines the boundary between `campaign-workflow` and an external optimizer.

## Core rule

`campaign-workflow` orchestrates optimization iterations. It does not implement the optimizer model.

The optimizer may use Optimas, Ax, BoTorch, Torch, scikit-learn, custom heuristics, or another backend. Those dependencies must stay outside the workflow package.

## Directory contract

The optimization root has this shape:

```text
optimization_root/
├── optimization.json
├── optimization_state.json
├── iterations/
│   ├── iter_000/
│   └── iter_001/
└── optimizer_runs/
    └── iter_001/
        └── outputs/
            ├── candidate_batch.tsv
            └── batch_campaign_plan.json
```

Each `iterations/iter_XXX` directory is a normal campaign root with its own `campaign.json`, `cases.tsv`, case directories, state files, validation files, logs, and reduced outputs.

## What the optimizer may read

The optimizer may read:

- `optimization.json`;
- `optimization_state.json`;
- previous iteration campaign configs and case manifests;
- validated reduced outputs declared in `campaign.json`;
- workflow validation evidence;
- optimizer run artifacts from previous iterations.

The optimizer must not require raw diagnostic files. Raw WarpX/openPMD files are an analysis concern, not an optimizer input.

## What the optimizer must write

For a proposed next iteration, the external command must write:

```text
optimizer_runs/iter_NNN/outputs/candidate_batch.tsv
optimizer_runs/iter_NNN/outputs/batch_campaign_plan.json
```

`candidate_batch.tsv` must satisfy `docs/CANDIDATE_BATCH_CONTRACT.md`.

`batch_campaign_plan.json` must include a `campaign_template` object when non-default template filenames are needed:

```json
{
  "schema_version": 1,
  "campaign_template": {
    "campaign_json": "campaign.json",
    "input_template": "input_template.py"
  }
}
```

If `campaign_template` is absent, the workflow assumes `campaign.json` and `input_template.py` in the template campaign root.

## External command contract

`optimization.json` declares the command:

```json
"optimizer": {
  "command": ["bash", "{optimization_root}/run_optimizer.sh", "{from_iteration}", "{next_iteration}", "{optimizer_run_dir}"],
  "working_directory": "{optimization_root}",
  "env_script": "~/apps/env/optimas_sunrise.sh"
}
```

Supported placeholders include:

```text
{optimization_root}
{from_iteration}
{next_iteration}
{optimizer_run_dir}
```

The command must exit with code `0` on success and non-zero on failure. A zero exit code is not sufficient: `campaign-workflow` still verifies the expected output files.

## What campaign-workflow does after optimizer output exists

The workflow can:

1. validate `candidate_batch.tsv`;
2. validate/read `batch_campaign_plan.json`;
3. copy the template `campaign.json` and `input_template.py` into the next iteration campaign root;
4. write next iteration `cases.tsv`;
5. write `optimizer_batch_provenance.json`;
6. materialize cases;
7. initialize case states;
8. update `optimization_state.json`;
9. optionally submit the next iteration through a static SLURM script.

## What the optimizer must not do

The optimizer must not:

- submit SLURM jobs directly as part of the workflow contract;
- modify existing iteration campaign roots;
- delete raw diagnostics;
- write workflow state files by hand;
- edit case-local `state.json` or `validation.json`;
- assume a resident daemon exists.

## Human review

A human may review or edit `candidate_batch.tsv` before campaign preparation, provided the final file still satisfies the candidate batch contract. This is compatible with the workflow.

## Failure semantics

If the optimizer command fails or produces invalid outputs, the optimization tick fails and the next campaign is not prepared. Existing completed iterations are left untouched.
