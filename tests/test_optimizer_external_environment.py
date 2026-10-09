import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import shlex

from campaign_workflow.propose_next_iteration import render_external_command


class OptimizerExternalEnvironmentTests(unittest.TestCase):
    def test_environment_cd_cannot_select_another_optimizer_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = root / "selected checkout"
            generic = root / "generic checkout"
            selected.mkdir()
            generic.mkdir()
            for directory, label in ((selected, "selected"), (generic, "generic")):
                (directory / "fixture_optimizer.py").write_text(f"print({label!r})\n")
            env = root / "environment.sh"
            env.write_text(f"cd {shlex.quote(str(generic))}\n")
            command = render_external_command(
                optimization_root=root,
                config={"optimizer": {
                    "working_directory": str(selected),
                    "env_script": str(env),
                    "command": [sys.executable, "-m", "fixture_optimizer"],
                }},
                from_iteration=0,
                next_iteration=1,
                optimizer_run_dir=root / "run",
            )
            result = subprocess.run(
                command.effective_command,
                cwd=command.working_directory,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "selected")
