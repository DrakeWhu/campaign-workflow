#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
import numpy as np


def validate_video(path: Path, *, minimum_frames: int) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"missing or empty animation: {path}")
    reader = imageio.get_reader(path)
    frame_count = 0
    shape: tuple[int, ...] | None = None
    try:
        for frame in reader:
            array = np.asarray(frame)
            if array.ndim != 3 or array.shape[2] not in {3, 4}:
                raise ValueError(f"invalid decoded frame shape in {path}: {array.shape}")
            if array.size == 0 or not np.isfinite(array).all():
                raise ValueError(f"invalid decoded frame data in {path}")
            if shape is None:
                shape = tuple(int(value) for value in array.shape)
            elif tuple(array.shape) != shape:
                raise ValueError(f"frame shape changes within {path}")
            frame_count += 1
            if frame_count > 10000:
                raise ValueError(f"unreasonable frame count in {path}")
    finally:
        reader.close()
    if frame_count < minimum_frames:
        raise ValueError(
            f"animation {path} has {frame_count} frames; expected >= {minimum_frames}"
        )
    digest_builder = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest_builder.update(chunk)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": digest_builder.hexdigest(),
        "frame_count": frame_count,
        "frame_shape": list(shape or ()),
        "status": "ok",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--minimum-frames", type=int, default=2)
    parser.add_argument("videos", nargs="+")
    args = parser.parse_args()
    if args.minimum_frames < 2:
        raise ValueError("minimum-frames must be at least two")
    records = [
        validate_video(Path(value), minimum_frames=args.minimum_frames)
        for value in args.videos
    ]
    payload = {
        "schema_version": 1,
        "status": "ok",
        "minimum_frames": args.minimum_frames,
        "videos": records,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[ANIMATION] validated {len(records)} videos -> {output}")


if __name__ == "__main__":
    main()
