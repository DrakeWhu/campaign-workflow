#!/usr/bin/env bash
set -Eeuo pipefail

OLD_ROOT="${HOME}/warpx_runs/clpu_capillary_guiding_bo_004_corrected_n2_soft50"
ROOT="${HOME}/warpx_runs/clpu_capillary_guiding_bo_004_corrected_n2_soft50_v2"

GA="${HOME}/apps/src/guiding_analysis_module-clpu-adk"
WF="${HOME}/apps/src/campaign-workflow-clpu-adk"
OPT="${HOME}/apps/src/campaign-optimizer-clpu-adk"

GA_SHA="d8a42b79840935e829020ebb86dfc3bc4e1a1936"
WF_SHA=""
OPT_SHA="fee95b96ee9697bded0ac261003ee87cf1c6260b"

GA_BRANCH="feat/clpu-soft50-v1"
WF_BRANCH="feat/clpu-corrected-capillary-ionization"
OPT_BRANCH="feat/clpu-soft50-sobol"

ORIG_GA="${HOME}/apps/src/guiding_analysis_module"
ORIG_WF="${HOME}/apps/src/campaign-workflow"
ORIG_OPT="${HOME}/apps/src/campaign-optimizer"

OVERLAY="${HOME}/apps/python-overlays/clpu-adk-imageio-ffmpeg-py310"
GA_PY="${HOME}/apps/venvs/guiding-analysis-py310/bin/python"
WF_PY="${HOME}/apps/venvs/campaign-workflow-py310/bin/python"
OPT_PY="${HOME}/apps/venvs/optimas-py310/bin/python"

[[ -d "${OLD_ROOT}" ]] || {
    echo "ERROR: falta la raíz anterior de auditoría: ${OLD_ROOT}"
    exit 10
}
[[ ! -e "${ROOT}" ]] || {
    echo "ERROR: la raíz v2 ya existe; no se toca: ${ROOT}"
    exit 11
}

if ! type module >/dev/null 2>&1; then
    source /etc/profile.d/modules.sh
fi
module load Git/2.41.0
GIT_BIN="$(command -v git)"
[[ "$(${GIT_BIN} --version)" = "git version 2.41.0" ]]

for required in \
    "${GA}" "${WF}" "${OPT}" \
    "${ORIG_GA}" "${ORIG_WF}" "${ORIG_OPT}" \
    "${OVERLAY}" "${GA_PY}" "${WF_PY}" "${OPT_PY}"
do
    [[ -e "${required}" ]] || {
        echo "ERROR: falta ${required}"
        exit 12
    }
done

orig_ga_head="$(${GIT_BIN} -C "${ORIG_GA}" rev-parse HEAD)"
orig_wf_head="$(${GIT_BIN} -C "${ORIG_WF}" rev-parse HEAD)"
orig_opt_head="$(${GIT_BIN} -C "${ORIG_OPT}" rev-parse HEAD)"
orig_ga_status="$(${GIT_BIN} -C "${ORIG_GA}" status --porcelain)"
orig_wf_status="$(${GIT_BIN} -C "${ORIG_WF}" status --porcelain)"
orig_opt_status="$(${GIT_BIN} -C "${ORIG_OPT}" status --porcelain)"

update_worktree() {
    local repo="$1"
    local branch="$2"
    local expected="$3"
    local actual

    [[ -z "$(${GIT_BIN} -C "${repo}" status --porcelain)" ]] || {
        echo "ERROR: worktree ADK no limpio: ${repo}"
        return 20
    }

    "${GIT_BIN}" -C "${repo}" fetch --no-tags origin \
        "refs/heads/${branch}:refs/remotes/origin/${branch}"
    actual="$(${GIT_BIN} -C "${repo}" rev-parse "refs/remotes/origin/${branch}")"
    [[ "${actual}" = "${expected}" ]] || {
        echo "ERROR: SHA remoto inesperado para ${repo}: ${actual}"
        return 21
    }

    "${GIT_BIN}" -C "${repo}" switch --detach "${expected}"
    [[ "$(${GIT_BIN} -C "${repo}" rev-parse HEAD)" = "${expected}" ]]
    [[ -z "$(${GIT_BIN} -C "${repo}" status --porcelain)" ]]
    echo "UPDATED ${repo}@${expected}"
}

