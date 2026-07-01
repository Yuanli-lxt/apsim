"""Generate Sobol/Saltelli samples for screened system parameters."""

from __future__ import annotations

import argparse
import json

import pandas as pd

from system_common import (
    SOBOL_PARAMETER_RANGES_CSV,
    SOBOL_PROBLEM_JSON,
    SOBOL_SAMPLES_LONG_CSV,
    SOBOL_SAMPLES_WIDE_CSV,
    ensure_dirs,
    load_included_ranges,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Sobol samples for screened APSIM system parameters.")
    parser.add_argument("--N", type=int, default=64, help="Base Sobol sample size.")
    parser.add_argument("--second-order", action="store_true", help="Calculate second-order interactions.")
    parser.add_argument("--ranges", default=SOBOL_PARAMETER_RANGES_CSV, help="Screened parameter range CSV.")
    return parser.parse_args()


def main() -> None:
    ensure_dirs()
    args = parse_args()
    ranges = load_included_ranges(args.ranges, include_col="include_in_sobol")
    problem = {
        "num_vars": len(ranges),
        "names": ranges["parameter_key"].tolist(),
        "bounds": ranges[["lower_bound", "upper_bound"]].astype(float).values.tolist(),
        "metadata": {
            "method": "Sobol",
            "n_base": args.N,
            "calc_second_order": bool(args.second_order),
            "sample_formula": "N*(2D+2)" if args.second_order else "N*(D+2)",
        },
        "parameter_table": ranges.to_dict(orient="records"),
    }
    with open(SOBOL_PROBLEM_JSON, "w", encoding="utf-8") as handle:
        json.dump(problem, handle, ensure_ascii=False, indent=2)
    try:
        from SALib.sample import sobol as sobol_sample

        X = sobol_sample.sample(problem, args.N, calc_second_order=args.second_order, scramble=True)
    except Exception:
        from SALib.sample import saltelli

        X = saltelli.sample(problem, args.N, calc_second_order=args.second_order)
    wide = pd.DataFrame(X, columns=problem["names"])
    wide.insert(0, "sample_id", range(1, len(wide) + 1))
    wide.to_csv(SOBOL_SAMPLES_WIDE_CSV, index=False, encoding="utf-8-sig")

    meta = ranges.set_index("parameter_key")
    long_rows = []
    for _, sample in wide.iterrows():
        sid = int(sample["sample_id"])
        for key in problem["names"]:
            row = meta.loc[key]
            long_rows.append(
                {
                    "sample_id": sid,
                    "parameter_key": key,
                    "parameter_name": row["parameter_name"],
                    "group": row["group"],
                    "module": row["module"],
                    "sampled_value": sample[key],
                }
            )
    pd.DataFrame(long_rows).to_csv(SOBOL_SAMPLES_LONG_CSV, index=False, encoding="utf-8-sig")
    print(f"Wrote {SOBOL_SAMPLES_WIDE_CSV}")
    print(f"Parameters: {len(ranges)}; samples: {len(wide)}")


if __name__ == "__main__":
    main()
