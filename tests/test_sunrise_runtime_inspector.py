"""Read-only inspector tests; fixtures never touch a real campaign."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / ".agents/skills/sunrise-runtime/scripts/inspect_runtime.py"
SPEC = importlib.util.spec_from_file_location("inspect_sunrise_runtime", SCRIPT)
assert SPEC and SPEC.loader
INSPECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSPECTOR)


class RuntimeInspectorTests(unittest.TestCase):
    def test_assignment_validation(self):
        self.assertEqual(INSPECTOR.parse_assignments(["workflow=/tmp/a"], "--checkout"), {"workflow": "/tmp/a"})
        for raw in (["workflow"], ["=x"], ["workflow="], ["workflow=a", "workflow=b"]):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                INSPECTOR.parse_assignments(raw, "--checkout")

    def test_containment_uses_resolved_paths_and_not_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            other = Path(tmp) / "repo-elsewhere"
            root.mkdir()
            other.mkdir()
            (root / "package.py").write_text("x = 1\n")
            (other / "package.py").write_text("x = 2\n")
            self.assertTrue(INSPECTOR.is_within(root / "package.py", root))
            self.assertFalse(INSPECTOR.is_within(other / "package.py", root))

    def test_python_role_mismatch_blocks(self):
        self.assertEqual(INSPECTOR.check_python_version("optimizer", (3, 14))["status"], "BLOCKED")
        self.assertEqual(INSPECTOR.check_python_version("warpx", (3, 14))["status"], "PASS")

    def test_wrong_import_checkout_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            correct = Path(tmp) / "expected"
            wrong = Path(tmp) / "wrong"
            correct.mkdir()
            wrong.mkdir()
            module = type("Mock", (), {"__file__": str(wrong / "campaign_optimizer" / "__init__.py")})()
            with patch.object(INSPECTOR.importlib, "import_module", return_value=module):
                self.assertEqual(INSPECTOR.check_import("optimizer", correct)["status"], "BLOCKED")

    def test_git_checkout_mismatch_and_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.test"], check=True)
            (root / "file.txt").write_text("a\n")
            subprocess.run(["git", "-C", str(root), "add", "file.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "initial"], check=True)
            _, head, _ = INSPECTOR.run_git(root, "rev-parse", "HEAD")
            checks = INSPECTOR.check_checkout("workflow", root, head)
            self.assertTrue(all(item["status"] == "PASS" for item in checks))
            self.assertEqual(next(item for item in INSPECTOR.check_checkout("workflow", root, "0" * 40) if item["id"] == "revision_workflow")["status"], "BLOCKED")
            (root / "file.txt").write_text("b\n")
            self.assertEqual(next(item for item in INSPECTOR.check_checkout("workflow", root, head) if item["id"] == "clean_workflow")["status"], "BLOCKED")

    def test_guiding_entrypoint_bootstrap_matches_production(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "cap_guiding").mkdir()
            (root / "cap_guiding" / "__init__.py").write_text("version = 1\n")
            script = root / "scripts" / "analyze_case.py"
            script.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "PROJECT_ROOT = Path(__file__).resolve().parents[1]\n"
                "sys.path.insert(0, str(PROJECT_ROOT))\n"
            )
            with patch.object(sys, "path", list(sys.path)):
                sys.modules.pop("cap_guiding", None)
                report = INSPECTOR.inspect("guiding", {"guiding": str(root)}, {}, guiding_script=str(script))
                checks = {row["id"]: row for row in report["checks"]}
                self.assertEqual(checks["guiding_script_context"]["status"], "PASS")
                self.assertEqual(checks["import_guiding"]["status"], "PASS")
                sys.modules.pop("cap_guiding", None)

    def test_guiding_bootstrap_refuses_unverified_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            script = root / "scripts" / "analyze_case.py"
            script.write_text("print('no bootstrap')\n")
            report = INSPECTOR.inspect("guiding", {"guiding": str(root)}, {}, guiding_script=str(script))
            checks = {row["id"]: row for row in report["checks"]}
            self.assertEqual(checks["guiding_script_context"]["status"], "BLOCKED")

    def test_linker_missing_library_blocks(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="libpython3.10.so.1.0 => not found\n", stderr="")
        with patch.object(INSPECTOR.shutil, "which", return_value="/usr/bin/ldd"), patch.object(INSPECTOR.subprocess, "run", return_value=fake):
            self.assertEqual(INSPECTOR.check_linker(sys.executable)["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
