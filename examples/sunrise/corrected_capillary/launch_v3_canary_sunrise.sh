#!/usr/bin/env bash
set -Eeuo pipefail

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

WF_ENV="${ROOT}/env/campaign-workflow-clpu-adk.sh"
TICK="${WF}/campaign_workflow/cli/optimizer_tick.py"
RUNNER="${WF}/examples/sunrise/run_warpx_case_sunrise.sh"
STOCK_SUBMIT="${WF}/examples/sunrise/submit_case_cycle_array.sh"
LOCAL_SUBMIT="${ROOT}/runtime/submit_case_cycle_array_t12h.sh"
PYTHON_BIN="${HOME}/apps/venvs/campaign-workflow-py310/bin/python"

AUDIT="${ROOT}/audits/canary_iter000_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${AUDIT}"
exec > >(tee "${AUDIT}/canary_launch.log") 2>&1

SUBMISSION_PHASE_STARTED=0
SUBMISSION_RECORDED=0

on_exit() {
    local rc=$?
    if (( rc != 0 )); then
        echo "CLPU_ADK_CANARY_FAILED=${rc}"
        echo "SUBMISSION_PHASE_STARTED=${SUBMISSION_PHASE_STARTED}"
        echo "SUBMISSION_RECORDED=${SUBMISSION_RECORDED}"
        if (( SUBMISSION_PHASE_STARTED == 1 && SUBMISSION_RECORDED == 0 )); then
            echo "ATENCION: comprueba squeue y submit_canary_execute.json antes de reintentar."
        fi
        echo "AUDIT=${AUDIT}"
    fi
}
trap on_exit EXIT

for required in \
    "${ROOT}/optimization.json" \
    "${ROOT}/optimizer.json" \
    "${ROOT}/optimization_state.json" \
    "${ROOT}/optimizer_runs/iter_000/outputs/candidate_batch.tsv" \
    "${ITER}/campaign.json" \
    "${ITER}/cases.tsv" \
    "${WF_ENV}" \
    "${TICK}" \
    "${RUNNER}" \
    "${STOCK_SUBMIT}" \
    "${PYTHON_BIN}" \
    "${GA}" "${WF}" "${OPT}" \
    "${ORIG_GA}" "${ORIG_WF}" "${ORIG_OPT}"
do
    [[ -e "${required}" ]] || {
        echo "ERROR: falta ${required}"
        exit 10
    }
done

[[ -x "${RUNNER}" ]] || {
    echo "ERROR: runner no ejecutable: ${RUNNER}"
    exit 11
}

if ! type module >/dev/null 2>&1; then
    source /etc/profile.d/modules.sh
fi
module load Git/2.41.0
GIT_BIN="$(command -v git)"
[[ "$(${GIT_BIN} --version)" = "git version 2.41.0" ]]

echo "=== Fuentes ADK y aislamiento multichannel ==="
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

export ROOT ITER AUDIT GA_SHA OPT_SHA WF_SHA

PREFLIGHT_AUDIT="$(
"${PYTHON_BIN}" - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["ROOT"])
paths = list((root / "audits").glob("resume_preflight_*/resume_preflight_audit.json"))
assert paths, "no resume_preflight_audit.json found"
print(max(paths, key=lambda path: (path.stat().st_mtime_ns, str(path))))
PY
)"
export PREFLIGHT_AUDIT

STATE_SHA_BEFORE="$(sha256sum "${ROOT}/optimization_state.json" | awk '{print $1}')"

echo "=== Gate cerrado de preflight y estado ==="
"${PYTHON_BIN}" - <<'PY'
import csv
import json
import math
import os
from collections import Counter
from pathlib import Path

root = Path(os.environ["ROOT"])
iteration = Path(os.environ["ITER"])
preflight_path = Path(os.environ["PREFLIGHT_AUDIT"])

preflight = json.loads(preflight_path.read_text())
assert preflight["status"] == "ready_for_canary"
assert Path(preflight["root"]).resolve() == root.resolve()
assert preflight["case_count"] == 35
assert preflight["state_counts"] == {"Created": 35}
assert preflight["submitted_case_count"] == 0
assert preflight["hdf5_file_count"] == 0
assert preflight["source_commits"] == {
    "guiding_analysis_module": os.environ["GA_SHA"],
    "campaign_workflow": os.environ["WF_SHA"],
    "campaign_optimizer": os.environ["OPT_SHA"],
}

