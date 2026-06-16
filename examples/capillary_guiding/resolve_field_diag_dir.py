from __future__ import annotations

import argparse
import sys
from pathlib import Path


FIELD_DIAG_CANDIDATES = (
    Path("diags/fields"),
    Path("diags/diag1"),
)


def resolve_field_diag_dir(case_dir: Path | str) -> Path:
    """Resolve the field openPMD diagnostic directory for guiding analysis.

    New WarpX/PyWarpX inputs use ``diags/fields`` because the PICMI
    FieldDiagnostic is named ``fields`` with ``write_dir="diags"``.
    Legacy campaigns used ``diags/diag1``. Prefer the new path when both
    exist, but keep legacy campaigns analyzable.
    """
    root = Path(case_dir)
    checked: list[Path] = []

    for relative in FIELD_DIAG_CANDIDATES:
        candidate = root / relative
        checked.append(candidate)
        if candidate.is_dir():
            return candidate

    relative_text = ", ".join(path.as_posix() for path in FIELD_DIAG_CANDIDATES)
    checked_text = ", ".join(str(path) for path in checked)

    raise FileNotFoundError(
        "no field diagnostic directory found for guiding analysis; "
        f"expected one of: {relative_text}; "
        f"checked absolute paths: {checked_text}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve the field diagnostic directory for capillary guiding analysis."
    )
    parser.add_argument("case_dir", type=Path, help="Case directory to inspect.")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        print(resolve_field_diag_dir(args.case_dir))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())