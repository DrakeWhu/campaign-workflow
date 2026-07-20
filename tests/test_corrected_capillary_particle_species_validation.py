from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np


SCRIPT = Path(
    "examples/sunrise/corrected_capillary/validate_particle_species_outputs.py"
)
SCOPES = [
    "all_electrons",
    "preionized_background_electrons",
    "nitrogen_ionized_electrons",
]


def load_validator():
    imageio_package = types.ModuleType("imageio")
    imageio_v2 = types.ModuleType("imageio.v2")
    imageio_v2.imread = lambda _path: np.ones((120, 160, 3), dtype=np.uint8)
    imageio_package.v2 = imageio_v2
    spec = importlib.util.spec_from_file_location("particle_species_validator", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    replacements = {"imageio": imageio_package, "imageio.v2": imageio_v2}
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in replacements}
    sys.modules.update(replacements)
    try:
        spec.loader.exec_module(module)
    finally:
        for name, value in previous.items():
            if value is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    return module


class CorrectedCapillaryParticleSpeciesValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = load_validator()

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name) / "particle_analysis"
        self.plots = self.outdir / "plots"
        self.plots.mkdir(parents=True)
        self.resolved = Path(self.tmpdir.name) / "resolved_parameters.json"
        self.resolved.write_text(
            json.dumps(
                {
                    "particle_diagnostic_policy": (
                        "single_plateau_exit_field_aligned_filtered_v1"
                    ),
                    "particle_diagnostic_iteration": 42,
                    "particle_diagnostic_intervals": "42:42",
                    "particle_diagnostic_dump_last_timestep": False,
                    "particle_diagnostic_min_energy_MeV": 5.0,
                    "particle_diagnostic_forward_only": True,
                    "particle_diagnostic_target": "plateau_exit",
                    "particle_diagnostic_target_distance_m": 10.0e-3,
                    "particle_diagnostic_target_iteration_unaligned": 40,
                    "particle_diagnostic_alignment_error_steps": 2,
                    "baseline_steps_per_5mm": 20,
                    "field_diagnostic_period": 6,
                    "plasma_start_z": 0.0,
                    "plateau_end_z": 10.0e-3,
                    "particle_diagnostic_filter_expression": (
                        "(uz > 0.0)*((sqrt(1.0+ux*ux+uy*uy+uz*uz)-1.0)*"
                        "0.51099895 >= 5)"
                    ),
                }
            ),
            encoding="utf-8",
        )
        self._write_table("particle_summary.csv", one_row_per_scope=True)
        self._write_table("particle_soft50_curves.csv", one_row_per_scope=False)
        self._write_table("particle_acceptance_curves.csv", one_row_per_scope=False)
        self._write_plots()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _write_table(self, name: str, *, one_row_per_scope: bool) -> None:
        metadata = {
            "selection_mode": "exit",
            "target_guiding_iteration": "42",
            "selected_particle_iteration": "42",
            "target_iteration_delta": "0",
            "maximum_target_iteration_delta": "0",
            "target_iteration_alignment_status": "ok",
            "n_available_particle_iterations": "1",
            "available_particle_iterations_min": "42",
            "available_particle_iterations_max": "42",
            "n_macroparticles_total": "0",
        }
        rows = [
            {"species_scope": scope, "value": "1", **metadata}
            for scope in SCOPES
        ]
        if not one_row_per_scope:
            rows += [
                {"species_scope": scope, "value": "2", **metadata}
                for scope in SCOPES
            ]
        with (self.outdir / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def _write_plots(self) -> None:
        for scope in SCOPES:
            scope_prefix = (
                ""
                if scope == "all_electrons"
                else f"{self.validator.filename_token(scope)}_"
            )
            for plot_prefix in self.validator.PLOT_PREFIXES:
                path = self.plots / f"{plot_prefix}{scope_prefix}it00000042.png"
                path.write_bytes(b"x" * 10_000)

    def test_all_combined_and_separate_products_validate(self) -> None:
        summary = self.validator.validate_scope_table(
            self.outdir / "particle_summary.csv",
            expected_scopes=SCOPES,
            exactly_one_row_per_scope=True,
        )
        plots = self.validator.validate_scope_plots(
            self.plots,
            expected_scopes=SCOPES,
        )
        self.assertEqual(summary["scope_counts"], {scope: 1 for scope in SCOPES})
        self.assertEqual(len(plots), len(SCOPES) * len(self.validator.PLOT_PREFIXES))

        selection = self.validator.validate_selection_contract(
            summary_path=self.outdir / "particle_summary.csv",
            related_table_paths=[
                self.outdir / "particle_soft50_curves.csv",
                self.outdir / "particle_acceptance_curves.csv",
            ],
            resolved_parameters_path=self.resolved,
        )
        self.assertEqual(selection["selected_particle_iteration"], 42)
        self.assertTrue(selection["empty_particle_populations_allowed"])

    def test_missing_nitrogen_plot_blocks_validation(self) -> None:
        missing = self.plots / (
            "energy_spectrum_nitrogen_ionized_electrons_it00000042.png"
        )
        missing.unlink()
        with self.assertRaisesRegex(ValueError, "expected exactly one plot"):
            self.validator.validate_scope_plots(
                self.plots,
                expected_scopes=SCOPES,
            )

    def test_unequal_metric_rows_block_validation(self) -> None:
        path = self.outdir / "particle_soft50_curves.csv"
        with path.open("a", encoding="utf-8") as stream:
            stream.write("all_electrons,3\n")
        with self.assertRaisesRegex(ValueError, "unequal row counts"):
            self.validator.validate_scope_table(
                path,
                expected_scopes=SCOPES,
                exactly_one_row_per_scope=False,
                require_equal_rows_per_scope=True,
            )

    def test_distant_final_dump_blocks_selection_validation(self) -> None:
        path = self.outdir / "particle_summary.csv"
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
            fieldnames = list(rows[0])
        for row in rows:
            row["target_guiding_iteration"] = "10"
            row["target_iteration_delta"] = "32"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        with self.assertRaisesRegex(ValueError, "not aligned"):
            self.validator.validate_selection_contract(
                summary_path=path,
                related_table_paths=[
                    self.outdir / "particle_soft50_curves.csv",
                    self.outdir / "particle_acceptance_curves.csv",
                ],
                resolved_parameters_path=self.resolved,
            )

    def test_second_particle_iteration_blocks_selection_validation(self) -> None:
        path = self.outdir / "particle_summary.csv"
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
            fieldnames = list(rows[0])
        for row in rows:
            row["n_available_particle_iterations"] = "2"
            row["available_particle_iterations_max"] = "192000"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        with self.assertRaisesRegex(ValueError, "exactly the one resolved"):
            self.validator.validate_selection_contract(
                summary_path=path,
                related_table_paths=[
                    self.outdir / "particle_soft50_curves.csv",
                    self.outdir / "particle_acceptance_curves.csv",
                ],
                resolved_parameters_path=self.resolved,
            )


if __name__ == "__main__":
    unittest.main()
