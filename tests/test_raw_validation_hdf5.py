from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

try:
    import h5py  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - exercised only when h5py is unavailable
    h5py = None

from campaign_workflow.cli.init_case_states import main as init_case_states_main
from campaign_workflow.cli.validate_raw_case import main as validate_raw_case_main
from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import now_utc


@unittest.skipIf(h5py is None, "h5py is not installed; skipping openpmd_hdf5 smoke tests")
class OpenPMDHDF5RawValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name) / "hdf5_campaign"
        self.root.mkdir(parents=True)
        self._write_hdf5_campaign()

        rc = init_case_states_main(
            [
                "--campaign-root",
                str(self.root),
                "--create-missing-case-dirs",
            ]
        )
        self.assertEqual(rc, 0)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_valid_h5_file_is_raw_validated(self) -> None:
        case_dir = self.root / "000_hdf5_case"
        self._mark_sim_done(case_dir)
        self._write_minimal_hdf5(case_dir / "diags/openpmd/raw_000.h5")

        rc = validate_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        manifest = read_json(case_dir / "manifests/raw_openpmd_raw.json")

        self.assertEqual(state["state"], "Raw_validated")
        self.assertTrue(validation["raw"]["openpmd_raw"]["ok"])
        self.assertEqual(validation["raw"]["openpmd_raw"]["diagnostic_kind"], "openpmd_hdf5")
        self.assertEqual(validation["raw"]["openpmd_raw"]["file_count"], 1)
        self.assertEqual(validation["raw"]["openpmd_raw"]["manifest_path"], "manifests/raw_openpmd_raw.json")
        self.assertFalse(validation["cleanup"]["cleanup_allowed"])
        self.assertEqual(manifest["diagnostic_kind"], "openpmd_hdf5")
        self.assertEqual(manifest["files"][0]["relative_path"], "diags/openpmd/raw_000.h5")
        self.assertEqual(manifest["destructive_operations"], 0)

    def test_valid_hdf5_suffix_is_raw_validated(self) -> None:
        case_dir = self.root / "000_hdf5_case"
        self._mark_sim_done(case_dir)
        self._write_minimal_hdf5(case_dir / "diags/openpmd/raw_000.hdf5")

        rc = validate_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 0)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")
        manifest = read_json(case_dir / "manifests/raw_openpmd_raw.json")

        self.assertEqual(state["state"], "Raw_validated")
        self.assertTrue(validation["raw"]["openpmd_raw"]["ok"])
        self.assertEqual(manifest["files"][0]["relative_path"], "diags/openpmd/raw_000.hdf5")

    def test_invalid_h5_payload_marks_validation_failed(self) -> None:
        case_dir = self.root / "000_hdf5_case"
        self._mark_sim_done(case_dir)
        self._write_raw(case_dir, "diags/openpmd/raw_000.h5", b"not an HDF5 file")

        rc = validate_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Validation_failed")
        self.assertFalse(validation["raw"]["openpmd_raw"]["ok"])
        self.assertIsNone(validation["raw"]["openpmd_raw"]["manifest_path"])
        self.assertFalse((case_dir / "manifests/raw_openpmd_raw.json").exists())

        joined_errors = "\n".join(validation["raw"]["openpmd_raw"]["errors"])
        self.assertIn("failed to open HDF5 file", joined_errors)

    def test_non_hdf5_suffix_is_rejected(self) -> None:
        case_dir = self.root / "000_hdf5_case"
        self._mark_sim_done(case_dir)
        self._write_raw(case_dir, "diags/openpmd/raw_000.txt", b"payload")

        rc = validate_raw_case_main(["--campaign-root", str(self.root), "--case-id", "0"])

        self.assertEqual(rc, 1)

        state = read_json(case_dir / "state.json")
        validation = read_json(case_dir / "validation.json")

        self.assertEqual(state["state"], "Validation_failed")
        self.assertFalse(validation["raw"]["openpmd_raw"]["ok"])
        self.assertIsNone(validation["raw"]["openpmd_raw"]["manifest_path"])
        self.assertFalse((case_dir / "manifests/raw_openpmd_raw.json").exists())

        joined_errors = "\n".join(validation["raw"]["openpmd_raw"]["errors"])
        self.assertIn("invalid suffix", joined_errors)
        self.assertIn("allowed=['.h5', '.hdf5']", joined_errors)

    def _write_hdf5_campaign(self) -> None:
        campaign = {
            "schema_version": 1,
            "campaign_name": "hdf5_campaign",
            "case_manifest": "cases.tsv",
            "case_manifest_format": "tsv",
            "case_id_column": "CASE_ID",
            "case_name_column": "CASE_NAME",
            "simulation": {
                "backend": "fake",
                "scheduler": "none",
                "input_script": "input.py",
                "completion_marker": "post/sim_done.json",
                "failure_marker": "post/sim_failed.json",
            },
            "raw_diagnostics": [
                {
                    "name": "openpmd_raw",
                    "kind": "openpmd_hdf5",
                    "path": "diags/openpmd",
                    "glob": "diags/openpmd/**/*",
                    "min_files": 1,
                    "min_age_seconds": 0,
                    "required": True,
                }
            ],
            "analysis": {
                "name": "fake_analysis",
                "kind": "fake",
                "adapter": "fake",
                "inputs": ["openpmd_raw"],
                "outputs": [
                    {
                        "name": "fake_metrics",
                        "kind": "csv",
                        "path": "post/fake_metrics.csv",
                        "min_rows": 1,
                        "required_columns": ["iteration"],
                        "required": True,
                    }
                ],
            },
            "cleanup": {
                "raw_delete_globs": ["diags/openpmd/**/*.h5", "diags/openpmd/**/*.hdf5"],
                "require_raw_validated": True,
                "require_reduced_validated": True,
                "require_delete_manifest": True,
                "allow_directory_delete": False,
            },
            "state": {
                "state_file": "state.json",
                "validation_file": "validation.json",
                "locks_dir": "locks",
                "manifests_dir": "manifests",
                "post_dir": "post",
                "logs_dir": "logs",
            },
        }

        (self.root / "campaign.json").write_text(json.dumps(campaign, indent=2) + "\n", encoding="utf-8")
        (self.root / "cases.tsv").write_text(
            "CASE_ID\tCASE_NAME\tKIND\n"
            "0\t000_hdf5_case\thdf5\n",
            encoding="utf-8",
        )

    def _mark_sim_done(self, case_dir: Path) -> None:
        state_path = case_dir / "state.json"
        state = read_json(state_path)

        previous = state["state"]
        timestamp = now_utc()

        state["state"] = "Sim_done"
        state["updated_at"] = timestamp
        state.setdefault("history", []).append(
            {
                "timestamp": timestamp,
                "from": previous,
                "to": "Sim_done",
                "operation": "test_mark_sim_done",
                "reason": "test fixture simulates completed simulation",
                "actor": {
                    "user": "test",
                    "pid": os.getpid(),
                    "hostname": "test",
                },
            }
        )

        write_json_atomic(state_path, state)

    def _write_raw(self, case_dir: Path, relative_path: str, payload: bytes) -> None:
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def _write_minimal_hdf5(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        assert h5py is not None
        with h5py.File(path, "w") as h5:
            h5.attrs["openPMD"] = "2.0.0"
            h5.create_group("data")


if __name__ == "__main__":
    unittest.main()