echo "=== Actualización de worktrees ADK aislados ==="
update_worktree "${GA}" "${GA_BRANCH}" "${GA_SHA}"
update_worktree "${OPT}" "${OPT_BRANCH}" "${OPT_SHA}"
[[ -z "$(${GIT_BIN} -C "${WF}" status --porcelain)" ]] || {
    echo "ERROR: worktree ADK no limpio: ${WF}"
    exit 22
}
WF_SHA="$(${GIT_BIN} -C "${WF}" rev-parse HEAD)"
echo "USING ${WF}@${WF_SHA}"

export PYTHONDONTWRITEBYTECODE=1
export MPLBACKEND=Agg
export MPLCONFIGDIR="/tmp/${USER}-clpu-adk-matplotlib"
mkdir -p "${MPLCONFIGDIR}"

echo "=== Pruebas de guiding-analysis ==="
(
    cd "${GA}"
    PYTHONPATH="${OVERLAY}:${GA}" \
        "${GA_PY}" -m unittest discover -s tests -p 'test_*.py'
)

echo "=== Pruebas de campaign-workflow ==="
(
    cd "${WF}"
    PYTHONPATH="${OVERLAY}:${WF}:${GA}" \
        "${WF_PY}" -m unittest discover -s tests -p 'test_*.py'
)

echo "=== Pruebas de campaign-optimizer ==="
source "${HOME}/apps/env/campaign-optimizer.sh"
(
    cd "${OPT}"
    PYTHONPATH="${OPT}:${WF}:${GA}" \
        "${OPT_PY}" -m unittest discover -s tests -p 'test_*.py'
)

[[ -z "$(${GIT_BIN} -C "${GA}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${WF}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${OPT}" status --porcelain)" ]]

echo "=== Creación de la raíz fresca v2 ==="
umask 002
mkdir -p \
    "${ROOT}/template_campaign" \
    "${ROOT}/iterations" \
    "${ROOT}/optimizer_runs" \
    "${ROOT}/loop_logs" \
    "${ROOT}/runtime" \
    "${ROOT}/env" \
    "${ROOT}/audits"

AUDIT="${ROOT}/audits/rebuild_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${AUDIT}"
exec > >(tee "${AUDIT}/rebuild.log") 2>&1

trap '
rc=$?
if (( rc != 0 )); then
    echo "CLPU_ADK_REBUILD_V2_FAILED=${rc}"
    echo "Se conserva la raíz parcial para auditoría: '"${ROOT}"'"
    echo "NO_SBATCH_CALLED=1"
fi
' EXIT

cp "${WF}/examples/sunrise/corrected_capillary/campaign.json" \
   "${ROOT}/template_campaign/campaign.json"
cp "${WF}/examples/sunrise/corrected_capillary/input_template.py" \
   "${ROOT}/template_campaign/input_template.py"
cp "${WF}/examples/sunrise/corrected_capillary/optimization.json" \
   "${ROOT}/optimization.json"
cp "${OPT}/examples/optimizer_clpu_corrected_soft50_sunrise.json" \
   "${ROOT}/optimizer.json"

cat > "${ROOT}/env/campaign-workflow-clpu-adk.sh" <<'WF_ENV_FILE'
#!/usr/bin/env bash
set -Eeuo pipefail

source "${HOME}/apps/env/campaign-workflow.sh"

export CAMPAIGN_WORKFLOW_SRC="${HOME}/apps/src/campaign-workflow-clpu-adk"
export GUIDING_ANALYSIS_SRC="${HOME}/apps/src/guiding_analysis_module-clpu-adk"
export GUIDING_ANALYSIS_ROOT="${GUIDING_ANALYSIS_SRC}"
export GUIDING_ANALYSIS_VENV="${HOME}/apps/venvs/guiding-analysis-py310"
export CLPU_ADK_IMAGEIO_OVERLAY="${HOME}/apps/python-overlays/clpu-adk-imageio-ffmpeg-py310"

export PYTHONDONTWRITEBYTECODE=1
export MPLBACKEND=Agg
export PYTHONPATH="${CLPU_ADK_IMAGEIO_OVERLAY}:${CAMPAIGN_WORKFLOW_SRC}:${GUIDING_ANALYSIS_SRC}${PYTHONPATH:+:${PYTHONPATH}}"
WF_ENV_FILE

cat > "${ROOT}/env/campaign-optimizer-clpu-adk.sh" <<'OPT_ENV_FILE'
#!/usr/bin/env bash
set -Eeuo pipefail

source "${HOME}/apps/env/campaign-optimizer.sh"

