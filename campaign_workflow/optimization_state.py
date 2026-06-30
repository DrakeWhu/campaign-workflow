from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from campaign_workflow.core.atomic_io import read_json, write_json_atomic
from campaign_workflow.core.state import now_utc


OPTIMIZATION_STATE_FILENAME = "optimization_state.json"
PAUSE_FILENAME = "PAUSE_OPTIMIZATION"
LOCK_DIRNAME = ".optimizer_tick.lock"

VALID_OPTIMIZATION_STATUSES = {
    "planned",
    "optimizer_outputs_ready",
    "campaign_prepared",
    "campaign_materialized",
    "submitted",
    "running",
    "postprocessing",
    "reduced_ready",
    "closed",
    "failed",
    "needs_inspection",
    "aborted",
    "paused",
}

VALID_RECOMMENDED_ACTIONS = {
    "initialize_optimization_state",
    "submit_iteration",
    "wait_for_jobs",
    "inspect_failures",
    "close_iteration",
    "propose_next_iteration",
    "paused",
    "no_action",
}


class OptimizationStateError(RuntimeError):
    """Raised when optimization-level state cannot be read or written safely."""


@dataclass(frozen=True)
class OptimizationStateInfo:
    path: Path
    exists: bool
    data: dict[str, Any] | None


def optimization_state_path(optimization_root: Path) -> Path:
    return optimization_root / OPTIMIZATION_STATE_FILENAME


def pause_file_path(optimization_root: Path) -> Path:
    return optimization_root / PAUSE_FILENAME


def lock_dir_path(optimization_root: Path) -> Path:
    return optimization_root / LOCK_DIRNAME


def read_optimization_state(optimization_root: Path) -> OptimizationStateInfo:
    path = optimization_state_path(optimization_root)
    if not path.exists():
        return OptimizationStateInfo(path=path, exists=False, data=None)

    try:
        data = read_json(path)
    except Exception as exc:
        raise OptimizationStateError(f"failed to read {path}: {exc}") from exc

    if data.get("schema_version") != 1:
        raise OptimizationStateError(f"{path} schema_version must be 1")

    return OptimizationStateInfo(path=path, exists=True, data=data)


def build_optimization_state_document(
    *,
    optimization_root: Path,
    iteration_summaries: list[dict[str, Any]],
    paused: bool,
) -> dict[str, Any]:
    latest_iteration = None
    if iteration_summaries:
        latest_iteration = max(int(item["iteration"]) for item in iteration_summaries)

    if paused:
        status = "paused"
    elif not iteration_summaries:
        status = "planned"
    else:
        statuses = {str(item.get("status", "")) for item in iteration_summaries}
        if "failed" in statuses:
            status = "failed"
        elif statuses == {"closed"}:
            status = "closed"
        elif statuses & {"submitted", "running", "postprocessing", "reduced_ready"}:
            status = "running"
        else:
            status = "campaign_materialized"

    return {
        "schema_version": 1,
        "optimization_name": optimization_root.name,
        "status": status,
        "updated_at": now_utc(),
        "latest_iteration": latest_iteration,
        "iterations": iteration_summaries,
    }


def write_optimization_state(optimization_root: Path, data: dict[str, Any]) -> None:
    path = optimization_state_path(optimization_root)
    write_json_atomic(path, data, dry_run=False)


@contextmanager
def optimizer_tick_lock(optimization_root: Path, *, acquire: bool) -> Iterator[None]:
    """Detect or acquire a simple optimization-root lock.

    Pure dry-runs pass acquire=False and only fail if an existing lock is present.
    Write modes pass acquire=True and create a lock directory that is removed on exit.
    """
    lock_path = lock_dir_path(optimization_root)

    if lock_path.exists():
        raise OptimizationStateError(f"optimizer tick lock already exists: {lock_path}")

    created = False
    if acquire:
        try:
            lock_path.mkdir()
            created = True
            (lock_path / "owner.txt").write_text(
                f"pid={os.getpid()}\ncreated_at={now_utc()}\n",
                encoding="utf-8",
            )
        except FileExistsError as exc:
            raise OptimizationStateError(
                f"optimizer tick lock already exists: {lock_path}"
            ) from exc
        except Exception as exc:
            raise OptimizationStateError(
                f"failed to acquire optimizer tick lock {lock_path}: {exc}"
            ) from exc

    try:
        yield
    finally:
        if created:
            owner = lock_path / "owner.txt"
            try:
                if owner.exists():
                    owner.unlink()
                lock_path.rmdir()
            except OSError:
                pass
