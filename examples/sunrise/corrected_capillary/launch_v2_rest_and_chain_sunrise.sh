#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${HOME}/warpx_runs/clpu_capillary_guiding_bo_004_corrected_n2_soft50_v2"
ITER="${ROOT}/iterations/iter_000"

GA="${HOME}/apps/src/guiding_analysis_module-clpu-adk"
WF="${HOME}/apps/src/campaign-workflow-clpu-adk"
OPT="${HOME}/apps/src/campaign-optimizer-clpu-adk"

GA_SHA="d8a42b79840935e829020ebb86dfc3bc4e1a1936"
CANARY_WF_SHA="280e90c349c32c43bf20f2edad1c67eeb1f9c988"
OPT_SHA="fee95b96ee9697bded0ac261003ee87cf1c6260b"

ORIG_GA="${HOME}/apps/src/guiding_analysis_module"
ORIG_WF="${HOME}/apps/src/campaign-workflow"
ORIG_OPT="${HOME}/apps/src/campaign-optimizer"

WF_ENV="${ROOT}/env/campaign-workflow-clpu-adk.sh"
TICK="${WF}/campaign_workflow/cli/optimizer_tick.py"
RUNNER="${WF}/examples/sunrise/run_warpx_case_sunrise.sh"
STOCK_SUBMIT="${WF}/examples/sunrise/submit_case_cycle_array.sh"
LOCAL_SUBMIT="${ROOT}/runtime/submit_case_cycle_array_t12h.sh"
CHAIN_SUBMITTER="${WF}/examples/sunrise/submit_morbo_chain.py"
TICK_SCRIPT="${WF}/examples/sunrise/run_optimizer_tick_materialize_only.sh"
PYTHON_BIN="${HOME}/apps/venvs/campaign-workflow-py310/bin/python"

# The first 20 MORBO batches are released. The final five remain an explicit
# convergence/quota review point. Iteration 21's tick may materialize iter_022,
# but this launcher never submits that optional batch.
CHAIN_START_ITERATION=1
CHAIN_ITERATION_COUNT=21
CHAIN_FINAL_ITERATION=21
REST_ARRAY_SPEC="2-34"
CHAIN_ARRAY_SPEC="0-31"
JOB_PREFIX="clpu4v2"

AUDIT="${ROOT}/audits/rest_chain_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${AUDIT}"
exec > >(tee "${AUDIT}/rest_chain_launch.log") 2>&1

SUBMISSION_PHASE="none"
REST_JOB_ID=""
TICK0_JOB_ID=""
CHAIN_MANIFEST=""

on_exit() {
    local rc=$?
    if (( rc != 0 )); then
        echo "CLPU_ADK_REST_CHAIN_FAILED=${rc}"
        echo "SUBMISSION_PHASE=${SUBMISSION_PHASE}"
        [[ -z "${REST_JOB_ID}" ]] || echo "REST_JOB_ID=${REST_JOB_ID}"
        [[ -z "${TICK0_JOB_ID}" ]] || echo "TICK0_JOB_ID=${TICK0_JOB_ID}"
        [[ -z "${CHAIN_MANIFEST}" ]] || echo "CHAIN_MANIFEST=${CHAIN_MANIFEST}"
        if [[ "${SUBMISSION_PHASE}" != "none" ]]; then
            echo "ATENCION: puede existir un envío parcial; no reintentes sin auditar squeue, optimization_state.json y ${AUDIT}."
        fi
        echo "AUDIT=${AUDIT}"
    fi
}
trap on_exit EXIT

for required in \
    "${ROOT}/optimization.json" \
    "${ROOT}/optimizer.json" \
    "${ROOT}/optimization_state.json" \
    "${ITER}/campaign.json" \
    "${ITER}/cases.tsv" \
    "${WF_ENV}" \
    "${TICK}" \
    "${RUNNER}" \
    "${STOCK_SUBMIT}" \
    "${CHAIN_SUBMITTER}" \
    "${TICK_SCRIPT}" \
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

echo "=== Fuentes revisadas y aislamiento multichannel ==="
[[ "$(${GIT_BIN} -C "${GA}" rev-parse HEAD)" = "${GA_SHA}" ]]
[[ "$(${GIT_BIN} -C "${OPT}" rev-parse HEAD)" = "${OPT_SHA}" ]]
[[ -z "$(${GIT_BIN} -C "${GA}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${WF}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${OPT}" status --porcelain)" ]]

