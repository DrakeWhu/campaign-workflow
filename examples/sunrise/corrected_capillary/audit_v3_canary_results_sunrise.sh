#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${HOME}/warpx_runs/clpu_capillary_guiding_bo_004_corrected_n2_soft50_v3"
ITER="${ROOT}/iterations/iter_000"

GA="${HOME}/apps/src/guiding_analysis_module-clpu-adk"
WF="${HOME}/apps/src/campaign-workflow-clpu-adk"
OPT="${HOME}/apps/src/campaign-optimizer-clpu-adk"

GA_SHA="d8a42b79840935e829020ebb86dfc3bc4e1a1936"
OPT_SHA="9fc7e612f00a5ca4ee1b85de62167c6690546058"
CANARY_LAUNCH_WF_SHA="05734dbbc7490c393888f8347c249273094b81d2"

ORIG_GA="${HOME}/apps/src/guiding_analysis_module"
ORIG_WF="${HOME}/apps/src/campaign-workflow"
ORIG_OPT="${HOME}/apps/src/campaign-optimizer"
PYTHON_BIN="${HOME}/apps/venvs/campaign-workflow-py310/bin/python"

for required in \
    "${ROOT}/optimization_state.json" \
    "${ITER}/campaign.json" \
    "${ITER}/cases.tsv" \
    "${GA}" "${WF}" "${OPT}" \
    "${ORIG_GA}" "${ORIG_WF}" "${ORIG_OPT}" \
    "${PYTHON_BIN}"
do
    [[ -e "${required}" ]] || {
        echo "ERROR: falta ${required}"
        exit 10
    }
done

if ! type module >/dev/null 2>&1; then
    source /etc/profile.d/modules.sh
fi
module load Git/2.41.0
GIT_BIN="$(command -v git)"
[[ "$(${GIT_BIN} --version)" = "git version 2.41.0" ]]

[[ "$(${GIT_BIN} -C "${GA}" rev-parse HEAD)" = "${GA_SHA}" ]]
[[ "$(${GIT_BIN} -C "${OPT}" rev-parse HEAD)" = "${OPT_SHA}" ]]
[[ -z "$(${GIT_BIN} -C "${GA}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${WF}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${OPT}" status --porcelain)" ]]

WF_SHA="$(${GIT_BIN} -C "${WF}" rev-parse HEAD)"

orig_ga_head="$(${GIT_BIN} -C "${ORIG_GA}" rev-parse HEAD)"
orig_wf_head="$(${GIT_BIN} -C "${ORIG_WF}" rev-parse HEAD)"
orig_opt_head="$(${GIT_BIN} -C "${ORIG_OPT}" rev-parse HEAD)"
orig_ga_status="$(${GIT_BIN} -C "${ORIG_GA}" status --porcelain)"
orig_wf_status="$(${GIT_BIN} -C "${ORIG_WF}" status --porcelain)"
orig_opt_status="$(${GIT_BIN} -C "${ORIG_OPT}" status --porcelain)"

STATE_SHA_BEFORE="$(sha256sum "${ROOT}/optimization_state.json" | awk '{print $1}')"
AUDIT="${ROOT}/audits/canary_results_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${AUDIT}"
exec > >(tee "${AUDIT}/canary_results.log") 2>&1

on_exit() {
    local rc=$?
    if (( rc != 0 )); then
        echo "CLPU_ADK_CANARY_AUDIT_FAILED=${rc}"
        echo "NO_STATE_CHANGED=1"
        echo "NO_SBATCH_CALLED=1"
        echo "AUDIT=${AUDIT}"
    fi
}
trap on_exit EXIT

export ROOT ITER AUDIT GA_SHA OPT_SHA WF_SHA CANARY_LAUNCH_WF_SHA

CANARY_LAUNCH_AUDIT="$(
"${PYTHON_BIN}" - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["ROOT"])
paths = list((root / "audits").glob("canary_iter000_*/canary_launch_audit.json"))
assert paths, "no canary_launch_audit.json found"
print(max(paths, key=lambda path: (path.stat().st_mtime_ns, str(path))))
PY
)"
export CANARY_LAUNCH_AUDIT

echo "=== Auditoría cerrada de los dos controles ==="
"${PYTHON_BIN}" - <<'PY'
import csv
import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path

