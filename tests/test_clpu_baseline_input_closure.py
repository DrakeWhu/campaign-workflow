from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sunrise" / "corrected_capillary"
WRAPPER = EXAMPLE / "baseline_input_template.py"
SHARED_BASE = EXAMPLE / "input_template.py"
EXPECTED_BASE_SHA256 = (
    "9dc068c3e43a2df2c92414e9d161618e752a83e1e6c67ef7b1ef97ccebcfdd3a"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_wrapper(path: Path, env: dict[str, str]):
    spec = importlib.util.spec_from_file_location(
        f"clpu_baseline_closure_{path.parent.name}_{path.stem}",
        path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, env, clear=True):
        spec.loader.exec_module(module)
    return module


class ClpuBaselineInputClosureTests(unittest.TestCase):
    def base_env(self) -> dict[str, str]:
        return {
            "CAP_REQUIRE_CONVENTION_ACK": "true",
            "CAP_INPUT_CONVENTIONS_ACK": (
                "clpu_document_spot_values_are_picmi_w0_"
                "and_30fs_intensity_fwhm_v2"
            ),
            "CAP_LASER_SPOT_DEFINITION": "picmi_waist_w0_1e2_intensity",
            "CAP_NITROGEN_DOPANT_FRACTION": "0",
            "CAP_LASER_CASE": "f32",
            "CAP_N0_CM3": "4e18",
            "CAP_RADIUS_M": "150e-6",
            "CAP_RMAX_M": "180e-6",
            "CAP_NR": "192",
            "CAP_PLATEAU_LENGTH_M": "5e-3",
            "CAP_LONG_PROFILE": "both",
            "CAP_RAMP_LENGTH_M": "5e-3",
            "CAP_LASER_INTENSITY_FWHM_S": "30e-15",
        }

    def copy_materialized_wrapper(self, root: Path) -> Path:
        case_dir = root / "case"
        case_dir.mkdir(parents=True)
        case_input = case_dir / "input.py"
        shutil.copyfile(WRAPPER, case_input)
        return case_input

    def make_workflow_root(self, root: Path, *, valid: bool) -> Path:
        workflow_root = root / "workflow"
        base_path = (
            workflow_root
            / "examples"
            / "sunrise"
            / "corrected_capillary"
            / "input_template.py"
        )
        base_path.parent.mkdir(parents=True)
        if valid:
            shutil.copyfile(SHARED_BASE, base_path)
        else:
            base_path.write_text("# wrong corrected base\n", encoding="utf-8")
        return workflow_root

    def test_repository_shared_base_matches_pinned_sha256(self) -> None:
        self.assertEqual(sha256_file(SHARED_BASE), EXPECTED_BASE_SHA256)

    def test_materialized_wrapper_resolves_verified_workflow_root_and_records_closure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            case_input = self.copy_materialized_wrapper(root)
            workflow_root = self.make_workflow_root(root, valid=True)
            env = {**self.base_env(), "WFLOW_SRC": str(workflow_root)}

            module = load_wrapper(case_input, env)
            resolved = module.resolve_parameters(env)

            closure = resolved["input_closure"]
            expected_base = (
                workflow_root
                / "examples"
                / "sunrise"
                / "corrected_capillary"
                / "input_template.py"
            ).resolve()
            self.assertEqual(closure["schema_version"], 1)
            self.assertEqual(closure["base_resolution_mode"], "workflow_root")
            self.assertEqual(closure["base_path"], str(expected_base))
            self.assertEqual(closure["base_sha256"], EXPECTED_BASE_SHA256)
            self.assertEqual(
                closure["expected_base_sha256"], EXPECTED_BASE_SHA256
            )
            self.assertEqual(
                closure["wrapper_sha256"], sha256_file(case_input)
            )

    def test_missing_workflow_base_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            case_input = self.copy_materialized_wrapper(root)
            workflow_root = root / "missing_workflow"
            env = {**self.base_env(), "WFLOW_SRC": str(workflow_root)}

            with self.assertRaisesRegex(
                ImportError,
                "input template is missing for workflow_root",
            ):
                load_wrapper(case_input, env)

    def test_wrong_workflow_base_hash_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            case_input = self.copy_materialized_wrapper(root)
            workflow_root = self.make_workflow_root(root, valid=False)
            env = {**self.base_env(), "WFLOW_SRC": str(workflow_root)}

            with self.assertRaisesRegex(
                ImportError,
                "SHA256 mismatch for workflow_root",
            ):
                load_wrapper(case_input, env)

    def test_wrong_sibling_blocks_fallback_to_valid_workflow_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            case_input = self.copy_materialized_wrapper(root)
            sibling = case_input.with_name("input_template.py")
            sibling.write_text("# stale sibling base\n", encoding="utf-8")
            workflow_root = self.make_workflow_root(root, valid=True)
            env = {**self.base_env(), "WFLOW_SRC": str(workflow_root)}

            with self.assertRaisesRegex(
                ImportError,
                "SHA256 mismatch for sibling",
            ):
                load_wrapper(case_input, env)

    def test_missing_explicit_override_blocks_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            case_input = self.copy_materialized_wrapper(root)
            workflow_root = self.make_workflow_root(root, valid=True)
            env = {
                **self.base_env(),
                "CAP_CORRECTED_INPUT_TEMPLATE": str(root / "missing.py"),
                "WFLOW_SRC": str(workflow_root),
            }

            with self.assertRaisesRegex(
                ImportError,
                "input template is missing for explicit_override",
            ):
                load_wrapper(case_input, env)

    def test_wrong_explicit_override_blocks_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            case_input = self.copy_materialized_wrapper(root)
            wrong = root / "wrong.py"
            wrong.write_text("# wrong explicit base\n", encoding="utf-8")
            workflow_root = self.make_workflow_root(root, valid=True)
            env = {
                **self.base_env(),
                "CAP_CORRECTED_INPUT_TEMPLATE": str(wrong),
                "WFLOW_SRC": str(workflow_root),
            }

            with self.assertRaisesRegex(
                ImportError,
                "SHA256 mismatch for explicit_override",
            ):
                load_wrapper(case_input, env)

    def test_valid_explicit_override_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            case_input = self.copy_materialized_wrapper(root)
            explicit = root / "verified_base.py"
            shutil.copyfile(SHARED_BASE, explicit)
            env = {
                **self.base_env(),
                "CAP_CORRECTED_INPUT_TEMPLATE": str(explicit),
            }

            module = load_wrapper(case_input, env)
            resolved = module.resolve_parameters(env)

            closure = resolved["input_closure"]
            self.assertEqual(
                closure["base_resolution_mode"], "explicit_override"
            )
            self.assertEqual(closure["base_path"], str(explicit.resolve()))
            self.assertEqual(closure["base_sha256"], EXPECTED_BASE_SHA256)


if __name__ == "__main__":
    unittest.main()
