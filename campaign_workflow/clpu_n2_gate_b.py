from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


GATE_B_CONTRACT_ID = "clpu_n2_materialized_picmi_gate_b_v1"
EXPECTED_GUIDING_COMMIT = "e809b43d2071e5fa5cb39de2613f3e9d170bea84"
EXPECTED_OPTIMIZER_COMMIT = "73dab76305f547581c57b70a900706374c929141"
EXPECTED_BASE_SHA256 = "9dc068c3e43a2df2c92414e9d161618e752a83e1e6c67ef7b1ef97ccebcfdd3a"
EXPECTED_PHYSICS_MODEL = (
    "clpu_carlos_plateau_quasiparabolic_h_n5_adk_uniform_"
    "soft50_dual_exit_picmi_w0_v1"
)
EXPECTED_PARTICLE_POLICY = "dual_plateau_capillary_exit_exact_step_unfiltered_v1"
EXPECTED_NITROGEN_SEMANTICS = (
    "fraction_of_atomic_nuclei_nitrogen_equal_H2_N2_molecular_fraction"
)
EXPECTED_PROFILE_ID = "uniform_nitrogen_fraction_v1"
EXPECTED_WAISTS_M = {"f20": 26.0e-6, "f32": 42.0e-6, "f40": 52.0e-6}
EXPECTED_SCOPES = [
    "all_electrons",
    "preionized_background_electrons",
    "nitrogen_ionized_electrons",
]