root = Path(os.environ["ROOT"])
iteration = Path(os.environ["ITER"])
audit = Path(os.environ["AUDIT"])
launch_path = Path(os.environ["CANARY_LAUNCH_AUDIT"])

launch = json.loads(launch_path.read_text(encoding="utf-8"))
assert launch["status"] == "canary_submitted"
assert Path(launch["root"]).resolve() == root.resolve()
assert launch["iteration"] == 0
assert launch["case_ids"] == [0, 1]
assert launch["array_spec"] == "0-1"
assert str(launch["slurm_job_id"]).isdigit()
assert launch["partition"] == "T12H"
assert launch["walltime"] == "12:00:00"
assert launch["cleanup_after_required_output_validation"] is True
assert launch["source_commits"] == {
    "guiding_analysis_module": os.environ["GA_SHA"],
    "campaign_workflow": os.environ["CANARY_LAUNCH_WF_SHA"],
    "campaign_optimizer": os.environ["OPT_SHA"],
}

with (iteration / "cases.tsv").open(newline="", encoding="utf-8-sig") as stream:
    rows = list(csv.DictReader(stream, delimiter="\t"))
assert len(rows) == 35
assert [int(row["CASE_ID"]) for row in rows] == list(range(35))

optimization_state = json.loads(
    (root / "optimization_state.json").read_text(encoding="utf-8")
)
assert optimization_state["status"] == "running"
item = optimization_state["iterations"][0]
assert item["iteration"] == 0
assert item["submitted"] is True
assert item["submitted_case_ids"] == [0, 1]
assert item["submitted_case_count"] == 2
assert item["array_spec"] == "0-1"
assert item["confirm_cleanup_execute"] is True
assert str(launch["slurm_job_id"]) in item["slurm_job_ids"]

required_history_edges = {
    ("Created", "Submitted"),
    ("Submitted", "Running"),
    ("Running", "Sim_done"),
    ("Sim_done", "Raw_validated"),
    ("Raw_validated", "Analyzing"),
    ("Analyzing", "Reduced_validated"),
    ("Reduced_validated", "Raw_delete_eligible"),
    ("Raw_delete_eligible", "Raw_deleted"),
}
required_files = [
    "guiding_metrics.csv",
    "guiding_singlecase_score.csv",
    "plots/laser_waist_rms.png",
    "plots/laser_a0_peak.png",
    "plots/guiding_summary_multipanel.png",
    "particle_analysis/particle_summary.csv",
    "particle_analysis/particle_soft50_curves.csv",
    "particle_analysis/particle_acceptance_curves.csv",
    "particle_analysis/species_validation.json",
    "animations/eperp2.mp4",
    "animations/ez_wake.mp4",
    "animations/animation_frame_metrics.csv",
    "animations/validation.json",
    "post/sim_done.json",
    "post/analysis_done.json",
    "post/raw_delete_eligible.json",
    "post/raw_deleted.json",
    "manifests/raw_delete_manifest.json",
]
expected_scopes = [
    "all_electrons",
    "preionized_background_electrons",
    "nitrogen_ionized_electrons",
]

