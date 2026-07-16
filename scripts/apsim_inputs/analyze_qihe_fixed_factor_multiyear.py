"""Archive the Qihe baseline and analyse fixed-factor transfer across years.

The script is intentionally post-processing only: it never modifies APSIM model,
management, weather, soil, mask, or existing simulation output files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from scipy.optimize import minimize_scalar


ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020"
CALIBRATION = PILOT / "calibration"
SOURCE_VALIDATION = (
    ROOT / "outputs" / "spatial" / "county_pilot_2020" / "corrected_baseline"
    / "multiyear_resolution_validation"
)
BASELINE_RUN = ROOT / "outputs" / "spatial" / "county_pilot_2020" / "corrected_baseline"
OUTPUT_ROOT = (
    ROOT / "outputs" / "spatial" / "county_pilot_2020"
    / "fixed_factor_multiyear_validation"
)
STATS = CALIBRATION / "qihe_multiyear_official_crop_statistics.csv"
APSIM_EXE = Path(r"F:\APSIM710-r4221\Model\Apsim.exe")
FIXED_YEAR = 2020
BOOTSTRAP_SEED = 20260715


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def metric_values(observed: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    error = predicted - observed
    return {
        "mean_bias_kg_ha": float(np.mean(error)),
        "mae_kg_ha": float(np.mean(np.abs(error))),
        "rmse_kg_ha": float(np.sqrt(np.mean(error**2))),
        "mape_percent": float(np.mean(np.abs(error / observed)) * 100.0),
    }


def zero_intercept_ls(raw: np.ndarray, official: np.ndarray) -> float:
    return float(np.dot(raw, official) / np.dot(raw, raw))


def robust_zero_intercept(raw: np.ndarray, official: np.ndarray) -> float:
    """Huber-loss zero-intercept slope with scale based on response MAD."""
    scale = float(stats.median_abs_deviation(official, scale="normal"))
    scale = max(scale, float(np.mean(official)) * 0.01, 1.0)
    delta = 1.345 * scale

    def objective(factor: float) -> float:
        residual = official - factor * raw
        absolute = np.abs(residual)
        loss = np.where(absolute <= delta, 0.5 * residual**2, delta * (absolute - 0.5 * delta))
        return float(loss.sum())

    return float(minimize_scalar(objective, bounds=(0.0, 2.0), method="bounded").x)


def run_status(resolution_m: int) -> dict[str, float | int]:
    root = BASELINE_RUN / "ordinary_farmer"
    if resolution_m == 10000:
        root = BASELINE_RUN / "resolution_10km" / "ordinary_farmer"
    frame = pd.read_csv(root / "case_run_status.csv")
    return {
        "successful_cases": int((frame.status == "success").sum()),
        "failed_cases": int((frame.status != "success").sum()),
        "total_cases": int(len(frame)),
        "case_runtime_sum_seconds": float(frame.elapsed_seconds.sum()),
    }


def archive_manifest(run_dir: Path) -> pd.DataFrame:
    paths: list[tuple[str, Path]] = []
    fixed = [
        ("model", ROOT / "models" / "apsim_classic" / "modified_from_truth.apsim"),
        ("management", ROOT / "configs" / "spatial" / "qihe_2020_management_scenarios.json"),
        ("rotation_mask", PILOT / "qihe_2020_wheat_maize_rotation_mask.tif"),
        ("crop_mask", PILOT / "qihe_2020_winter_wheat_mask.tif"),
        ("crop_mask", PILOT / "qihe_2020_summer_maize_mask.tif"),
        ("grid", PILOT / "grid_resolution_experiment" / "grids" / "qihe_2020_rotation_5km.gpkg"),
        ("grid", PILOT / "grid_resolution_experiment" / "grids" / "qihe_2020_rotation_10km.gpkg"),
        ("units", PILOT / "corrected_baseline" / "corrected_baseline_units_5km.csv"),
        ("units", PILOT / "corrected_baseline" / "corrected_baseline_units_10km.csv"),
        ("script", ROOT / "scripts" / "apsim_inputs" / "run_corrected_baseline.py"),
        ("script", ROOT / "scripts" / "apsim_inputs" / "validate_multiyear_and_resolution.py"),
        ("script", ROOT / "scripts" / "spatial_data" / "prepare_corrected_baseline_inputs.py"),
        ("script", ROOT / "scripts" / "spatial_data" / "download_agera5_qihe.py"),
        ("script", Path(__file__)),
        ("statistics", STATS),
        ("apsim_executable", APSIM_EXE),
    ]
    paths.extend(fixed)
    paths.extend(("weather", p) for p in sorted((PILOT / "corrected_baseline" / "weather").glob("*.met")))
    paths.extend(("soil_profile", p) for p in sorted((PILOT / "soil_profiles").glob("**/soil_profile.json")))
    paths.extend(("official_yearbook", p) for p in sorted((ROOT / "data" / "raw" / "shandong_public" / "statistics").glob("dezhou_yearbook_*.pdf")))
    for resolution_m, base in [
        (5000, BASELINE_RUN / "ordinary_farmer"),
        (10000, BASELINE_RUN / "resolution_10km" / "ordinary_farmer"),
    ]:
        for name in ["case_run_status.csv", "unique_case_results.csv", "soil_subunit_results.csv", "baseline_summary.json", "management_scenario.json"]:
            paths.append((f"raw_result_{resolution_m}m", base / name))
        paths.extend((f"raw_harvest_{resolution_m}m", p) for p in sorted((base / "cases").glob("*/* Harvest.out")))
    paths.extend(("validation_source", p) for p in sorted(SOURCE_VALIDATION.glob("*")) if p.is_file())

    rows = []
    seen: set[str] = set()
    for category, path in paths:
        key = str(path.resolve())
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        stat = path.stat()
        rows.append({
            "category": category,
            "path": relative(path),
            "bytes": stat.st_size,
            "modified_time": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
            "sha256": sha256(path),
        })
    frame = pd.DataFrame(rows).sort_values(["category", "path"])
    frame.to_csv(run_dir / "baseline_file_manifest.csv", index=False, encoding="utf-8-sig")
    return frame


def prepare_annual(run_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    source = pd.read_csv(SOURCE_VALIDATION / "annual_yield_comparison_5km_10km.csv")
    official = pd.read_csv(STATS).rename(columns={
        "statistical_year": "year",
        "official_yield_kg_ha": "official_yield_kg_ha_verified",
        "production_t": "official_production_t",
        "area_ha": "official_area_ha",
    })
    official = official[["year", "crop", "official_yield_kg_ha_verified", "official_production_t", "official_area_ha"]]
    source = source.drop(columns=[c for c in ["official_yield_kg_ha", "crop_zh"] if c in source])
    annual = source.merge(official, on=["year", "crop"], how="left", validate="many_to_one")
    annual = annual.rename(columns={"official_yield_kg_ha_verified": "official_yield_kg_ha"})

    calibration = annual[(annual.resolution_m == 5000) & (annual.year == FIXED_YEAR)]
    factors = dict(zip(calibration.crop, calibration.official_yield_kg_ha / calibration.raw_apsim_yield_kg_ha))
    annual["fixed_factor_source_year"] = FIXED_YEAR
    annual["fixed_factor_source_resolution_m"] = 5000
    annual["fixed_2020_factor"] = annual.crop.map(factors)
    annual["fixed_corrected_yield_kg_ha"] = annual.raw_apsim_yield_kg_ha * annual.fixed_2020_factor
    annual["raw_simulated_production_t"] = annual.raw_apsim_yield_kg_ha * annual.represented_rotation_area_ha / 1000.0
    annual["fixed_corrected_simulated_production_t"] = annual.fixed_corrected_yield_kg_ha * annual.represented_rotation_area_ha / 1000.0
    annual["raw_bias_kg_ha"] = annual.raw_apsim_yield_kg_ha - annual.official_yield_kg_ha
    annual["raw_relative_bias_percent"] = annual.raw_bias_kg_ha / annual.official_yield_kg_ha * 100.0
    annual["raw_absolute_error_kg_ha"] = annual.raw_bias_kg_ha.abs()
    annual["fixed_bias_kg_ha"] = annual.fixed_corrected_yield_kg_ha - annual.official_yield_kg_ha
    annual.loc[annual.fixed_bias_kg_ha.abs() < 1e-9, "fixed_bias_kg_ha"] = 0.0
    annual["fixed_relative_bias_percent"] = annual.fixed_bias_kg_ha / annual.official_yield_kg_ha * 100.0
    annual["fixed_absolute_error_kg_ha"] = annual.fixed_bias_kg_ha.abs()
    annual["implicit_factor"] = annual.official_yield_kg_ha / annual.raw_apsim_yield_kg_ha
    annual["year_role"] = "resolution_transfer_diagnostic"
    annual.loc[(annual.resolution_m == 5000) & (annual.year != FIXED_YEAR), "year_role"] = "independent_validation"
    annual.loc[(annual.resolution_m == 5000) & (annual.year == FIXED_YEAR), "year_role"] = "calibration_definition"
    for resolution in (5000, 10000):
        status = run_status(resolution)
        mask = annual.resolution_m == resolution
        for key, value in status.items():
            annual.loc[mask, key] = value
    annual["management_assumption"] = "2020 rotation mask and uniform ordinary-farmer management held fixed"
    annual = annual.sort_values(["resolution_m", "crop", "year"])
    annual.to_csv(run_dir / "annual_raw_and_corrected_yields.csv", index=False, encoding="utf-8-sig")

    grid = pd.read_csv(SOURCE_VALIDATION / "grid_cell_annual_yields.csv")
    grid = grid.rename(columns={"yield_kg_ha": "raw_apsim_yield_kg_ha", "calibrated_yield_kg_ha": "fixed_corrected_yield_kg_ha"})
    grid.to_csv(run_dir / "baseline_raw_grid_yields_2018_2020.csv", index=False, encoding="utf-8-sig")
    annual[[c for c in annual.columns if c not in ["fixed_corrected_yield_kg_ha", "fixed_bias_kg_ha", "fixed_relative_bias_percent", "fixed_absolute_error_kg_ha"]]].to_csv(
        run_dir / "baseline_raw_county_yields_2018_2020.csv", index=False, encoding="utf-8-sig"
    )
    return annual, grid, factors


def analyse_factors(run_dir: Path, annual: pd.DataFrame) -> pd.DataFrame:
    five = annual[annual.resolution_m == 5000].copy()
    rows = []
    for crop, group in five.groupby("crop"):
        values = group.implicit_factor.to_numpy()
        years = group.year.to_numpy(dtype=float)
        n = len(values)
        mean = float(values.mean())
        sd = float(values.std(ddof=1))
        sem = sd / np.sqrt(n)
        tcrit = float(stats.t.ppf(0.975, n - 1)) if n > 1 else np.nan
        regression = stats.linregress(years, values)
        for _, item in group.iterrows():
            rows.append({
                "record_type": "annual", "crop": crop, "year": int(item.year),
                "official_yield_kg_ha": item.official_yield_kg_ha,
                "raw_apsim_yield_kg_ha": item.raw_apsim_yield_kg_ha,
                "implicit_factor": item.implicit_factor,
                "mean_factor": np.nan, "sd_factor": np.nan, "cv_percent": np.nan,
                "minimum_factor": np.nan, "maximum_factor": np.nan,
                "mean_95ci_lower": np.nan, "mean_95ci_upper": np.nan,
                "linear_trend_factor_per_year": np.nan, "trend_p_value": np.nan,
                "sample_years": "2018-2020",
            })
        rows.append({
            "record_type": "summary", "crop": crop, "year": np.nan,
            "official_yield_kg_ha": np.nan, "raw_apsim_yield_kg_ha": np.nan,
            "implicit_factor": np.nan, "mean_factor": mean, "sd_factor": sd,
            "cv_percent": sd / mean * 100.0, "minimum_factor": float(values.min()),
            "maximum_factor": float(values.max()), "mean_95ci_lower": mean - tcrit * sem,
            "mean_95ci_upper": mean + tcrit * sem,
            "linear_trend_factor_per_year": float(regression.slope),
            "trend_p_value": float(regression.pvalue), "sample_years": "2018-2020",
        })
    result = pd.DataFrame(rows)
    result.to_csv(run_dir / "annual_implicit_factors.csv", index=False, encoding="utf-8-sig")
    return result


def fixed_metrics(run_dir: Path, annual: pd.DataFrame) -> pd.DataFrame:
    rows = []
    scopes = {
        "independent_validation_2018_2019": [2018, 2019],
        "all_available_2018_2020_descriptive": [2018, 2019, 2020],
    }
    five = annual[annual.resolution_m == 5000]
    for crop, crop_group in five.groupby("crop"):
        for scope, years in scopes.items():
            group = crop_group[crop_group.year.isin(years)]
            observed = group.official_yield_kg_ha.to_numpy()
            raw_metrics = metric_values(observed, group.raw_apsim_yield_kg_ha.to_numpy())
            fixed = metric_values(observed, group.fixed_corrected_yield_kg_ha.to_numpy())
            for method, metrics in [("raw_apsim", raw_metrics), ("fixed_2020_factor", fixed)]:
                rows.append({
                    "crop": crop, "evaluation_scope": scope, "method": method,
                    "years": ",".join(map(str, years)), **metrics,
                    "mae_improvement_percent": np.nan,
                    "rmse_improvement_percent": np.nan,
                    "mape_improvement_percent": np.nan,
                })
            rows.append({
                "crop": crop, "evaluation_scope": scope, "method": "fixed_vs_raw_improvement",
                "years": ",".join(map(str, years)),
                "mean_bias_kg_ha": np.nan,
                "mae_kg_ha": np.nan, "rmse_kg_ha": np.nan, "mape_percent": np.nan,
                "mae_improvement_percent": (1.0 - fixed["mae_kg_ha"] / raw_metrics["mae_kg_ha"]) * 100.0,
                "rmse_improvement_percent": (1.0 - fixed["rmse_kg_ha"] / raw_metrics["rmse_kg_ha"]) * 100.0,
                "mape_improvement_percent": (1.0 - fixed["mape_percent"] / raw_metrics["mape_percent"]) * 100.0,
            })
    result = pd.DataFrame(rows)
    result["interpretation"] = np.where(
        result.evaluation_scope.str.startswith("independent"),
        "Independent of the 2020 factor fit",
        "Descriptive; includes the 2020 calibration year",
    )
    result.to_csv(run_dir / "fixed_2020_factor_metrics.csv", index=False, encoding="utf-8-sig")
    return result


def cross_validation(run_dir: Path, annual: pd.DataFrame, factors: dict[str, float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    five = annual[annual.resolution_m == 5000]
    predictions = []
    metric_rows = []
    rolling_rows = []
    for crop, group in five.groupby("crop"):
        group = group.sort_values("year")
        x = group.raw_apsim_yield_kg_ha.to_numpy()
        y = group.official_yield_kg_ha.to_numpy()
        years = group.year.to_numpy(dtype=int)
        pooled = zero_intercept_ls(x, y)
        for index, year in enumerate(years):
            train = np.arange(len(years)) != index
            loo_factor = zero_intercept_ls(x[train], y[train])
            methods = {
                "raw_apsim": (1.0, x[index]),
                "fixed_2020_factor": (factors[crop], x[index] * factors[crop]),
                "pooled_all_years_zero_intercept_descriptive": (pooled, x[index] * pooled),
                "leave_one_year_out_zero_intercept": (loo_factor, x[index] * loo_factor),
            }
            for method, (factor, prediction) in methods.items():
                predictions.append({
                    "crop": crop, "held_out_year": int(year), "method": method,
                    "training_years": "none" if method == "raw_apsim" else (
                        "2020" if method == "fixed_2020_factor" else (
                            "2018-2020_including_target" if method.startswith("pooled") else ",".join(map(str, years[train]))
                        )
                    ),
                    "factor": factor, "raw_apsim_yield_kg_ha": x[index],
                    "official_yield_kg_ha": y[index], "predicted_yield_kg_ha": prediction,
                    "bias_kg_ha": prediction - y[index],
                    "absolute_error_kg_ha": abs(prediction - y[index]),
                    "relative_bias_percent": (prediction - y[index]) / y[index] * 100.0,
                    "independent_for_held_out_year": method in ["raw_apsim", "leave_one_year_out_zero_intercept"] or (method == "fixed_2020_factor" and year != 2020),
                })
        crop_predictions = pd.DataFrame([r for r in predictions if r["crop"] == crop])
        for method, method_group in crop_predictions.groupby("method"):
            metrics = metric_values(method_group.official_yield_kg_ha.to_numpy(), method_group.predicted_yield_kg_ha.to_numpy())
            metric_rows.append({"crop": crop, "method": method, "years": "2018-2020", **metrics})

        for index in range(1, len(years)):
            factor = zero_intercept_ls(x[:index], y[:index])
            prediction = x[index] * factor
            rolling_rows.append({
                "crop": crop, "target_year": int(years[index]),
                "training_years": ",".join(map(str, years[:index])), "factor": factor,
                "raw_apsim_yield_kg_ha": x[index], "official_yield_kg_ha": y[index],
                "predicted_yield_kg_ha": prediction, "bias_kg_ha": prediction - y[index],
                "absolute_error_kg_ha": abs(prediction - y[index]),
                "relative_bias_percent": (prediction - y[index]) / y[index] * 100.0,
            })
    prediction_frame = pd.DataFrame(predictions)
    metric_frame = pd.DataFrame(metric_rows)
    metric_frame["evaluation_interpretation"] = metric_frame.method.map({
        "raw_apsim": "No statistical calibration; out-of-sample terminology not applicable",
        "fixed_2020_factor": "Mixed: 2018-2019 independent, 2020 calibration definition",
        "pooled_all_years_zero_intercept_descriptive": "In-sample descriptive; not independent validation",
        "leave_one_year_out_zero_intercept": "Each prediction uses only the other years",
    })
    prediction_frame.to_csv(run_dir / "leave_one_year_out_predictions.csv", index=False, encoding="utf-8-sig")
    metric_frame.to_csv(run_dir / "leave_one_year_out_metrics.csv", index=False, encoding="utf-8-sig")
    rolling = pd.DataFrame(rolling_rows)
    rolling.to_csv(run_dir / "rolling_origin_predictions.csv", index=False, encoding="utf-8-sig")
    return prediction_frame, metric_frame


def uncertainty(run_dir: Path, annual: pd.DataFrame, bootstrap_samples: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    rows, yield_rows, influence_rows = [], [], []
    five = annual[annual.resolution_m == 5000]
    for crop, group in five.groupby("crop"):
        group = group.sort_values("year")
        years = group.year.to_numpy(dtype=int)
        x = group.raw_apsim_yield_kg_ha.to_numpy()
        y = group.official_yield_kg_ha.to_numpy()
        implied = y / x
        samples = rng.integers(0, len(group), size=(bootstrap_samples, len(group)))
        method_values: dict[str, np.ndarray] = {
            "mean_implicit_factor": implied[samples].mean(axis=1),
            "zero_intercept_least_squares": np.sum(x[samples] * y[samples], axis=1) / np.sum(x[samples] ** 2, axis=1),
        }
        robust_cache: dict[tuple[int, ...], float] = {}
        robust_values = np.empty(bootstrap_samples)
        for i, draw in enumerate(samples):
            counts = tuple(np.bincount(draw, minlength=len(group)).tolist())
            if counts not in robust_cache:
                robust_cache[counts] = robust_zero_intercept(x[draw], y[draw])
            robust_values[i] = robust_cache[counts]
        method_values["robust_huber_zero_intercept"] = robust_values
        for method, values in method_values.items():
            rows.append({
                "crop": crop, "method": method, "sample_years": "2018-2020",
                "n_years": len(group), "bootstrap_samples": bootstrap_samples,
                "bootstrap_seed": BOOTSTRAP_SEED, "factor_median": float(np.median(values)),
                "factor_95ci_lower": float(np.quantile(values, 0.025)),
                "factor_95ci_upper": float(np.quantile(values, 0.975)),
                "exploratory_small_sample": True,
            })
            for year, raw in zip(years, x):
                predicted = raw * values
                yield_rows.append({
                    "crop": crop, "year": int(year), "method": method,
                    "raw_apsim_yield_kg_ha": raw,
                    "corrected_yield_median_kg_ha": float(np.median(predicted)),
                    "corrected_yield_95ci_lower_kg_ha": float(np.quantile(predicted, 0.025)),
                    "corrected_yield_95ci_upper_kg_ha": float(np.quantile(predicted, 0.975)),
                })
        full = zero_intercept_ls(x, y)
        for index, year in enumerate(years):
            keep = np.arange(len(years)) != index
            deleted = zero_intercept_ls(x[keep], y[keep])
            influence_rows.append({
                "crop": crop, "deleted_year": int(year), "full_sample_factor": full,
                "delete_one_factor": deleted, "absolute_factor_change": abs(deleted - full),
                "relative_factor_change_percent": (deleted - full) / full * 100.0,
            })
    factor_frame = pd.DataFrame(rows)
    yield_frame = pd.DataFrame(yield_rows)
    factor_frame.to_csv(run_dir / "factor_uncertainty.csv", index=False, encoding="utf-8-sig")
    yield_frame.to_csv(run_dir / "uncertainty_yield_intervals.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(influence_rows).to_csv(run_dir / "factor_influence_by_year.csv", index=False, encoding="utf-8-sig")
    return factor_frame, yield_frame


def diagnostics(run_dir: Path, annual: pd.DataFrame) -> None:
    five = annual[annual.resolution_m == 5000]
    rows, change_rows = [], []
    for crop, group in five.groupby("crop"):
        group = group.sort_values("year")
        observed = group.official_yield_kg_ha.to_numpy()
        raw = group.raw_apsim_yield_kg_ha.to_numpy()
        fixed = group.fixed_corrected_yield_kg_ha.to_numpy()
        for method, values in [("raw_apsim", raw), ("fixed_2020_factor", fixed)]:
            delta_observed = np.diff(observed)
            delta_predicted = np.diff(values)
            rows.append({
                "crop": crop, "method": method,
                "pearson_correlation_level": float(np.corrcoef(observed, values)[0, 1]),
                "pearson_correlation_annual_change": float(np.corrcoef(delta_observed, delta_predicted)[0, 1]),
                "annual_change_direction_matches": int(np.sum(np.sign(delta_observed) == np.sign(delta_predicted))),
                "annual_change_intervals": len(delta_observed),
                "note": "Fixed multiplication cannot change correlation, ranking, or change direction; n=3 is descriptive only.",
            })
            for i in range(len(delta_observed)):
                change_rows.append({
                    "crop": crop, "method": method,
                    "from_year": int(group.year.iloc[i]), "to_year": int(group.year.iloc[i + 1]),
                    "official_change_kg_ha": delta_observed[i], "predicted_change_kg_ha": delta_predicted[i],
                    "direction_match": bool(np.sign(delta_observed[i]) == np.sign(delta_predicted[i])),
                })
    pd.DataFrame(rows).to_csv(run_dir / "interannual_response_diagnostics.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(change_rows).to_csv(run_dir / "annual_change_comparison.csv", index=False, encoding="utf-8-sig")

    relative = five.sort_values(["crop", "year"])
    pattern_rows = []
    for crop, group in relative.groupby("crop"):
        for method, column in [("raw_apsim", "raw_relative_bias_percent"), ("fixed_2020_factor", "fixed_relative_bias_percent")]:
            signs = np.sign(group[column].to_numpy())
            longest = 1
            current = 1
            for i in range(1, len(signs)):
                current = current + 1 if signs[i] == signs[i - 1] and signs[i] != 0 else 1
                longest = max(longest, current)
            pattern_rows.append({"crop": crop, "method": method, "longest_consecutive_same_sign_years": longest, "bias_signs_by_year": ",".join(f"{y}:{'+' if s > 0 else '-' if s < 0 else '0'}" for y, s in zip(group.year, signs))})
    pd.DataFrame(pattern_rows).to_csv(run_dir / "systematic_bias_patterns.csv", index=False, encoding="utf-8-sig")


def save_resolution(run_dir: Path, annual: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (year, crop), group in annual.groupby(["year", "crop"]):
        five = group[group.resolution_m == 5000].iloc[0]
        ten = group[group.resolution_m == 10000].iloc[0]
        rows.append({
            "year": int(year), "crop": crop, "shared_factor_source": "2020 5km",
            "fixed_factor": five.fixed_2020_factor,
            "raw_5km_kg_ha": five.raw_apsim_yield_kg_ha, "raw_10km_kg_ha": ten.raw_apsim_yield_kg_ha,
            "raw_10km_minus_5km_kg_ha": ten.raw_apsim_yield_kg_ha - five.raw_apsim_yield_kg_ha,
            "raw_10km_minus_5km_percent": (ten.raw_apsim_yield_kg_ha / five.raw_apsim_yield_kg_ha - 1.0) * 100.0,
            "corrected_5km_kg_ha": five.fixed_corrected_yield_kg_ha,
            "corrected_10km_kg_ha": ten.fixed_corrected_yield_kg_ha,
            "corrected_10km_minus_5km_percent": (ten.fixed_corrected_yield_kg_ha / five.fixed_corrected_yield_kg_ha - 1.0) * 100.0,
        })
    result = pd.DataFrame(rows)
    result.to_csv(run_dir / "resolution_comparison.csv", index=False, encoding="utf-8-sig")
    return result


def figures(run_dir: Path, annual: pd.DataFrame, grid: pd.DataFrame, loo: pd.DataFrame, factor_uncertainty: pd.DataFrame) -> None:
    out = run_dir / "figures"
    out.mkdir()
    five = annual[annual.resolution_m == 5000].sort_values("year")
    labels = {"wheat": "Wheat", "maize": "Maize"}

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharex=True)
    for ax, (crop, group) in zip(axes, five.groupby("crop")):
        ax.plot(group.year, group.official_yield_kg_ha, "o-", label="Official")
        ax.plot(group.year, group.raw_apsim_yield_kg_ha, "o-", label="Raw APSIM")
        ax.plot(group.year, group.fixed_corrected_yield_kg_ha, "o-", label="2020 fixed factor")
        ax.set_title(labels[crop]); ax.set_xlabel("Statistical year"); ax.grid(alpha=.25)
    axes[0].set_ylabel("Yield (kg/ha)"); axes[0].legend(fontsize=8)
    fig.tight_layout(); fig.savefig(out / "01_multiyear_yield_series.png", dpi=220); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    for crop, group in five.groupby("crop"):
        ax.plot(group.year, group.implicit_factor, "o-", label=labels[crop])
    ax.axvline(2020, color="0.5", ls="--", lw=1); ax.set(xlabel="Statistical year", ylabel="Official / raw APSIM factor")
    ax.grid(alpha=.25); ax.legend(); fig.tight_layout(); fig.savefig(out / "02_annual_implicit_factors.png", dpi=220); plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    width = .34
    for ax, (crop, group) in zip(axes, five.groupby("crop")):
        positions = np.arange(len(group))
        ax.bar(positions - width / 2, group.raw_relative_bias_percent, width, label="Raw")
        ax.bar(positions + width / 2, group.fixed_relative_bias_percent, width, label="Fixed")
        ax.axhline(0, color="black", lw=.8); ax.set_xticks(positions, group.year.astype(str)); ax.set_title(labels[crop]); ax.grid(axis="y", alpha=.25)
    axes[0].set_ylabel("Relative bias (%)"); axes[0].legend(); fig.tight_layout(); fig.savefig(out / "03_relative_error_before_after.png", dpi=220); plt.close(fig)

    cv = loo[loo.method == "leave_one_year_out_zero_intercept"]
    fig, ax = plt.subplots(figsize=(5, 5))
    for crop, group in cv.groupby("crop"):
        ax.scatter(group.official_yield_kg_ha, group.predicted_yield_kg_ha, s=45, label=labels[crop])
        for _, row in group.iterrows(): ax.annotate(str(int(row.held_out_year)), (row.official_yield_kg_ha, row.predicted_yield_kg_ha), fontsize=7)
    limits = [min(cv.official_yield_kg_ha.min(), cv.predicted_yield_kg_ha.min()) * .95, max(cv.official_yield_kg_ha.max(), cv.predicted_yield_kg_ha.max()) * 1.05]
    ax.plot(limits, limits, "k--", lw=1); ax.set(xlim=limits, ylim=limits, xlabel="Official yield (kg/ha)", ylabel="LOYO prediction (kg/ha)")
    ax.legend(); ax.grid(alpha=.25); fig.tight_layout(); fig.savefig(out / "04_loyo_observed_vs_predicted.png", dpi=220); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4))
    methods = ["mean_implicit_factor", "zero_intercept_least_squares", "robust_huber_zero_intercept"]
    positions, labels_x = [], []
    for ci, crop in enumerate(["wheat", "maize"]):
        for mi, method in enumerate(methods):
            row = factor_uncertainty[(factor_uncertainty.crop == crop) & (factor_uncertainty.method == method)].iloc[0]
            pos = ci * 4 + mi
            ax.errorbar(pos, row.factor_median, yerr=[[row.factor_median-row.factor_95ci_lower], [row.factor_95ci_upper-row.factor_median]], fmt="o", capsize=4)
            positions.append(pos); labels_x.append(f"{labels[crop]}\n{['Mean','LS','Huber'][mi]}")
    ax.set_xticks(positions, labels_x, fontsize=8); ax.set_ylabel("Calibration factor (bootstrap 95% interval)"); ax.grid(axis="y", alpha=.25)
    fig.tight_layout(); fig.savefig(out / "05_factor_uncertainty.png", dpi=220); plt.close(fig)

    spatial = grid[(grid.resolution_m == 5000) & (grid.year == 2020)].copy()
    geometry = gpd.read_file(PILOT / "grid_resolution_experiment" / "grids" / "qihe_2020_rotation_5km.gpkg")
    id_candidates = [c for c in geometry.columns if c.lower() in {"grid_id", "id"}]
    if not id_candidates:
        raise KeyError(f"No grid id column in spatial grid: {list(geometry.columns)}")
    grid_id = id_candidates[0]
    fig, axes = plt.subplots(2, 2, figsize=(10, 8))
    for row_index, crop in enumerate(["wheat", "maize"]):
        crop_data = spatial[spatial.crop == crop]
        mapped = geometry.merge(crop_data, left_on=grid_id, right_on="grid_id", how="inner")
        vmin = min(mapped.raw_apsim_yield_kg_ha.min(), mapped.fixed_corrected_yield_kg_ha.min())
        vmax = max(mapped.raw_apsim_yield_kg_ha.max(), mapped.fixed_corrected_yield_kg_ha.max())
        for col_index, (column, title) in enumerate([("raw_apsim_yield_kg_ha", "Raw APSIM"), ("fixed_corrected_yield_kg_ha", "2020 fixed factor")]):
            mapped.plot(column=column, ax=axes[row_index, col_index], cmap="viridis", vmin=vmin, vmax=vmax, legend=True, legend_kwds={"shrink": .65})
            axes[row_index, col_index].set_title(f"{labels[crop]} 2020, 5 km: {title}"); axes[row_index, col_index].set_axis_off()
    fig.tight_layout(); fig.savefig(out / "06_5km_raw_and_corrected_spatial_yield.png", dpi=220); plt.close(fig)

    invariance_rows = []
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, crop in zip(axes, ["wheat", "maize"]):
        crop_data = spatial[spatial.crop == crop].copy()
        crop_data["difference_kg_ha"] = crop_data.fixed_corrected_yield_kg_ha - crop_data.raw_apsim_yield_kg_ha
        mapped = geometry.merge(crop_data, left_on=grid_id, right_on="grid_id", how="inner")
        mapped.plot(column="difference_kg_ha", ax=ax, cmap="coolwarm", legend=True, legend_kwds={"shrink": .7})
        ax.set_title(f"{labels[crop]} 2020, 5 km: corrected - raw"); ax.set_axis_off()
        weights = crop_data.rotation_area_ha.to_numpy()
        raw_values = crop_data.raw_apsim_yield_kg_ha.to_numpy()
        fixed_values = crop_data.fixed_corrected_yield_kg_ha.to_numpy()
        raw_mean = float(np.average(raw_values, weights=weights))
        fixed_mean = float(np.average(fixed_values, weights=weights))
        raw_cv = float(np.sqrt(np.average((raw_values - raw_mean) ** 2, weights=weights)) / raw_mean * 100.0)
        fixed_cv = float(np.sqrt(np.average((fixed_values - fixed_mean) ** 2, weights=weights)) / fixed_mean * 100.0)
        invariance_rows.append({
            "crop": crop, "year": 2020, "resolution_m": 5000,
            "spearman_rank_correlation_raw_vs_corrected": crop_data.raw_apsim_yield_kg_ha.corr(crop_data.fixed_corrected_yield_kg_ha, method="spearman"),
            "area_weighted_raw_spatial_cv_percent": raw_cv,
            "area_weighted_corrected_spatial_cv_percent": fixed_cv,
            "cv_difference_percentage_points": fixed_cv - raw_cv,
            "interpretation": "A positive uniform county factor preserves rank and spatial CV.",
        })
    fig.suptitle("County factor difference; calibration-year diagnostic, not spatial validation", fontsize=10)
    fig.tight_layout(); fig.savefig(out / "08_5km_corrected_minus_raw_difference.png", dpi=220); plt.close(fig)
    pd.DataFrame(invariance_rows).to_csv(run_dir / "spatial_factor_invariance.csv", index=False, encoding="utf-8-sig")

    spatial_all = grid[grid.resolution_m == 5000]
    summary = spatial_all.groupby(["crop", "grid_id"], as_index=False).agg(mean_yield_kg_ha=("raw_apsim_yield_kg_ha", "mean"), sd_yield_kg_ha=("raw_apsim_yield_kg_ha", "std"))
    summary["interannual_cv_percent"] = summary.sd_yield_kg_ha / summary.mean_yield_kg_ha * 100.0
    summary.to_csv(run_dir / "grid_multiyear_mean_and_cv.csv", index=False, encoding="utf-8-sig")
    fig, axes = plt.subplots(2, 2, figsize=(10, 8))
    for row_index, crop in enumerate(["wheat", "maize"]):
        mapped = geometry.merge(summary[summary.crop == crop], left_on=grid_id, right_on="grid_id", how="inner")
        for col_index, (column, title) in enumerate([("mean_yield_kg_ha", "2018-2020 mean"), ("interannual_cv_percent", "2018-2020 CV (%)")]):
            mapped.plot(column=column, ax=axes[row_index, col_index], cmap="viridis", legend=True, legend_kwds={"shrink": .65})
            axes[row_index, col_index].set_title(f"{labels[crop]} 5 km: {title}"); axes[row_index, col_index].set_axis_off()
    fig.tight_layout(); fig.savefig(out / "07_5km_multiyear_mean_and_cv.png", dpi=220); plt.close(fig)


def metadata(run_dir: Path, run_id: str, started: datetime, factors: dict[str, float], manifest: pd.DataFrame, bootstrap_samples: int) -> None:
    completed = datetime.now().astimezone()
    official_years = sorted(pd.read_csv(STATS).statistical_year.astype(int).unique().tolist())
    simulated_years = sorted(pd.read_csv(run_dir / "annual_raw_and_corrected_yields.csv").year.astype(int).unique().tolist())
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        worktree = subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        head, branch, worktree = None, None, []
    content = {
        "run_id": run_id,
        "analysis_started": started.isoformat(),
        "analysis_completed": completed.isoformat(),
        "analysis_runtime_seconds": (completed - started).total_seconds(),
        "purpose": "Freeze 2018-2020 baseline and evaluate transfer, cross-validation, resolution, and uncertainty of the 2020 fixed factor.",
        "git": {"commit": head, "branch": branch, "worktree_status_at_archive": worktree},
        "apsim": {
            "label": "APSIM Classic 7.10 r4221 (inferred from installation directory; executable has no version-report option)",
            "executable": str(APSIM_EXE),
            "sha256": sha256(APSIM_EXE),
        },
        "fixed_factor_definition": "2020 official county yield divided by 2020 raw 5 km APSIM area-weighted yield",
        "fixed_factors": factors,
        "calibration_year": 2020,
        "independent_fixed_factor_validation_years": [2018, 2019],
        "available_official_statistics_years": official_years,
        "available_simulation_years": simulated_years,
        "official_statistics_without_matched_simulation": sorted(set(official_years) - set(simulated_years)),
        "weather": "AgERA5 v2.0 daily, 0.1 degree, 2017-2020; 2017 is spin-up/antecedent-state weather for the validated seasons.",
        "soil": "HWSD v2 mapping-unit subunits; no parameter changes in this analysis.",
        "rotation_mask": "2020 wheat-maize rotation mask held fixed for all simulated years.",
        "management": "Uniform ordinary-farmer scenario held fixed; it is a model assumption, not observed annual management.",
        "baseline_runs": {"5000m": run_status(5000), "10000m": run_status(10000)},
        "bootstrap": {"samples": bootstrap_samples, "seed": BOOTSTRAP_SEED, "sampling_unit": "year", "warning": "Only three matched years; intervals are exploratory."},
        "reproduction_commands": [
            "python scripts/apsim_inputs/build_qihe_multiyear_official_statistics.py",
            f"python scripts/apsim_inputs/analyze_qihe_fixed_factor_multiyear.py --run-id {run_id} --bootstrap-samples {bootstrap_samples}",
        ],
        "inputs": {"statistics": relative(STATS), "source_validation": relative(SOURCE_VALIDATION)},
        "output": relative(run_dir),
        "manifest_records": len(manifest),
        "warnings": [
            "The zero 2020 fixed-factor residual is mathematical calibration, not validation.",
            "A fixed multiplicative factor can correct proportional scale but cannot correct erroneous interannual response.",
            "LOYO and bootstrap results with n=3 years are exploratory.",
            "Pooled all-year coefficient performance is descriptive in-sample performance, not independent validation.",
            "No township, field, or plot yields are available; maps show simulated spatial heterogeneity, not completed spatial validation.",
        ],
    }
    (run_dir / "run_metadata.json").write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_outputs(run_dir: Path, annual: pd.DataFrame, factors: dict[str, float]) -> None:
    required = [
        "annual_raw_and_corrected_yields.csv", "annual_implicit_factors.csv",
        "fixed_2020_factor_metrics.csv", "leave_one_year_out_predictions.csv",
        "leave_one_year_out_metrics.csv", "factor_uncertainty.csv",
        "resolution_comparison.csv", "spatial_factor_invariance.csv",
    ]
    checks = {
        "required_tables_exist": all((run_dir / name).is_file() for name in required),
        "annual_rows_equal_12": len(annual) == 12,
        "annual_years_are_2018_2020": set(annual.year) == {2018, 2019, 2020},
        "resolutions_are_5km_10km": set(annual.resolution_m) == {5000, 10000},
        "official_yields_complete": bool(annual.official_yield_kg_ha.notna().all()),
        "raw_yields_positive": bool((annual.raw_apsim_yield_kg_ha > 0).all()),
        "fixed_factors_match_definition": bool(
            np.isclose(factors["wheat"], 1.0038739284437488)
            and np.isclose(factors["maize"], 0.6040688344680943)
        ),
        "calibration_residuals_exact_zero": bool(
            (annual[(annual.resolution_m == 5000) & (annual.year == 2020)].fixed_bias_kg_ha == 0.0).all()
        ),
        "baseline_5km_all_cases_success": run_status(5000)["failed_cases"] == 0,
        "baseline_10km_all_cases_success": run_status(10000)["failed_cases"] == 0,
        "eight_figures_exist": len(list((run_dir / "figures").glob("*.png"))) == 8,
    }
    invariance = pd.read_csv(run_dir / "spatial_factor_invariance.csv")
    checks["uniform_factor_preserves_spatial_rank"] = bool(np.allclose(invariance.spearman_rank_correlation_raw_vs_corrected, 1.0))
    checks["uniform_factor_preserves_spatial_cv"] = bool(np.allclose(invariance.cv_difference_percentage_points, 0.0, atol=1e-12))
    report = {
        "status": "pass" if all(checks.values()) else "fail",
        "checked_at": datetime.now().astimezone().isoformat(),
        "checks": checks,
    }
    (run_dir / "output_validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["status"] != "pass":
        raise RuntimeError(f"Output validation failed: {checks}")


def main(args: argparse.Namespace) -> None:
    started = datetime.now().astimezone()
    run_id = args.run_id or started.strftime("qihe_fixed_factor_%Y%m%dT%H%M%S%z")
    run_dir = OUTPUT_ROOT / run_id
    if run_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing run directory: {run_dir}")
    run_dir.mkdir(parents=True)
    for path in [STATS, SOURCE_VALIDATION / "annual_yield_comparison_5km_10km.csv", SOURCE_VALIDATION / "grid_cell_annual_yields.csv", APSIM_EXE]:
        if not path.exists():
            raise FileNotFoundError(path)

    archive_dir = run_dir / "baseline_archive"
    archive_dir.mkdir()
    for name in ["annual_yield_comparison_5km_10km.csv", "grid_cell_annual_yields.csv", "cross_year_validation_metrics.csv", "resolution_10km_vs_5km.csv", "validation_metadata.json"]:
        shutil.copy2(SOURCE_VALIDATION / name, archive_dir / name)

    annual, grid, factors = prepare_annual(run_dir)
    analyse_factors(run_dir, annual)
    fixed_metrics(run_dir, annual)
    loo, _ = cross_validation(run_dir, annual, factors)
    factor_uncertainty, _ = uncertainty(run_dir, annual, args.bootstrap_samples)
    diagnostics(run_dir, annual)
    save_resolution(run_dir, annual)
    figures(run_dir, annual, grid, loo, factor_uncertainty)
    validate_outputs(run_dir, annual, factors)
    manifest = archive_manifest(run_dir)
    metadata(run_dir, run_id, started, factors, manifest, args.bootstrap_samples)
    print(run_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", help="New output directory name; existing directories are never overwritten")
    parser.add_argument("--bootstrap-samples", type=int, default=10000)
    return parser.parse_args()


if __name__ == "__main__":
    main(parse_args())
