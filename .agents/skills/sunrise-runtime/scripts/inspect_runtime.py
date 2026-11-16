#!/usr/bin/env python3
"""Inspect an already-loaded SUNRISE Python runtime without changing it."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True

ROLE_PYTHON = {"workflow": (3, 10), "optimizer": (3, 10), "guiding": (3, 10), "warpx": (3, 14)}
REPO_PACKAGES = {"workflow": "campaign_workflow", "optimizer": "campaign_optimizer", "guiding": "cap_guiding"}
STATUS_PRIORITY = {"PASS": 0, "WARN": 1, "UNKNOWN": 2, "BLOCKED": 3}


def parse_assignments(values: list[str], flag: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in values:
        key, separator, value = entry.partition("=")
        if not separator or not key or not value or key in result:
            raise ValueError(f"{flag}: expected distinct NAME=VALUE entries; got {entry!r}")
        result[key] = value
    return result


def is_within(candidate: str | Path, checkout: str | Path) -> bool:
    try:
        Path(candidate).resolve(strict=False).relative_to(Path(checkout).resolve(strict=False))
        return True
    except ValueError:
        return False


def check_python_version(role: str, version: tuple[int, int]) -> dict[str, str]:
    expected = ROLE_PYTHON[role]
    return {
        "id": "python_version",
        "status": "PASS" if version == expected else "BLOCKED",
        "detail": f"expected {expected[0]}.{expected[1]}, got {version[0]}.{version[1]}",
    }


def check_import(name: str, checkout: Path) -> dict[str, str]:
    package = REPO_PACKAGES[name]
    try:
        # Only the inert package root is imported; check the actual __file__,
        # not just the intended PYTHONPATH or an importlib spec.
        loaded = importlib.import_module(package)
        origin = getattr(loaded, "__file__", None)
    except Exception as exc:
        return {"id": f"import_{name}", "status": "BLOCKED", "detail": f"{type(exc).__name__}: {exc}"}
    if not origin:
        return {"id": f"import_{name}", "status": "BLOCKED", "detail": "import has no __file__"}
    status = "PASS" if is_within(origin, checkout) else "BLOCKED"
    return {"id": f"import_{name}", "status": status, "detail": f"{package} imported from {Path(origin).resolve()} (expected under {checkout.resolve()})"}


def run_git(checkout: Path, *args: str) -> tuple[int, str, str]:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        completed = subprocess.run(["git", "-C", str(checkout), *args], env=env, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def check_checkout(name: str, path: Path, expected_sha: str | None) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    if not path.is_dir():
        return [{"id": f"checkout_{name}", "status": "BLOCKED", "detail": f"directory does not exist: {path}"}]
    rc, head, err = run_git(path, "rev-parse", "HEAD")
    if rc:
        return [{"id": f"checkout_{name}", "status": "BLOCKED", "detail": f"git HEAD unavailable: {err}"}]
    resolved = path.resolve()
    results.append({"id": f"checkout_{name}", "status": "PASS", "detail": f"{resolved} @ {head}"})
    if expected_sha:
        results.append({"id": f"revision_{name}", "status": "PASS" if head == expected_sha else "BLOCKED", "detail": f"expected {expected_sha}, got {head}"})
    else:
        results.append({"id": f"revision_{name}", "status": "UNKNOWN", "detail": "no expected commit supplied; do not infer one from a branch name"})
    rc, changes, err = run_git(path, "status", "--porcelain", "--untracked-files=normal")
    results.append({"id": f"clean_{name}", "status": "BLOCKED" if rc or changes else "PASS", "detail": err if rc else ("checkout is dirty" if changes else "checkout is clean")})
    return results


def check_linker(executable: str) -> dict[str, str]:
    ldd = shutil.which("ldd")
    if not ldd:
        return {"id": "linked_libraries", "status": "UNKNOWN", "detail": "ldd not found; linker inspection unavailable"}
    try:
        result = subprocess.run([ldd, executable], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"id": "linked_libraries", "status": "UNKNOWN", "detail": str(exc)}
    output = result.stdout + "\n" + result.stderr
    if "not found" in output:
        missing = [line.strip() for line in output.splitlines() if "not found" in line]
        return {"id": "linked_libraries", "status": "BLOCKED", "detail": "; ".join(missing)}
    if result.returncode:
        return {"id": "linked_libraries", "status": "UNKNOWN", "detail": f"ldd exited {result.returncode}: {result.stderr.strip()}"}
    return {"id": "linked_libraries", "status": "PASS", "detail": "no unresolved direct shared-library references reported by ldd"}


def inspect(role: str, checkouts: dict[str, str], expected_shas: dict[str, str], verify_imports: set[str] | None = None) -> dict[str, object]:
    checks: list[dict[str, str]] = [check_python_version(role, sys.version_info[:2])]
    checks.append(check_linker(sys.executable))
    loaded = os.environ.get("LOADEDMODULES")
    expected_module = "Python/3.14.3" if role == "warpx" else "Python/3.10.12"
    module_status = "UNKNOWN" if not loaded else ("PASS" if expected_module in loaded.split(":") else "WARN")
    checks.append({"id": "loaded_modules", "status": module_status, "detail": f"expected {expected_module}; LOADEDMODULES={loaded or '<unset>'}"})
    for name, raw_path in checkouts.items():
        path = Path(raw_path).expanduser()
        checks.extend(check_checkout(name, path, expected_shas.get(name)))
        if name in ({role} | (verify_imports or set())) and name in REPO_PACKAGES and path.is_dir():
            checks.append(check_import(name, path))
    if role == "warpx":
        try:
            spec = importlib.util.find_spec("pywarpx")
        except Exception as exc:
            spec = None
            detail = f"pywarpx discovery failed: {exc}"
        else:
            detail = f"pywarpx resolved at {spec.origin}" if spec else "pywarpx not importable"
        checks.append({"id": "pywarpx_available", "status": "PASS" if spec else "BLOCKED", "detail": detail})
    if role in REPO_PACKAGES and role not in checkouts:
        checks.append({"id": f"checkout_{role}", "status": "UNKNOWN", "detail": "no checkout provided; cannot verify import provenance"})
    unused = set(expected_shas) - set(checkouts)
    for name in sorted(unused):
        checks.append({"id": f"revision_{name}", "status": "BLOCKED", "detail": "expected SHA provided without checkout"})
    status = max((entry["status"] for entry in checks), key=STATUS_PRIORITY.__getitem__)
    return {"schema_version": 1, "role": role, "status": status, "python_executable": sys.executable, "python_version": sys.version.split()[0], "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=tuple(ROLE_PYTHON))
    parser.add_argument("--checkout", action="append", default=[], metavar="ROLE=PATH", help="named workflow/optimizer/guiding checkout; repeat as needed")
    parser.add_argument("--expected-sha", action="append", default=[], metavar="ROLE=SHA", help="expected full commit; repeat as needed")
    parser.add_argument("--verify-import", action="append", choices=tuple(REPO_PACKAGES), default=[], help="also check this package import against its checkout")
    args = parser.parse_args(argv)
    try:
        checkouts = parse_assignments(args.checkout, "--checkout")
        expected = parse_assignments(args.expected_sha, "--expected-sha")
        if (set(checkouts) | set(expected)) - set(REPO_PACKAGES):
            raise ValueError("checkout/revision names must be workflow, optimizer, or guiding")
        if any(len(value) != 40 or any(c not in "0123456789abcdefABCDEF" for c in value) for value in expected.values()):
            raise ValueError("--expected-sha requires a full 40-character Git SHA")
    except ValueError as exc:
        parser.error(str(exc))
    report = inspect(args.role, checkouts, expected, set(args.verify_import))
    print(json.dumps(report, indent=2, sort_keys=True))
    return {"PASS": 0, "WARN": 1, "UNKNOWN": 1, "BLOCKED": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
