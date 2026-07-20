#!/usr/bin/env bash
set -Eeuo pipefail

OLD_ROOT="${HOME}/warpx_runs/clpu_capillary_guiding_bo_004_corrected_n2_soft50_v2"
ROOT="${HOME}/warpx_runs/clpu_capillary_guiding_bo_004_corrected_n2_soft50_v3"
ITER="${ROOT}/iterations/iter_000"

GA="${HOME}/apps/src/guiding_analysis_module-clpu-adk"
WF="${HOME}/apps/src/campaign-workflow-clpu-adk"
OPT="${HOME}/apps/src/campaign-optimizer-clpu-adk"

GA_SHA="d8a42b79840935e829020ebb86dfc3bc4e1a1936"
OPT_SHA="9fc7e612f00a5ca4ee1b85de62167c6690546058"

ORIG_GA="${HOME}/apps/src/guiding_analysis_module"
ORIG_WF="${HOME}/apps/src/campaign-workflow"
ORIG_OPT="${HOME}/apps/src/campaign-optimizer"

if ! type module >/dev/null 2>&1; then
    source /etc/profile.d/modules.sh
fi
module load Git/2.41.0
GIT_BIN="$(command -v git)"
[[ "$(${GIT_BIN} --version)" = "git version 2.41.0" ]]

for required in \
    "${OLD_ROOT}" \
    "${ROOT}/optimization.json" \
    "${ROOT}/optimizer.json" \
    "${ROOT}/optimization_state.json" \
    "${ROOT}/env/campaign-workflow-clpu-adk.sh" \
    "${ROOT}/optimizer_runs/iter_000/outputs/candidate_batch.tsv" \
    "${ITER}/campaign.json" \
    "${ITER}/cases.tsv" \
    "${ITER}/input_template.py" \
    "${GA}" "${WF}" "${OPT}" \
    "${ORIG_GA}" "${ORIG_WF}" "${ORIG_OPT}"
do
    [[ -e "${required}" ]] || {
        echo "ERROR: falta ${required}"
        exit 10
    }
done

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
AUDIT="${ROOT}/audits/resume_preflight_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${AUDIT}"
exec > >(tee "${AUDIT}/resume_preflight.log") 2>&1

trap '
rc=$?
if (( rc != 0 )); then
    echo "CLPU_ADK_RESUME_PREFLIGHT_FAILED=${rc}"
    echo "La raíz se conserva para auditoría: '"${ROOT}"'"
    echo "NO_SBATCH_CALLED=1"
fi
' EXIT

export OLD_ROOT ROOT ITER AUDIT GA_SHA WF_SHA OPT_SHA

echo "=== Auditoría del punto exacto de reanudación ==="
"${HOME}/apps/venvs/campaign-workflow-py310/bin/python" - <<'PY'
import csv
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

old_root = Path(os.environ["OLD_ROOT"])
root = Path(os.environ["ROOT"])
iteration = Path(os.environ["ITER"])

old_batch = old_root / "optimizer_runs/iter_000/outputs/candidate_batch.tsv"
new_batch = root / "optimizer_runs/iter_000/outputs/candidate_batch.tsv"
assert old_batch.read_bytes() == new_batch.read_bytes()
assert hashlib.sha256(new_batch.read_bytes()).hexdigest() == (
    "56684b6315f4bce225839c241ccad5bd7575b2d1b3dae8e67e3612c191032723"
)

with (iteration / "cases.tsv").open(newline="", encoding="utf-8-sig") as stream:
    rows = list(csv.DictReader(stream, delimiter="\t"))
assert len(rows) == 35
assert [int(row["CASE_ID"]) for row in rows] == list(range(35))
assert Counter(row["OPT_SAMPLE_SOURCE"] for row in rows) == {
    "reference": 3,
    "sobol": 32,
}

template = (iteration / "input_template.py").read_bytes()
states = Counter()
for row in rows:
    case_dir = iteration / row["CASE_NAME"]
    assert (case_dir / "input.py").read_bytes() == template
    assert (case_dir / "case.env").is_file()
    state = json.loads((case_dir / "state.json").read_text())
    states[state["state"]] += 1
assert states == {"Created": 35}

state = json.loads((root / "optimization_state.json").read_text())
assert state["status"] == "campaign_materialized"
assert state["latest_iteration"] == 0
assert len(state["iterations"]) == 1
item = state["iterations"][0]
assert item["status"] == "campaign_materialized"
assert item["n_cases"] == 35
assert item["state_counts"] == {"Created": 35}
assert item["submitted"] is False
assert item["submitted_case_count"] == 0
assert item["submitted_case_ids"] == []
assert item["slurm_job_ids"] == []
assert item["recommended_action"] == "submit_iteration"

