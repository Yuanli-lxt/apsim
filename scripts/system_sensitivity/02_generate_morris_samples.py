"""Generate Morris samples for system-level APSIM sensitivity screening."""

from __future__ import annotations

import argparse
import json

import pandas as pd

from system_common import (
    MORRIS_PROBLEM_JSON,
    MORRIS_SAMPLES_LONG_CSV,
    MORRIS_SAMPLES_WIDE_CSV,
    ensure_dirs,
    load_included_ranges,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Morris samples for APSIM system sensitivity.")
    parser.add_argument("--trajectories", type=int, default=12, help="Number of Morris trajectories.")
    parser.add_argument("--levels", type=int, default=4, help="Number of Morris grid levels.")
    parser.add_argument("--optimal-trajectories", type=int, default=None, help="Optional SALib optimal trajectory count.")
    return parser.parse_args()


def main() -> None:
    ensure_dirs()
    args = parse_args()
    ranges = load_included_ranges()
    problem = {
        "num_vars": len(ranges),
        "names": ranges["parameter_key"].tolist(),
        "bounds": ranges[["lower_bound", "upper_bound"]].astype(float).values.tolist(),
        "metadata": {
            "method": "Morris",
            "trajectories": args.trajectories,
            "levels": args.levels,
            "optimal_trajectories": args.optimal_trajectories,
            "groups_note": "Parameter groups are kept in parameter_table metadata; SALib Morris is run ungrouped to rank individual parameters.",
        },
        "parameter_table": ranges.to_dict(orient="records"),
    }
    with open(MORRIS_PROBLEM_JSON, "w", encoding="utf-8") as handle:
        json.dump(problem, handle, ensure_ascii=False, indent=2)

    try:
        from SALib.sample import morris
    except Exception as exc:
        raise RuntimeError("SALib is required for Morris sampling. Install SALib before running this step.") from exc

    sample_kwargs = {
        "N": args.trajectories,
        "num_levels": args.levels,
    }
    if args.optimal_trajectories is not None:
        sample_kwargs["optimal_trajectories"] = args.optimal_trajectories
    X = morris.sample(problem, **sample_kwargs)
    wide = pd.DataFrame(X, columns=problem["names"])
    wide.insert(0, "sample_id", range(1, len(wide) + 1))
    wide.to_csv(MORRIS_SAMPLES_WIDE_CSV, index=False, encoding="utf-8-sig")

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
    pd.DataFrame(long_rows).to_csv(MORRIS_SAMPLES_LONG_CSV, index=False, encoding="utf-8-sig")
    print(f"Wrote {MORRIS_SAMPLES_WIDE_CSV}")
    print(f"Parameters: {len(ranges)}; samples: {len(wide)}")


if __name__ == "__main__":
    main()
