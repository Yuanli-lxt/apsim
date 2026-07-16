"""Validate fixed 2020 yield factors across 2018-2019 and compare 5/10 km grids."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020"
BASELINE = PILOT / "corrected_baseline"
RUN_ROOT = ROOT / "outputs" / "spatial" / "county_pilot_2020" / "corrected_baseline"
OUT = RUN_ROOT / "multiyear_resolution_validation"
STATS = PILOT / "calibration" / "qihe_2018_2020_official_crop_statistics.csv"


def read_apsim_output(path: Path) -> pd.DataFrame:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header = next(i for i, line in enumerate(lines) if line.strip().startswith("Date"))
    frame = pd.read_csv(path, sep=r"\s+", skiprows=header)
    if not frame.empty and str(frame.iloc[0, 0]).startswith("("):
        frame = frame.iloc[1:].reset_index(drop=True)
    frame["Date"] = pd.to_datetime(frame["Date"], format="%d/%m/%Y", errors="coerce")
    return frame


def crop_column(frame: pd.DataFrame, crop: str) -> str:
    candidates = [column for column in frame if str(column).lower().endswith(f"{crop}.yield")]
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one {crop} yield column, got {candidates}")
    return str(candidates[0])


def collect_resolution(resolution_m: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    label = f"{resolution_m // 1000}km"
    units = pd.read_csv(BASELINE / f"corrected_baseline_units_{label}.csv")
    scenario_root = RUN_ROOT / "ordinary_farmer" if resolution_m == 5000 else RUN_ROOT / f"resolution_{label}" / "ordinary_farmer"
    case_rows = []
    for case_id in sorted(units.case_id.unique()):
        harvest_path = scenario_root / "cases" / case_id / f"{case_id} Harvest.out"
        if not harvest_path.exists():
            raise FileNotFoundError(harvest_path)
        harvest = read_apsim_output(harvest_path)
        for year in (2018, 2019, 2020):
            annual = harvest.loc[harvest.Date.dt.year == year]
            case_rows.append({
                "case_id": case_id,
                "year": year,
                "wheat_yield_kg_ha": float(pd.to_numeric(annual[crop_column(harvest, "wheat")], errors="coerce").fillna(0).sum()),
                "maize_yield_kg_ha": float(pd.to_numeric(annual[crop_column(harvest, "maize")], errors="coerce").fillna(0).sum()),
            })
    cases = pd.DataFrame(case_rows)
    mapped = units.merge(cases, on="case_id", how="left", validate="many_to_many")
    rows, grid_rows = [], []
    for year, group in mapped.groupby("year"):
        weights = group.soil_rotation_area_ha
        for crop in ("wheat", "maize"):
            rows.append({
                "resolution_m": resolution_m,
                "year": int(year),
                "crop": crop,
                "raw_apsim_yield_kg_ha": float(np.average(group[f"{crop}_yield_kg_ha"], weights=weights)),
                "represented_rotation_area_ha": float(weights.sum()),
                "grid_cells": int(group.grid_id.nunique()),
                "soil_subunits": int(group.subunit_id.nunique()),
                "unique_cases": int(group.case_id.nunique()),
                "weather_cells": int(group.weather_grid_id.nunique()),
            })
            for grid_id, grid in group.groupby("grid_id"):
                grid_weights = grid.soil_rotation_area_ha
                grid_rows.append({
                    "resolution_m": resolution_m,
                    "year": int(year),
                    "crop": crop,
                    "grid_id": grid_id,
                    "rotation_area_ha": float(grid_weights.sum()),
                    "yield_kg_ha": float(np.average(grid[f"{crop}_yield_kg_ha"], weights=grid_weights)),
                    "soil_types": int(grid.hwsd_soil_unit.nunique()),
                    "weather_cells": int(grid.weather_grid_id.nunique()),
                })
    return pd.DataFrame(rows), pd.DataFrame(grid_rows)


def metrics(group: pd.DataFrame, prediction: str) -> dict:
    error = group[prediction] - group.official_yield_kg_ha
    return {
        "mean_bias_kg_ha": float(error.mean()),
        "mae_kg_ha": float(error.abs().mean()),
        "rmse_kg_ha": float(np.sqrt(np.mean(error**2))),
        "mape_percent": float(np.mean(np.abs(error / group.official_yield_kg_ha)) * 100.0),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    statistics = pd.read_csv(STATS)
    county_frames, grid_frames = [], []
    for resolution in (5000, 10000):
        county, grids = collect_resolution(resolution)
        county_frames.append(county)
        grid_frames.append(grids)
    simulated = pd.concat(county_frames, ignore_index=True)
    grid_results = pd.concat(grid_frames, ignore_index=True)
    comparison = simulated.merge(
        statistics[["year", "crop", "crop_zh", "yield_kg_ha"]].rename(columns={"yield_kg_ha": "official_yield_kg_ha"}),
        on=["year", "crop"], how="left", validate="many_to_one",
    )

    calibration_base = comparison.loc[(comparison.resolution_m == 5000) & (comparison.year == 2020)].copy()
    factors = dict(zip(calibration_base.crop, calibration_base.official_yield_kg_ha / calibration_base.raw_apsim_yield_kg_ha))
    grid_results["fixed_2020_factor"] = grid_results.crop.map(factors)
    grid_results["calibrated_yield_kg_ha"] = grid_results.yield_kg_ha * grid_results.fixed_2020_factor
    grid_results.to_csv(OUT / "grid_cell_annual_yields.csv", index=False, encoding="utf-8-sig")
    comparison["fixed_2020_factor"] = comparison.crop.map(factors)
    comparison["implied_annual_factor"] = comparison.official_yield_kg_ha / comparison.raw_apsim_yield_kg_ha
    comparison["calibrated_yield_kg_ha"] = comparison.raw_apsim_yield_kg_ha * comparison.fixed_2020_factor
    comparison["raw_bias_kg_ha"] = comparison.raw_apsim_yield_kg_ha - comparison.official_yield_kg_ha
    comparison["raw_relative_bias_percent"] = comparison.raw_bias_kg_ha / comparison.official_yield_kg_ha * 100.0
    comparison["calibrated_bias_kg_ha"] = comparison.calibrated_yield_kg_ha - comparison.official_yield_kg_ha
    comparison["calibrated_relative_bias_percent"] = comparison.calibrated_bias_kg_ha / comparison.official_yield_kg_ha * 100.0
    comparison.to_csv(OUT / "annual_yield_comparison_5km_10km.csv", index=False, encoding="utf-8-sig")

    validation = comparison.loc[comparison.year.isin([2018, 2019])]
    metric_rows = []
    for (resolution, crop), group in validation.groupby(["resolution_m", "crop"]):
        metric_rows.append({
            "resolution_m": int(resolution), "crop": crop, "years": "2018-2019",
            **{f"raw_{key}": value for key, value in metrics(group, "raw_apsim_yield_kg_ha").items()},
            **{f"calibrated_{key}": value for key, value in metrics(group, "calibrated_yield_kg_ha").items()},
        })
    metrics_frame = pd.DataFrame(metric_rows)
    metrics_frame.to_csv(OUT / "cross_year_validation_metrics.csv", index=False, encoding="utf-8-sig")

    wide = comparison.pivot(index=["year", "crop"], columns="resolution_m", values=["raw_apsim_yield_kg_ha", "calibrated_yield_kg_ha"]).reset_index()
    resolution_rows = []
    for _, row in wide.iterrows():
        raw5, raw10 = row[("raw_apsim_yield_kg_ha", 5000)], row[("raw_apsim_yield_kg_ha", 10000)]
        cal5, cal10 = row[("calibrated_yield_kg_ha", 5000)], row[("calibrated_yield_kg_ha", 10000)]
        resolution_rows.append({
            "year": int(row[("year", "")]), "crop": row[("crop", "")],
            "raw_5km_kg_ha": raw5, "raw_10km_kg_ha": raw10,
            "raw_10km_minus_5km_kg_ha": raw10 - raw5,
            "raw_10km_minus_5km_percent": (raw10 - raw5) / raw5 * 100.0,
            "calibrated_5km_kg_ha": cal5, "calibrated_10km_kg_ha": cal10,
            "calibrated_10km_minus_5km_kg_ha": cal10 - cal5,
            "calibrated_10km_minus_5km_percent": (cal10 - cal5) / cal5 * 100.0,
        })
    pd.DataFrame(resolution_rows).to_csv(OUT / "resolution_10km_vs_5km.csv", index=False, encoding="utf-8-sig")

    spatial_rows = []
    for (resolution, year, crop), group in grid_results.groupby(["resolution_m", "year", "crop"]):
        weights = group.rotation_area_ha.to_numpy()
        values = group.yield_kg_ha.to_numpy()
        mean = float(np.average(values, weights=weights))
        sd = float(np.sqrt(np.average((values - mean) ** 2, weights=weights)))
        spatial_rows.append({
            "resolution_m": int(resolution), "year": int(year), "crop": crop,
            "grid_cells": len(group), "area_weighted_mean_kg_ha": mean,
            "area_weighted_sd_kg_ha": sd, "area_weighted_cv_percent": sd / mean * 100.0,
            "minimum_grid_yield_kg_ha": float(values.min()), "maximum_grid_yield_kg_ha": float(values.max()),
        })
    pd.DataFrame(spatial_rows).to_csv(OUT / "spatial_variability_by_resolution.csv", index=False, encoding="utf-8-sig")

    five_km = comparison.loc[comparison.resolution_m == 5000]
    pooled_diagnostic = {}
    for crop, group in five_km.groupby("crop"):
        simulated_values = group.raw_apsim_yield_kg_ha
        observed_values = group.official_yield_kg_ha
        pooled_diagnostic[crop] = {
            "ratio_of_three_year_sums": float(observed_values.sum() / simulated_values.sum()),
            "zero_intercept_least_squares": float((simulated_values * observed_values).sum() / (simulated_values**2).sum()),
            "note": "Diagnostic only; it uses all three years and is not an independent validation coefficient.",
        }
    summary = {
        "calibration_definition": "2020 official yield divided by raw 5 km APSIM yield",
        "independent_validation_years": [2018, 2019],
        "fixed_factors": factors,
        "three_year_pooled_factor_diagnostic": pooled_diagnostic,
        "warning": "The 2020 rotation mask and ordinary-farmer management scenario are held fixed across years.",
        "files": {
            "annual": "annual_yield_comparison_5km_10km.csv",
            "metrics": "cross_year_validation_metrics.csv",
            "resolution": "resolution_10km_vs_5km.csv",
            "grid_results": "grid_cell_annual_yields.csv",
            "spatial_variability": "spatial_variability_by_resolution.csv",
        },
    }
    (OUT / "validation_metadata.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
