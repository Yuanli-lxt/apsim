"""Calculate Sobol indices for screened system-parameter runs."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from system_common import (
    FINAL_RESULTS_DIR,
    INTERMEDIATE_DIR,
    SOBOL_INDICES_SUMMARY_CSV,
    SOBOL_MISSING_VALUES_REPORT_CSV,
    SOBOL_PROBLEM_JSON,
    SOBOL_SAMPLES_WIDE_CSV,
    SYSTEM_DIR,
)


def main() -> None:
    env = os.environ.copy()
    env["SOBOL_OUTPUT_DIR"] = str(SYSTEM_DIR)
    expected_problem = INTERMEDIATE_DIR / "sobol_problem_definition.json"
    expected_samples = FINAL_RESULTS_DIR / "sobol_samples_wide.csv"
    if SOBOL_PROBLEM_JSON != expected_problem or SOBOL_SAMPLES_WIDE_CSV != expected_samples:
        raise RuntimeError("Unexpected system Sobol paths; wrapper assumes standard filenames.")
    cmd = [sys.executable, "scripts/sobol/07_calculate_sobol_indices.py", *sys.argv[1:]]
    code = subprocess.call(cmd, env=env)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
