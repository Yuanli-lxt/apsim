"""Build a Sobol range table from top Morris parameters."""

from __future__ import annotations

import argparse

import pandas as pd

from system_common import MORRIS_INDICES_CSV, PARAMETER_RANGES_CSV, SOBOL_PARAMETER_RANGES_CSV, ensure_dirs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select top Morris parameters for Sobol follow-up.")
    parser.add_argument("--top", type=int, default=12, help="Number of parameters to include in Sobol.")
    parser.add_argument("--morris", default=MORRIS_INDICES_CSV, help="Morris indices summary CSV.")
    parser.add_argument("--ranges", default=PARAMETER_RANGES_CSV, help="Original system ranges CSV.")
    parser.add_argument("--out", default=SOBOL_PARAMETER_RANGES_CSV, help="Output Sobol range CSV.")
    return parser.parse_args()


def main() -> None:
    ensure_dirs()
    args = parse_args()
    morris = pd.read_csv(args.morris)
    ranges = pd.read_csv(args.ranges)
    scored = (
        morris.groupby("parameter_key", as_index=False)["mu_star"]
        .mean()
        .sort_values("mu_star", ascending=False)
        .head(args.top)
    )
    top_keys = set(scored["parameter_key"])
    out = ranges.copy()
    out["include_in_sobol"] = out["parameter_key"].isin(top_keys).map({True: "TRUE", False: "FALSE"})
    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"Wrote {args.out}")
    print("Selected parameters:")
    for key in scored["parameter_key"]:
        print(f"  {key}")


if __name__ == "__main__":
    main()
