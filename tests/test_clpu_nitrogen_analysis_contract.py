from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


RUNNER = Path(
    "examples/sunrise/corrected_capillary/run_nitrogen_case_analysis_sunrise.sh"
)
VALIDATOR = Path(
    "examples/sunrise/corrected_capillary/validate_nitrogen_particle_outputs.py"
)
EXPECTED_SCOPES = [
    "all_electrons",
    "preionized_background_electrons",
    "nitrogen_ionized_electrons",
]
REQUIRED_GUIDING_COMMIT = "e809b43d2071e5fa5cb39de2613f3e9d170bea84"


def load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_nitrogen_particle_outputs",
        VALIDATOR,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {VALIDATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClpuNitrogenAnalysisContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = load_validator()

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.particle_root = self.root / "particle_analysis"
        self.resolved = self.root / "resolved_parameters.json"
        self._write_resolved()
        self._write_exit("plateau_exit", 60973, ionized_count=0)
        self._write_exit("capillary_exit", 91459, ionized_count=7)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _write_resolved(self) -> None:
        self.resolved.write_text(
            json.dumps(
                {
                    "particle_diagnostic_policy": (
                        "dual_plateau_capillary_exit_exact_step_unfiltered_v1"
                    ),
                    "particle_diagnostic_dump_last_timestep": False,
                    "particle_diagnostic_min_energy_MeV": 0.0,
                    "particle_diagnostic_forward_only": False,
                    "particle_diagnostic_filter_expression": None,
                    "particle_diagnostic_targets": {
                        "plateau_exit": {
                            "target_distance_m": 10.0e-3,
                            "iteration": 60973,
                            "dump_distance_m": 10.00003e-3,
                            "distance_error_m": 0.00003e-3,
                        },
                        "capillary_exit": {
                            "target_distance_m": 15.0e-3,
                            "iteration": 91459,
                            "dump_distance_m": 15.00002e-3,
                            "distance_error_m": 0.00002e-3,
                        },
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def _write_exit(
        self,
        exit_name: str,
        iteration: int,
        *,
        ionized_count: int,
    ) -> None:
        outdir = self.particle_root / exit_name
        target_key = exit_name
        summary_rows = []
        counts = {
            "all_electrons": 10 + ionized_count,
            "preionized_background_electrons": 10,
            "nitrogen_ionized_electrons": ionized_count,
        }
        for scope in EXPECTED_SCOPES:
            summary_rows.append(
                {
                    "species_scope": scope,
                    "selection_mode": "exit",
                    "particle_exit_selection_policy": "exact_resolved_v1",
                    "resolved_particle_target_key": target_key,
                    "target_particle_iteration": iteration,
                    "selected_particle_iteration": iteration,
                    "target_iteration_delta": 0,
                    "time_fs": 123.456,
                    "n_macroparticles_total": counts[scope],
                }
            )
        self._write_csv(outdir / "particle_summary.csv", summary_rows)

        soft50_rows = []
        for scope in EXPECTED_SCOPES:
            for low in (5.0, 10.0):
                soft50_rows.append(
                    {
                        "species_scope": scope,
                        "selection_mode": "exit",
                        "selected_particle_iteration": iteration,
                        "soft50_energy_low_MeV": low,
                        "charge_soft50_pC": 0.0,
                    }
                )
        self._write_csv(outdir / "particle_soft50_curves.csv", soft50_rows)

        acceptance_rows = []
        for scope in EXPECTED_SCOPES:
            for theta in (5.0, 10.0):
                for energy in (50.0, 100.0):
                    acceptance_rows.append(
                        {
                            "species_scope": scope,
                            "selection_mode": "exit",
                            "selected_particle_iteration": iteration,
                            "theta_cut_mrad": theta,
                            "E_min_MeV": energy,
                            "accepted_charge_pC": 0.0,
                        }
                    )
        self._write_csv(
            outdir / "particle_acceptance_curves.csv",
            acceptance_rows,
        )

        plots = outdir / "plots"
        plots.mkdir(parents=True, exist_ok=True)
        for scope in EXPECTED_SCOPES:
            token = "" if scope == "all_electrons" else f"{scope}_"
            for prefix in self.validator.PLOT_PREFIXES:
                (plots / f"{prefix}{token}it{iteration:08d}.png").write_bytes(b"png")

    def _summary_rows(self, exit_name: str) -> tuple[Path, list[dict[str, str]]]:
        path = self.particle_root / exit_name / "particle_summary.csv"
        with path.open(newline="", encoding="utf-8") as stream:
            return path, list(csv.DictReader(stream))

    def test_runner_declares_exact_multispecies_guiding_capability(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn(REQUIRED_GUIDING_COMMIT, source)
        self.assertIn("merge-base --is-ancestor", source)
        self.assertIn(
            'PARTICLE_SPECIES="preionized_background_electrons,nitrogen_ionized_electrons"',
            source,
        )
        self.assertIn('run_exit_analysis plateau "${PLATEAU_OUT}"', source)
        self.assertIn('run_exit_analysis capillary "${CAPILLARY_OUT}"', source)
        self.assertIn('--resolved-parameters "${RESOLVED}"', source)
        self.assertNotIn("--target-propagation-mm", source)
        self.assertNotIn("maximum-target-iteration-delta", source)
        self.assertIn("validate_nitrogen_particle_outputs.py", source)

    def test_valid_dual_exit_contract_accepts_empty_ionized_population(self) -> None:
        result = self.validator.validate_analysis(
            particle_analysis_root=self.particle_root,
            resolved_parameters_path=self.resolved,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["selection_mode"], "exit")
        self.assertEqual(
            result["particle_exit_selection_policy"],
            "exact_resolved_v1",
        )
        self.assertEqual(result["species_scope_order"], EXPECTED_SCOPES)
        self.assertEqual(
            result["exits"]["plateau_exit"]["n_macroparticles_total_by_scope"][
                "nitrogen_ionized_electrons"
            ],
            0,
        )

    def test_summary_scope_reordering_is_rejected(self) -> None:
        path, rows = self._summary_rows("plateau_exit")
        rows[0], rows[1] = rows[1], rows[0]
        self._write_csv(path, rows)
        with self.assertRaisesRegex(ValueError, "scope order"):
            self.validator.validate_analysis(
                particle_analysis_root=self.particle_root,
                resolved_parameters_path=self.resolved,
            )

    def test_duplicate_summary_scope_is_rejected(self) -> None:
        path, rows = self._summary_rows("plateau_exit")
        rows.append(dict(rows[-1]))
        self._write_csv(path, rows)
        with self.assertRaisesRegex(ValueError, "exactly one summary row"):
            self.validator.validate_analysis(
                particle_analysis_root=self.particle_root,
                resolved_parameters_path=self.resolved,
            )

    def test_selected_iteration_must_match_resolved_exit_target(self) -> None:
        path, rows = self._summary_rows("plateau_exit")
        for row in rows:
            row["selected_particle_iteration"] = "60974"
        self._write_csv(path, rows)
        with self.assertRaisesRegex(ValueError, "exact target iteration"):
            self.validator.validate_analysis(
                particle_analysis_root=self.particle_root,
                resolved_parameters_path=self.resolved,
            )

    def test_soft50_threshold_grid_must_match_all_scopes(self) -> None:
        path = self.particle_root / "plateau_exit" / "particle_soft50_curves.csv"
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        rows = [
            row
            for row in rows
            if not (
                row["species_scope"] == "nitrogen_ionized_electrons"
                and float(row["soft50_energy_low_MeV"]) == 10.0
            )
        ]
        self._write_csv(path, rows)
        with self.assertRaisesRegex(ValueError, "threshold grid differs"):
            self.validator.validate_analysis(
                particle_analysis_root=self.particle_root,
                resolved_parameters_path=self.resolved,
            )

    def test_acceptance_threshold_grid_must_match_all_scopes(self) -> None:
        path = self.particle_root / "capillary_exit" / "particle_acceptance_curves.csv"
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        rows = [
            row
            for row in rows
            if not (
                row["species_scope"] == "preionized_background_electrons"
                and float(row["theta_cut_mrad"]) == 10.0
                and float(row["E_min_MeV"]) == 100.0
            )
        ]
        self._write_csv(path, rows)
        with self.assertRaisesRegex(ValueError, "threshold grid differs"):
            self.validator.validate_analysis(
                particle_analysis_root=self.particle_root,
                resolved_parameters_path=self.resolved,
            )

    def test_filtered_or_final_dump_policy_is_rejected(self) -> None:
        payload = json.loads(self.resolved.read_text(encoding="utf-8"))
        payload["particle_diagnostic_dump_last_timestep"] = True
        payload["particle_diagnostic_min_energy_MeV"] = 5.0
        self.resolved.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "must not force final-timestep"):
            self.validator.validate_analysis(
                particle_analysis_root=self.particle_root,
                resolved_parameters_path=self.resolved,
            )

    def test_missing_ionized_scope_is_not_treated_as_empty(self) -> None:
        path, rows = self._summary_rows("plateau_exit")
        rows = [
            row
            for row in rows
            if row["species_scope"] != "nitrogen_ionized_electrons"
        ]
        self._write_csv(path, rows)
        with self.assertRaisesRegex(ValueError, "scope order"):
            self.validator.validate_analysis(
                particle_analysis_root=self.particle_root,
                resolved_parameters_path=self.resolved,
            )


if __name__ == "__main__":
    unittest.main()