assert not list(root.rglob("*.h5"))
assert not list(root.rglob("*.hdf5"))
assert not list(iteration.rglob("post/sim_submitted.json"))

print("RESUME_POINT_OK=1")
print("CASES", len(rows))
print("STATE_COUNTS", dict(states))
print("SUBMITTED_CASES", 0)
print("HDF5_FILES", 0)
PY

echo "=== Integración PICMI/WarpX corregida ==="
module purge
module use "${HOME}/apps/modules"
module load GCC/12.1.0
module load Python/3.14.3
module load OpenBLAS/0.3.31
module load warpx/26.05-gcc12-openmpi413-all-dims

export PYTHON314_ROOT=/APPS/centos7/centos79/software/Compiler/GCC-12.1/Python/3.14.3
export LD_LIBRARY_PATH="${PYTHON314_ROOT}/lib:${LD_LIBRARY_PATH:-}"
source "${HOME}/apps/venvs/warpx-26.05-py314/bin/activate"

export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

(
    cd "${WF}"
    PYTHONPATH="${WF}" python -m unittest \
        tests.test_corrected_capillary_sunrise_example.CorrectedCapillarySunriseExampleTests.test_warpx_picmi_serializes_exact_filtered_particle_interval
)

CONTROL_NAMES=(
    "000_f32_chan_n4e18cm3_L5mm_d300um_foc0mm_N2pct0_tau30fs_rz"
    "001_f32_chan_n4e18cm3_L5mm_d300um_foc0mm_N2pct0p5_tau30fs_rz"
)

echo "=== Preflight materializado de los controles 0 y 1 ==="
for case_id in 0 1; do
    case_name="${CONTROL_NAMES[case_id]}"
    case_dir="${ITER}/${case_name}"
    log="${AUDIT}/picmi_preflight_case_${case_id}.log"
    (
        cd "${case_dir}"
        source ./case.env
        CAP_DRY_RUN=1 python ./input.py 2
    ) > "${log}" 2>&1
    grep -F \
        "[CLPU] PICMI preflight completed; simulation not started" \
        "${log}"
    echo "PICMI_PREFLIGHT_OK case_id=${case_id} log=${log}"
done

echo "=== Validación final de serialización y estado ==="
python - <<'PY'
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

with (iteration / "cases.tsv").open(newline="", encoding="utf-8-sig") as stream:
    rows = list(csv.DictReader(stream, delimiter="\t"))
assert len(rows) == 35

expected_filter = (
    "(uz > 0.0)*((sqrt(1.0+ux*ux+uy*uy+uz*uz)-1.0)*"
    "0.51099895 >= 5)"
)
preflight = []
for row, expected_fraction in zip(rows[:2], [0.0, 0.005]):
    case_dir = iteration / row["CASE_NAME"]
    resolved = json.loads((case_dir / "resolved_parameters.json").read_text())
    serialized = (case_dir / f"inputs_capillary_{row['CASE_NAME']}").read_text()

    assert resolved["schema_version"] == 3
    assert resolved["physics_model_id"] == (
        "clpu_carlos_plateau_quasiparabolic_n5_adk_v5_grid_cfl"
    )
    assert resolved["channel_profile_longitudinal_scope"] == "plateau_only"
    assert resolved["ramp_radial_model"] == "uniform_inside_capillary"
    assert math.isclose(
        resolved["nitrogen_fraction_atomic_nuclei"],
        expected_fraction,
        rel_tol=0.0,
        abs_tol=1.0e-15,
    )
    assert resolved["particle_diagnostic_policy"] == (
        "single_plateau_exit_field_aligned_filtered_v1"
    )
    assert resolved["particle_diagnostic_target"] == "plateau_exit"
    assert resolved["particle_diagnostic_target_distance_m"] == (
        resolved["plateau_end_z"] - resolved["plasma_start_z"]
    )
    assert resolved["time_step_model"] == (
        "WarpX_CylindricalYeeAlgorithm_ComputeMaxDt"
    )
    assert resolved["max_steps"] == 91459
    assert resolved["max_steps_grid_cfl_derived"] == 91459
    assert resolved["field_diagnostic_period"] == 1946
    assert resolved["particle_diagnostic_target_iteration_unaligned"] == 60973
    assert resolved["particle_diagnostic_iteration"] == 60326
    assert resolved["particle_diagnostic_intervals"] == "60326:60326"
    assert resolved["particle_diagnostic_iteration"] % resolved["field_diagnostic_period"] == 0
    assert resolved["particle_diagnostic_alignment_error_steps"] == -647
    assert math.isclose(
        resolved["particle_diagnostic_aligned_distance_m"],
        resolved["particle_diagnostic_iteration"]
        * resolved["moving_window_step_distance_m"],
        rel_tol=1.0e-14,
        abs_tol=1.0e-15,
    )
    assert abs(resolved["particle_diagnostic_alignment_error_m"]) <= (
        0.5
        * resolved["field_diagnostic_period"]
        * resolved["moving_window_step_distance_m"]
    )
    assert resolved["particle_diagnostic_dump_last_timestep"] is False
    assert resolved["particle_diagnostic_min_energy_MeV"] == 5.0
    assert resolved["particle_diagnostic_forward_only"] is True
    assert resolved["particle_diagnostic_filter_expression"] == expected_filter
    assert resolved["particle_diagnostic_iteration"] != resolved["max_steps"]

    assert 'plasma_electrons.intervals = "60326:60326"' in serialized
    assert "plasma_electrons.dump_last_timestep = 0" in serialized
    assert "plasma_electrons.dump_last_timestep = 1" not in serialized
    for species in [
        "preionized_background_electrons",
        "nitrogen_ionized_electrons",
    ]:
        assert (
            f"plasma_electrons.{species}."
            "plot_filter_function(t,x,y,z,ux,uy,uz)"
        ) in serialized
    assert expected_filter in serialized

    if expected_fraction == 0.0:
        assert "nitrogen_ions.do_field_ionization" not in serialized
    else:
        assert "nitrogen_ions.do_field_ionization = 1" in serialized
        assert "nitrogen_ions.ionization_initial_level = 5" in serialized

    preflight.append({
        "case_id": int(row["CASE_ID"]),
        "case_name": row["CASE_NAME"],
        "nitrogen_fraction": expected_fraction,
        "particle_iteration": resolved["particle_diagnostic_iteration"],
        "particle_intervals": resolved["particle_diagnostic_intervals"],
        "particle_filter": expected_filter,
    })

