"""Write a tidy, source-traceable Qihe wheat/maize statistics table."""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020" / "calibration" / "qihe_2018_2020_official_crop_statistics.csv"

# Values are transcribed from the county rows of the named official yearbooks.
# Units in the source tables: 10,000 mu, 10,000 t, kg/mu.
ROWS = [
    (2018, "wheat", "小麦", 115.45, 52.19, 452.08, 2019, "6-8", 114, 102),
    (2018, "maize", "玉米", 113.66, 55.02, 484.04, 2019, "6-8 continuation 1", 115, 103),
    (2019, "wheat", "小麦", 115.21, 53.12, 461.10, 2020, "6-7", 116, 104),
    (2019, "maize", "玉米", 114.49, 57.50, 502.22, 2020, "6-7 continuation 1", 117, 105),
    (2020, "wheat", "小麦", 114.30, 54.32, 475.25, 2021, "6-7", 102, 90),
    (2020, "maize", "玉米", 111.82, 56.43, 504.64, 2021, "6-7 continuation", 103, 91),
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "year", "crop", "crop_zh", "area_10k_mu", "area_ha",
        "production_10k_t", "production_t", "yield_kg_mu", "yield_kg_ha",
        "yearbook", "table", "pdf_page", "printed_page", "pdf_path", "pdf_url",
    ]
    with OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for year, crop, crop_zh, area, production, crop_yield, yearbook, table, pdf_page, printed_page in ROWS:
            writer.writerow({
                "year": year,
                "crop": crop,
                "crop_zh": crop_zh,
                "area_10k_mu": area,
                "area_ha": area * 10000.0 / 15.0,
                "production_10k_t": production,
                "production_t": production * 10000.0,
                "yield_kg_mu": crop_yield,
                "yield_kg_ha": crop_yield * 15.0,
                "yearbook": f"Dezhou Statistical Yearbook {yearbook}",
                "table": table,
                "pdf_page": pdf_page,
                "printed_page": printed_page,
                "pdf_path": f"data/raw/shandong_public/statistics/dezhou_yearbook_{yearbook}.pdf",
                "pdf_url": (
                    "http://www.dezhou.gov.cn/pdf/2019tjnj.pdf" if yearbook == 2019 else
                    f"http://www.dezhou.gov.cn/pdf/tjnj{yearbook}.pdf"
                ),
            })
    print(OUT)


if __name__ == "__main__":
    main()