WF_SHA="$(${GIT_BIN} -C "${WF}" rev-parse HEAD)"
${GIT_BIN} -C "${WF}" merge-base --is-ancestor "${CANARY_WF_SHA}" "${WF_SHA}"

orig_ga_head="$(${GIT_BIN} -C "${ORIG_GA}" rev-parse HEAD)"
orig_wf_head="$(${GIT_BIN} -C "${ORIG_WF}" rev-parse HEAD)"
orig_opt_head="$(${GIT_BIN} -C "${ORIG_OPT}" rev-parse HEAD)"
orig_ga_status="$(${GIT_BIN} -C "${ORIG_GA}" status --porcelain)"
orig_wf_status="$(${GIT_BIN} -C "${ORIG_WF}" status --porcelain)"
orig_opt_status="$(${GIT_BIN} -C "${ORIG_OPT}" status --porcelain)"

export ROOT ITER AUDIT GA_SHA OPT_SHA WF_SHA REST_ARRAY_SPEC CHAIN_ARRAY_SPEC

CANARY_RESULTS_AUDIT="$(
"${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["ROOT"])
paths = list((root / "audits").glob("canary_results_*/canary_results_audit.json"))
ready = []
for path in paths:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    if doc.get("status") == "ready_for_rest_and_chain":
        ready.append(path)
assert ready, "no ready canary_results_audit.json found"
print(max(ready, key=lambda path: (path.stat().st_mtime_ns, str(path))))
PY
)"
export CANARY_RESULTS_AUDIT

echo "=== Gate inmutable de resultados del canario ==="
"${PYTHON_BIN}" - <<'PY'
import csv
import json
import os
from collections import Counter
from pathlib import Path

root = Path(os.environ["ROOT"])
iteration = Path(os.environ["ITER"])
audit_path = Path(os.environ["CANARY_RESULTS_AUDIT"])

audit = json.loads(audit_path.read_text(encoding="utf-8"))
assert audit["status"] == "ready_for_rest_and_chain"
assert Path(audit["root"]).resolve() == root.resolve()
assert audit["state_counts"] == {"Created": 33, "Raw_deleted": 2}
assert audit["source_commits"] == {
    "guiding_analysis_module": os.environ["GA_SHA"],
    "campaign_workflow": os.environ["WF_SHA"],
    "campaign_optimizer": os.environ["OPT_SHA"],
}
assert [case["case_id"] for case in audit["case_summaries"]] == [0, 1]
assert all(case["state"] == "Raw_deleted" for case in audit["case_summaries"])

with (iteration / "cases.tsv").open(newline="", encoding="utf-8-sig") as stream:
    rows = list(csv.DictReader(stream, delimiter="\t"))
assert len(rows) == 35
assert [int(row["CASE_ID"]) for row in rows] == list(range(35))

counts = Counter()
for row in rows:
    state = json.loads(
        (iteration / row["CASE_NAME"] / "state.json").read_text(encoding="utf-8")
    )
    counts[state["state"]] += 1
assert counts == {"Created": 33, "Raw_deleted": 2}

state = json.loads((root / "optimization_state.json").read_text(encoding="utf-8"))
assert state["status"] == "running"
assert state["latest_iteration"] == 0
assert len(state["iterations"]) == 1
item = state["iterations"][0]
assert item["iteration"] == 0
assert item["submitted"] is True
assert item["submitted_case_ids"] == [0, 1]
assert item["submitted_case_count"] == 2
assert item["array_spec"] == "0-1"
assert item["confirm_cleanup_execute"] is True

assert not list(root.rglob("*.h5"))
assert not list(root.rglob("*.hdf5"))

executed_manifests = []
for path in (root / "loop_logs").glob("morbo_chain_*.json"):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    if doc.get("dry_run") is False:
        executed_manifests.append(path)
assert not executed_manifests, executed_manifests

previous_launches = list(
    (root / "audits").glob("rest_chain_*/rest_chain_launch_audit.json")
)
assert not previous_launches, previous_launches

print("CANARY_RESULTS_AUDIT", audit_path)
print("STATE_COUNTS", dict(counts))
print("PREVIOUS_SUBMITTED_CASE_IDS", item["submitted_case_ids"])
print("NO_HDF5_REMAINING=1")
print("NO_EXISTING_EXECUTED_CHAIN=1")
PY
echo "READY_FOR_REST_AND_CHAIN_AUDIT=1"

echo "=== Submit script T12H reproducible ==="
export STOCK_SUBMIT LOCAL_SUBMIT
"${PYTHON_BIN}" - <<'PY'
import hashlib
import os
from pathlib import Path