with (iteration / "cases.tsv").open(newline="", encoding="utf-8-sig") as stream:
    rows = list(csv.DictReader(stream, delimiter="\t"))
assert len(rows) == 35
assert [int(row["CASE_ID"]) for row in rows] == list(range(35))
assert rows[0]["CASE_NAME"] == (
    "000_f32_chan_n4e18cm3_L5mm_d300um_foc0mm_N2pct0_tau30fs_rz"
)
assert rows[1]["CASE_NAME"] == (
    "001_f32_chan_n4e18cm3_L5mm_d300um_foc0mm_N2pct0p5_tau30fs_rz"
)
assert math.isclose(float(rows[0]["NITROGEN_DOPANT_FRACTION"]), 0.0)
assert math.isclose(float(rows[1]["NITROGEN_DOPANT_FRACTION"]), 0.005)

states = Counter()
for row in rows:
    case_dir = iteration / row["CASE_NAME"]
    state = json.loads((case_dir / "state.json").read_text())
    states[state["state"]] += 1
assert states == {"Created": 35}

state = json.loads((root / "optimization_state.json").read_text())
assert state["status"] == "campaign_materialized"
assert state["latest_iteration"] == 0
assert len(state["iterations"]) == 1
item = state["iterations"][0]
assert item["status"] == "campaign_materialized"
assert item["submitted"] is False
assert item["submitted_case_ids"] == []
assert item["submitted_case_count"] == 0
assert item["slurm_job_ids"] == []
assert item["recommended_action"] == "submit_iteration"

campaign = json.loads((iteration / "campaign.json").read_text())
outputs = {output["name"]: output for output in campaign["analysis"]["outputs"]}
required_outputs = {
    "particle_summary",
    "particle_soft50_curves",
    "particle_acceptance_curves",
    "particle_species_validation",
    "animation_eperp2",
    "animation_ez_wake",
    "animation_frame_metrics",
    "animation_validation",
}
assert required_outputs.issubset(outputs)
assert all(outputs[name]["required"] is True for name in required_outputs)
cleanup = campaign["cleanup"]
assert cleanup["require_raw_validated"] is True
assert cleanup["require_reduced_validated"] is True
assert cleanup["require_delete_manifest"] is True
assert cleanup["allow_directory_delete"] is False