class GateBError(RuntimeError):
    """Raised when the materialized CLPU N2 preflight contract is violated."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise GateBError(f"missing JSON artifact: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GateBError(f"could not parse JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GateBError(f"JSON root must be an object: {path}")
    return data


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise GateBError(
            f"git {' '.join(args)} failed for {root}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def git_snapshot(root: Path, *, expected_head: str | None = None) -> dict[str, Any]:
    resolved = root.expanduser().resolve(strict=True)
    head = _git(resolved, "rev-parse", "HEAD")
    dirty = _git(resolved, "status", "--porcelain")
    if dirty:
        raise GateBError(f"repository is dirty: {resolved}\n{dirty}")
    if expected_head is not None and head != expected_head:
        raise GateBError(
            f"unexpected repository HEAD for {resolved}: expected={expected_head} got={head}"
        )
    return {"root": str(resolved), "head": head, "dirty": False}


def _asset(path: Path) -> dict[str, str]:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise GateBError(f"asset is not a regular file: {resolved}")
    return {"path": str(resolved), "sha256": sha256_file(resolved)}


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise GateBError(f"missing TSV artifact: {path}")
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if not rows:
        raise GateBError(f"TSV contains no data rows: {path}")
    return [{str(k): "" if v is None else str(v) for k, v in row.items()} for row in rows]


def parse_case_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise GateBError(f"missing case.env: {path}")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith("export "):
            raise GateBError(f"unexpected case.env line in {path}: {raw!r}")
        tokens = shlex.split(line[len("export ") :], posix=True)
        if len(tokens) != 1 or "=" not in tokens[0]:
            raise GateBError(f"could not parse case.env line in {path}: {raw!r}")
        name, value = tokens[0].split("=", 1)
        if name in values:
            raise GateBError(f"duplicate environment variable {name} in {path}")
        values[name] = value
    return values


def _float(value: Any, *, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise GateBError(f"{label} is not numeric: {value!r}") from exc
    if not math.isfinite(result):
        raise GateBError(f"{label} is not finite: {value!r}")
    return result


def _close(actual: Any, expected: Any, *, label: str, rel: float = 1e-12, abs_: float = 1e-15) -> None:
    a = _float(actual, label=label)
    e = _float(expected, label=label)
    if not math.isclose(a, e, rel_tol=rel, abs_tol=abs_):
        raise GateBError(f"{label} mismatch: expected={e:.17g} got={a:.17g}")


def _assignment_float(text: str, key: str) -> float:
    prefix = key + " ="
    matches = [line.strip() for line in text.splitlines() if line.strip().startswith(prefix)]
    if len(matches) != 1:
        raise GateBError(f"serialized input must contain exactly one {key!r} assignment")
    raw = matches[0].split("=", 1)[1].strip().strip('"')
    return _float(raw, label=f"serialized {key}")


def _validate_materialization(
    *,
    case_dir: Path,
    campaign_root: Path,
    env_path: Path,
    input_path: Path,
) -> dict[str, Any]:
    manifest_path = case_dir / "manifests" / "input_materialization.json"
    manifest = _read_json(manifest_path)
    if manifest.get("operation") != "materialize_cases":
        raise GateBError(f"unexpected materialization operation: {manifest_path}")
    source = manifest.get("source_input_template", {})
    materialized = manifest.get("materialized_input", {})
    case_env = manifest.get("case_env", {})
    expected_template = (campaign_root / "input_template.py").resolve(strict=True)
    if Path(str(source.get("path", ""))).resolve(strict=False) != expected_template:
        raise GateBError(f"materialization source template mismatch: {case_dir}")
    if source.get("sha256") != sha256_file(expected_template):
        raise GateBError(f"materialization source template hash mismatch: {case_dir}")
    if materialized.get("sha256") != sha256_file(input_path):
        raise GateBError(f"materialized input hash mismatch: {case_dir}")
    if source.get("sha256") != materialized.get("sha256"):
        raise GateBError(f"materialized input differs from campaign template: {case_dir}")
    if case_env.get("sha256") != sha256_file(env_path):
        raise GateBError(f"case.env hash mismatch: {case_dir}")
    return {
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "input_sha256": sha256_file(input_path),
        "env_sha256": sha256_file(env_path),
    }


def validate_materialized_case(
    *,
    row: dict[str, str],
    campaign_root: Path,
    workflow_root: Path,
) -> dict[str, Any]:
    case_id = int(row["CASE_ID"])
    case_name = row["CASE_NAME"]
    case_dir = campaign_root / case_name
    input_path = case_dir / "input.py"
    env_path = case_dir / "case.env"
    resolved_path = case_dir / "resolved_parameters.json"
    serialized_path = case_dir / f"inputs_capillary_{case_name}"
    for path in (case_dir, input_path, env_path, resolved_path, serialized_path):
        if not path.exists():
            raise GateBError(f"missing Gate B case artifact: {path}")

    state = _read_json(case_dir / "state.json")
    if state.get("state") != "Created":
        raise GateBError(f"case {case_name} is not in Created state: {state.get('state')!r}")
    if (case_dir / "post" / "sim_submitted.json").exists():
        raise GateBError(f"case {case_name} already has sim_submitted receipt")
    if list(case_dir.rglob("*.h5")) or list(case_dir.rglob("*.hdf5")):
        raise GateBError(f"case {case_name} already contains HDF5 diagnostics")

    materialization = _validate_materialization(
        case_dir=case_dir,
        campaign_root=campaign_root,
        env_path=env_path,
        input_path=input_path,
    )
    env = parse_case_env(env_path)
    required_env = {
        "CAP_CASE_ID": str(case_id),
        "CAP_CASE_NAME": case_name,
        "CAP_LASER_CASE": row["LASER_CASE"],
        "CAP_PLASMA_KIND": "chan",
        "CAP_LONG_PROFILE": "both",
        "CAP_REQUIRE_CONVENTION_ACK": "true",
        "CAP_INPUT_CONVENTIONS_ACK": "clpu_document_spot_values_are_picmi_w0_and_30fs_intensity_fwhm_v2",
        "CAP_LASER_SPOT_DEFINITION": "picmi_waist_w0_1e2_intensity",
        "CAP_MAX_NITROGEN_DOPANT_FRACTION": "0.01",
    }
    for key, expected in required_env.items():
        if env.get(key) != expected:
            raise GateBError(f"{case_name}: {key} expected={expected!r} got={env.get(key)!r}")
    for forbidden in ("CAP_PARTICLE_DIAG_MIN_ENERGY_MEV", "CAP_PARTICLE_DIAG_FORWARD_ONLY"):
        if forbidden in env:
            raise GateBError(f"{case_name}: forbidden historical diagnostic override {forbidden}")

    _close(env.get("CAP_N0_CM3"), row["N0_CM3"], label=f"{case_name} n0")
    _close(env.get("CAP_PLATEAU_LENGTH_M"), float(row["PLATEAU_LENGTH_MM"]) * 1e-3, label=f"{case_name} plateau")
    _close(env.get("CAP_RADIUS_M"), float(row["RADIUS_UM"]) * 1e-6, label=f"{case_name} radius")
    _close(env.get("CAP_FOCUS_OFFSET_FROM_PLATEAU_START_MM"), row["FOCUS_OFFSET_FROM_PLATEAU_START_MM"], label=f"{case_name} focus")
    _close(env.get("CAP_NITROGEN_DOPANT_FRACTION"), row["NITROGEN_DOPANT_FRACTION"], label=f"{case_name} nitrogen")
    _close(env.get("CAP_PULSE_RESONANCE_FACTOR"), row["PULSE_RESONANCE_FACTOR"], label=f"{case_name} resonance", rel=1e-10)
    _close(env.get("CAP_LASER_INTENSITY_FWHM_S"), float(row["LASER_DURATION_FWHM_FS"]) * 1e-15, label=f"{case_name} duration")
    _close(env.get("CAP_RMAX_M"), float(row["CAP_RMAX_UM"]) * 1e-6, label=f"{case_name} rmax")
    if int(float(env.get("CAP_NR", "nan"))) != int(float(row["CAP_NR"])):
        raise GateBError(f"{case_name}: CAP_NR mismatch")

    resolved = _read_json(resolved_path)
    if resolved.get("schema_version") != 6 or resolved.get("physics_model_id") != EXPECTED_PHYSICS_MODEL:
        raise GateBError(f"{case_name}: wrong nitrogen resolved schema/model")
    if resolved.get("nitrogen_fraction_semantics") != EXPECTED_NITROGEN_SEMANTICS:
        raise GateBError(f"{case_name}: nitrogen fraction semantics mismatch")
    profile = resolved.get("nitrogen_profile", {})
    if profile.get("profile_id") != EXPECTED_PROFILE_ID or profile.get("longitudinal_shape") != "uniform":
        raise GateBError(f"{case_name}: nitrogen profile metadata mismatch")

    fraction = _float(row["NITROGEN_DOPANT_FRACTION"], label="nitrogen_fraction")
    _close(resolved.get("nitrogen_fraction_atomic_nuclei"), fraction, label=f"{case_name} resolved nitrogen")
    expected_ionization = "ADK" if fraction > 0.0 else "disabled_zero_fraction"
    if resolved.get("ionization_model") != expected_ionization or resolved.get("nitrogen_initial_charge_state") != 5:
        raise GateBError(f"{case_name}: N5+/ADK resolved contract mismatch")
    mixture = resolved.get("mixture_density_factors_relative_to_initial_electron_density", {})
    nuclei = 1.0 / (1.0 + 4.0 * fraction)
    _close(mixture.get("background_electron"), 1.0, label=f"{case_name} background factor")
    _close(mixture.get("hydrogen_ion"), (1.0 - fraction) * nuclei, label=f"{case_name} H factor")
    _close(mixture.get("nitrogen_ion"), fraction * nuclei, label=f"{case_name} N factor")
    _close(mixture.get("initial_charge_balance"), 1.0, label=f"{case_name} charge balance")

    laser_case = row["LASER_CASE"].strip().lower()
    if laser_case not in EXPECTED_WAISTS_M:
        raise GateBError(f"{case_name}: unsupported laser case {laser_case!r}")
    expected_waist = EXPECTED_WAISTS_M[laser_case]
    _close(resolved.get("laser_waist_radius_m"), expected_waist, label=f"{case_name} w0")
    _close(resolved.get("laser_intensity_fwhm_s"), 30.0e-15, label=f"{case_name} 30fs")
    _close(
        resolved.get("laser_picmi_duration_s"),
        30.0e-15 / math.sqrt(2.0 * math.log(2.0)),
        label=f"{case_name} PICMI duration",
    )
    _close(resolved.get("laser_pulse_resonance_factor"), row["PULSE_RESONANCE_FACTOR"], label=f"{case_name} resolved resonance", rel=1e-10)

    grid = resolved.get("grid", {})
    if int(grid.get("nr", -1)) != int(float(row["CAP_NR"])):
        raise GateBError(f"{case_name}: resolved nr mismatch")
    _close(grid.get("rmax_m"), float(row["CAP_RMAX_UM"]) * 1e-6, label=f"{case_name} resolved rmax")
    if int(grid.get("nz", -1)) != 1536 or int(grid.get("n_azimuthal_modes", -1)) != 2:
        raise GateBError(f"{case_name}: unexpected nz/mode count")
    _close(resolved.get("cfl"), 1.0, label=f"{case_name} CFL")
    if resolved.get("time_step_model") != "WarpX_CylindricalYeeAlgorithm_ComputeMaxDt":
        raise GateBError(f"{case_name}: unexpected timestep model")
    if _float(resolved.get("time_step_s"), label="time_step_s") <= 0.0:
        raise GateBError(f"{case_name}: non-positive timestep")

    if resolved.get("particle_diagnostic_policy") != EXPECTED_PARTICLE_POLICY:
        raise GateBError(f"{case_name}: wrong particle diagnostic policy")
    if resolved.get("particle_diagnostic_min_energy_MeV") != 0.0:
        raise GateBError(f"{case_name}: particle diagnostic is energy filtered")
    if resolved.get("particle_diagnostic_forward_only") is not False:
        raise GateBError(f"{case_name}: particle diagnostic is forward-only")
    if resolved.get("particle_diagnostic_filter_expression") is not None:
        raise GateBError(f"{case_name}: particle diagnostic has a filter expression")
    if resolved.get("particle_diagnostic_dump_last_timestep") is not False:
        raise GateBError(f"{case_name}: particle diagnostic dumps final timestep")
    targets = resolved.get("particle_diagnostic_targets", {})
    if set(targets) != {"plateau_exit", "capillary_exit"}:
        raise GateBError(f"{case_name}: exact dual target set mismatch")
    target_iterations = sorted(int(targets[key]["iteration"]) for key in ("plateau_exit", "capillary_exit"))
    expected_intervals = ",".join(f"{it}:{it}" for it in target_iterations)
    if resolved.get("particle_diagnostic_intervals") != expected_intervals:
        raise GateBError(f"{case_name}: exact particle intervals mismatch")
    if any(it <= 0 or it > int(resolved["max_steps"]) for it in target_iterations):
        raise GateBError(f"{case_name}: exit target is outside simulated range")

    closure = resolved.get("input_closure", {})
    expected_base = (workflow_root / "examples/sunrise/corrected_capillary/input_template.py").resolve(strict=True)
    if Path(str(closure.get("wrapper_path", ""))).resolve(strict=False) != input_path.resolve(strict=True):
        raise GateBError(f"{case_name}: wrapper closure path mismatch")
    if closure.get("wrapper_sha256") != sha256_file(input_path):
        raise GateBError(f"{case_name}: wrapper closure hash mismatch")
    if Path(str(closure.get("base_path", ""))).resolve(strict=False) != expected_base:
        raise GateBError(f"{case_name}: base closure path mismatch")
    if closure.get("base_resolution_mode") != "explicit_override":
        raise GateBError(f"{case_name}: Gate B requires explicit base closure")
    if closure.get("base_sha256") != EXPECTED_BASE_SHA256 or sha256_file(expected_base) != EXPECTED_BASE_SHA256:
        raise GateBError(f"{case_name}: corrected base hash mismatch")

    serialized = serialized_path.read_text(encoding="utf-8")
    if f'plasma_electrons.intervals = "{expected_intervals}"' not in serialized:
        raise GateBError(f"{case_name}: serialized exact particle intervals missing")
    if "plasma_electrons.dump_last_timestep = 0" not in serialized or "plasma_electrons.dump_last_timestep = 1" in serialized:
        raise GateBError(f"{case_name}: serialized dump-last contract mismatch")
    if "plot_filter_function" in serialized:
        raise GateBError(f"{case_name}: serialized particle filter unexpectedly present")
    for species in ("preionized_background_electrons", "nitrogen_ionized_electrons"):
        if species not in serialized:
            raise GateBError(f"{case_name}: serialized electron species missing: {species}")
    _close(_assignment_float(serialized, "laser1.profile_waist"), expected_waist, label=f"{case_name} serialized w0")
    _close(
        _assignment_float(serialized, "laser1.profile_duration"),
        resolved["laser_picmi_duration_s"],
        label=f"{case_name} serialized duration",
    )
    _close(_assignment_float(serialized, "warpx.cfl"), resolved["cfl"], label=f"{case_name} serialized CFL")
    if fraction > 0.0:
        for token in (
            "nitrogen_ions.do_field_ionization = 1",
            "nitrogen_ions.ionization_initial_level = 5",
            'nitrogen_ions.ionization_product_species = "nitrogen_ionized_electrons"',
            'nitrogen_ions.physical_element = "N"',
        ):
            if token not in serialized:
                raise GateBError(f"{case_name}: serialized ADK token missing: {token}")
    elif "nitrogen_ions.do_field_ionization" in serialized:
        raise GateBError(f"{case_name}: fN=0 unexpectedly enables nitrogen ionization")

    return {
        "case_id": case_id,
        "case_name": case_name,
        "nitrogen_fraction": fraction,
        "laser_case": laser_case,
        "waist_m": expected_waist,
        "duration_intensity_fwhm_fs": 30.0,
        "picmi_duration_s": resolved["laser_picmi_duration_s"],
        "time_step_s": resolved["time_step_s"],
        "particle_iterations": target_iterations,
        "particle_intervals": expected_intervals,
        "ionization_model": expected_ionization,
        "materialization": materialization,
        "resolved_sha256": sha256_file(resolved_path),
        "serialized_sha256": sha256_file(serialized_path),
    }


def _runtime_info() -> dict[str, Any]:
    try:
        import pywarpx  # type: ignore
    except Exception as exc:
        raise GateBError(f"pywarpx is unavailable in Gate B runtime: {exc}") from exc
    try:
        distribution_version = importlib.metadata.version("warpx")
    except importlib.metadata.PackageNotFoundError:
        distribution_version = None
    return {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "pywarpx_file": str(Path(pywarpx.__file__).resolve()),
        "warpx_distribution_version": distribution_version,
    }


def build_gate_b_receipt(
    *,
    optimization_root: Path,
    campaign_root: Path,
    workflow_root: Path,
    guiding_analysis_root: Path,
    optimizer_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    optimization_root = optimization_root.expanduser().resolve(strict=True)
    campaign_root = campaign_root.expanduser().resolve(strict=True)
    workflow_root = workflow_root.expanduser().resolve(strict=True)
    guiding_analysis_root = guiding_analysis_root.expanduser().resolve(strict=True)
    optimizer_root = optimizer_root.expanduser().resolve(strict=True)

    gate_a_path = optimization_root / "optimizer_runs/iter_000/reports/clpu_n2_gate_a.json"
    gate_a = _read_json(gate_a_path)
    if gate_a.get("status") != "pass" or gate_a.get("observation_rows") != 344 or gate_a.get("candidate_count") != 8:
        raise GateBError("Gate A receipt is not the certified 344 -> 8 pass result")

    candidate_batch = optimization_root / "optimizer_runs/iter_000/outputs/candidate_batch.tsv"
    rows = _read_tsv(candidate_batch)
    if len(rows) != 8 or [int(row["CASE_ID"]) for row in rows] != list(range(8)):
        raise GateBError("Gate B requires the exact eight Gate A candidate rows with CASE_ID 0..7")
    if any(not math.isclose(float(row["LASER_DURATION_FWHM_FS"]), 30.0, rel_tol=0.0, abs_tol=1e-12) for row in rows):
        raise GateBError("Gate B candidate batch is not fixed at 30 fs intensity FWHM")

    workflow_repo = git_snapshot(workflow_root)
    guiding_repo = git_snapshot(guiding_analysis_root, expected_head=EXPECTED_GUIDING_COMMIT)
    optimizer_repo = git_snapshot(optimizer_root, expected_head=EXPECTED_OPTIMIZER_COMMIT)

    workflow_assets_rel = [
        "campaign_workflow/clpu_n2_gate_b.py",
        "examples/sunrise/run_warpx_case_sunrise.sh",
        "examples/sunrise/corrected_capillary/input_template.py",
        "examples/sunrise/corrected_capillary/nitrogen_input_template.py",
        "examples/sunrise/corrected_capillary/campaign_nitrogen_uniform_soft50.json",
        "examples/sunrise/corrected_capillary/run_nitrogen_warpx_case_sunrise.sh",
        "examples/sunrise/corrected_capillary/run_nitrogen_case_analysis_sunrise.sh",
        "examples/sunrise/corrected_capillary/validate_nitrogen_particle_outputs.py",
        "examples/sunrise/corrected_capillary/validate_nitrogen_gate_b.py",
        "examples/sunrise/corrected_capillary/run_nitrogen_gate_b_sunrise.sh",
    ]
    assets = {rel: _asset(workflow_root / rel) for rel in workflow_assets_rel}
    assets.update(
        {
            "materialized/campaign.json": _asset(campaign_root / "campaign.json"),
            "materialized/input_template.py": _asset(campaign_root / "input_template.py"),
            "materialized/cases.tsv": _asset(campaign_root / "cases.tsv"),
            "gate_a": _asset(gate_a_path),
            "candidate_batch": _asset(candidate_batch),
            "batch_campaign_plan": _asset(optimization_root / "optimizer_runs/iter_000/outputs/batch_campaign_plan.json"),
        }
    )
    source_campaign = workflow_root / "examples/sunrise/corrected_capillary/campaign_nitrogen_uniform_soft50.json"
    source_wrapper = workflow_root / "examples/sunrise/corrected_capillary/nitrogen_input_template.py"
    if sha256_file(campaign_root / "campaign.json") != sha256_file(source_campaign):
        raise GateBError("materialized campaign.json differs from reviewed nitrogen campaign contract")
    if sha256_file(campaign_root / "input_template.py") != sha256_file(source_wrapper):
        raise GateBError("materialized input_template.py differs from reviewed nitrogen wrapper")

    case_results = [
        validate_materialized_case(
            row=row,
            campaign_root=campaign_root,
            workflow_root=workflow_root,
        )
        for row in rows
    ]
    if list(campaign_root.rglob("*.h5")) or list(campaign_root.rglob("*.hdf5")):
        raise GateBError("Gate B campaign root contains HDF5 diagnostics")
    if list(campaign_root.rglob("post/sim_submitted.json")):
        raise GateBError("Gate B campaign root contains sim_submitted receipts")

    payload = {
        "schema_version": 1,
        "contract_id": GATE_B_CONTRACT_ID,
        "status": "pass",
        "optimization_root": str(optimization_root),
        "campaign_root": str(campaign_root),
        "iteration": 0,
        "gate_a": {"path": str(gate_a_path), "sha256": sha256_file(gate_a_path)},
        "candidate_count": len(rows),
        "case_ids": [int(row["CASE_ID"]) for row in rows],
        "all_cases_created": True,
        "hdf5_file_count": 0,
        "submitted_case_count": 0,
        "repos": {
            "campaign_workflow": workflow_repo,
            "guiding_analysis_module": guiding_repo,
            "campaign_optimizer": optimizer_repo,
        },
        "runtime": _runtime_info(),
        "assets": assets,
        "cases": case_results,
        "particle_diagnostic_policy": EXPECTED_PARTICLE_POLICY,
        "cleanup_execute": False,
        "note": (
            "Gate B proves materialized PICMI construction/serialization only. "
            "It does not prove MPI evolution or realized ADK ionization and does not authorize sbatch by itself."
        ),
    }
    _write_json_atomic(output_path, payload)
    return payload


def verify_runtime_assets(
    *,
    receipt_path: Path,
    optimization_root: Path,
    workflow_root: Path,
    guiding_analysis_root: Path,
    optimizer_root: Path,
) -> dict[str, Any]:
    receipt = _read_json(receipt_path)
    if receipt.get("contract_id") != GATE_B_CONTRACT_ID or receipt.get("status") != "pass":
        raise GateBError("runtime asset verification requires a passing Gate B receipt")
    expected_opt = Path(str(receipt.get("optimization_root", ""))).resolve(strict=False)
    actual_opt = optimization_root.expanduser().resolve(strict=True)
    if expected_opt != actual_opt:
        raise GateBError("Gate B receipt belongs to a different optimization root")

    current_repos = {
        "campaign_workflow": git_snapshot(workflow_root.expanduser().resolve(strict=True)),
        "guiding_analysis_module": git_snapshot(guiding_analysis_root.expanduser().resolve(strict=True), expected_head=EXPECTED_GUIDING_COMMIT),
        "campaign_optimizer": git_snapshot(optimizer_root.expanduser().resolve(strict=True), expected_head=EXPECTED_OPTIMIZER_COMMIT),
    }
    recorded_repos = receipt.get("repos", {})
    for name, current in current_repos.items():
        recorded = recorded_repos.get(name, {})
        if current.get("root") != recorded.get("root") or current.get("head") != recorded.get("head"):
            raise GateBError(f"Gate B repository drift detected for {name}")

    checked_assets = 0
    for label, recorded in receipt.get("assets", {}).items():
        if not isinstance(recorded, dict):
            raise GateBError(f"invalid Gate B asset record: {label}")
        path = Path(str(recorded.get("path", "")))
        if not path.is_file():
            raise GateBError(f"Gate B asset disappeared: {label}: {path}")
        if sha256_file(path) != recorded.get("sha256"):
            raise GateBError(f"Gate B asset hash drift detected: {label}: {path}")
        checked_assets += 1

    return {
        "status": "pass",
        "contract_id": GATE_B_CONTRACT_ID,
        "receipt": str(receipt_path.resolve()),
        "checked_assets": checked_assets,
        "repo_heads": {name: data["head"] for name, data in current_repos.items()},
    }
