"""Compare all 12 Qihe N-management/initial-N combinations with official yields."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "outputs" / "spatial" / "county_pilot_2020" / "warmup_sensitivity" / "qihe_warmup_10km_full_2010_2023_20260716_v1"
OFFICIAL = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020" / "calibration" / "qihe_multiyear_official_crop_statistics.csv"
ANNUAL_OUT = RUN / "annual_n_scenarios_vs_official.csv"
METRICS_OUT = RUN / "n_scenario_official_metrics.csv"
META_OUT = RUN / "n_scenario_official_comparison_metadata.json"


def main() -> None:
    simulated = pd.read_csv(RUN / "annual_county_yield_and_mineral_n.csv").query("2015 <= year <= 2023").copy()
    official = pd.read_csv(OFFICIAL)[["statistical_year", "crop", "official_yield_kg_ha"]].rename(
        columns={"statistical_year": "year"}
    )
    rows = []
    for crop, column in (("wheat", "wheat_yield_kg_ha"), ("maize", "maize_yield_kg_ha")):
        frame = simulated[["management_scenario", "initial_n_multiplier", "year", column]].rename(
            columns={column: "raw_apsim_yield_kg_ha"}
        )
        frame["crop"] = crop
        frame = frame.merge(official.query("crop == @crop"), on=["year", "crop"], how="left", validate="many_to_one")
        rows.append(frame)
    annual = pd.concat(rows, ignore_index=True)
    annual["bias_kg_ha"] = annual.raw_apsim_yield_kg_ha - annual.official_yield_kg_ha
    annual["relative_bias_percent"] = annual.bias_kg_ha / annual.official_yield_kg_ha * 100
    annual["absolute_error_kg_ha"] = annual.bias_kg_ha.abs()
    annual["absolute_percentage_error"] = annual.relative_bias_percent.abs()
    annual["comparison_status"] = "descriptive_external_comparison_not_independent_validation"
    annual = annual.sort_values(["crop", "management_scenario", "initial_n_multiplier", "year"])
    if len(annual) != 12 * 9 * 2 or annual.official_yield_kg_ha.isna().any():
        raise RuntimeError("Expected 216 complete scenario-crop-year comparisons")
    annual.to_csv(ANNUAL_OUT, index=False, encoding="utf-8-sig")

    metrics = []
    for keys, group in annual.groupby(["crop", "management_scenario", "initial_n_multiplier"], sort=True):
        crop, scenario, multiplier = keys
        error = group.bias_kg_ha.to_numpy()
        correlation = group[["raw_apsim_yield_kg_ha", "official_yield_kg_ha"]].corr().iloc[0, 1]
        metrics.append({
            "crop": crop,
            "management_scenario": scenario,
            "initial_n_multiplier": float(multiplier),
            "years": int(len(group)),
            "mean_official_yield_kg_ha": float(group.official_yield_kg_ha.mean()),
            "mean_raw_apsim_yield_kg_ha": float(group.raw_apsim_yield_kg_ha.mean()),
            "mean_bias_kg_ha": float(error.mean()),
            "mean_relative_bias_percent": float(group.relative_bias_percent.mean()),
            "mae_kg_ha": float(np.abs(error).mean()),
            "rmse_kg_ha": float(np.sqrt(np.mean(error ** 2))),
            "mape_percent": float(group.absolute_percentage_error.mean()),
            "pearson_r": float(correlation),
            "comparison_status": "descriptive_external_comparison_not_independent_validation",
        })
    metric_frame = pd.DataFrame(metrics).sort_values(["crop", "mape_percent", "rmse_kg_ha"])
    metric_frame["rank_by_mape_within_crop"] = metric_frame.groupby("crop").mape_percent.rank(method="min").astype(int)
    metric_frame.to_csv(METRICS_OUT, index=False, encoding="utf-8-sig")
    metadata = {
        "years": list(range(2015, 2024)),
        "official_source": str(OFFICIAL.relative_to(ROOT)).replace("\\", "/"),
        "simulated_source": str((RUN / "annual_county_yield_and_mineral_n.csv").relative_to(ROOT)).replace("\\", "/"),
        "combinations": 12,
        "crops": ["wheat", "maize"],
        "rows": len(annual),
        "metrics_rows": len(metric_frame),
        "calibration_rule": "No coefficient was estimated or re-estimated for any N scenario.",
        "interpretation": "Descriptive comparison against county official yields; scenario ranking is in-sample diagnostic, not independent validation.",
        "limitations": [
            "The 2020 crop mask is held fixed across years.",
            "County fertilizer statistics are not crop-specific field observations.",
            "Management other than annual N remains spatially and temporally uniform.",
            "Raw APSIM yields are compared; the old 2020 fixed yield factors are not transferred to the altered management experiments.",
        ],
    }
    META_OUT.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(metric_frame.to_string(index=False))


if __name__ == "__main__":
    main()
