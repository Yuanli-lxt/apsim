"""Run generated system-sensitivity APSIM samples.

This wrapper reuses the existing APSIM Classic batch runner by pointing its
SOBOL_OUTPUT_DIR environment variable at the system sensitivity workspace.
"""

from __future__ import annotations

import os
import subprocess
import sys

from system_common import SYSTEM_DIR


def main() -> None:
    env = os.environ.copy()
    env["SOBOL_OUTPUT_DIR"] = str(SYSTEM_DIR)
    cmd = [sys.executable, "scripts/sobol/05_run_apsim_batch.py", *sys.argv[1:]]
    raise SystemExit(subprocess.call(cmd, env=env))


if __name__ == "__main__":
    main()
