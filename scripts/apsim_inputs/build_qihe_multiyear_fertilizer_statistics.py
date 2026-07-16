"""Build traceable Qihe fertilizer statistics and APSIM nitrogen constraints.

The yearbook table is county-wide and covers all agricultural uses.  It does
not identify crops, application dates, or the N share of compound fertilizer.
Consequently, the derived file is a mass-balance diagnostic, not a crop-level
fertilizer prescription.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CALIBRATION = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020" / "calibration"
OFFICIAL_OUT = CALIBRATION / "qihe_multiyear_official_fertilizer_statistics.csv"
DIAGNOSTIC_OUT = CALIBRATION / "qihe_fertilizer_npk_constraint_diagnostics.csv"
METADATA_OUT = CALIBRATION / "qihe_fertilizer_statistics_metadata.json"
POLICY = ROOT / "configs" / "spatial" / "qihe_fertilizer_allocation_policy.json"
CROP_STATS = CALIBRATION / "qihe_multiyear_official_crop_statistics.csv"

# Source columns, in order: physical total/N/P/K/compound and nutrient-equivalent
# total/N/P/K/compound, all in tonnes.  Crop area is in 10,000 mu.
ROWS = [
    (2015, 2016, "6-9", 151, 139, 262.35, 183548, 79890, 51747, 11608, 40302, 69577, 40297, 7520, 5808, 15952),
    (2016, 2017, "6-9", 155, 143, 267.55, 171285, 70915, 45724, 8715, 45932, 58208, 30882, 7496, 3975, 15855),
    (2017, 2018, "6-8", 148, 136, 261.17, 163066, 67463, 42363, 8415, 44825, 56020, 29934, 6871, 3856, 15359),
    (2018, 2019, "6-7", 114, 102, 261.00, 159886, 58345, 34796, 6679, 60067, 51156, 22786, 6187, 2503, 19681),
    (2019, 2020, "6-6", 115, 103, 256.51, 155083, 55761, 32844, 5500, 60978, 49613, 20220, 5880, 2367, 21147),
    (2020, 2021, "6-6", 101, 89, 256.20, 153139, 55370, 32547, 5434, 59787, 49149, 20125, 5831, 2349, 20844),
    (2021, 2022, "6-6", 99, 87, 258.36, 149668, 41588, 17396, 5899, 84785, 48654, 13868, 3738, 2236, 28812),
    (2022, 2023, "6-6", 101, 85, 258.55, 147829, 40955, 16871, 5722, 84281, 48122, 13681, 3676, 2167, 28598),
    (2023, 2024, "6-6", 97, 85, 259.07, 145988, 40401, 16532, 5626, 83428, 47545, 13426, 3568, 2114, 28438),
]

URLS = {
    2016: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c97852113/part/100748230.pdf",
    2017: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c29924076/part/40739760.pdf",
    2018: "http://www.dezhou.gov.cn/pdf/tjnj2018.pdf",
    2019: "http://www.dezhou.gov.cn/pdf/2019tjnj.pdf",
    2020: "http://www.dezhou.gov.cn/pdf/tjnj2020.pdf",
    2021: "http://www.dezhou.gov.cn/pdf/tjnj2021.pdf",
    2022: "http://www.dezhou.gov.cn/pdf/tjnj-2022.pdf",
    2023: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c87039976/part/87039981.pdf",
    2024: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c92860358/part/92860363.pdf",
}


def read_rotation_areas() -> dict[int, float]:
    areas: dict[int, float] = {}
    with CROP_STATS.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            year = int(row["statistical_year"])
            areas[year] = areas.get(year, 0.0) + float(row["area_ha"])
    return areas


def load_policy() -> dict:
    with POLICY.open(encoding="utf-8") as handle:
        policy = json.load(handle)
    compositions = policy["compound_nutrient_composition_scenarios"]
    if not compositions:
        raise ValueError("At least one compound nutrient composition scenario is required")
    for item in compositions:
        fractions = [float(item[key]) for key in ("n_fraction", "p2o5_fraction", "k2o_fraction")]
        if any(not 0 <= value <= 1 for value in fractions) or abs(sum(fractions) - 1.0) > 1e-9:
            raise ValueError(f"Compound nutrient fractions must be in [0, 1] and sum to 1: {item}")
    return policy


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    CALIBRATION.mkdir(parents=True, exist_ok=True)
    rotation_areas = read_rotation_areas()
    policy = load_policy()
    source_index = "https://dztj.dezhou.gov.cn/n3100530/n38260319/index.html"
    official_fields = [
        "statistical_year", "publication_year", "county", "source_table", "pdf_page", "printed_page",
        "physical_total_t", "physical_n_fertilizer_t", "physical_p_fertilizer_t", "physical_k_fertilizer_t",
        "physical_compound_fertilizer_t", "nutrient_total_t", "nutrient_straight_n_t", "nutrient_p_t",
        "nutrient_k_t", "nutrient_compound_total_t", "component_sum_t", "component_sum_difference_t",
        "total_crop_sown_area_10k_mu", "total_crop_sown_area_ha", "source_yearbook", "pdf_path",
        "download_url", "source_index_url", "verification_method", "scope_note", "quality_note",
    ]
    with OFFICIAL_OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=official_fields)
        writer.writeheader()
        for row in ROWS:
            (year, pub, table, pdf_page, printed_page, crop_area, physical_total, physical_n, physical_p,
             physical_k, physical_compound, nutrient_total, nutrient_n, nutrient_p, nutrient_k,
             nutrient_compound) = row
            component_sum = nutrient_n + nutrient_p + nutrient_k + nutrient_compound
            writer.writerow({
                "statistical_year": year, "publication_year": pub, "county": "Qihe County",
                "source_table": table, "pdf_page": pdf_page, "printed_page": printed_page,
                "physical_total_t": physical_total, "physical_n_fertilizer_t": physical_n,
                "physical_p_fertilizer_t": physical_p, "physical_k_fertilizer_t": physical_k,
                "physical_compound_fertilizer_t": physical_compound, "nutrient_total_t": nutrient_total,
                "nutrient_straight_n_t": nutrient_n, "nutrient_p_t": nutrient_p, "nutrient_k_t": nutrient_k,
                "nutrient_compound_total_t": nutrient_compound, "component_sum_t": component_sum,
                "component_sum_difference_t": component_sum - nutrient_total,
                "total_crop_sown_area_10k_mu": crop_area,
                "total_crop_sown_area_ha": crop_area * 10000.0 / 15.0,
                "source_yearbook": f"Dezhou Statistical Yearbook {pub}",
                "pdf_path": f"data/raw/shandong_public/statistics/dezhou_yearbook_{pub}.pdf",
                "download_url": URLS[pub], "source_index_url": source_index,
                "verification_method": "visual_review_of_official_county_yearbook_table",
                "scope_note": "County total for all agricultural uses; not crop-specific and not an application schedule.",
                "quality_note": "Compound nutrient-equivalent amount is total active nutrients; its nitrogen share is not reported.",
            })

    diagnostic_fields = [
        "statistical_year", "compound_composition_scenario", "compound_n_fraction",
        "compound_p2o5_fraction", "compound_k2o_fraction", "allocation_method",
        "rotation_allocation_share", "area_proportional_rotation_share", "nutrient_straight_n_t",
        "nutrient_straight_p2o5_t", "nutrient_straight_k2o_t", "nutrient_compound_total_t",
        "estimated_county_n_t", "estimated_county_p2o5_t", "estimated_county_k2o_t",
        "estimated_county_npk_total_t", "reported_nutrient_total_t", "total_crop_sown_area_ha",
        "wheat_maize_sown_area_ha", "county_n_kg_per_total_crop_ha",
        "county_p2o5_kg_per_total_crop_ha", "county_k2o_kg_per_total_crop_ha",
        "allocated_rotation_n_kg_per_crop_ha", "allocated_rotation_p2o5_kg_per_crop_ha",
        "allocated_rotation_k2o_kg_per_crop_ha", "reference_test_n_kg_per_crop_ha",
        "n_difference_from_reference_test_kg_ha", "interpretation",
    ]
    reference_rate = float(policy["reference_test_scenario_n_kg_per_crop_ha"])
    sensitivity_shares = [float(x) for x in policy["additional_rotation_allocation_share_sensitivity"]]
    with DIAGNOSTIC_OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=diagnostic_fields)
        writer.writeheader()
        for row in ROWS:
            year, _, _, _, _, crop_area, *values = row
            nutrient_total = float(values[-5])
            nutrient_n, nutrient_p, nutrient_k, nutrient_compound = map(float, values[-4:])
            total_crop_ha = crop_area * 10000.0 / 15.0
            rotation_area_ha = rotation_areas[year]
            area_share = rotation_area_ha / total_crop_ha
            allocation_cases = [("area_proportional_reference", area_share)] + [
                ("fixed_share_sensitivity", share) for share in sensitivity_shares
            ]
            for composition in policy["compound_nutrient_composition_scenarios"]:
                n_fraction = float(composition["n_fraction"])
                p_fraction = float(composition["p2o5_fraction"])
                k_fraction = float(composition["k2o_fraction"])
                estimated_n_t = nutrient_n + nutrient_compound * n_fraction
                estimated_p_t = nutrient_p + nutrient_compound * p_fraction
                estimated_k_t = nutrient_k + nutrient_compound * k_fraction
                for allocation_method, allocation_share in allocation_cases:
                    n_rate = estimated_n_t * 1000.0 * allocation_share / rotation_area_ha
                    p_rate = estimated_p_t * 1000.0 * allocation_share / rotation_area_ha
                    k_rate = estimated_k_t * 1000.0 * allocation_share / rotation_area_ha
                    writer.writerow({
                        "statistical_year": year, "compound_composition_scenario": composition["id"],
                        "compound_n_fraction": n_fraction, "compound_p2o5_fraction": p_fraction,
                        "compound_k2o_fraction": k_fraction, "allocation_method": allocation_method,
                        "rotation_allocation_share": allocation_share,
                        "area_proportional_rotation_share": area_share, "nutrient_straight_n_t": nutrient_n,
                        "nutrient_straight_p2o5_t": nutrient_p, "nutrient_straight_k2o_t": nutrient_k,
                        "nutrient_compound_total_t": nutrient_compound, "estimated_county_n_t": estimated_n_t,
                        "estimated_county_p2o5_t": estimated_p_t, "estimated_county_k2o_t": estimated_k_t,
                        "estimated_county_npk_total_t": estimated_n_t + estimated_p_t + estimated_k_t,
                        "reported_nutrient_total_t": nutrient_total,
                        "total_crop_sown_area_ha": total_crop_ha, "wheat_maize_sown_area_ha": rotation_area_ha,
                        "county_n_kg_per_total_crop_ha": estimated_n_t * 1000.0 / total_crop_ha,
                        "county_p2o5_kg_per_total_crop_ha": estimated_p_t * 1000.0 / total_crop_ha,
                        "county_k2o_kg_per_total_crop_ha": estimated_k_t * 1000.0 / total_crop_ha,
                        "allocated_rotation_n_kg_per_crop_ha": n_rate,
                        "allocated_rotation_p2o5_kg_per_crop_ha": p_rate,
                        "allocated_rotation_k2o_kg_per_crop_ha": k_rate,
                        "reference_test_n_kg_per_crop_ha": reference_rate,
                        "n_difference_from_reference_test_kg_ha": n_rate - reference_rate,
                        "interpretation": "Exploratory N-P2O5-K2O allocation scenario; not observed crop fertilizer rates. The 180 N value is comparison-only.",
                    })

    input_pdfs = []
    for publication_year in sorted(URLS):
        path = ROOT / "data" / "raw" / "shandong_public" / "statistics" / f"dezhou_yearbook_{publication_year}.pdf"
        input_pdfs.append({
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(path),
            "size_bytes": path.stat().st_size,
        })
    metadata = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "reproduction_command": "python scripts/apsim_inputs/build_qihe_multiyear_fertilizer_statistics.py",
        "statistical_years": [row[0] for row in ROWS],
        "official_row_count": len(ROWS),
        "diagnostic_row_count": len(ROWS) * len(policy["compound_nutrient_composition_scenarios"]) * (1 + len(policy["additional_rotation_allocation_share_sensitivity"])),
        "inputs": input_pdfs + [
            {"path": str(CROP_STATS.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(CROP_STATS)},
            {"path": str(POLICY.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(POLICY)},
        ],
        "outputs": [
            {"path": str(OFFICIAL_OUT.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(OFFICIAL_OUT)},
            {"path": str(DIAGNOSTIC_OUT.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(DIAGNOSTIC_OUT)},
        ],
        "scope_warning": "County-wide fertilizer totals are constraints, not crop-specific APSIM management observations. P2O5 and K2O are diagnostics only because the current model is not configured to simulate P or K limitation.",
    }
    with METADATA_OUT.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(OFFICIAL_OUT)
    print(DIAGNOSTIC_OUT)
    print(METADATA_OUT)


if __name__ == "__main__":
    main()
