# Candidate batch contract

`candidate_batch.tsv` is the reviewed optimizer output consumed by `campaign-workflow` to prepare a new campaign root.

The current `1.0.0` validator is intentionally capillary-campaign-specific because it is the proven production path. Generalizing this schema belongs to a later minor version.

## File format

- UTF-8 or UTF-8 with BOM is accepted.
- Delimiter: tab.
- First non-empty row is the header.
- Empty rows are ignored.
- Column names must be unique and non-empty.

## Required columns

```text
CASE_ID
CASE_NAME
LASER_CASE
PLASMA_KIND
N0_CM3
PLATEAU_LENGTH_MM
DIAMETER_UM
RADIUS_UM
FOCUS_OFFSET_FROM_PLATEAU_START_MM
CAP_RMAX_UM
CAP_NR
```

Extra columns are allowed and preserved in `cases.tsv`.

## Value rules

`CASE_ID`:

- integer-like;
- unique within the batch.

`CASE_NAME`:

- non-empty;
- unique within the batch;
- safe single relative directory name;
- no `/` or `\` path separators;
- must not resolve to `.` or outside the campaign root.

`LASER_CASE`:

```text
f20
f32
f40
```

`PLASMA_KIND`:

```text
chan
uni
vac
```

Numeric finite-decimal columns:

```text
N0_CM3
PLATEAU_LENGTH_MM
DIAMETER_UM
RADIUS_UM
FOCUS_OFFSET_FROM_PLATEAU_START_MM
CAP_RMAX_UM
CAP_NR
```

`CAP_NR` must be integer-like.

## Example

```tsv
CASE_ID	CASE_NAME	LASER_CASE	PLASMA_KIND	N0_CM3	PLATEAU_LENGTH_MM	DIAMETER_UM	RADIUS_UM	FOCUS_OFFSET_FROM_PLATEAU_START_MM	CAP_RMAX_UM	CAP_NR
0	case_000	f20	chan	4.0e18	10	300	150	0	180	192
1	case_001	f32	uni	3.0e18	10	300	150	0	180	192
```

## Preparation behavior

`prepare_batch_campaign.py` consumes:

```text
candidate_batch.tsv
batch_campaign_plan.json
template_campaign_root/
```

and writes a new campaign root containing:

```text
campaign.json
input_template.py
cases.tsv
array_logs/
optimizer_batch_provenance.json
```

It does not materialize case directories, submit jobs, run WarpX, run analysis, or clean data. Those steps are separate workflow actions.

## Relationship to optimization

The optimizer proposes candidates. The workflow validates whether those candidates are launchable under the current production schema. The workflow does not judge whether the candidates are scientifically good.
