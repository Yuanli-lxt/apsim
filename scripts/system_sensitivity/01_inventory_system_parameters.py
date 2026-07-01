"""Build the system-level sensitivity parameter range template."""

from __future__ import annotations

import argparse
from pathlib import Path

from system_common import BASE_APSIM, PARAMETER_RANGES_CSV, build_system_parameter_rows, ensure_dirs, write_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory APSIM system/process parameters for Morris screening.")
    parser.add_argument("--apsim", type=Path, default=BASE_APSIM, help="Calibrated baseline .apsim file.")
    parser.add_argument("--out", type=Path, default=PARAMETER_RANGES_CSV, help="Output CSV range template.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_dirs()
    rows = build_system_parameter_rows(args.apsim)
    write_csv(args.out, rows)
    included = sum(row["include_in_morris"] == "TRUE" for row in rows)
    fixed = sum(row["include_in_morris"] == "FALSE" for row in rows)
    print(f"Wrote {args.out}")
    print(f"Included in Morris: {included}; fixed/protected rows: {fixed}")
    print("Review lower_bound/upper_bound/include_in_morris before formal runs.")


if __name__ == "__main__":
    main()