states = Counter()
for row in rows:
    state = json.loads((iteration / row["CASE_NAME"] / "state.json").read_text())
    states[state["state"]] += 1
assert states == {"Created": 35}

optimization_state = json.loads((root / "optimization_state.json").read_text())
item = optimization_state["iterations"][0]
assert item["submitted"] is False
assert item["submitted_case_count"] == 0
assert item["slurm_job_ids"] == []
assert item["recommended_action"] == "submit_iteration"

hdf5 = [*root.rglob("*.h5"), *root.rglob("*.hdf5")]
assert not hdf5, hdf5

summary = {
    "schema_version": 1,
    "status": "ready_for_canary",
    "resume_reason": "existing materialization revalidated after source update",
    "root": str(root),
    "source_commits": {
        "guiding_analysis_module": os.environ["GA_SHA"],
        "campaign_workflow": os.environ["WF_SHA"],
        "campaign_optimizer": os.environ["OPT_SHA"],
    },
    "candidate_batch_sha256": hashlib.sha256(
        (root / "optimizer_runs/iter_000/outputs/candidate_batch.tsv").read_bytes()
    ).hexdigest(),
    "case_count": len(rows),
    "state_counts": dict(states),
    "submitted_case_count": 0,
    "hdf5_file_count": 0,
    "preflight": preflight,
}
(audit / "resume_preflight_audit.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n"
)

print("MATERIALIZED_CASES", len(rows))
print("STATE_COUNTS", dict(states))
print("SUBMITTED_CASES", 0)
print("HDF5_FILES", len(hdf5))
for item in preflight:
    print("PREFLIGHT", json.dumps(item, sort_keys=True))
print("AUDIT", audit / "resume_preflight_audit.json")
PY

STATE_SHA_AFTER="$(sha256sum "${ROOT}/optimization_state.json" | awk '{print $1}')"
[[ "${STATE_SHA_AFTER}" = "${STATE_SHA_BEFORE}" ]]

module load Git/2.41.0
[[ "$(${GIT_BIN} -C "${ORIG_GA}" rev-parse HEAD)" = "${orig_ga_head}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_WF}" rev-parse HEAD)" = "${orig_wf_head}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_OPT}" rev-parse HEAD)" = "${orig_opt_head}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_GA}" status --porcelain)" = "${orig_ga_status}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_WF}" status --porcelain)" = "${orig_wf_status}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_OPT}" status --porcelain)" = "${orig_opt_status}" ]]
[[ -z "$(${GIT_BIN} -C "${GA}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${WF}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${OPT}" status --porcelain)" ]]

du -sh "${ROOT}"

trap - EXIT
echo "RESUME_PREFLIGHT_OK=1"
echo "READY_FOR_CANARY=1"
echo "MATERIALIZED_CASES=35"
echo "SUBMITTED_CASES=0"
echo "NO_HDF5_CREATED=1"
echo "NO_SBATCH_CALLED=1"
echo "NO_OPTIMIZATION_STATE_CHANGED=1"
echo "OLD_ROOT_UNMODIFIED=1"
echo "MULTICHANNEL_BASELINE_PRESERVED=1"
echo "AUDIT=${AUDIT}"