source = Path(os.environ["STOCK_SUBMIT"])
target = Path(os.environ["LOCAL_SUBMIT"])
stock = source.read_text(encoding="utf-8")

replacements = {
    "#SBATCH --partition=T6H": "#SBATCH --partition=T12H",
    "#SBATCH --time=06:00:00": "#SBATCH --time=12:00:00",
}
expected = stock
for old, new in replacements.items():
    assert expected.count(old) == 1, old
    expected = expected.replace(old, new)

assert target.is_file(), target
assert target.read_text(encoding="utf-8") == expected
assert "#SBATCH --partition=T12H" in expected
assert "#SBATCH --time=12:00:00" in expected
assert "#SBATCH --partition=T6H" not in expected

print("STOCK_SHA256", hashlib.sha256(stock.encode()).hexdigest())
print("LOCAL_SHA256", hashlib.sha256(expected.encode()).hexdigest())
PY

source "${WF_ENV}"
python_path="$(command -v python)"
[[ "$(readlink -f "${python_path}")" = "$(readlink -f "${PYTHON_BIN}")" ]]

STATE_SHA_BEFORE="$(sha256sum "${ROOT}/optimization_state.json" | awk '{print $1}')"
REST_DRY_RUN_JSON="${AUDIT}/submit_rest_iter000_dry_run.json"
REST_EXECUTE_JSON="${AUDIT}/submit_rest_iter000_execute.json"
CHAIN_DRY_RUN_JSON="${AUDIT}/submit_chain_dry_run.json"
CHAIN_EXECUTE_JSON="${AUDIT}/submit_chain_execute.json"
export REST_DRY_RUN_JSON REST_EXECUTE_JSON CHAIN_DRY_RUN_JSON CHAIN_EXECUTE_JSON
export WF WF_ENV RUNNER LOCAL_SUBMIT TICK_SCRIPT

echo "=== Dry-run del resto de iter_000 ==="
"${PYTHON_BIN}" "${TICK}" \
    --optimization-root "${ROOT}" \
    --iteration 0 \
    --action submit_iteration \
    --array-spec "${REST_ARRAY_SPEC}" \
    --submit-script "${LOCAL_SUBMIT}" \
    --workflow-root "${WF}" \
    --workflow-env "${WF_ENV}" \
    --job-name "${JOB_PREFIX}_A000_rest" \
    --case-runner "${RUNNER}" \
    --allow-additional-cases \
    --confirm-cleanup-execute \
    --dry-run \
    > "${REST_DRY_RUN_JSON}"

[[ "$(sha256sum "${ROOT}/optimization_state.json" | awk '{print $1}')" = \
    "${STATE_SHA_BEFORE}" ]]

"${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

doc = json.loads(Path(os.environ["REST_DRY_RUN_JSON"]).read_text())
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
assert plan["array_spec"] == os.environ["REST_ARRAY_SPEC"]
assert plan["submitted_case_ids"] == list(range(2, 35))
assert plan["submitted_case_count"] == 33
assert plan["previous_submitted_case_ids"] == [0, 1]
assert plan["cumulative_submitted_case_ids"] == list(range(35))
assert plan["additional_submission"] is True
assert plan["confirm_cleanup_execute"] is True
assert os.path.samefile(plan["submit_script"], os.environ["LOCAL_SUBMIT"])
assert os.path.samefile(plan["workflow_root"], os.environ["WF"])
assert os.path.samefile(plan["workflow_env"], os.environ["WF_ENV"])
assert os.path.samefile(plan["case_runner"], os.environ["RUNNER"])
command = plan["submit_command"]
assert command[0] == "sbatch"
assert f"--array={os.environ['REST_ARRAY_SPEC']}" in command
assert not any("%" in part for part in command if part.startswith("--array="))
assert any("CONFIRM_CLEANUP_EXECUTE=1" in part for part in command)

print("REST_DRY_RUN_OK=1")
print("REST_SUBMITTED_CASE_IDS", plan["submitted_case_ids"])
print("REST_SUBMIT_COMMAND", " ".join(command))
PY

