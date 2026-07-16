"""Split Qihe crop masks into FTW remote-sensing field units.

The boundary source is the 10 m Fields of The World (FTW) global prediction.
These polygons are AI-derived field units, not legal/cadastral parcels.  Crop
attributes are measured from the already aligned 10 m pilot masks; no county
statistics are spatially allocated and no non-wheat land is called maize.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch
from rasterio.features import rasterize


ROOT = Path(__file__).resolve().parents[2]
PILOT_YEAR = 2024
FIELD_BOUNDARY_YEAR = 2024
RAW = ROOT / "data" / "raw" / "shandong_public" / f"county_pilot_{FIELD_BOUNDARY_YEAR}"
PILOT = ROOT / "data" / "processed" / "spatial" / f"county_pilot_{PILOT_YEAR}"
FIG = ROOT / "outputs" / "spatial" / f"county_pilot_{PILOT_YEAR}"
FTW_FILES = [
    RAW / "field_boundaries" / f"ftw_{FIELD_BOUNDARY_YEAR}_N36E116.parquet",
    RAW / "field_boundaries" / f"ftw_{FIELD_BOUNDARY_YEAR}_N37E116.parquet",
]
COUNTY = PILOT / "pilot_county_boundary.gpkg"
GRID = PILOT / "apsim_grid.gpkg"
MASKS = {
    "cropland": PILOT / f"qihe_cropland_mask_for_{PILOT_YEAR}.tif",
    "wheat": PILOT / f"qihe_{PILOT_YEAR}_winter_wheat_mask.tif",
    "maize": PILOT / f"qihe_{PILOT_YEAR}_summer_maize_mask.tif",
    "rotation": PILOT / f"qihe_{PILOT_YEAR}_wheat_maize_rotation_mask.tif",
}
AREA_CRS = "+proj=aea +lat_1=34 +lat_2=40 +lat_0=0 +lon_0=117 +datum=WGS84 +units=m +no_defs +type=crs"
FTW_CONFIDENCE_MAX = 0.578178


def configure(pilot_year: int, boundary_year: int) -> None:
    global PILOT_YEAR, FIELD_BOUNDARY_YEAR, RAW, PILOT, FIG, FTW_FILES, COUNTY, GRID, MASKS
    PILOT_YEAR, FIELD_BOUNDARY_YEAR = pilot_year, boundary_year
    RAW = ROOT / "data" / "raw" / "shandong_public" / f"county_pilot_{boundary_year}"
    PILOT = ROOT / "data" / "processed" / "spatial" / f"county_pilot_{pilot_year}"
    FIG = ROOT / "outputs" / "spatial" / f"county_pilot_{pilot_year}"
    FTW_FILES = [
        RAW / "field_boundaries" / f"ftw_{boundary_year}_N36E116.parquet",
        RAW / "field_boundaries" / f"ftw_{boundary_year}_N37E116.parquet",
    ]
    COUNTY = PILOT / "pilot_county_boundary.gpkg"
    GRID = PILOT / "apsim_grid.gpkg"
    MASKS = {
        "cropland": PILOT / f"qihe_cropland_mask_for_{pilot_year}.tif",
        "wheat": PILOT / f"qihe_{pilot_year}_winter_wheat_mask.tif",
        "maize": PILOT / f"qihe_{pilot_year}_summer_maize_mask.tif",
        "rotation": PILOT / f"qihe_{pilot_year}_wheat_maize_rotation_mask.tif",
    }


def read_masks() -> tuple[dict[str, np.ndarray], dict]:
    arrays: dict[str, np.ndarray] = {}
    reference: dict | None = None
    for name, path in MASKS.items():
        if not path.exists():
            raise FileNotFoundError(path)
        with rasterio.open(path) as src:
            current = {"shape": src.shape, "transform": src.transform, "crs": src.crs,
                       "nodata": src.nodata, "profile": src.profile.copy()}
            if reference is None:
                reference = current
            elif (current["shape"] != reference["shape"] or current["transform"] != reference["transform"]
                  or current["crs"] != reference["crs"]):
                raise RuntimeError(f"Mask grid mismatch: {path}")
            arrays[name] = src.read(1)
    assert reference is not None
    return arrays, reference


def load_ftw(county_wgs84: gpd.GeoDataFrame, raw_threshold: float) -> gpd.GeoDataFrame:
    parts = []
    bounds = county_wgs84.total_bounds
    for path in FTW_FILES:
        if not path.exists():
            raise FileNotFoundError(path)
        # These download tiles lack row-group bbox metadata, so each 1 degree
        # tile is read once and filtered immediately with the spatial index.
        frame = gpd.read_parquet(path)
        frame = frame.cx[bounds[0]:bounds[2], bounds[1]:bounds[3]]
        frame = frame[(frame["label"] == "field") & (frame["confidence_mean"] >= raw_threshold)].copy()
        parts.append(frame)
    fields = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")
    fields = fields.drop_duplicates(subset=["geometry"]).reset_index(drop=True)
    fields = gpd.clip(fields, county_wgs84.geometry.iloc[0], keep_geom_type=True)
    fields = fields[~fields.geometry.is_empty & fields.geometry.notna()].copy()
    fields.geometry = fields.geometry.make_valid()
    fields = fields.explode(index_parts=False, ignore_index=True)
    fields = fields[fields.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    return fields.to_crs(AREA_CRS)


def counts_by_id(ids: np.ndarray, condition: np.ndarray, length: int) -> np.ndarray:
    return np.bincount(ids[condition].ravel(), minlength=length + 1)[1:length + 1]


def assign_crop_system(row: pd.Series, threshold: float) -> str:
    wheat = row["wheat_fraction_of_field"]
    maize = row["maize_fraction_of_field"]
    rotation = row["rotation_fraction_of_field"]
    if rotation >= threshold:
        return "wheat_maize_rotation"
    if wheat >= threshold and maize < threshold:
        return "wheat"
    if maize >= threshold and wheat < threshold:
        return "maize"
    if wheat > 0 and maize > 0:
        return "mixed_wheat_maize_nonrotation"
    if row["cropland_fraction_of_field"] > 0:
        return "other_cropland"
    return "unclassified_field"


def save_id_raster(ids: np.ndarray, reference: dict) -> None:
    profile = reference["profile"].copy()
    profile.update(driver="GTiff", dtype="uint32", nodata=0, count=1,
                   compress="DEFLATE", predictor=2, tiled=True,
                   blockxsize=512, blockysize=512)
    path = PILOT / f"qihe_{PILOT_YEAR}_field_parcel_id.tif"
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(ids.astype("uint32"), 1)
        dst.update_tags(
            source=f"Fields of The World {FIELD_BOUNDARY_YEAR} 1-degree GeoParquet tiles",
            classes=f"0=no retained FTW field; positive value maps to field_seq in field_parcels_{PILOT_YEAR}.gpkg",
            rasterization="pixel-centre (all_touched=False)",
            caveat="AI-derived remote-sensing field units; not cadastral parcels",
        )


def plot_fields(fields: gpd.GeoDataFrame, county: gpd.GeoDataFrame) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(12, 6), constrained_layout=True)
    fields.boundary.plot(ax=axes[0], color="#315a78", linewidth=0.12)
    county.to_crs(fields.crs).boundary.plot(ax=axes[0], color="black", linewidth=0.8)
    axes[0].set_title(f"a  FTW field units ({FIELD_BOUNDARY_YEAR} boundary proxy)", loc="left", fontsize=10)
    axes[0].set_axis_off()
    systems = ["wheat_maize_rotation", "wheat", "maize", "mixed_wheat_maize_nonrotation",
               "other_cropland", "unclassified_field"]
    colors = ["#6a3d9a", "#e6ab02", "#33a02c", "#1f78b4", "#bdbdbd", "#f0f0f0"]
    for system, color in zip(systems, colors):
        subset = fields[fields.crop_system == system]
        if not subset.empty:
            subset.plot(ax=axes[1], color=color, edgecolor="none")
    county.to_crs(fields.crs).boundary.plot(ax=axes[1], color="black", linewidth=0.8)
    axes[1].set_title("b  Field crop-system assignment", loc="left", fontsize=10)
    axes[1].set_axis_off()
    present = set(fields.crop_system)
    handles = [Patch(facecolor=color, label=system) for system, color in zip(systems, colors)
               if system in present]
    axes[1].legend(handles=handles, loc="lower left", fontsize=7, frameon=True)
    fig.suptitle(f"Qihe {PILOT_YEAR}: crop masks summarized by FTW {FIELD_BOUNDARY_YEAR} field units", fontsize=12)
    fig.savefig(FIG / f"qihe_{PILOT_YEAR}_field_parcels_quality_check.png", dpi=240)
    fig.savefig(FIG / f"qihe_{PILOT_YEAR}_field_parcels_quality_check.pdf")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-year", type=int, default=2024)
    parser.add_argument("--field-boundary-year", type=int, default=2024)
    parser.add_argument("--allow-temporal-proxy", action="store_true",
                        help="Explicitly allow field-boundary and crop-mask years to differ")
    parser.add_argument("--confidence-percent", type=float, default=70.0,
                        help="FTW explorer-equivalent confidence threshold (0-100; default 70)")
    parser.add_argument("--crop-assignment-threshold", type=float, default=0.5,
                        help="Minimum within-field fraction for a dominant crop-system label")
    parser.add_argument("--min-field-area-ha", type=float, default=0.04,
                        help="Minimum exact clipped polygon area retained")
    parser.add_argument("--min-cropland-evidence", type=float, default=0.2,
                        help="Keep fields with at least this cropland fraction, or any wheat/maize pixels")
    args = parser.parse_args()
    configure(args.pilot_year, args.field_boundary_year)
    if PILOT_YEAR != FIELD_BOUNDARY_YEAR and not args.allow_temporal_proxy:
        parser.error("field-boundary year differs from crop year; pass --allow-temporal-proxy only for an explicitly labelled diagnostic")
    if not 0 <= args.confidence_percent <= 100:
        parser.error("--confidence-percent must be in [0,100]")
    if not 0 <= args.crop_assignment_threshold <= 1 or not 0 <= args.min_cropland_evidence <= 1:
        parser.error("fraction thresholds must be in [0,1]")

    PILOT.mkdir(parents=True, exist_ok=True)
    masks, reference = read_masks()
    county = gpd.read_file(COUNTY).to_crs("EPSG:4326")
    raw_threshold = args.confidence_percent / 100.0 * FTW_CONFIDENCE_MAX
    fields = load_ftw(county, raw_threshold)
    fields["area_ha_exact"] = fields.geometry.area / 10000.0
    fields = fields[fields.area_ha_exact >= args.min_field_area_ha].reset_index(drop=True)
    fields["field_seq"] = np.arange(1, len(fields) + 1, dtype="int32")

    ids = rasterize(
        ((geom, int(seq)) for geom, seq in zip(fields.geometry, fields.field_seq)),
        out_shape=reference["shape"], transform=reference["transform"], fill=0,
        dtype="uint32", all_touched=False,
    )
    n = len(fields)
    assigned = ids > 0
    total_pixels = counts_by_id(ids, assigned, n)
    valid_all = assigned & np.logical_and.reduce([arr != 255 for arr in masks.values()])
    fields["rasterized_pixel_count"] = total_pixels
    fields["mapped_pixel_count"] = counts_by_id(ids, valid_all, n)
    for name, arr in masks.items():
        fields[f"{name}_pixel_count"] = counts_by_id(ids, assigned & (arr == 1), n)

    pixel_ha = abs(reference["transform"].a * reference["transform"].e) / 10000.0
    denom = fields.rasterized_pixel_count.replace(0, np.nan)
    fields["rasterized_area_ha"] = fields.rasterized_pixel_count * pixel_ha
    for name in MASKS:
        fields[f"{name}_area_ha"] = fields[f"{name}_pixel_count"] * pixel_ha
        fields[f"{name}_fraction_of_field"] = fields[f"{name}_pixel_count"] / denom
    fields["valid_mapping_fraction"] = fields.mapped_pixel_count / denom

    keep = ((fields.cropland_fraction_of_field >= args.min_cropland_evidence)
            | (fields.wheat_pixel_count > 0) | (fields.maize_pixel_count > 0))
    fields = fields[keep].copy().reset_index(drop=True)
    # Re-number after filtering, then rasterize once more so the raster IDs and
    # output table are exactly one-to-one.
    fields["field_seq"] = np.arange(1, len(fields) + 1, dtype="int32")
    fields["field_id"] = fields.field_seq.map(lambda x: f"371425_{PILOT_YEAR}_F{x:06d}")
    fields["county_code"] = "371425"
    fields["county_name"] = "齐河县"
    fields["field_boundary_source"] = "Fields of The World global predictions"
    fields["field_boundary_year"] = FIELD_BOUNDARY_YEAR
    fields["crop_mapping_year"] = PILOT_YEAR
    fields["field_boundary_year_gap"] = FIELD_BOUNDARY_YEAR - PILOT_YEAR
    fields["ftw_threshold_percent"] = args.confidence_percent
    fields["ftw_threshold_raw"] = raw_threshold
    fields["crop_system"] = fields.apply(assign_crop_system, axis=1,
                                           threshold=args.crop_assignment_threshold)
    fields["quality_flag"] = np.where(
        fields.valid_mapping_fraction >= 0.8,
        f"FTW_AI_FIELD_NOT_CADASTRAL;FIELD_BOUNDARY_YEAR_GAP_{FIELD_BOUNDARY_YEAR-PILOT_YEAR}",
        f"FTW_AI_FIELD_NOT_CADASTRAL;FIELD_BOUNDARY_YEAR_GAP_{FIELD_BOUNDARY_YEAR-PILOT_YEAR};LOW_VALID_MAPPING_FRACTION",
    )

    centers = fields.geometry.centroid.to_crs("EPSG:4326")
    fields["longitude"] = centers.x
    fields["latitude"] = centers.y
    grids = gpd.read_file(GRID, layer="apsim_grid").to_crs(fields.crs)
    center_gdf = gpd.GeoDataFrame(fields[["field_id"]].copy(), geometry=fields.geometry.centroid, crs=fields.crs)
    grid_join = gpd.sjoin(center_gdf, grids[["grid_id", "geometry"]], predicate="within", how="left")
    fields["grid_id"] = grid_join.drop_duplicates("field_id").set_index("field_id").reindex(fields.field_id)["grid_id"].values

    ids = rasterize(((geom, int(seq)) for geom, seq in zip(fields.geometry, fields.field_seq)),
                    out_shape=reference["shape"], transform=reference["transform"], fill=0,
                    dtype="uint32", all_touched=False)
    save_id_raster(ids, reference)

    out_gpkg = PILOT / f"field_parcels_{PILOT_YEAR}.gpkg"
    fields.to_file(out_gpkg, layer="field_parcels", driver="GPKG")
    csv = pd.DataFrame(fields.drop(columns="geometry"))
    csv.to_csv(PILOT / f"field_crop_attributes_{PILOT_YEAR}.csv", index=False, encoding="utf-8-sig")
    plot_fields(fields, county)

    county_area = county.to_crs(AREA_CRS).geometry.area.sum() / 10000.0
    mask_coverage = {}
    for name, arr in masks.items():
        pixels = int((arr == 1).sum())
        covered = int(((ids > 0) & (arr == 1)).sum())
        mask_coverage[name] = {
            "mask_area_ha": pixels * pixel_ha,
            "area_in_retained_fields_ha": covered * pixel_ha,
            "field_coverage_fraction": covered / pixels if pixels else None,
        }
    crop_area = mask_coverage["cropland"]["mask_area_ha"]
    field_crop_area = mask_coverage["cropland"]["area_in_retained_fields_ha"]
    metadata = {
        "created": date.today().isoformat(), "county": "Qihe 371425", "year": PILOT_YEAR,
        "boundary_source": f"Fields of The World global {FIELD_BOUNDARY_YEAR} predictions",
        "field_boundary_year": FIELD_BOUNDARY_YEAR,
        "field_boundary_year_gap": FIELD_BOUNDARY_YEAR - PILOT_YEAR,
        "temporal_proxy_warning": "field geometry is not contemporaneous with crop masks" if FIELD_BOUNDARY_YEAR != PILOT_YEAR else None,
        "source_tiles": [str(p.relative_to(ROOT)).replace("\\", "/") for p in FTW_FILES],
        "license": "CC BY 4.0", "field_semantics": "AI-derived remote-sensing field unit; not cadastral parcel",
        "confidence_percent": args.confidence_percent, "confidence_raw": raw_threshold,
        "confidence_conversion": f"percent / 100 * {FTW_CONFIDENCE_MAX}",
        "crop_assignment_threshold": args.crop_assignment_threshold,
        "min_field_area_ha": args.min_field_area_ha,
        "min_cropland_evidence": args.min_cropland_evidence,
        "field_count": len(fields), "county_area_ha": county_area,
        "cropland_mask_area_ha": crop_area,
        "cropland_mask_covered_by_retained_fields_ha": field_crop_area,
        "cropland_mask_field_coverage_fraction": field_crop_area / crop_area if crop_area else None,
        "mask_field_coverage": mask_coverage,
        "crop_masks": {k: str(v.relative_to(ROOT)).replace("\\", "/") for k, v in MASKS.items()},
        "area_crs": AREA_CRS, "rasterization": "10 m pixel-centre; all_touched=False",
    }
    (PILOT / "field_parcel_processing_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Retained fields: {len(fields):,}")
    print(f"FTW coverage of mapped cropland: {metadata['cropland_mask_field_coverage_fraction']:.2%}")
    print(out_gpkg)


if __name__ == "__main__":
    main()