expected_controls = [(rows[0], 0.0), (rows[1], 0.005)]
for row, expected_fraction in expected_controls:
    case_dir = iteration / row["CASE_NAME"]
    resolved = json.loads((case_dir / "resolved_parameters.json").read_text())
    assert math.isclose(
        resolved["nitrogen_fraction_atomic_nuclei"],
        expected_fraction,
        rel_tol=0.0,
        abs_tol=1.0e-15,
    )
    assert resolved["particle_diagnostic_policy"] == (
        "single_plateau_exit_field_aligned_filtered_v1"
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
    assert resolved["particle_diagnostic_min_energy_MeV"] == 5.0
    assert resolved["particle_diagnostic_forward_only"] is True

assert not list(root.rglob("*.h5"))
assert not list(root.rglob("*.hdf5"))
assert not list(iteration.rglob("post/sim_submitted.json"))

print("PREFLIGHT_AUDIT", preflight_path)
print("MATERIALIZED_CASES", len(rows))
print("STATE_COUNTS", dict(states))
print("CANARY_CASES", [0, 1])
print("MANDATORY_PARTICLE_AND_ANIMATION_OUTPUTS=1")
print("CLEANUP_MANIFEST_GATED=1")
PY

echo "=== Submit script local T12H reproducible ==="
export STOCK_SUBMIT LOCAL_SUBMIT
"${PYTHON_BIN}" - <<'PY'
import hashlib
import os
from pathlib import Path

source = Path(os.environ["STOCK_SUBMIT"])
target = Path(os.environ["LOCAL_SUBMIT"])
stock = source.read_text()

replacements = {
    "#SBATCH --partition=T6H": "#SBATCH --partition=T12H",
    "#SBATCH --time=06:00:00": "#SBATCH --time=12:00:00",
}
expected = stock
for old, new in replacements.items():
    assert expected.count(old) == 1, old
    expected = expected.replace(old, new)

target.parent.mkdir(parents=True, exist_ok=True)
if target.exists():
    assert target.read_text() == expected, f"contenido inesperado en {target}"
else:
    target.write_text(expected)
target.chmod(0o750)

actual = target.read_text()
assert actual == expected
assert "#SBATCH --partition=T12H" in actual
assert "#SBATCH --time=12:00:00" in actual
assert "#SBATCH --partition=T6H" not in actual

print("STOCK_SHA256", hashlib.sha256(stock.encode()).hexdigest())
print("LOCAL_SHA256", hashlib.sha256(actual.encode()).hexdigest())
for line in actual.splitlines():
    if line.startswith((
        "#SBATCH --partition=",
        "#SBATCH --nodes=",
        "#SBATCH --ntasks=",
        "#SBATCH --mem=",
        "#SBATCH --time=",
    )):
        print(line)
PY

source "${WF_ENV}"
python_path="$(command -v python)"
[[ "$(readlink -f "${python_path}")" = "$(readlink -f "${PYTHON_BIN}")" ]]

DRY_RUN_JSON="${AUDIT}/submit_canary_dry_run.json"
EXECUTE_JSON="${AUDIT}/submit_canary_execute.json"
export DRY_RUN_JSON EXECUTE_JSON WF WF_ENV RUNNER

echo "=== optimizer_tick: dry-run exacto del canario ==="
"${PYTHON_BIN}" "${TICK}" \
    --optimization-root "${ROOT}" \
    --iteration 0 \
    --action submit_iteration \
    --array-spec '0-1' \
    --submit-script "${LOCAL_SUBMIT}" \
    --workflow-root "${WF}" \
    --workflow-env "${WF_ENV}" \
    --job-name "clpu4v3_adk_i000_c01" \
    --case-runner "${RUNNER}" \
    --confirm-cleanup-execute \
    --dry-run \
    > "${DRY_RUN_JSON}"

[[ "$(sha256sum "${ROOT}/optimization_state.json" | awk '{print $1}')" = \
    "${STATE_SHA_BEFORE}" ]]

"${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

doc = json.loads(Path(os.environ["DRY_RUN_JSON"]).read_text())
assert doc["mode"] == "dry-run"
assert doc["action"] == "submit_iteration"
assert doc["state_written"] is False
assert not doc.get("submit_blocked_by_guards", False)
blocked = [
    guard for guard in doc["guard_report"].get("guards", [])
    if guard.get("status") == "blocked"
]
assert not blocked, blocked

plan = doc["submit_plan"]
assert plan["iteration"] == 0
assert plan["array_spec"] == "0-1"
assert plan["submitted_case_ids"] == [0, 1]
assert plan["submitted_case_count"] == 2
assert plan["additional_submission"] is False
assert plan["confirm_cleanup_execute"] is True
assert os.path.samefile(plan["submit_script"], os.environ["LOCAL_SUBMIT"])
assert os.path.samefile(plan["workflow_root"], os.environ["WF"])
assert os.path.samefile(plan["workflow_env"], os.environ["WF_ENV"])
assert os.path.samefile(plan["case_runner"], os.environ["RUNNER"])

command = plan["submit_command"]
assert command[0] == "sbatch"
assert "--array=0-1" in command
assert "--job-name=clpu4v3_adk_i000_c01" in command
assert any("CONFIRM_CLEANUP_EXECUTE=1" in item for item in command)

print("DRY_RUN_GUARDS_OK=1")
print("ARRAY_SPEC", plan["array_spec"])
print("SUBMITTED_CASE_IDS", plan["submitted_case_ids"])
print("CLEANUP_EXECUTE_GATED", plan["confirm_cleanup_execute"])
print("SUBMIT_COMMAND", " ".join(command))
PY

echo "=== Envío único del canario ==="
SUBMISSION_PHASE_STARTED=1

"${PYTHON_BIN}" "${TICK}" \
    --optimization-root "${ROOT}" \
    --iteration 0 \
    --action submit_iteration \
    --array-spec '0-1' \
    --submit-script "${LOCAL_SUBMIT}" \
    --workflow-root "${WF}" \
    --workflow-env "${WF_ENV}" \
    --job-name "clpu4v3_adk_i000_c01" \
    --case-runner "${RUNNER}" \
    --confirm-cleanup-execute \
    --execute \
    > "${EXECUTE_JSON}"

SUBMISSION_RECORDED=1

JOB_ID="$(
"${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

doc = json.loads(Path(os.environ["EXECUTE_JSON"]).read_text())
assert doc["mode"] == "execute"
assert doc["action"] == "submit_iteration"
assert doc["state_written"] is True

result = doc["submission_result"]
assert result["return_code"] == 0
assert str(result["job_id"]).isdigit()

state = doc["optimization_state_after_submit"]
item = next(
    entry for entry in state["iterations"]
    if int(entry["iteration"]) == 0
)
assert state["status"] == "running"
assert item["status"] == "submitted"
assert item["submitted"] is True
assert item["submitted_case_ids"] == [0, 1]
assert item["submitted_case_count"] == 2
assert item["array_spec"] == "0-1"
assert item["array_specs"][-1] == "0-1"
assert item["confirm_cleanup_execute"] is True
assert str(result["job_id"]) in item["slurm_job_ids"]
print(result["job_id"])
PY
)"
export JOB_ID