echo "=== Dry-run de la cadena finita ==="
"${PYTHON_BIN}" "${CHAIN_SUBMITTER}" \
    --optimization-root "${ROOT}" \
    --start-iteration "${CHAIN_START_ITERATION}" \
    --num-additional-iterations "${CHAIN_ITERATION_COUNT}" \
    --array-spec "${CHAIN_ARRAY_SPEC}" \
    --initial-dependency-job-id 999999999 \
    --workflow-root "${WF}" \
    --workflow-env "${WF_ENV}" \
    --job-name-prefix "${JOB_PREFIX}" \
    --partition T12H \
    --time 12:00:00 \
    --nodes 1 \
    --ntasks 24 \
    --mem 64G \
    --tick-partition T1H \
    --tick-time 01:00:00 \
    --tick-nodes 1 \
    --tick-ntasks 1 \
    --tick-mem 16G \
    --optimization-config "${ROOT}/optimizer.json" \
    --case-runner "${RUNNER}" \
    > "${CHAIN_DRY_RUN_JSON}"

[[ "$(sha256sum "${ROOT}/optimization_state.json" | awk '{print $1}')" = \
    "${STATE_SHA_BEFORE}" ]]

"${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

doc = json.loads(Path(os.environ["CHAIN_DRY_RUN_JSON"]).read_text())
assert doc["dry_run"] is True
assert doc["start_iteration"] == 1
assert doc["final_iteration"] == 21
assert doc["num_additional_iterations"] == 21
assert doc["array_spec"] == os.environ["CHAIN_ARRAY_SPEC"]
assert doc["initial_dependency_job_id"] == "999999999"
assert doc["partition"] == "T12H"
assert doc["time"] == "12:00:00"
assert doc["tick_resources"] == {
    "partition": "T1H",
    "time": "01:00:00",
    "nodes": 1,
    "ntasks": 1,
    "mem": "16G",
}
assert len(doc["jobs"]) == 42
assert doc["jobs"][0]["kind"] == "array"
assert doc["jobs"][0]["iteration"] == 1
assert doc["jobs"][0]["dependency"] == "afterok:999999999"
assert doc["jobs"][1]["dependency"] == "afterok:A_001"
assert doc["jobs"][2]["dependency"] == "afterok:T_001"
assert doc["jobs"][-1]["kind"] == "tick"
assert doc["jobs"][-1]["iteration"] == 21
for job in doc["jobs"]:
    command = job["submit_command"]
    assert command[0] == "sbatch"
    if job["kind"] == "array":
        assert f"--array={os.environ['CHAIN_ARRAY_SPEC']}" in command
        assert not any("%" in part for part in command if part.startswith("--array="))
        assert any("CONFIRM_CLEANUP_EXECUTE=1" in part for part in command)

print("CHAIN_DRY_RUN_OK=1")
print("CHAIN_JOB_COUNT", len(doc["jobs"]))
print("CHAIN_TEXT", doc["chain_text"])
PY

echo "=== Envío del resto de iter_000 ==="
SUBMISSION_PHASE="rest_iter000"
"${PYTHON_BIN}" "${TICK}" \
    --optimization-root "${ROOT}" \
    --iteration 0 \
    --action submit_iteration \
    --array-spec "${REST_ARRAY_SPEC}" \
    --submit-script "${LOCAL_SUBMIT}" \
    --workflow-root "${WF}" \
    --workflow-env "${WF_ENV}" \
    --job-name "${JOB_PREFIX}_A000_rest" \
    --case-runner "${RUNNER}" \
    --allow-additional-cases \
    --confirm-cleanup-execute \
    --execute \
    > "${REST_EXECUTE_JSON}"

REST_JOB_ID="$(
"${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

doc = json.loads(Path(os.environ["REST_EXECUTE_JSON"]).read_text())
assert doc["mode"] == "execute"
assert doc["action"] == "submit_iteration"
assert doc["state_written"] is True
result = doc["submission_result"]
assert result["return_code"] == 0
assert str(result["job_id"]).isdigit()

state = doc["optimization_state_after_submit"]
item = next(entry for entry in state["iterations"] if entry["iteration"] == 0)
assert item["submitted_case_ids"] == list(range(35))
assert item["submitted_case_count"] == 35
assert item["array_specs"][-1] == os.environ["REST_ARRAY_SPEC"]
assert item["confirm_cleanup_execute"] is True
assert str(result["job_id"]) in item["slurm_job_ids"]
print(result["job_id"])
PY
)"
[[ "${REST_JOB_ID}" =~ ^[1-9][0-9]*$ ]]
export REST_JOB_ID
echo "REST_JOB_ID=${REST_JOB_ID}"

