# campaign_workflow/slurm_submit_guard.py

from __future__ import annotations

import os
from typing import Mapping

NESTED_SBATCH_MESSAGE = (
    "Refusing to call sbatch from inside a SLURM job on SUNRISE. "
    "Use submit_morbo_chain.py from login/control instead."
)


def slurm_job_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if env is None else env
    return {
        key: str(source[key])
        for key in ("SLURM_JOB_ID", "SLURM_ARRAY_JOB_ID")
        if source.get(key)
    }


def running_inside_slurm_job(env: Mapping[str, str] | None = None) -> bool:
    return bool(slurm_job_environment(env))


def assert_not_inside_slurm_job_for_sbatch(
    env: Mapping[str, str] | None = None,
) -> None:
    identifiers = slurm_job_environment(env)
    if identifiers:
        details = ", ".join(
            f"{key}={value}" for key, value in sorted(identifiers.items())
        )
        raise RuntimeError(f"{NESTED_SBATCH_MESSAGE} Detected {details}.")