doped_canary_forward_ge5mev_observed = None
case_summaries = []
for row, expected_fraction in zip(rows[:2], [0.0, 0.005]):
    case_id = int(row["CASE_ID"])
    case_dir = iteration / row["CASE_NAME"]

    state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
    assert state["state"] == "Raw_deleted"
    history_edges = {
        (entry.get("from"), entry.get("to"))
        for entry in state.get("history", [])
    }
    assert required_history_edges.issubset(history_edges)

    for relative in required_files:
        path = case_dir / relative
        assert path.is_file() and path.stat().st_size > 0, path
    assert not (case_dir / "post/sim_failed.json").exists()
    assert not (case_dir / "post/analysis_failed.json").exists()

    validation = json.loads(
        (case_dir / "validation.json").read_text(encoding="utf-8")
    )
    for section_name in ["raw", "reduced"]:
        section = validation[section_name]
        required = [record for record in section.values() if record.get("required")]
        assert required
        assert all(record.get("ok") is True for record in required)
    cleanup = validation["cleanup"]
    assert cleanup["cleanup_allowed"] is False
    assert cleanup["raw_deleted"] is True
    assert cleanup["delete_manifest_mode"] == "executed"
    assert cleanup["execute_required"] is False
    assert cleanup["deleted_file_count"] >= 2
    assert cleanup["deleted_total_size_bytes"] > 0

    deleted = json.loads(
        (case_dir / "post/raw_deleted.json").read_text(encoding="utf-8")
    )
    assert deleted["deleted_file_count"] == cleanup["deleted_file_count"]
    assert deleted["deleted_total_size_bytes"] == cleanup["deleted_total_size_bytes"]
    assert deleted["directory_delete_allowed"] is False

    resolved = json.loads(
        (case_dir / "resolved_parameters.json").read_text(encoding="utf-8")
    )
    assert math.isclose(
        resolved["nitrogen_fraction_atomic_nuclei"],
        expected_fraction,
        rel_tol=0.0,
        abs_tol=1.0e-15,
    )
    assert resolved["schema_version"] == 3
    assert resolved["physics_model_id"] == (
        "clpu_carlos_plateau_quasiparabolic_n5_adk_v5_grid_cfl"
    )
    assert resolved["max_steps"] == 91459
    assert resolved["field_diagnostic_period"] == 1946
    assert resolved["particle_diagnostic_iteration"] == 60326
    assert resolved["particle_diagnostic_intervals"] == "60326:60326"
    assert resolved["particle_diagnostic_dump_last_timestep"] is False

    species_validation = json.loads(
        (case_dir / "particle_analysis/species_validation.json").read_text(
            encoding="utf-8"
        )
    )
    assert species_validation["schema_version"] == 2
    assert species_validation["status"] == "ok"
    assert species_validation["expected_scopes"] == expected_scopes
    selection = species_validation["selection"]
    assert selection["status"] == "ok"
    assert selection["selection_mode"] == "exit"
    assert selection["selected_particle_iteration"] == (
        resolved["particle_diagnostic_iteration"]
    )
    assert selection["target_guiding_iteration"] == (
        resolved["particle_diagnostic_iteration"]
    )
    assert selection["target_iteration_delta"] == 0
    assert selection["maximum_target_iteration_delta"] == 0
    assert selection["n_available_particle_iterations"] == 1
    assert len(species_validation["plots"]) == 21
    assert all(record["status"] == "ok" for record in species_validation["plots"])

    animation = json.loads(
        (case_dir / "animations/validation.json").read_text(encoding="utf-8")
    )
    assert animation["schema_version"] == 1
    assert animation["status"] == "ok"
    assert animation["minimum_frames"] >= 2
    assert len(animation["videos"]) == 2
    assert all(video["status"] == "ok" for video in animation["videos"])
    assert all(video["frame_count"] >= 2 for video in animation["videos"])
    for video in animation["videos"]:
        path = Path(video["path"])
        if not path.is_absolute():
            path = case_dir / path
        assert path.is_file()
        assert path.stat().st_size == int(video["bytes"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == video["sha256"]

    summary_path = case_dir / "particle_analysis/particle_summary.csv"
    with summary_path.open(newline="", encoding="utf-8-sig") as stream:
        summary_rows = list(csv.DictReader(stream))
    assert [record["species_scope"] for record in summary_rows] == expected_scopes
    by_scope = {record["species_scope"]: record for record in summary_rows}
    total_macro = {
        scope: int(float(by_scope[scope]["n_macroparticles_total"]))
        for scope in expected_scopes
    }
    assert total_macro["all_electrons"] == (
        total_macro["preionized_background_electrons"]
        + total_macro["nitrogen_ionized_electrons"]
    )
    if expected_fraction == 0.0:
        assert total_macro["nitrogen_ionized_electrons"] == 0
    else:
        doped_canary_forward_ge5mev_observed = (
            total_macro["nitrogen_ionized_electrons"] > 0
        )

    compact_rows = []
    display_columns = [
        "species_scope",
        "n_macroparticles_total",
        "n_macroparticles_soft50",
        "charge_soft50_pC",
        "energy_p90_soft50_MeV",
        "energy_relative_spread_rms_soft50",
        "theta_r_p95_soft50_mrad",
        "emitn_xy_soft50_um_rad",
        "soft50_status",
    ]
    for record in summary_rows:
        compact_rows.append({key: record.get(key, "") for key in display_columns})

    case_summary = {
        "case_id": case_id,
        "case_name": row["CASE_NAME"],
        "nitrogen_fraction": expected_fraction,
        "state": state["state"],
        "particle_iteration": selection["selected_particle_iteration"],
        "particle_scopes": compact_rows,
        "animation_frames": {
            Path(video["path"]).name: video["frame_count"]
            for video in animation["videos"]
        },
        "raw_deleted_files": cleanup["deleted_file_count"],
        "raw_deleted_bytes": cleanup["deleted_total_size_bytes"],
    }
    case_summaries.append(case_summary)

assert isinstance(doped_canary_forward_ge5mev_observed, bool)

state_counts = Counter()
for row in rows:
    state = json.loads(
        (iteration / row["CASE_NAME"] / "state.json").read_text(encoding="utf-8")
    )
    state_counts[state["state"]] += 1
assert state_counts == {"Created": 33, "Raw_deleted": 2}

assert not list(iteration.rglob("*.h5"))
assert not list(iteration.rglob("*.hdf5"))

result = {
    "schema_version": 1,
    "status": "ready_for_rest_and_chain",
    "root": str(root),
    "canary_launch_audit": str(launch_path),
    "canary_job_id": str(launch["slurm_job_id"]),
    "canary_launch_source_commits": launch["source_commits"],
    "state_counts": dict(state_counts),
    "case_summaries": case_summaries,
    "nitrogen_species_diagnostic_validated": True,
    "doped_canary_forward_ge5mev_observed": (
        doped_canary_forward_ge5mev_observed
    ),
    "audit_source_commits": {
        "guiding_analysis_module": os.environ["GA_SHA"],
        "campaign_workflow": os.environ["WF_SHA"],
        "campaign_optimizer": os.environ["OPT_SHA"],
    },
}
(audit / "canary_results_audit.json").write_text(
    json.dumps(result, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

print("CANARY_JOB_ID", launch["slurm_job_id"])
print("STATE_COUNTS", dict(state_counts))
for case_summary in case_summaries:
    print("CANARY_CASE", json.dumps(case_summary, sort_keys=True))
print("AUDIT_JSON", audit / "canary_results_audit.json")
PY

DOPED_CANARY_FORWARD_GE5MEV_OBSERVED="$(
"${PYTHON_BIN}" - "${AUDIT}/canary_results_audit.json" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))[
    "doped_canary_forward_ge5mev_observed"
]
assert isinstance(value, bool)
print(int(value))
PY
)"
[[ "${DOPED_CANARY_FORWARD_GE5MEV_OBSERVED}" =~ ^[01]$ ]]

[[ "$(sha256sum "${ROOT}/optimization_state.json" | awk '{print $1}')" = \
    "${STATE_SHA_BEFORE}" ]]

[[ "$(${GIT_BIN} -C "${ORIG_GA}" rev-parse HEAD)" = "${orig_ga_head}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_WF}" rev-parse HEAD)" = "${orig_wf_head}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_OPT}" rev-parse HEAD)" = "${orig_opt_head}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_GA}" status --porcelain)" = "${orig_ga_status}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_WF}" status --porcelain)" = "${orig_wf_status}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_OPT}" status --porcelain)" = "${orig_opt_status}" ]]
[[ -z "$(${GIT_BIN} -C "${GA}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${WF}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${OPT}" status --porcelain)" ]]

trap - EXIT
echo "CANARY_RESULTS_OK=1"
echo "READY_FOR_REST_AND_CHAIN=1"
echo "CANARY_CASES_RAW_DELETED=2"
echo "REMAINING_CASES_CREATED=33"
echo "PARTICLE_EXIT_SELECTION_VALIDATED=1"
echo "SPECIES_PROVENANCE_VALIDATED=1"
echo "NITROGEN_SPECIES_DIAGNOSTIC_VALIDATED=1"
echo "DOPED_CANARY_FORWARD_GE5MEV_OBSERVED=${DOPED_CANARY_FORWARD_GE5MEV_OBSERVED}"
echo "ANIMATIONS_VALIDATED=1"
echo "NO_HDF5_REMAINING=1"
echo "NO_STATE_CHANGED=1"
echo "NO_SBATCH_CALLED=1"
echo "MULTICHANNEL_BASELINE_PRESERVED=1"
echo "AUDIT=${AUDIT}"