echo "=== Tick 000 tras el resto completo ==="
SUBMISSION_PHASE="tick000"
TICK0_RAW="$(
sbatch \
    --parsable \
    --partition=T1H \
    --time=01:00:00 \
    --nodes=1 \
    --ntasks=1 \
    --mem=16G \
    --job-name="${JOB_PREFIX}_T000" \
    --dependency="afterok:${REST_JOB_ID}" \
    --output="${ROOT}/loop_logs/${JOB_PREFIX}_T000_%j.out" \
    --error="${ROOT}/loop_logs/${JOB_PREFIX}_T000_%j.err" \
    --export="ALL,CW_OPTIMIZATION_ROOT=${ROOT},CW_ITERATION=0,CW_NEXT_ITERATION=1,CW_ARRAY_SPEC=${CHAIN_ARRAY_SPEC},CW_WORKFLOW_ROOT=${WF},CW_WORKFLOW_ENV=${WF_ENV},CW_JOB_NAME_PREFIX=${JOB_PREFIX},CW_OPTIMIZATION_CONFIG=${ROOT}/optimizer.json" \
    "${TICK_SCRIPT}"
)"
TICK0_JOB_ID="${TICK0_RAW%%;*}"
[[ "${TICK0_JOB_ID}" =~ ^[1-9][0-9]*$ ]]
export TICK0_JOB_ID
echo "TICK0_JOB_ID=${TICK0_JOB_ID}"

echo "=== Cadena Sobol restante + 20 MORBO ==="
SUBMISSION_PHASE="finite_chain"
"${PYTHON_BIN}" "${CHAIN_SUBMITTER}" \
    --optimization-root "${ROOT}" \
    --start-iteration "${CHAIN_START_ITERATION}" \
    --num-additional-iterations "${CHAIN_ITERATION_COUNT}" \
    --array-spec "${CHAIN_ARRAY_SPEC}" \
    --initial-dependency-job-id "${TICK0_JOB_ID}" \
    --workflow-root "${WF}" \
    --workflow-env "${WF_ENV}" \
    --job-name-prefix "${JOB_PREFIX}" \
    --partition T12H \
    --time 12:00:00 \
    --nodes 1 \
    --ntasks 24 \
    --mem 64G \
    --tick-partition T1H \
    --tick-time 01:00:00 \
    --tick-nodes 1 \
    --tick-ntasks 1 \
    --tick-mem 16G \
    --optimization-config "${ROOT}/optimizer.json" \
    --case-runner "${RUNNER}" \
    --execute \
    > "${CHAIN_EXECUTE_JSON}"

CHAIN_MANIFEST="$(
"${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

doc = json.loads(Path(os.environ["CHAIN_EXECUTE_JSON"]).read_text())
assert doc["dry_run"] is False
assert doc["start_iteration"] == 1
assert doc["final_iteration"] == 21
assert doc["num_additional_iterations"] == 21
assert doc["array_spec"] == os.environ["CHAIN_ARRAY_SPEC"]
assert doc["initial_dependency_job_id"] == os.environ["TICK0_JOB_ID"]
assert len(doc["jobs"]) == 42
assert doc["jobs"][0]["dependency"] == f"afterok:{os.environ['TICK0_JOB_ID']}"
assert doc["jobs"][-1]["kind"] == "tick"
assert doc["jobs"][-1]["iteration"] == 21

previous_tick = os.environ["TICK0_JOB_ID"]
for offset in range(0, len(doc["jobs"]), 2):
    array_job = doc["jobs"][offset]
    tick_job = doc["jobs"][offset + 1]
    expected_iteration = 1 + offset // 2
    assert array_job["kind"] == "array"
    assert array_job["iteration"] == expected_iteration
    assert array_job["dependency"] == f"afterok:{previous_tick}"
    assert str(array_job["job_id"]).isdigit()
    assert f"--array={os.environ['CHAIN_ARRAY_SPEC']}" in array_job["submit_command"]
    assert not any(
        "%" in part for part in array_job["submit_command"]
        if part.startswith("--array=")
    )
    assert any(
        "CONFIRM_CLEANUP_EXECUTE=1" in part
        for part in array_job["submit_command"]
    )
    assert tick_job["kind"] == "tick"
    assert tick_job["iteration"] == expected_iteration
    assert tick_job["dependency"] == f"afterok:{array_job['job_id']}"
    assert str(tick_job["job_id"]).isdigit()
    previous_tick = str(tick_job["job_id"])