export CAMPAIGN_OPTIMIZER_SRC="${HOME}/apps/src/campaign-optimizer-clpu-adk"
export CAMPAIGN_WORKFLOW_SRC="${HOME}/apps/src/campaign-workflow-clpu-adk"
export GUIDING_ANALYSIS_SRC="${HOME}/apps/src/guiding_analysis_module-clpu-adk"

export PYTHONDONTWRITEBYTECODE=1
export MPLBACKEND=Agg
export PYTHONPATH="${CAMPAIGN_OPTIMIZER_SRC}:${CAMPAIGN_WORKFLOW_SRC}:${GUIDING_ANALYSIS_SRC}${PYTHONPATH:+:${PYTHONPATH}}"
OPT_ENV_FILE

chmod 750 \
    "${ROOT}/env/campaign-workflow-clpu-adk.sh" \
    "${ROOT}/env/campaign-optimizer-clpu-adk.sh"

export ROOT OPT
"${OPT_PY}" - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["ROOT"])
path = root / "optimization.json"
payload = json.loads(path.read_text(encoding="utf-8"))

expected = "clpu_capillary_guiding_bo_004_corrected_n2_soft50_v2"
assert payload["optimization_name"] == expected
assert payload["campaign_preparation"]["campaign_name_template"] == (
    expected + "_iter_{next_iteration:03d}"
)

payload["optimizer"]["working_directory"] = os.environ["OPT"]
payload["optimizer"]["env_script"] = str(
    root / "env/campaign-optimizer-clpu-adk.sh"
)
payload["optimizer"]["optimizer_config"] = str(root / "optimizer.json")
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

echo "=== Construcción determinista de iteration 0 ==="
source "${ROOT}/env/campaign-optimizer-clpu-adk.sh"
cd "${ROOT}"
python - <<'PY'
import campaign_optimizer
print("campaign_optimizer", campaign_optimizer.__file__)
assert "campaign-optimizer-clpu-adk" in campaign_optimizer.__file__
PY

python -m campaign_optimizer.cli.run_iteration \
    --config "${ROOT}/optimizer.json" \
    --iteration 0 \
    --build-candidate-batch \
    --build-report

OLD_BATCH="${OLD_ROOT}/optimizer_runs/iter_000/outputs/candidate_batch.tsv"
BATCH="${ROOT}/optimizer_runs/iter_000/outputs/candidate_batch.tsv"
PLAN="${ROOT}/optimizer_runs/iter_000/outputs/batch_campaign_plan.json"
TEMPLATE="${ROOT}/template_campaign"
ITER="${ROOT}/iterations/iter_000"
CAMPAIGN_NAME="clpu_capillary_guiding_bo_004_corrected_n2_soft50_v2_iter_000"

export OLD_BATCH BATCH PLAN ITER AUDIT
python - <<'PY'
import csv
import hashlib
import os
from collections import Counter
from pathlib import Path

old = Path(os.environ["OLD_BATCH"])
new = Path(os.environ["BATCH"])
assert old.is_file(), old
assert new.is_file(), new
assert old.read_bytes() == new.read_bytes(), "v2 changed the reviewed iteration-0 batch"

sha = hashlib.sha256(new.read_bytes()).hexdigest()
assert sha == "56684b6315f4bce225839c241ccad5bd7575b2d1b3dae8e67e3612c191032723"

with new.open(newline="", encoding="utf-8-sig") as stream:
    rows = list(csv.DictReader(stream, delimiter="\t"))
assert len(rows) == 35
assert [int(row["CASE_ID"]) for row in rows] == list(range(35))
assert Counter(row["OPT_SAMPLE_SOURCE"] for row in rows) == {
    "reference": 3,
    "sobol": 32,
}
assert [float(row["NITROGEN_DOPANT_FRACTION"]) for row in rows[:3]] == [
    0.0,
    0.005,
    0.01,
]
assert all(int(row["CAP_NR"]) == 192 for row in rows)
print("BATCH_BYTE_IDENTICAL_TO_V1=1")
print("BATCH_ROWS", len(rows))
print("BATCH_SHA256", sha)
PY

echo "=== Preparación y materialización sin submit ==="
source "${ROOT}/env/campaign-workflow-clpu-adk.sh"

PREPARE="${WF}/campaign_workflow/cli/prepare_batch_campaign.py"
MATERIALIZE="${WF}/campaign_workflow/cli/materialize_cases.py"
INIT_STATES="${WF}/campaign_workflow/cli/init_case_states.py"
OPTIMIZER_TICK="${WF}/campaign_workflow/cli/optimizer_tick.py"

