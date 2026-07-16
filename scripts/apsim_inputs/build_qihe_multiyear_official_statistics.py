"""Build the source-traceable Qihe official crop statistics available locally.

Only values transcribed from verified county rows in official Dezhou statistical
yearbooks are included. Missing target years are written to a separate gap table;
they are never represented by interpolated or proxy yields.
"""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CALIBRATION = (
    ROOT / "data" / "processed" / "spatial" / "county_pilot_2020" / "calibration"
)
OUT = CALIBRATION / "qihe_multiyear_official_crop_statistics.csv"
GAPS = CALIBRATION / "qihe_multiyear_statistics_gaps.csv"

# Source units: 10,000 mu, 10,000 t, kg/mu. Statistical year and publication
# year are deliberately separate fields.
ROWS = [
    (2015, "wheat", "小麦", 114.79, 64.89, 565.30, 2016, "6-10 continuation 1", 152, 140),
    (2015, "maize", "玉米", 108.77, 68.12, 626.30, 2016, "6-10 continuation 2", 152, 140),
    (2016, "wheat", "小麦", 115.38, 65.96, 571.70, 2017, "6-10 continuation 1", 156, 144),
    (2016, "maize", "玉米", 115.52, 72.45, 627.20, 2017, "6-10 continuation 2", 156, 144),
    (2017, "wheat", "小麦", 116.02, 53.11, 457.80, 2018, "6-9", 149, 137),
    (2017, "maize", "玉米", 112.38, 54.94, 488.86, 2018, "6-9 continuation 2", 150, 138),
    (2018, "wheat", "小麦", 115.45, 52.19, 452.08, 2019, "6-8", 114, 102),
    (2018, "maize", "玉米", 113.66, 55.02, 484.04, 2019, "6-8 continuation 1", 115, 103),
    (2019, "wheat", "小麦", 115.21, 53.12, 461.10, 2020, "6-7", 116, 104),
    (2019, "maize", "玉米", 114.49, 57.50, 502.22, 2020, "6-7 continuation 1", 117, 105),
    (2020, "wheat", "小麦", 114.30, 54.32, 475.25, 2021, "6-7", 102, 90),
    (2020, "maize", "玉米", 111.82, 56.43, 504.64, 2021, "6-7 continuation", 103, 91),
    (2021, "wheat", "小麦", 115.12, 54.95, 477.30, 2022, "6-7 continuation 1", 100, 88),
    (2021, "maize", "玉米", 111.57, 56.16, 503.34, 2022, "6-7 continuation 2", 101, 89),
    (2022, "wheat", "小麦", 115.27, 55.15, 478.42, 2023, "6-7 continuation 1", 102, 86),
    (2022, "maize", "玉米", 110.17, 57.01, 517.50, 2023, "6-7 continuation 2", 103, 87),
    (2023, "wheat", "小麦", 115.39, 55.64, 482.18, 2024, "6-7 continuation 1", 98, 86),
    (2023, "maize", "玉米", 110.27, 58.42, 529.73, 2024, "6-7 continuation 2", 99, 87),
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

SOURCE_PAGES = {
    2016: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c97852113/content.html",
    2017: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c29924076/content.html",
    2018: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c96128415/content.html",
    2019: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c53173660/content.html",
    2020: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c60991754/content.html",
    2021: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c68576092/content.html",
    2022: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c79459606/content.html",
    2023: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c87039976/content.html",
    2024: "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c92860358/content.html",
}

VERIFICATION_METHODS = {
    2016: "visual_review_of_official_pdf_table_page",
    2017: "visual_review_of_official_pdf_table_page_due_to_broken_text_encoding",
    2018: "extractable_text_and_visual_table_review",
    2019: "extractable_text_and_visual_table_review",
    2020: "extractable_text_and_visual_table_review",
    2021: "extractable_text_and_visual_table_review",
    2022: "visual_review_of_scanned_official_pdf_table_page",
    2023: "extractable_text_and_visual_table_review",
    2024: "extractable_text_and_visual_table_review",
}

GAP_ROWS: list[tuple[int, str, str]] = []


def main() -> None:
    CALIBRATION.mkdir(parents=True, exist_ok=True)
    fields = [
        "statistical_year", "publication_year", "crop", "crop_zh",
        "area_10k_mu", "area_ha", "production_10k_t", "production_t",
        "official_yield_kg_mu", "official_yield_kg_ha",
        "production_implied_by_area_yield_t", "rounding_difference_t",
        "rounding_difference_percent", "source_yearbook", "source_table",
        "pdf_page", "printed_page", "pdf_path", "download_url",
        "source_page_url", "verification_method", "data_quality", "quality_note",
    ]
    with OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for year, crop, crop_zh, area, production, crop_yield, pub_year, table, pdf_page, printed_page in ROWS:
            area_ha = area * 10000.0 / 15.0
            production_t = production * 10000.0
            implied_t = area * 10000.0 * crop_yield / 1000.0
            difference_t = implied_t - production_t
            if year in (2015, 2016):
                quality_note = (
                    "Official contemporaneous county-yearbook value. Potential series break: the 2018 yearbook states that "
                    "2016-2017 grain data were revised using the Third National Agricultural Census; confirm retrospective "
                    "county revisions before pooled calibration. Area, production and yield rounding discrepancy is quantified."
                )
            elif year == 2017:
                quality_note = (
                    "The 2018 yearbook states that 2016-2017 grain data were revised using the Third National Agricultural "
                    "Census. Area, production and yield rounding discrepancy is quantified."
                )
            else:
                quality_note = (
                    "Area, production and yield are independently rounded in the source table; discrepancies are retained "
                    "and quantified."
                )
            writer.writerow({
                "statistical_year": year,
                "publication_year": pub_year,
                "crop": crop,
                "crop_zh": crop_zh,
                "area_10k_mu": area,
                "area_ha": area_ha,
                "production_10k_t": production,
                "production_t": production_t,
                "official_yield_kg_mu": crop_yield,
                "official_yield_kg_ha": crop_yield * 15.0,
                "production_implied_by_area_yield_t": implied_t,
                "rounding_difference_t": difference_t,
                "rounding_difference_percent": difference_t / production_t * 100.0,
                "source_yearbook": f"Dezhou Statistical Yearbook {pub_year}",
                "source_table": table,
                "pdf_page": pdf_page,
                "printed_page": printed_page,
                "pdf_path": f"data/raw/shandong_public/statistics/dezhou_yearbook_{pub_year}.pdf",
                "download_url": URLS[pub_year],
                "source_page_url": SOURCE_PAGES[pub_year],
                "verification_method": VERIFICATION_METHODS[pub_year],
                "data_quality": "official_county_yearbook_verified",
                "quality_note": quality_note,
            })

    with GAPS.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["statistical_year", "status", "note", "proxies_forbidden"])
        for year, status, note in GAP_ROWS:
            writer.writerow([year, status, note, True])

    print(OUT)
    print(GAPS)


if __name__ == "__main__":
    main()
