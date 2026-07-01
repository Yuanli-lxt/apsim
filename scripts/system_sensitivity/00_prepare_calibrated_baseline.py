"""Copy the calibrated best APSIM/cultivar files into a fixed baseline folder."""

from __future__ import annotations

import argparse
from pathlib import Path

from system_common import BASELINE_DIR, DEFAULT_BEST_DIR, DEFAULT_WEATHER_FILE, copy_calibrated_baseline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare calibrated baseline for system sensitivity analysis.")
    parser.add_argument("--best-dir", type=Path, default=DEFAULT_BEST_DIR, help="Directory containing truth.apsim, Wheat.xml, Maize.xml, best_selection.json.")
    parser.add_argument("--target-dir", type=Path, default=BASELINE_DIR, help="Destination baseline directory.")
    parser.add_argument("--weather", type=Path, default=DEFAULT_WEATHER_FILE, help="Reliable real APSIM .met file to use if the best .apsim has a stale weather path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = copy_calibrated_baseline(args.best_dir, args.target_dir, weather_path=args.weather)
    print(f"Prepared calibrated baseline: {manifest['baseline_apsim']}")
    print(f"Source best dir: {manifest['source_best_dir']}")
    print(f"Weather file: {manifest['fixed_inputs'].get('weather_file', '')}")
    print(f"Manifest: {args.target_dir / 'baseline_manifest.json'}")


if __name__ == "__main__":
    main()