python "${PREPARE}" \
    --candidate-batch "${BATCH}" \
    --batch-plan "${PLAN}" \
    --template-campaign-root "${TEMPLATE}" \
    --output-campaign-root "${ITER}" \
    --campaign-name "${CAMPAIGN_NAME}" \
    --dry-run

[[ ! -e "${ITER}" ]]

python "${PREPARE}" \
    --candidate-batch "${BATCH}" \
    --batch-plan "${PLAN}" \
    --template-campaign-root "${TEMPLATE}" \
    --output-campaign-root "${ITER}" \
    --campaign-name "${CAMPAIGN_NAME}" \
    --execute

python "${MATERIALIZE}" --campaign-root "${ITER}" --dry-run
python "${MATERIALIZE}" --campaign-root "${ITER}"
python "${INIT_STATES}" --campaign-root "${ITER}" --dry-run
python "${INIT_STATES}" --campaign-root "${ITER}"
python "${INIT_STATES}" --campaign-root "${ITER}" --check

python "${OPTIMIZER_TICK}" \
    --optimization-root "${ROOT}" \
    --init-state \
    > "${AUDIT}/optimizer_tick_init_output.json"

echo "=== Integración PICMI/WarpX y serialización materializada ==="
module purge
module use "${HOME}/apps/modules"
module load GCC/12.1.0
module load Python/3.14.3
module load OpenBLAS/0.3.31
module load warpx/26.05-gcc12-openmpi413-all-dims

export PYTHON314_ROOT=/APPS/centos7/centos79/software/Compiler/GCC-12.1/Python/3.14.3
export LD_LIBRARY_PATH="${PYTHON314_ROOT}/lib:${LD_LIBRARY_PATH:-}"
source "${HOME}/apps/venvs/warpx-26.05-py314/bin/activate"

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

export ROOT ITER AUDIT GA_SHA WF_SHA OPT_SHA
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

    assert resolved["schema_version"] == 2
    assert resolved["physics_model_id"] == "clpu_carlos_plateau_quasiparabolic_n5_adk_v4"
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
    assert resolved["particle_diagnostic_target_iteration_unaligned"] == 128000
    assert resolved["particle_diagnostic_iteration"] == 126666
    assert resolved["particle_diagnostic_intervals"] == "126666:126666"
    assert resolved["particle_diagnostic_iteration"] % resolved["field_diagnostic_period"] == 0
    assert resolved["particle_diagnostic_alignment_error_steps"] == -1334
    assert resolved["particle_diagnostic_dump_last_timestep"] is False
    assert resolved["particle_diagnostic_min_energy_MeV"] == 5.0
    assert resolved["particle_diagnostic_forward_only"] is True
    assert resolved["particle_diagnostic_filter_expression"] == expected_filter
    assert resolved["particle_diagnostic_iteration"] != resolved["max_steps"]

    assert 'plasma_electrons.intervals = "126666:126666"' in serialized
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
assert optimization_state["status"] == "campaign_materialized"
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
    "root": str(root),
    "source_commits": {
        "guiding_analysis_module": os.environ["GA_SHA"],
        "campaign_workflow": os.environ["WF_SHA"],
        "campaign_optimizer": os.environ["OPT_SHA"],
    },
    "candidate_batch_sha256": hashlib.sha256(
        (root / "optimizer_runs/iter_000/outputs/candidate_batch.tsv").read_bytes()
    ).hexdigest(),
    "candidate_batch_byte_identical_to_previous_root": True,
    "case_count": len(rows),
    "state_counts": dict(states),
    "submitted_case_count": 0,
    "hdf5_file_count": 0,
    "preflight": preflight,
}
(audit / "rebuild_audit.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n"
)

print("MATERIALIZED_CASES", len(rows))
print("STATE_COUNTS", dict(states))
print("SUBMITTED_CASES", 0)
print("HDF5_FILES", len(hdf5))
for item in preflight:
    print("PREFLIGHT", json.dumps(item, sort_keys=True))
print("AUDIT", audit / "rebuild_audit.json")
PY

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
echo "REBUILD_V2_OK=1"
echo "READY_FOR_CANARY=1"
echo "MATERIALIZED_CASES=35"
echo "SUBMITTED_CASES=0"
echo "NO_HDF5_CREATED=1"
echo "NO_SBATCH_CALLED=1"
echo "OLD_ROOT_UNMODIFIED=1"
echo "MULTICHANNEL_BASELINE_PRESERVED=1"
echo "AUDIT=${AUDIT}"
