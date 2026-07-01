"""Analyze Morris sensitivity indices for all stable collected outputs."""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from system_common import MORRIS_INDICES_CSV, MORRIS_PROBLEM_JSON, MORRIS_SAMPLES_WIDE_CSV, ensure_dirs


DEFAULT_TARGETS = [
    "grain_yield",
    "biomass",
    "lai",
    "grain_number",
    "grain_weight",
    "water_use_efficiency",
    "water_use_efficiency_yield",
    "water_use_efficiency_biomass",
    "evapotranspiration",
    "transpiration",
    "soil_evaporation",
    "runoff",
    "drainage",
    "rainfall",
    "irrigation",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Morris indices from collected APSIM outputs.")
    parser.add_argument("--outputs", default="outputs/system_sensitivity/final_results/sobol_model_outputs.csv", help="Collected output CSV.")
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS), help="Comma-separated output variables to analyze.")
    parser.add_argument("--levels", type=int, default=4, help="Morris grid levels used during sampling.")
    return parser.parse_args()


def to_numeric(series: pd.Series) -> pd.Series:
    if series.dtype == object:
        dt = pd.to_datetime(series, errors="coerce")
        if dt.notna().sum() >= max(1, series.notna().sum() * 0.5):
            return (dt - pd.Timestamp("1970-01-01")).dt.days.astype(float)
    return pd.to_numeric(series, errors="coerce")


def main() -> None:
    ensure_dirs()
    args = parse_args()
    try:
        from SALib.analyze import morris
    except Exception as exc:
        raise RuntimeError("SALib is required for Morris analysis. Install SALib before running this step.") from exc

    with open(MORRIS_PROBLEM_JSON, "r", encoding="utf-8") as handle:
        problem = json.load(handle)
    problem_for_salib = {
        "num_vars": problem["num_vars"],
        "names": problem["names"],
        "bounds": problem["bounds"],
    }
    samples = pd.read_csv(MORRIS_SAMPLES_WIDE_CSV)
    outputs = pd.read_csv(args.outputs)
    targets = [item.strip() for item in args.targets.split(",") if item.strip()]
    rows = []
    missing = []
    for (crop, cultivar), group in outputs.groupby(["crop", "cultivar"], dropna=False):
        ordered = samples[["sample_id"]].merge(group, on="sample_id", how="left")
        X = samples[problem["names"]].to_numpy(dtype=float)
        for target in targets:
            if target not in ordered.columns:
                missing.append((crop, cultivar, target, "target_column_missing"))
                continue
            y = to_numeric(ordered[target])
            if y.isna().any() or np.isclose(y.var(), 0):
                missing.append((crop, cultivar, target, "missing_or_zero_variance"))
                continue
            result = morris.analyze(problem_for_salib, X, y.to_numpy(dtype=float), num_levels=args.levels, print_to_console=False)
            names = list(result["names"]) if "names" in result else problem["names"]
            order = np.argsort(-np.asarray(result["mu_star"], dtype=float))
            ranks = {names[idx]: rank + 1 for rank, idx in enumerate(order)}
            for idx, name in enumerate(names):
                rows.append(
                    {
                        "crop": crop,
                        "cultivar": cultivar,
                        "target_variable": target,
                        "parameter_key": name,
                        "mu": result["mu"][idx],
                        "mu_star": result["mu_star"][idx],
                        "sigma": result["sigma"][idx],
                        "mu_star_conf": result.get("mu_star_conf", [np.nan] * len(names))[idx],
                        "rank": ranks[name],
                    }
                )
    pd.DataFrame(rows).to_csv(MORRIS_INDICES_CSV, index=False, encoding="utf-8-sig")
    if missing:
        miss = MORRIS_INDICES_CSV.parent / "morris_missing_targets.csv"
        pd.DataFrame(missing, columns=["crop", "cultivar", "target_variable", "issue"]).to_csv(miss, index=False, encoding="utf-8-sig")
        print(f"Wrote missing target report: {miss}")
    print(f"Wrote {MORRIS_INDICES_CSV}")


if __name__ == "__main__":
    main()