manifest = Path(doc["manifest_path"])
assert manifest.is_file()
assert json.loads(manifest.read_text(encoding="utf-8"))["jobs"] == doc["jobs"]
print(manifest)
PY
)"
export CHAIN_MANIFEST

echo "=== Auditoría final del envío ==="
"${PYTHON_BIN}" - <<'PY'
import hashlib
import json
import os
from pathlib import Path

audit = Path(os.environ["AUDIT"])
chain = json.loads(Path(os.environ["CHAIN_EXECUTE_JSON"]).read_text())
job_ids = [str(job["job_id"]) for job in chain["jobs"]]
summary = {
    "schema_version": 1,
    "status": "rest_and_chain_submitted",
    "root": os.environ["ROOT"],
    "canary_results_audit": os.environ["CANARY_RESULTS_AUDIT"],
    "rest_iteration": 0,
    "rest_case_ids": list(range(2, 35)),
    "rest_array_spec": os.environ["REST_ARRAY_SPEC"],
    "rest_job_id": os.environ["REST_JOB_ID"],
    "tick0_job_id": os.environ["TICK0_JOB_ID"],
    "chain_manifest": os.environ["CHAIN_MANIFEST"],
    "chain_start_iteration": 1,
    "chain_final_iteration": 21,
    "chain_job_ids": job_ids,
    "submitted_iterations": "0-21",
    "sobol_iterations": "0-1",
    "morbo_iterations": "2-21",
    "optional_iterations_not_submitted": "22-26",
    "no_array_throttle": True,
    "cleanup_after_required_output_validation": True,
    "source_commits": {
        "guiding_analysis_module": os.environ["GA_SHA"],
        "campaign_workflow": os.environ["WF_SHA"],
        "campaign_optimizer": os.environ["OPT_SHA"],
    },
    "input_sha256": {
        "rest_dry_run": hashlib.sha256(
            Path(os.environ["REST_DRY_RUN_JSON"]).read_bytes()
        ).hexdigest(),
        "rest_execute": hashlib.sha256(
            Path(os.environ["REST_EXECUTE_JSON"]).read_bytes()
        ).hexdigest(),
        "chain_dry_run": hashlib.sha256(
            Path(os.environ["CHAIN_DRY_RUN_JSON"]).read_bytes()
        ).hexdigest(),
        "chain_execute": hashlib.sha256(
            Path(os.environ["CHAIN_EXECUTE_JSON"]).read_bytes()
        ).hexdigest(),
    },
}
(audit / "rest_chain_launch_audit.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print("AUDIT_JSON", audit / "rest_chain_launch_audit.json")
print("CHAIN_JOB_IDS", ",".join(job_ids))
PY

[[ "$(${GIT_BIN} -C "${ORIG_GA}" rev-parse HEAD)" = "${orig_ga_head}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_WF}" rev-parse HEAD)" = "${orig_wf_head}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_OPT}" rev-parse HEAD)" = "${orig_opt_head}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_GA}" status --porcelain)" = "${orig_ga_status}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_WF}" status --porcelain)" = "${orig_wf_status}" ]]
[[ "$(${GIT_BIN} -C "${ORIG_OPT}" status --porcelain)" = "${orig_opt_status}" ]]
[[ -z "$(${GIT_BIN} -C "${GA}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${WF}" status --porcelain)" ]]
[[ -z "$(${GIT_BIN} -C "${OPT}" status --porcelain)" ]]

SUBMISSION_PHASE="complete"
trap - EXIT

echo "=== Jobs enviados ==="
squeue \
    -j "${REST_JOB_ID},${TICK0_JOB_ID}" \
    -o '%.18i %.9P %.28j %.8u %.2t %.10M %.6D %R' \
    || true

echo "REST_ITER000_SUBMITTED=1"
echo "REST_JOB_ID=${REST_JOB_ID}"
echo "TICK0_JOB_ID=${TICK0_JOB_ID}"
echo "CHAIN_SUBMITTED=1"
echo "SUBMITTED_ITERATIONS=0-21"
echo "SOBOL_ITERATIONS=0-1"
echo "MORBO_ITERATIONS=2-21"
echo "OPTIONAL_MORBO_ITERATIONS_22-26_NOT_SUBMITTED=1"
echo "NO_ARRAY_THROTTLE=1"
echo "CLEANUP_AFTER_REQUIRED_OUTPUT_VALIDATION=1"
echo "MULTICHANNEL_BASELINE_PRESERVED=1"
echo "CHAIN_MANIFEST=${CHAIN_MANIFEST}"
echo "AUDIT=${AUDIT}"
