"""Generate APSIM files for screened system Sobol samples."""

from __future__ import annotations

import argparse
from pathlib import Path

import generate_runs_entry
from system_common import BASE_APSIM, SOBOL_PARAMETER_RANGES_CSV, SOBOL_SAMPLES_WIDE_CSV


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate .apsim files for screened system Sobol samples.")
    parser.add_argument("--apsim", type=Path, default=BASE_APSIM)
    parser.add_argument("--samples", type=Path, default=SOBOL_SAMPLES_WIDE_CSV)
    parser.add_argument("--ranges", type=Path, default=SOBOL_PARAMETER_RANGES_CSV)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generate_runs_entry.generate(args.apsim, args.samples, args.ranges, include_col="include_in_sobol")


if __name__ == "__main__":
    main()