echo "CANARY_JOB_ID=${JOB_ID}"
squeue \
    -j "${JOB_ID}" \
    -o '%.18i %.9P %.28j %.8u %.2t %.10M %.6D %R' \
    || true

echo "=== Verificación final de aislamiento y auditoría ==="
[[ "$(${GIT_BIN} -C "${ORIG_GA}" rev-parse HEAD)" = "${orig_ga_head}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_WF}" rev-parse HEAD)" = "${orig_wf_head}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_OPT}" rev-parse HEAD)" = "${orig_opt_head}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_GA}" status --porcelain)" = "${orig_ga_status}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_WF}" status --porcelain)" = "${orig_wf_status}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_OPT}" status --porcelain)" = "${orig_opt_status}" ]]
[[ -z "$(${GIT_BIN} -C "${GA}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${WF}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${OPT}" status --porcelain)" ]]

"${PYTHON_BIN}" - <<'PY'
import hashlib
import json
import os
from pathlib import Path

audit = Path(os.environ["AUDIT"])
summary = {
    "schema_version": 1,
    "status": "canary_submitted",
    "root": os.environ["ROOT"],
    "iteration": 0,
    "case_ids": [0, 1],
    "array_spec": "0-1",
    "slurm_job_id": os.environ["JOB_ID"],
    "partition": "T12H",
    "walltime": "12:00:00",
    "cleanup_after_required_output_validation": True,
    "preflight_audit": os.environ["PREFLIGHT_AUDIT"],
    "source_commits": {
        "guiding_analysis_module": os.environ["GA_SHA"],
        "campaign_workflow": os.environ["WF_SHA"],
        "campaign_optimizer": os.environ["OPT_SHA"],
    },
    "dry_run_sha256": hashlib.sha256(
        Path(os.environ["DRY_RUN_JSON"]).read_bytes()
    ).hexdigest(),
    "execute_sha256": hashlib.sha256(
        Path(os.environ["EXECUTE_JSON"]).read_bytes()
    ).hexdigest(),
}
(audit / "canary_launch_audit.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n"
)
print("AUDIT_JSON", audit / "canary_launch_audit.json")
PY

trap - EXIT
echo "CLPU_ADK_CANARY_SUBMITTED=1"
echo "CANARY_CASES=0,1"
echo "CANARY_JOB_ID=${JOB_ID}"
echo "PARTITION=T12H"
echo "WALLTIME=12:00:00"
echo "NO_ARRAY_THROTTLE=1"
echo "CLEANUP_AFTER_REQUIRED_OUTPUT_VALIDATION=1"
echo "FULL_CHAIN_NOT_SUBMITTED=1"
echo "MULTICHANNEL_BASELINE_PRESERVED=1"
echo "AUDIT=${AUDIT}"
