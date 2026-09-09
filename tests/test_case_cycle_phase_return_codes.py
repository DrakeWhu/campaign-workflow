from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path


class CaseCyclePhaseReturnCodeTests(unittest.TestCase):
    SCRIPT_CASES = (
        (Path("examples/sunrise/submit_case_cycle_array.sh"), "[CASE-CYCLE]"),
        (Path("examples/lynx/submit_case_cycle_array_lynx.sh"), "[LYNX-CASE-CYCLE]"),
    )

    def _extract_run_phase(self, script_path: Path) -> str:
        text = script_path.read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^run_phase\(\) \{\n.*?^\}\n",
            text,
        )
        self.assertIsNotNone(match, f"run_phase() not found in {script_path}")
        return match.group(0)

    def _run_probe(self, script_path: Path, command: str) -> subprocess.CompletedProcess[str]:
        run_phase = self._extract_run_phase(script_path)
        with tempfile.TemporaryDirectory() as tmpdir:
            harness = f'''set +e
CASE_ID=0
CASE_NAME=test_case
CASE_DIR={tmpdir!r}
phase_log_path() {{
    local phase_name="$1"
    local stream_name="$2"
    printf '%s/%s_%s.log' "${{CASE_DIR}}" "${{phase_name}}" "${{stream_name}}"
}}
{run_phase}
run_phase probe bash -c {command!r}
rc=$?
printf 'OBSERVED_RC=%s\\n' "${{rc}}"
exit "${{rc}}"
'''
            return subprocess.run(
                ["bash", "-c", harness],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

    def test_success_returns_zero(self) -> None:
        for script_path, prefix in self.SCRIPT_CASES:
            with self.subTest(script=str(script_path)):
                result = self._run_probe(script_path, "exit 0")
                self.assertEqual(result.returncode, 0)
                self.assertIn(f"{prefix} END phase=probe return_code=0", result.stdout)
                self.assertIn("OBSERVED_RC=0", result.stdout)

    def test_nonzero_runner_return_code_is_preserved(self) -> None:
        for script_path, prefix in self.SCRIPT_CASES:
            with self.subTest(script=str(script_path)):
                result = self._run_probe(script_path, "exit 37")
                self.assertEqual(result.returncode, 37)
                self.assertIn(f"{prefix} END phase=probe return_code=37", result.stdout)
                self.assertIn("OBSERVED_RC=37", result.stdout)

    def test_signal_termination_return_code_is_preserved(self) -> None:
        for script_path, prefix in self.SCRIPT_CASES:
            with self.subTest(script=str(script_path)):
                result = self._run_probe(script_path, "kill -TERM $$")
                self.assertEqual(result.returncode, 143)
                self.assertIn(f"{prefix} END phase=probe return_code=143", result.stdout)
                self.assertIn("OBSERVED_RC=143", result.stdout)


if __name__ == "__main__":
    unittest.main()
