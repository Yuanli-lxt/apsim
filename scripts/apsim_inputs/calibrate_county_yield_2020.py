"""Calibrate Qihe 2020 APSIM yields to official county-average statistics.

This is a transparent statistical post-calibration.  It deliberately does not
change cultivar coefficients: one county-year target cannot identify cultivar,
management, and observation-basis effects separately.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = (
    ROOT
    / "outputs"
    / "spatial"
    / "county_pilot_2020"
    / "corrected_baseline"
    / "ordinary_farmer"
)
OUT_DIR = BASELINE_DIR / "absolute_yield_calibration"
DATA_DIR = (
    ROOT
    / "data"
    / "processed"
    / "spatial"
    / "county_pilot_2020"
    / "calibration"
)

SOURCE_PAGE = "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c68576092/content.html"
SOURCE_PDF = "http://www.dezhou.gov.cn/pdf/tjnj2021.pdf"

OFFICIAL = {
    "wheat": {
        "crop_zh": "小麦",
        "area_10k_mu": 114.30,
        "production_10k_t": 54.32,
        "yield_kg_mu": 475.25,
        "pdf_page": 102,
        "printed_page": 90,
    },
    "maize": {
        "crop_zh": "玉米",
        "area_10k_mu": 111.82,
        "production_10k_t": 56.43,
        "yield_kg_mu": 504.64,
        "pdf_page": 103,
        "printed_page": 91,
    },
}


def enrich_official(row: dict) -> dict:
    result = dict(row)
    result["area_ha"] = row["area_10k_mu"] * 10000.0 / 15.0
    result["production_t"] = row["production_10k_t"] * 10000.0
    result["yield_kg_ha"] = row["yield_kg_mu"] * 15.0
    result["source_page"] = SOURCE_PAGE
    result["source_pdf"] = SOURCE_PDF
    result["statistical_year"] = 2020
    result["yearbook"] = "Dezhou Statistical Yearbook 2021"
    return result


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    official = {crop: enrich_official(values) for crop, values in OFFICIAL.items()}
    official_csv = DATA_DIR / "qihe_2020_official_crop_statistics.csv"
    fieldnames = [
        "crop",
        "crop_zh",
        "statistical_year",
        "area_10k_mu",
        "area_ha",
        "production_10k_t",
        "production_t",
        "yield_kg_mu",
        "yield_kg_ha",
        "yearbook",
        "pdf_page",
        "printed_page",
        "source_page",
        "source_pdf",
    ]
    with official_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for crop, values in official.items():
            writer.writerow({"crop": crop, **values})

    with (BASELINE_DIR / "baseline_summary.json").open(encoding="utf-8") as handle:
        baseline = json.load(handle)

    calibration = {}
    comparison_rows = []
    for crop in ("wheat", "maize"):
        simulated = baseline[f"{crop}_area_weighted_yield_kg_ha"]
        observed = official[crop]["yield_kg_ha"]
        factor = observed / simulated
        bias = simulated - observed
        calibrated = simulated * factor
        official_area = official[crop]["area_ha"]
        raw_scaled_production = simulated * official_area / 1000.0
        calibrated_scaled_production = calibrated * official_area / 1000.0
        values = {
            "raw_apsim_yield_kg_ha": simulated,
            "official_yield_kg_ha": observed,
            "raw_bias_kg_ha": bias,
            "raw_relative_bias_percent": bias / observed * 100.0,
            "statistical_correction_factor": factor,
            "calibrated_yield_kg_ha": calibrated,
            "calibrated_bias_kg_ha": calibrated - observed,
            "official_crop_area_ha": official_area,
            "official_production_t": official[crop]["production_t"],
            "raw_yield_scaled_to_official_area_t": raw_scaled_production,
            "calibrated_yield_scaled_to_official_area_t": calibrated_scaled_production,
            "reported_area_times_reported_yield_t": observed * official_area / 1000.0,
        }
        calibration[crop] = values
        comparison_rows.append({"crop": crop, "crop_zh": official[crop]["crop_zh"], **values})

    comparison_csv = OUT_DIR / "county_yield_calibration_summary.csv"
    with comparison_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparison_rows[0]))
        writer.writeheader()
        writer.writerows(comparison_rows)

    calibrated_subunits = OUT_DIR / "soil_subunit_results_statistically_calibrated.csv"
    input_subunits = BASELINE_DIR / "soil_subunit_results.csv"
    with input_subunits.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        extra = [
            "wheat_statistical_correction_factor",
            "maize_statistical_correction_factor",
            "wheat_calibrated_yield_kg_ha",
            "maize_calibrated_yield_kg_ha",
            "wheat_calibrated_production_t",
            "maize_calibrated_production_t",
        ]
        with calibrated_subunits.open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=(reader.fieldnames or []) + extra)
            writer.writeheader()
            for row in reader:
                area = float(row["soil_rotation_area_ha"])
                wheat_yield = float(row["wheat_yield_kg_ha"])
                maize_yield = float(row["maize_yield_kg_ha"])
                wheat_factor = calibration["wheat"]["statistical_correction_factor"]
                maize_factor = calibration["maize"]["statistical_correction_factor"]
                wheat_calibrated = wheat_yield * wheat_factor
                maize_calibrated = maize_yield * maize_factor
                row.update(
                    {
                        "wheat_statistical_correction_factor": wheat_factor,
                        "maize_statistical_correction_factor": maize_factor,
                        "wheat_calibrated_yield_kg_ha": wheat_calibrated,
                        "maize_calibrated_yield_kg_ha": maize_calibrated,
                        "wheat_calibrated_production_t": wheat_calibrated * area / 1000.0,
                        "maize_calibrated_production_t": maize_calibrated * area / 1000.0,
                    }
                )
                writer.writerow(row)

    report = {
        "method": "multiplicative county-year statistical post-calibration",
        "scope": "Qihe County, 2020, ordinary_farmer scenario",
        "warning": (
            "Calibration and evaluation use the same county-year observations; "
            "these residuals are not independent validation metrics."
        ),
        "cultivar_parameters_modified": False,
        "baseline_rotation_area_ha": baseline["rotation_area_ha"],
        "official_statistics_file": str(official_csv.relative_to(ROOT)),
        "calibration": calibration,
    }
    with (OUT_DIR / "county_yield_calibration_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
