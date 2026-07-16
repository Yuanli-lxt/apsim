"""Build the Qihe 2020 crop-mask and configurable APSIM-grid smoke test.

The maize input is intentionally optional.  Without ``--maize-mask`` the
maize and rotation rasters are explicit all-nodata products and all maize /
rotation areas remain NaN (never zero and never inferred from non-wheat land).
Any supplied categorical maize mask is aligned with nearest-neighbour
resampling to the same 10 m China-Albers grid as wheat and cropland.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import ListedColormap
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from rasterio.windows import Window, from_bounds
from shapely.geometry import box


ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "shandong_public"
PILOT_YEAR = 2020
PILOT_RAW = RAW / f"county_pilot_{PILOT_YEAR}"
OUT = ROOT / "data" / "processed" / "spatial" / f"county_pilot_{PILOT_YEAR}"
FIG = ROOT / "outputs" / "spatial" / f"county_pilot_{PILOT_YEAR}"
WHEAT_SRC = PILOT_RAW / "crop_masks" / "CN-Wheat10_2020" / "CN-Wheat10_2020_WW_H.tif"
COUNTY_SRC = PILOT_RAW / "boundaries" / "371425_qihe_datav.geojson"
DEFAULT_CROPLAND_SRC = PILOT_RAW / "cropland" / "CACD-v1-2020_Qihe_subset.tif"
DEFAULT_ROTATION_SRC = PILOT_RAW / "crop_masks" / "wheat_maize_china_2020_mosaic.tif"
DEFAULT_MAIZE_SRC = PILOT_RAW / "classified-Shandong-maize-2020-WGS84-v1.tif"

# Equal-area Albers projection centred on Shandong.  All metric areas and the
# default 5 km grid are calculated in this CRS, never from degree-pixel counts.
AREA_CRS = "+proj=aea +lat_1=34 +lat_2=40 +lat_0=0 +lon_0=117 +datum=WGS84 +units=m +no_defs +type=crs"
PIXEL_SIZE_M = 10.0
NODATA = np.uint8(255)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_county() -> gpd.GeoDataFrame:
    county = gpd.read_file(COUNTY_SRC).to_crs("EPSG:4326")
    county = gpd.GeoDataFrame(
        {
            "county_code": ["371425"],
            "county_name": ["齐河县"],
            "prefecture": ["德州市"],
            "pilot_year": [PILOT_YEAR],
            "boundary_source": ["DataV areas_v3 public endpoint"],
            "source_url": ["https://geo.datav.aliyun.com/areas_v3/bound/371425.json"],
            "access_date": [date.today().isoformat()],
        },
        geometry=[county.geometry.union_all()],
        crs="EPSG:4326",
    )
    if county.geometry.iloc[0].is_empty or not county.geometry.iloc[0].is_valid:
        raise RuntimeError("Qihe boundary is empty or invalid")
    return county


def aligned_grid(county_area: gpd.GeoDataFrame) -> tuple[rasterio.Affine, int, int]:
    minx, miny, maxx, maxy = county_area.total_bounds
    left = math.floor(minx / PIXEL_SIZE_M) * PIXEL_SIZE_M
    bottom = math.floor(miny / PIXEL_SIZE_M) * PIXEL_SIZE_M
    right = math.ceil(maxx / PIXEL_SIZE_M) * PIXEL_SIZE_M
    top = math.ceil(maxy / PIXEL_SIZE_M) * PIXEL_SIZE_M
    return from_origin(left, top, PIXEL_SIZE_M, PIXEL_SIZE_M), int((right - left) / PIXEL_SIZE_M), int((top - bottom) / PIXEL_SIZE_M)


def warp_class(path: Path, transform: rasterio.Affine, width: int, height: int) -> tuple[np.ndarray, object, float | None]:
    destination = np.full((height, width), 255, dtype=np.uint16)
    with rasterio.open(path) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=destination,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=None,
            dst_transform=transform,
            dst_crs=AREA_CRS,
            dst_nodata=255,
            resampling=Resampling.nearest,
        )
        return destination, src.crs, src.nodata


def write_mask(path: Path, data: np.ndarray, transform: rasterio.Affine, tags: dict[str, str]) -> None:
    profile = {
        "driver": "GTiff", "height": data.shape[0], "width": data.shape[1],
        "count": 1, "dtype": "uint8", "crs": AREA_CRS, "transform": transform,
        "nodata": int(NODATA), "compress": "DEFLATE", "predictor": 2,
        "tiled": True, "blockxsize": 512, "blockysize": 512,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data.astype("uint8"), 1)
        dst.update_tags(**tags)


def build_grids(county_area: gpd.GeoDataFrame, grid_size_m: float) -> gpd.GeoDataFrame:
    geom = county_area.geometry.iloc[0]
    minx, miny, maxx, maxy = geom.bounds
    x0, y0 = math.floor(minx / grid_size_m) * grid_size_m, math.floor(miny / grid_size_m) * grid_size_m
    rows: list[dict] = []
    ncol, nrow = math.ceil((maxx - x0) / grid_size_m), math.ceil((maxy - y0) / grid_size_m)
    for row in range(nrow):
        for col in range(ncol):
            cell = box(x0 + col * grid_size_m, y0 + row * grid_size_m,
                       x0 + (col + 1) * grid_size_m, y0 + (row + 1) * grid_size_m)
            intersection = cell.intersection(geom)
            if intersection.is_empty or intersection.area <= 0:
                continue
            rows.append({
                "grid_id": f"371425_{int(grid_size_m):05d}m_R{row:03d}C{col:03d}",
                "county_code": "371425", "county_name": "齐河县", "row": row, "col": col,
                "grid_area_ha": cell.area / 10000.0,
                "county_intersection_area_ha": intersection.area / 10000.0,
                "county_fraction": intersection.area / cell.area,
                "geometry": cell,
            })
    grids = gpd.GeoDataFrame(rows, crs=AREA_CRS)
    centers = grids.geometry.centroid.to_crs("EPSG:4326")
    grids["longitude"] = centers.x
    grids["latitude"] = centers.y
    return grids


def pixel_summary(geom, transform, county_pixels, wheat, cropland, cropland_valid,
                  maize, maize_valid, rotation, rotation_valid) -> dict:
    raw = from_bounds(*geom.bounds, transform=transform)
    col0, row0 = max(0, math.floor(raw.col_off)), max(0, math.floor(raw.row_off))
    col1 = min(wheat.shape[1], math.ceil(raw.col_off + raw.width))
    row1 = min(wheat.shape[0], math.ceil(raw.row_off + raw.height))
    win = Window(col0, row0, col1 - col0, row1 - row0)
    shape = (int(win.height), int(win.width))
    inside = geometry_mask([geom], shape, rasterio.windows.transform(win, transform), invert=True)
    slices = (slice(row0, row1), slice(col0, col1))
    county = inside & county_pixels[slices]
    n = int(county.sum())
    wheat_sub = wheat[slices]
    crop_sub = cropland[slices]
    cv_sub = cropland_valid[slices]
    wheat_n = int((county & wheat_sub).sum())
    cropland_n = int((county & crop_sub).sum())
    wheat_on_crop_n = int((county & wheat_sub & crop_sub).sum())
    cropland_valid_n = int((county & cv_sub).sum())
    result = {"county_pixel_count": n, "wheat_pixel_count": wheat_n,
              "wheat_on_cropland_pixel_count": wheat_on_crop_n,
              "cropland_pixel_count": cropland_n, "cropland_valid_pixel_count": cropland_valid_n}
    if maize is None:
        result.update({"maize_pixel_count": None, "maize_on_cropland_pixel_count": None,
                       "other_cropland_pixel_count": None, "maize_valid_pixel_count": 0})
    else:
        maize_sub, mv_sub = maize[slices], maize_valid[slices]
        maize_n = int((county & maize_sub).sum())
        maize_on_crop_n = int((county & maize_sub & crop_sub).sum())
        other_n = int((county & crop_sub & ~wheat_sub & ~maize_sub).sum())
        result.update({"maize_pixel_count": maize_n, "maize_on_cropland_pixel_count": maize_on_crop_n,
                       "other_cropland_pixel_count": other_n,
                       "maize_valid_pixel_count": int((county & mv_sub).sum())})
    if rotation is None and maize is not None:
        rotation_sub = wheat_sub & maize_sub
        rv_sub = mv_sub
    elif rotation is not None:
        rotation_sub = rotation[slices]
        rv_sub = rotation_valid[slices]
    else:
        rotation_sub = rv_sub = None
    if rotation_sub is None:
        result.update({"rotation_pixel_count": None, "rotation_on_cropland_pixel_count": None,
                       "full_mapping_valid_pixel_count": 0})
    else:
        result.update({
            "rotation_pixel_count": int((county & rotation_sub).sum()),
            "rotation_on_cropland_pixel_count": int((county & rotation_sub & crop_sub).sum()),
            "full_mapping_valid_pixel_count": int((county & cv_sub & rv_sub).sum()),
        })
    return result


def fraction_table(grids: gpd.GeoDataFrame, transform, county_pixels, wheat, cropland,
                   cropland_valid, maize, maize_valid, rotation, rotation_valid, cropland_year: int,
                   cropland_source: str) -> gpd.GeoDataFrame:
    pixel_ha = PIXEL_SIZE_M * PIXEL_SIZE_M / 10000.0
    records = []
    for _, grid in grids.iterrows():
        # Use exact county intersection for denominator, centre-in-polygon pixels for crop numerators.
        summary = pixel_summary(grid.geometry, transform, county_pixels, wheat, cropland,
                                cropland_valid, maize, maize_valid, rotation, rotation_valid)
        denominator = float(grid.county_intersection_area_ha)
        cropland_area = summary["cropland_pixel_count"] * pixel_ha
        wheat_area = summary["wheat_pixel_count"] * pixel_ha
        wheat_on_crop_area = summary["wheat_on_cropland_pixel_count"] * pixel_ha
        maize_area = np.nan if summary["maize_pixel_count"] is None else summary["maize_pixel_count"] * pixel_ha
        rotation_area = np.nan if summary["rotation_pixel_count"] is None else summary["rotation_pixel_count"] * pixel_ha
        maize_on_crop_area = (np.nan if summary["maize_on_cropland_pixel_count"] is None
                              else summary["maize_on_cropland_pixel_count"] * pixel_ha)
        rotation_on_crop_area = (np.nan if summary["rotation_on_cropland_pixel_count"] is None
                                 else summary["rotation_on_cropland_pixel_count"] * pixel_ha)
        other_area = np.nan if summary["other_cropland_pixel_count"] is None else summary["other_cropland_pixel_count"] * pixel_ha
        denom_pixels = max(summary["county_pixel_count"], 1)
        rec = grid.drop(labels="geometry").to_dict()
        rec.update({
            "valid_crop_mapping_area_ha": summary["full_mapping_valid_pixel_count"] * pixel_ha,
            "cropland_area_ha": cropland_area, "wheat_area_ha": wheat_area,
            "maize_area_ha": maize_area, "wheat_maize_rotation_area_ha": rotation_area,
            "wheat_area_within_cropland_ha": wheat_on_crop_area,
            "maize_area_within_cropland_ha": maize_on_crop_area,
            "rotation_area_within_cropland_ha": rotation_on_crop_area,
            "other_cropland_area_ha": other_area,
            "non_cropland_area_ha": max(0.0, denominator - cropland_area),
            "wheat_fraction": wheat_area / denominator if denominator else np.nan,
            "maize_fraction": maize_area / denominator if denominator and np.isfinite(maize_area) else np.nan,
            "rotation_fraction": rotation_area / denominator if denominator and np.isfinite(rotation_area) else np.nan,
            "other_cropland_fraction": other_area / denominator if denominator and np.isfinite(other_area) else np.nan,
            "non_cropland_fraction": max(0.0, 1.0 - cropland_area / denominator) if denominator else np.nan,
            "cropland_fraction_of_county_cell": cropland_area / denominator if denominator else np.nan,
            "wheat_fraction_of_cropland": wheat_on_crop_area / cropland_area if cropland_area else np.nan,
            "maize_fraction_of_cropland": maize_on_crop_area / cropland_area if cropland_area and np.isfinite(maize_on_crop_area) else np.nan,
            "rotation_fraction_of_cropland": rotation_on_crop_area / cropland_area if cropland_area and np.isfinite(rotation_on_crop_area) else np.nan,
            "wheat_valid_pixel_ratio": 1.0,
            "maize_valid_pixel_ratio": summary["maize_valid_pixel_count"] / denom_pixels,
            "cropland_valid_pixel_ratio": summary["cropland_valid_pixel_count"] / denom_pixels,
            "data_quality_valid_pixel_ratio": summary["full_mapping_valid_pixel_count"] / denom_pixels,
            "cropland_source": cropland_source, "cropland_year": cropland_year,
            "cropland_year_gap": PILOT_YEAR - cropland_year,
            "quality_flag": ("MAIZE_MISSING_WHEAT_ONLY;" if maize is None else "")
                            + ("INDEPENDENT_MAIZE_AND_ROTATION_PRODUCTS_NOT_FORCED_TO_NEST;" if maize is not None and rotation is not None else "")
                            + f"CROPLAND_YEAR_GAP_{PILOT_YEAR-cropland_year}",
            "fraction_denominator": "*_fraction uses county_intersection_area_ha; *_fraction_of_cropland uses crop intersection with cropland / cropland_area_ha",
        })
        records.append(rec)
    return gpd.GeoDataFrame(records, geometry=grids.geometry.values, crs=grids.crs)


def save_comparison(fractions: gpd.GeoDataFrame, maize_available: bool) -> None:
    wheat_area = fractions.wheat_area_ha.sum()
    maize_area = fractions.maize_area_ha.sum(min_count=1)
    rotation_area = fractions.wheat_maize_rotation_area_ha.sum(min_count=1)
    if PILOT_YEAR != 2024:
        stats = {
            "wheat": {"area_10k_mu": 115.21, "production_10k_t": 53.12, "yield_kg_mu": 461.10,
                      "path": "data/raw/shandong_public/county_pilot_2020/小麦.png"},
            "maize": {"area_10k_mu": 114.49, "production_10k_t": 57.50, "yield_kg_mu": 502.22,
                      "path": "data/raw/shandong_public/county_pilot_2020/玉米.png"},
        }
        rows = []
        for crop_system, area in [("wheat", wheat_area), ("maize", maize_area)]:
            item = stats[crop_system]
            stat_area = item["area_10k_mu"] * 10000 / 15
            rows.append({
                "county_code": "371425", "county_name": "Qihe County", "year": PILOT_YEAR,
                "crop_system": crop_system, "remote_sensing_area_ha": area,
                "stat_sown_area_ha": stat_area,
                "stat_production_t": item["production_10k_t"] * 10000,
                "stat_yield_kg_ha": item["yield_kg_mu"] * 15,
                "area_difference_ha": area - stat_area,
                "area_difference_pct": 100 * (area - stat_area) / stat_area,
                "statistics_source": "2020 Dezhou Statistical Yearbook, table 6-7, p.116, Qihe row",
                "source_url": item["path"],
                "table_or_item": "official index: https://dztj.dezhou.gov.cn/n3100530/n38260319/index.html; units: 10,000 mu, 10,000 t, kg/mu",
            })
        rows.append({
            "county_code": "371425", "county_name": "Qihe County", "year": PILOT_YEAR,
            "crop_system": "wheat_maize_rotation", "remote_sensing_area_ha": rotation_area,
            "stat_sown_area_ha": np.nan, "stat_production_t": np.nan, "stat_yield_kg_ha": np.nan,
            "area_difference_ha": np.nan, "area_difference_pct": np.nan,
            "statistics_source": "rotation is a map-derived spatial intersection category",
            "source_url": "", "table_or_item": "not an independently reported statistic",
        })
        pd.DataFrame(rows).to_csv(OUT / "county_remote_sensing_statistics_comparison.csv",
                                  index=False, encoding="utf-8-sig")
        return
    source = "https://dezhou.dzwww.com/qx/qh/202410/t20241025_15003693.html"
    wheat_stat_area = 115.68 * 10000 / 15
    maize_stat_area = 110.51 * 10000 / 15
    rows = [
        {"county_code": "371425", "county_name": "齐河县", "year": 2024, "crop_system": "wheat",
         "remote_sensing_area_ha": wheat_area, "stat_sown_area_ha": wheat_stat_area, "stat_production_t": 56.36 * 10000,
         "stat_yield_kg_ha": 487.16 * 15, "area_difference_ha": wheat_area-wheat_stat_area,
         "area_difference_pct": 100*(wheat_area-wheat_stat_area)/wheat_stat_area,
         "statistics_source": "user-supplied official table screenshot", "source_url": "data/raw/小麦.png",
         "table_or_item": "齐河县 row; units 万亩、万吨、公斤/亩; exact publication/table/page pending"},
        {"county_code": "371425", "county_name": "齐河县", "year": 2024, "crop_system": "maize",
         "remote_sensing_area_ha": maize_area, "stat_sown_area_ha": maize_stat_area, "stat_production_t": 58.59 * 10000,
         "stat_yield_kg_ha": 530.18 * 15,
         "area_difference_ha": maize_area-maize_stat_area if maize_available else np.nan,
         "area_difference_pct": 100*(maize_area-maize_stat_area)/maize_stat_area if maize_available else np.nan,
         "statistics_source": "user-supplied official table screenshot", "source_url": "data/raw/玉米_24.png",
         "table_or_item": "齐河县 row; units 万亩、万吨、公斤/亩; exact publication/table/page pending"},
        {"county_code": "371425", "county_name": "齐河县", "year": 2024, "crop_system": "wheat_maize_rotation",
         "remote_sensing_area_ha": rotation_area, "stat_sown_area_ha": np.nan, "stat_production_t": np.nan,
         "stat_yield_kg_ha": np.nan, "area_difference_ha": np.nan, "area_difference_pct": np.nan,
         "statistics_source": "rotation intersection is a map-derived quantity", "source_url": "", "table_or_item": "not an independent statistic"},
        {"county_code": "371425", "county_name": "齐河县", "year": 2024, "crop_system": "all_grain_season_sown_area",
         "remote_sensing_area_ha": np.nan, "stat_sown_area_ha": 2306000 / 15,
         "stat_production_t": 3001000000 * 0.5 / 1000, "stat_yield_kg_ha": np.nan,
         "area_difference_ha": np.nan, "area_difference_pct": np.nan,
         "statistics_source": "Dazhong report attributed to Qihe Agriculture and Rural Affairs Bureau",
         "source_url": source, "table_or_item": "article text; 230.6万亩 and 30.01亿斤; includes multiple seasons"},
    ]
    pd.DataFrame(rows).to_csv(OUT / "county_remote_sensing_statistics_comparison.csv", index=False, encoding="utf-8-sig")


def plot_check(county_area, fractions, wheat, maize, rotation, cropland, transform,
               cropland_source: str, cropland_year: int) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    stride = max(1, math.ceil(max(wheat.shape) / 1400))
    extent = [transform.c, transform.c + wheat.shape[1] * transform.a,
              transform.f + wheat.shape[0] * transform.e, transform.f]
    fig, axes = plt.subplots(2, 3, figsize=(12, 7.4), constrained_layout=True)
    panels = [(wheat, f"a  Winter wheat (CN_Wheat10 {PILOT_YEAR})"),
              (maize, "b  Summer maize"), (rotation, "c  Wheat-maize rotation"),
              (cropland, f"d  Cropland context ({cropland_source} {cropland_year})")]
    for ax, (arr, title) in zip(axes.flat[:4], panels):
        shown = arr[::stride, ::stride].astype(float)
        shown[shown == 255] = np.nan
        ax.imshow(shown, extent=extent, origin="upper", cmap=ListedColormap(["#f2f0e6", "#2f8f46"]), vmin=0, vmax=1)
        county_area.boundary.plot(ax=ax, color="black", linewidth=0.7)
        ax.set_title(title, loc="left", fontsize=9); ax.set_axis_off()
        if np.all(arr == 255):
            ax.text(0.5, 0.5, f"NO RELIABLE {PILOT_YEAR} INPUT\n(all nodata)", transform=ax.transAxes,
                    ha="center", va="center", fontsize=9, color="#9d2f2f")
    ax = axes.flat[4]
    fractions.plot(column="wheat_fraction", cmap="YlGn", vmin=0, vmax=1, edgecolor="#555", linewidth=0.3,
                   legend=True, legend_kwds={"label": "wheat / county-cell area"}, ax=ax)
    county_area.boundary.plot(ax=ax, color="black", linewidth=0.8)
    ax.set_title("e  APSIM 5 km grid wheat coverage", loc="left", fontsize=9); ax.set_axis_off()
    ax = axes.flat[5]
    county_area.boundary.plot(ax=ax, color="black", linewidth=1.2)
    fractions.boundary.plot(ax=ax, color="#777", linewidth=0.35)
    ax.set_title("f  County boundary and retained cells", loc="left", fontsize=9); ax.set_axis_off()
    fig.suptitle(f"Qihe County crop-to-APSIM grid smoke test ({PILOT_YEAR})", fontsize=12, weight="bold")
    fig.savefig(FIG / f"qihe_{PILOT_YEAR}_crop_grid_quality_check.png", dpi=220)
    fig.savefig(FIG / f"qihe_{PILOT_YEAR}_crop_grid_quality_check.pdf")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid-size-m", type=float, default=5000.0)
    parser.add_argument("--cropland-mask", type=Path, default=DEFAULT_CROPLAND_SRC,
                        help="Annual categorical cropland raster; default is same-year CACD-v1 2020")
    parser.add_argument("--cropland-year", type=int, default=PILOT_YEAR)
    parser.add_argument("--cropland-values", type=int, nargs="+", default=[1])
    parser.add_argument("--cropland-source", default="CACD-v1")
    parser.add_argument("--maize-mask", type=Path, default=DEFAULT_MAIZE_SRC,
                        help="Same-year categorical maize raster")
    parser.add_argument("--maize-value", type=int, default=1)
    parser.add_argument("--maize-year", type=int, default=PILOT_YEAR)
    parser.add_argument("--rotation-mask", type=Path, default=DEFAULT_ROTATION_SRC,
                        help="Independent same-year wheat-maize rotation mask")
    parser.add_argument("--rotation-value", type=int, default=1)
    args = parser.parse_args()
    if args.maize_mask and not args.maize_year:
        parser.error("--maize-year is required with --maize-mask")
    if args.cropland_year > PILOT_YEAR:
        parser.error(f"--cropland-year cannot be later than the {PILOT_YEAR} mapping year")
    for required in [COUNTY_SRC, args.cropland_mask, WHEAT_SRC, args.maize_mask, args.rotation_mask]:
        if not required.exists():
            raise FileNotFoundError(required)
    OUT.mkdir(parents=True, exist_ok=True); FIG.mkdir(parents=True, exist_ok=True)
    county = load_county(); county_area = county.to_crs(AREA_CRS)
    county.to_file(OUT / "pilot_county_boundary.gpkg", layer="county_boundary", driver="GPKG")
    transform, width, height = aligned_grid(county_area)
    county_pixels = geometry_mask([county_area.geometry.iloc[0]], (height, width), transform, invert=True)

    wheat_raw, wheat_crs, wheat_nodata = warp_class(WHEAT_SRC, transform, width, height)
    wheat = county_pixels & (wheat_raw == 1)
    wheat_out = np.where(county_pixels, wheat.astype("uint8"), NODATA)
    crop_raw, crop_crs, crop_nodata = warp_class(args.cropland_mask, transform, width, height)
    cropland_valid = county_pixels & (crop_raw != (crop_nodata if crop_nodata is not None else 255))
    cropland = county_pixels & np.isin(crop_raw, args.cropland_values)
    crop_out = np.where(county_pixels & cropland_valid, cropland.astype("uint8"), NODATA)

    maize = maize_valid = None
    if args.maize_mask:
        maize_raw, _, maize_nodata = warp_class(args.maize_mask, transform, width, height)
        maize_valid = county_pixels & (maize_raw != (maize_nodata if maize_nodata is not None else 255))
        maize = maize_valid & (maize_raw == args.maize_value)
        maize_out = np.where(maize_valid, maize.astype("uint8"), NODATA)
        rotation = wheat & maize
        rotation_out = np.where(maize_valid & county_pixels, rotation.astype("uint8"), NODATA)
        maize_status = f"input={args.maize_mask}; year={args.maize_year}; nearest-neighbour aligned"
    else:
        maize_out = np.full((height, width), NODATA, dtype="uint8")
        rotation_out = np.full((height, width), NODATA, dtype="uint8")
        rotation = rotation_out
        maize_status = f"UNAVAILABLE: no independent {PILOT_YEAR} marginal maize mask supplied; all pixels nodata"

    rotation_raw, _, rotation_nodata = warp_class(args.rotation_mask, transform, width, height)
    rotation_valid = county_pixels & (rotation_raw != (rotation_nodata if rotation_nodata is not None else 255))
    rotation = rotation_valid & (rotation_raw == args.rotation_value)
    rotation_out = np.where(rotation_valid, rotation.astype("uint8"), NODATA)
    rotation_status = f"input={args.rotation_mask}; year={PILOT_YEAR}; independent ChinaCP-Wheat10m class"

    common = {"area_crs": AREA_CRS, "pixel_size_m": "10", "resampling": "nearest",
              "pilot_county": "Qihe 371425", "pilot_year": str(PILOT_YEAR)}
    write_mask(OUT / f"qihe_{PILOT_YEAR}_winter_wheat_mask.tif", wheat_out, transform,
               {**common, "source": str(WHEAT_SRC.relative_to(ROOT)), "source_crs": str(wheat_crs),
                "source_nodata": str(wheat_nodata), "classes": "0=non-wheat,1=wheat,255=outside county"})
    write_mask(OUT / f"qihe_cropland_mask_for_{PILOT_YEAR}.tif", crop_out, transform,
               {**common, "source": str(args.cropland_mask), "source_name": args.cropland_source,
                "source_year": str(args.cropland_year), "source_crs": str(crop_crs),
                "source_nodata": str(crop_nodata), "source_cropland_values": ",".join(map(str, args.cropland_values)),
                "classes": "0=non-cropland,1=cropland,255=nodata/outside county",
                "warning": f"cropland year gap relative to {PILOT_YEAR} = {PILOT_YEAR-args.cropland_year}"})
    write_mask(OUT / f"qihe_{PILOT_YEAR}_summer_maize_mask.tif", maize_out, transform,
               {**common, "status": maize_status, "classes": "0=non-maize,1=maize,255=nodata"})
    write_mask(OUT / f"qihe_{PILOT_YEAR}_wheat_maize_rotation_mask.tif", rotation_out, transform,
               {**common, "status": rotation_status, "logic": "independent published rotation class"})

    grids = build_grids(county_area, args.grid_size_m)
    grids.to_file(OUT / "apsim_grid.gpkg", layer="apsim_grid", driver="GPKG")
    fractions = fraction_table(grids, transform, county_pixels, wheat, cropland, cropland_valid,
                               maize, maize_valid, rotation, rotation_valid,
                               args.cropland_year, args.cropland_source)
    csv_frame = pd.DataFrame(fractions.drop(columns="geometry"))
    csv_frame.to_csv(OUT / "grid_crop_fractions.csv", index=False, encoding="utf-8-sig")
    fractions.to_file(OUT / "grid_crop_fractions.gpkg", layer="grid_crop_fractions", driver="GPKG")
    save_comparison(fractions, maize is not None)
    plot_check(county_area, fractions, wheat_out, maize_out, rotation_out, crop_out, transform,
               args.cropland_source, args.cropland_year)
    pixel_ha = PIXEL_SIZE_M * PIXEL_SIZE_M / 10000.0
    cross_product = {
        "note": "Independent products are compared without forcing one mask to be a subset of another",
        "rotation_area_ha": float((rotation & county_pixels).sum() * pixel_ha),
        "rotation_overlap_maize_area_ha": float((rotation & maize & county_pixels).sum() * pixel_ha) if maize is not None else None,
        "rotation_outside_maize_area_ha": float((rotation & ~maize & county_pixels).sum() * pixel_ha) if maize is not None else None,
        "rotation_overlap_wheat_area_ha": float((rotation & wheat & county_pixels).sum() * pixel_ha),
        "rotation_outside_wheat_area_ha": float((rotation & ~wheat & county_pixels).sum() * pixel_ha),
    }
    metadata = {
        "pilot": f"Qihe County (371425), Dezhou, {PILOT_YEAR}", "area_crs": AREA_CRS,
        "grid_size_m": args.grid_size_m, "analysis_pixel_size_m": PIXEL_SIZE_M,
        "classification_resampling": "nearest", "maize_status": maize_status,
        "cropland_source": args.cropland_source, "cropland_year": args.cropland_year,
        "cropland_year_gap": PILOT_YEAR-args.cropland_year, "cropland_path": str(args.cropland_mask),
        "wheat_source_sha256": sha256(WHEAT_SRC), "created": date.today().isoformat(),
        "maize_source_sha256": sha256(args.maize_mask) if args.maize_mask else None,
        "rotation_source": str(args.rotation_mask), "rotation_source_sha256": sha256(args.rotation_mask),
        "cross_product_diagnostics": cross_product,
        "fraction_denominators": {"*_fraction": "county_intersection_area_ha",
                                  "*_fraction_of_cropland": "crop area spatially intersected with cropland / cropland_area_ha"},
    }
    (OUT / "spatial_processing_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Cells: {len(fractions)}; county area: {fractions.county_intersection_area_ha.sum():.2f} ha")
    print(f"Mapped wheat area: {fractions.wheat_area_ha.sum():.2f} ha; maize status: {maize_status}")
    print(f"Outputs: {OUT}")


if __name__ == "__main__":
    main()
