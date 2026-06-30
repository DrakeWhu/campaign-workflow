from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from disk."""
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"JSON file does not contain an object: {path}")

    return data


def write_json_atomic(
    path: Path, data: dict[str, Any], *, dry_run: bool = False
) -> None:
    """Write JSON atomically by writing a temp file and renaming it into place.

    The temporary file is created in the same directory as the target so that
    os.replace is atomic on the target filesystem.

    On Windows, os.replace can occasionally raise PermissionError if the
    destination file is transiently locked by the OS, antivirus, indexing, etc.
    We retry a few times before surfacing the real error.
    """
    if dry_run:
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")

    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        _replace_with_short_retry(tmp_path, path)

    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _replace_with_short_retry(tmp_path: Path, path: Path) -> None:
    delays = [0.0, 0.02, 0.05, 0.1, 0.2]
    last_exc: PermissionError | None = None

    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError as exc:
            last_exc = exc

    assert last_exc is not None
    raise last_exc
