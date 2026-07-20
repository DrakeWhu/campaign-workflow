from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


_HELPER_PATH = Path("examples/capillary_guiding/run_particle_analysis_if_available.py")
_spec = importlib.util.spec_from_file_location("run_particle_analysis_if_available", _HELPER_PATH)
assert _spec is not None
assert _spec.loader is not None
_particle_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_particle_module)

particle_main = _particle_module.main
build_particle_analysis_argv = _particle_module.build_particle_analysis_argv


class CapillaryParticleAnalysisWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.case_dir = self.root / "000_case"
        self.case_dir.mkdir(parents=True)

        self.analysis_root = self.root / "guiding_analysis_module"
        (self.analysis_root / "scripts").mkdir(parents=True)

        self.fake_particle_script = self.analysis_root / "scripts" / "fake_particle_case.py"
        self.fake_particle_script.write_text(
            textwrap.dedent(
                """
                from __future__ import annotations

                import argparse
                import json
                from pathlib import Path

                parser = argparse.ArgumentParser()
                parser.add_argument("--diag", required=True)
                parser.add_argument("--outdir", required=True)
                parser.add_argument("--species", required=True)
                parser.add_argument("--which", required=True)
                parser.add_argument("--exit-kind", required=True)
                parser.add_argument("--overwrite", action="store_true")
                parser.add_argument("--spectrum-emin-mev", required=True)
                parser.add_argument("--spectrum-log-y", action="store_true")
                parser.add_argument("--maximum-target-iteration-delta")
                args = parser.parse_args()

                outdir = Path(args.outdir)
                outdir.mkdir(parents=True, exist_ok=True)
                (outdir / "particle_summary.csv").write_text(
                    "case_id,n_selected,energy_min_mev\\n0,3,1.0\\n",
                    encoding="utf-8",
                )
                (outdir / "argv.json").write_text(
                    json.dumps(vars(args), sort_keys=True),
                    encoding="utf-8",
                )
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_auto_with_particle_diag_executes_particle_command(self) -> None:
        (self.case_dir / "diags" / "plasma_electrons").mkdir(parents=True)

        rc, stdout, stderr = self._run_helper("auto")

        self.assertEqual(rc, 0, stderr)
        self.assertIn("particle diagnostic found", stdout)
        self.assertTrue((self.case_dir / "particle_analysis" / "particle_summary.csv").is_file())

        argv_log = (self.case_dir / "particle_analysis" / "argv.json").read_text(encoding="utf-8")
        self.assertIn("plasma_electrons", argv_log)
        self.assertIn('"species": "electrons"', argv_log)

    def test_auto_without_particle_diag_skips_without_failure(self) -> None:
        rc, stdout, stderr = self._run_helper("auto")

        self.assertEqual(rc, 0, stderr)
        self.assertIn("skipping optional particle analysis", stdout)
        self.assertFalse((self.case_dir / "particle_analysis" / "particle_summary.csv").exists())

    def test_always_without_particle_diag_fails_clearly(self) -> None:
        rc, _stdout, stderr = self._run_helper("always")

        self.assertEqual(rc, 1)
        self.assertIn("CAMPAIGN_RUN_PARTICLE_ANALYSIS=always", stderr)
        self.assertIn("diags", stderr)
        self.assertIn("plasma_electrons", stderr)

    def test_never_with_particle_diag_does_not_execute_particle_command(self) -> None:
        (self.case_dir / "diags" / "plasma_electrons").mkdir(parents=True)

        rc, stdout, stderr = self._run_helper("never")

        self.assertEqual(rc, 0, stderr)
        self.assertIn("disabled", stdout)
        self.assertFalse((self.case_dir / "particle_analysis" / "particle_summary.csv").exists())

    def test_builds_expected_particle_analysis_command(self) -> None:
        diag_dir = self.case_dir / "diags" / "plasma_electrons"
        with mock.patch.dict(
            os.environ,
            {"CAMPAIGN_PARTICLE_MAX_TARGET_ITERATION_DELTA": "0"},
            clear=False,
        ):
            argv = build_particle_analysis_argv(
                python_executable="python",
                analysis_root=self.analysis_root,
                case_dir=self.case_dir,
                diag_dir=diag_dir,
            )

        self.assertIn("--diag", argv)
        self.assertIn(str(diag_dir), argv)
        self.assertIn("--outdir", argv)
        self.assertIn(str(self.case_dir / "particle_analysis"), argv)
        self.assertIn("--species", argv)
        self.assertIn("electrons", argv)
        self.assertIn("--which", argv)
        self.assertIn("exit", argv)
        self.assertIn("--exit-kind", argv)
        self.assertIn("plateau", argv)
        self.assertIn("--spectrum-emin-mev", argv)
        self.assertIn("1", argv)
        self.assertIn("--spectrum-log-y", argv)
        self.assertIn("--maximum-target-iteration-delta", argv)
        delta_index = argv.index("--maximum-target-iteration-delta")
        self.assertEqual(argv[delta_index + 1], "0")

    def test_guiding_wrapper_still_runs_guiding_before_particle_phase(self) -> None:
        wrapper = Path("examples/capillary_guiding/run_guiding_case_analysis_sunrise.sh")
        text = wrapper.read_text(encoding="utf-8")

        guiding_index = text.index("python scripts/analyze_case.py")
        particle_index = text.index("run_particle_analysis_if_available.py")

        self.assertLess(guiding_index, particle_index)
        self.assertIn(
            'export CAMPAIGN_RUN_PARTICLE_ANALYSIS="${CAMPAIGN_RUN_PARTICLE_ANALYSIS:-auto}"',
            text,
        )
        self.assertIn('"${SCRIPT_DIR}/run_particle_analysis_if_available.py"', text)

    def test_case_cycle_exports_safe_particle_auto_mode(self) -> None:
        script = Path("examples/sunrise/submit_case_cycle_array.sh")
        text = script.read_text(encoding="utf-8")

        self.assertIn(
            'export CAMPAIGN_RUN_PARTICLE_ANALYSIS="${CAMPAIGN_RUN_PARTICLE_ANALYSIS:-auto}"',
            text,
        )
        self.assertIn('[[ "${CONFIRM_CLEANUP_EXECUTE:-0}" == "1" ]]', text)

    def _run_helper(self, mode: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        env = {
            "CAMPAIGN_PARTICLE_ANALYSIS_SCRIPT": str(self.fake_particle_script),
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = particle_main(
                    [
                        str(self.case_dir),
                        "--analysis-root",
                        str(self.analysis_root),
                        "--mode",
                        mode,
                    ]
                )
        return rc, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
