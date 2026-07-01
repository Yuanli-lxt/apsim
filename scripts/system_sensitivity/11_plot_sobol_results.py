"""Plot Sobol results for system-sensitivity runs."""

from __future__ import annotations

import os
import subprocess
import sys

from system_common import SYSTEM_DIR


def main() -> None:
    env = os.environ.copy()
    env["SOBOL_OUTPUT_DIR"] = str(SYSTEM_DIR)
    cmd = [sys.executable, "scripts/sobol/08_plot_sobol_results.py", *sys.argv[1:]]
    raise SystemExit(subprocess.call(cmd, env=env))


if __name__ == "__main__":
    main()
