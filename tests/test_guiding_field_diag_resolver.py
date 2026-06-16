from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


_HELPER_PATH = Path("examples/capillary_guiding/resolve_field_diag_dir.py")
_spec = importlib.util.spec_from_file_location("resolve_field_diag_dir", _HELPER_PATH)
assert _spec is not None
assert _spec.loader is not None
_resolver_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_resolver_module)

resolve_field_diag_dir = _resolver_module.resolve_field_diag_dir


class GuidingFieldDiagResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.case_dir = Path(self.tmpdir.name) / "000_case"
        self.case_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_prefers_fields_when_fields_and_diag1_exist(self) -> None:
        (self.case_dir / "diags" / "fields").mkdir(parents=True)
        (self.case_dir / "diags" / "diag1").mkdir(parents=True)

        resolved = resolve_field_diag_dir(self.case_dir)

        self.assertEqual(resolved, self.case_dir / "diags" / "fields")

    def test_uses_legacy_diag1_when_only_diag1_exists(self) -> None:
        (self.case_dir / "diags" / "diag1").mkdir(parents=True)

        resolved = resolve_field_diag_dir(self.case_dir)

        self.assertEqual(resolved, self.case_dir / "diags" / "diag1")

    def test_uses_fields_when_only_fields_exists(self) -> None:
        (self.case_dir / "diags" / "fields").mkdir(parents=True)

        resolved = resolve_field_diag_dir(self.case_dir)

        self.assertEqual(resolved, self.case_dir / "diags" / "fields")

    def test_errors_clearly_when_no_field_diag_exists(self) -> None:
        with self.assertRaises(FileNotFoundError) as ctx:
            resolve_field_diag_dir(self.case_dir)

        message = str(ctx.exception)
        self.assertIn("no field diagnostic directory found", message)
        self.assertIn("diags/fields", message)
        self.assertIn("diags/diag1", message)

    def test_wrapper_uses_resolved_field_diag_dir(self) -> None:
        wrapper = Path("examples/capillary_guiding/run_guiding_case_analysis_sunrise.sh")
        text = wrapper.read_text(encoding="utf-8")

        self.assertIn("resolve_field_diag_dir.py", text)
        self.assertIn("FIELD_DIAG_DIR=", text)
        self.assertIn('--diag "${FIELD_DIAG_DIR}"', text)
        self.assertNotIn('--diag "${CASE_DIR}/diags/diag1"', text)


if __name__ == "__main__":
    unittest.main()