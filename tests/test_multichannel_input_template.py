from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from pathlib import Path


def load_input_template():
    path = Path("examples/sunrise/multichannel/input_template.py").resolve()
    spec = importlib.util.spec_from_file_location("multichannel_input_template", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MultichannelInputTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_input_template()

    def test_reference_effective_density_uses_hexagonal_fill_fraction(self) -> None:
        resolved = self.module.resolve_parameters({})
        self.assertAlmostEqual(
            resolved["honeycomb_fill_fraction"], 0.6431199961827697, places=12
        )
        self.assertAlmostEqual(
            resolved["effective_electron_density_m3"] / 1.0e25,
            1.929359988548309,
            places=12,
        )
        self.assertAlmostEqual(resolved["laser_centroid_z_m"], 28.00622626e-6)
        self.assertEqual(resolved["laser_focus_z_m"], 45.0e-6)

    def test_general_linear_polarization_reduces_to_one_component_on_axes(self) -> None:
        y_linear = self.module.laser_component_parameters(90.0, 0.0)
        self.assertEqual(len(y_linear), 1)
        self.assertEqual(y_linear[0]["axis"], "y")
        self.assertEqual(y_linear[0]["name"], "laser_y")
        self.assertAlmostEqual(y_linear[0]["amplitude_fraction"], 1.0)

        rotated = self.module.laser_component_parameters(30.0, 0.0)
        self.assertEqual([item["axis"] for item in rotated], ["x", "y"])
        self.assertAlmostEqual(rotated[0]["amplitude_fraction"], math.cos(math.pi / 6))
        self.assertAlmostEqual(rotated[1]["amplitude_fraction"], 0.5)

    def test_elliptical_components_preserve_total_field_amplitude(self) -> None:
        for orientation in (0.0, 17.0, 90.0, 173.0):
            for ellipticity in (-45.0, -20.0, 0.0, 20.0, 45.0):
                components = self.module.laser_component_parameters(
                    orientation, ellipticity
                )
                norm = sum(item["amplitude_fraction"] ** 2 for item in components)
                self.assertAlmostEqual(norm, 1.0, places=12)

        circular = self.module.laser_component_parameters(0.0, 45.0)
        self.assertEqual([item["name"] for item in circular], ["laser_x", "laser_y"])
        self.assertAlmostEqual(circular[0]["cep_rad"], 0.0)
        self.assertAlmostEqual(circular[1]["cep_rad"], -math.pi / 2.0)

    def test_longer_plasma_produces_later_final_frame(self) -> None:
        short = self.module.resolve_parameters({"MC_PLASMA_LENGTH_M": "30e-6"})
        long = self.module.resolve_parameters({"MC_PLASMA_LENGTH_M": "150e-6"})
        self.assertLess(short["max_steps"], long["max_steps"])
        self.assertGreater(short["max_steps"], 1000)
        self.assertGreater(long["max_steps"], 4000)

    def test_density_expression_rotates_coordinates_before_lattice_lookup(self) -> None:
        expression = self.module.hexagonal_density_expression()
        self.assertIn("ct*x + st*y", expression)
        self.assertIn("-st*x + ct*y", expression)
        self.assertIn("floor", expression)


if __name__ == "__main__":
    unittest.main()
