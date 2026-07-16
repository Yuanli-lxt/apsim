"""Run invariant and metadata checks for the county-grid pilot outputs."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio


ROOT = Path(__file__).resolve().parents[2]
PILOT_YEAR = 2020
PILOT = ROOT / "data" / "processed" / "spatial" / f"county_pilot_{PILOT_YEAR}"
RASTERS = [
    f"qihe_{PILOT_YEAR}_winter_wheat_mask.tif", f"qihe_cropland_mask_for_{PILOT_YEAR}.tif",
    f"qihe_{PILOT_YEAR}_summer_maize_mask.tif", f"qihe_{PILOT_YEAR}_wheat_maize_rotation_mask.tif",
]


def check(name: str, passed: bool, detail: str) -> dict:
    return {"check": name, "passed": bool(passed), "detail": detail}


def main() -> None:
    frame = gpd.read_file(PILOT / "grid_crop_fractions.gpkg", layer="grid_crop_fractions")
    county = gpd.read_file(PILOT / "pilot_county_boundary.gpkg").to_crs(frame.crs)
    results: list[dict] = []
    tolerance_ha = 1.0
    county_area = county.geometry.area.sum() / 10000
    grid_area = frame.county_intersection_area_ha.sum()
    results.append(check("grid intersections reproduce county area", abs(county_area-grid_area) <= tolerance_ha,
                         f"boundary={county_area:.3f} ha, intersections={grid_area:.3f} ha"))
    results.append(check("grid IDs unique", frame.grid_id.is_unique, f"n={len(frame)}"))
    results.append(check("crop areas do not exceed cell county area",
                         bool((frame.wheat_area_ha <= frame.county_intersection_area_ha + tolerance_ha).all()),
                         "wheat checked; missing maize is not converted to zero"))
    for col in [c for c in frame.columns if "fraction" in c and c not in {"fraction_denominator"}]:
        values = pd.to_numeric(frame[col], errors="coerce").dropna()
        results.append(check(f"{col} in [0,1]", bool(((values >= -1e-9) & (values <= 1+1e-9)).all()),
                             f"valid_n={len(values)}"))
    available = frame.wheat_maize_rotation_area_ha.notna()
    if available.any():
        ok = (frame.loc[available, "wheat_maize_rotation_area_ha"] <=
              frame.loc[available, "county_intersection_area_ha"] + tolerance_ha).all()
        detail = "published rotation product checked against county-cell area; cross-product nesting is diagnostic, not forced"
        results.append(check("rotation area is physically bounded", bool(ok), detail))
    else:
        results.append(check("missing maize propagates to rotation NaN", frame.maize_area_ha.isna().all() and frame.wheat_maize_rotation_area_ha.isna().all(),
                             "expected pilot state; no fabricated zeros"))
    raster_metadata = {}
    for name in RASTERS:
        with rasterio.open(PILOT / name) as src:
            tags = src.tags()
            raster_metadata[name] = {"crs": str(src.crs), "nodata": src.nodata,
                                     "pixel_size": [abs(src.transform.a), abs(src.transform.e)], "tags": tags}
            results.append(check(f"{name} nearest-neighbour recorded", tags.get("resampling") == "nearest", tags.get("resampling", "missing")))
            results.append(check(f"{name} nodata=255", src.nodata == 255, str(src.nodata)))
    fields_path = PILOT / f"field_parcels_{PILOT_YEAR}.gpkg"
    field_raster_path = PILOT / f"qihe_{PILOT_YEAR}_field_parcel_id.tif"
    if fields_path.exists() and field_raster_path.exists():
        fields = gpd.read_file(fields_path, layer="field_parcels")
        results.append(check("field IDs unique", fields.field_id.is_unique, f"n={len(fields)}"))
        results.append(check("field exact areas positive", bool((fields.area_ha_exact > 0).all()),
                             f"min={fields.area_ha_exact.min():.6f} ha"))
        results.append(check("field rotation area is physically bounded",
                             bool((fields.rotation_area_ha <= fields.rasterized_area_ha + 1e-9).all()),
                             "independent crop products are not forced to nest; field rasterized area checked"))
        for col in [c for c in fields.columns if c.endswith("_fraction_of_field") or c == "valid_mapping_fraction"]:
            values = pd.to_numeric(fields[col], errors="coerce").dropna()
            results.append(check(f"field {col} in [0,1]",
                                 bool(((values >= -1e-9) & (values <= 1+1e-9)).all()),
                                 f"valid_n={len(values)}"))
        with rasterio.open(field_raster_path) as src:
            field_ids = src.read(1)
            positive = field_ids[field_ids > 0]
            results.append(check("field ID raster references output fields",
                                 bool(positive.size and positive.max() <= len(fields)),
                                 f"max_id={int(positive.max()) if positive.size else 0}, fields={len(fields)}"))
            results.append(check("field raster marks non-cadastral semantics",
                                 "not cadastral" in src.tags().get("caveat", "").lower(),
                                 src.tags().get("caveat", "missing")))
    failures = [r for r in results if not r["passed"]]
    report = {"status": "PASS" if not failures else "FAIL", "checks": results,
              "raster_metadata": raster_metadata, "failure_count": len(failures)}
    (PILOT / "quality_control_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(results).to_csv(PILOT / "quality_control_checks.csv", index=False, encoding="utf-8-sig")
    print(f"QC {report['status']}: {len(results)} checks, {len(failures)} failures")
    if failures:
        for item in failures:
            print(f"FAIL {item['check']}: {item['detail']}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
