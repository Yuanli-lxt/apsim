"""Create Qihe 2020 wheat/maize coverage maps for 5 km and 1 km grids.

The categorical crop masks have already been aligned to the common 10 m
equal-area analysis grid.  This script does not resample them again: it counts
crop pixels inside each county-clipped grid cell and reports crop area divided
by the county-intersection area of that cell.
"""

from __future__ import annotations

import math
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.font_manager import FontProperties, findfont
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds

from build_county_grid_pilot import AREA_CRS, build_grids


ROOT = Path(__file__).resolve().parents[2]
YEAR = 2020
PROCESSED = ROOT / "data" / "processed" / "spatial" / f"county_pilot_{YEAR}"
GRID_OUT = PROCESSED / "grid_resolution_comparison"
FIG_OUT = ROOT / "outputs" / "spatial" / f"county_pilot_{YEAR}"
COUNTY_PATH = PROCESSED / "pilot_county_boundary.gpkg"
CROP_PATHS = {
    "wheat": PROCESSED / f"qihe_{YEAR}_winter_wheat_mask.tif",
    "maize": PROCESSED / f"qihe_{YEAR}_summer_maize_mask.tif",
}


def choose_chinese_font() -> FontProperties:
    """Use an installed CJK font when available, otherwise fall back safely."""
    candidates = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Arial Unicode MS"]
    for family in candidates:
        path = findfont(FontProperties(family=family), fallback_to_default=False)
        if Path(path).exists():
            return FontProperties(fname=path)
    return FontProperties()


def validate_rasters() -> tuple[dict[str, np.ndarray], rasterio.Affine, object, float]:
    arrays: dict[str, np.ndarray] = {}
    reference = None
    pixel_area_ha = None
    for crop, path in CROP_PATHS.items():
        if not path.exists():
            raise FileNotFoundError(path)
        with rasterio.open(path) as src:
            signature = (src.width, src.height, src.transform, src.crs, src.nodata)
            if reference is None:
                reference = signature
                transform, crs = src.transform, src.crs
                pixel_area_ha = abs(src.transform.a * src.transform.e) / 10000.0
            elif signature != reference:
                raise RuntimeError(f"Crop masks are not aligned: {path}")
            arrays[crop] = src.read(1)
    assert pixel_area_ha is not None
    return arrays, transform, crs, pixel_area_ha


def count_crop_pixels(geometry, crop: np.ndarray, transform: rasterio.Affine) -> int:
    raw = from_bounds(*geometry.bounds, transform=transform)
    col0 = max(0, math.floor(raw.col_off))
    row0 = max(0, math.floor(raw.row_off))
    col1 = min(crop.shape[1], math.ceil(raw.col_off + raw.width))
    row1 = min(crop.shape[0], math.ceil(raw.row_off + raw.height))
    window = Window(col0, row0, col1 - col0, row1 - row0)
    window_transform = rasterio.windows.transform(window, transform)
    inside = geometry_mask(
        [geometry],
        out_shape=(int(window.height), int(window.width)),
        transform=window_transform,
        invert=True,
    )
    values = crop[row0:row1, col0:col1]
    return int((inside & (values == 1)).sum())


def summarize_grid(
    county: gpd.GeoDataFrame,
    arrays: dict[str, np.ndarray],
    transform: rasterio.Affine,
    pixel_area_ha: float,
    grid_size_m: int,
) -> gpd.GeoDataFrame:
    grids = build_grids(county, float(grid_size_m))
    county_geometry = county.geometry.iloc[0]
    records = []
    clipped_geometries = []
    for _, cell in grids.iterrows():
        clipped = cell.geometry.intersection(county_geometry)
        denominator_ha = clipped.area / 10000.0
        record = cell.drop(labels="geometry").to_dict()
        for crop, array in arrays.items():
            crop_area_ha = count_crop_pixels(clipped, array, transform) * pixel_area_ha
            record[f"{crop}_area_ha"] = crop_area_ha
            record[f"{crop}_fraction"] = crop_area_ha / denominator_ha if denominator_ha else np.nan
        record["grid_size_m"] = grid_size_m
        records.append(record)
        clipped_geometries.append(clipped)
    return gpd.GeoDataFrame(records, geometry=clipped_geometries, crs=county.crs)


def save_grid(fractions: gpd.GeoDataFrame, grid_size_m: int) -> None:
    label = f"{grid_size_m // 1000}km"
    fractions.to_file(
        GRID_OUT / f"qihe_{YEAR}_wheat_maize_{label}_grid.gpkg",
        layer=f"crop_fractions_{label}",
        driver="GPKG",
    )
    pd.DataFrame(fractions.drop(columns="geometry")).to_csv(
        GRID_OUT / f"qihe_{YEAR}_wheat_maize_{label}_grid.csv",
        index=False,
        encoding="utf-8-sig",
    )


def add_north_arrow(ax, font: FontProperties) -> None:
    ax.annotate(
        "N",
        xy=(0.94, 0.93),
        xytext=(0.94, 0.80),
        xycoords="axes fraction",
        ha="center",
        va="center",
        fontproperties=font,
        fontsize=9,
        arrowprops={"arrowstyle": "-|>", "color": "#222222", "lw": 1.0},
    )


def add_scale_bar(ax, length_km: int, font: FontProperties) -> None:
    xmin, xmax = ax.get_xlim()
    ymin, ymax = ax.get_ylim()
    x0 = xmin + 0.07 * (xmax - xmin)
    y0 = ymin + 0.06 * (ymax - ymin)
    length_m = length_km * 1000
    ax.plot([x0, x0 + length_m], [y0, y0], color="#222222", lw=2.0, solid_capstyle="butt")
    ax.plot([x0, x0], [y0 - 250, y0 + 250], color="#222222", lw=0.8)
    ax.plot([x0 + length_m, x0 + length_m], [y0 - 250, y0 + 250], color="#222222", lw=0.8)
    ax.text(
        x0 + length_m / 2,
        y0 + 450,
        f"{length_km} km",
        ha="center",
        va="bottom",
        fontproperties=font,
        fontsize=7,
    )


def plot_comparison(county: gpd.GeoDataFrame, results: dict[int, gpd.GeoDataFrame]) -> None:
    font = choose_chinese_font()
    crop_rows = [
        ("wheat", "冬小麦", mpl.colormaps["YlGn"]),
        ("maize", "夏玉米", mpl.colormaps["YlOrBr"]),
    ]
    sizes = [5000, 1000]
    fig, axes = plt.subplots(2, 2, figsize=(10.2, 9.0), layout="constrained")

    for row, (crop, crop_cn, cmap) in enumerate(crop_rows):
        for col, size in enumerate(sizes):
            ax = axes[row, col]
            data = results[size]
            line_width = 0.35 if size == 5000 else 0.08
            data.plot(
                column=f"{crop}_fraction",
                ax=ax,
                cmap=cmap,
                vmin=0,
                vmax=1,
                edgecolor="#4a4a4a",
                linewidth=line_width,
                missing_kwds={"color": "#d9d9d9"},
            )
            county.boundary.plot(ax=ax, color="#151515", linewidth=0.9)
            total_area = data[f"{crop}_area_ha"].sum()
            ax.set_title(
                f"{crop_cn} · {size // 1000} km 格网（{len(data):,} 个）",
                loc="left",
                fontproperties=font,
                fontsize=10,
            )
            ax.text(
                0.02,
                0.98,
                f"制图面积：{total_area:,.0f} ha",
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontproperties=font,
                fontsize=7.5,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.72, "pad": 2.0},
            )
            add_north_arrow(ax, font)
            add_scale_bar(ax, 10, font)
            ax.set_axis_off()

        scalar = mpl.cm.ScalarMappable(norm=mpl.colors.Normalize(0, 1), cmap=cmap)
        cbar = fig.colorbar(scalar, ax=axes[row, :], orientation="vertical", shrink=0.72, pad=0.015)
        cbar.set_label(f"{crop_cn}占县域内格网面积比例", fontproperties=font, fontsize=8)
        cbar.ax.yaxis.set_major_formatter(mpl.ticker.PercentFormatter(xmax=1.0))
        cbar.ax.tick_params(labelsize=7)

    fig.suptitle(
        "齐河县 2020 年冬小麦与夏玉米：5 km / 1 km 格网覆盖比例",
        fontproperties=font,
        fontsize=13,
    )
    fig.text(
        0.5,
        0.005,
        "每个格网的颜色表示作物像元面积 ÷ 格网与齐河县相交面积；分类栅格按最近邻方法对齐至 10 m 分析网格。",
        ha="center",
        fontproperties=font,
        fontsize=7.5,
        color="#333333",
    )
    png = FIG_OUT / f"qihe_{YEAR}_wheat_maize_5km_1km_grid_comparison.png"
    pdf = FIG_OUT / f"qihe_{YEAR}_wheat_maize_5km_1km_grid_comparison.pdf"
    fig.savefig(png, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    GRID_OUT.mkdir(parents=True, exist_ok=True)
    FIG_OUT.mkdir(parents=True, exist_ok=True)
    if not COUNTY_PATH.exists():
        raise FileNotFoundError(COUNTY_PATH)
    county = gpd.read_file(COUNTY_PATH).to_crs(AREA_CRS)
    county = gpd.GeoDataFrame(geometry=[county.geometry.union_all()], crs=AREA_CRS)
    arrays, transform, raster_crs, pixel_area_ha = validate_rasters()
    if raster_crs != county.crs:
        raise RuntimeError("County and crop rasters do not use the same analysis CRS")

    results = {}
    for grid_size_m in (5000, 1000):
        fractions = summarize_grid(county, arrays, transform, pixel_area_ha, grid_size_m)
        save_grid(fractions, grid_size_m)
        results[grid_size_m] = fractions
        print(
            f"{grid_size_m // 1000} km: {len(fractions)} cells; "
            f"wheat={fractions.wheat_area_ha.sum():.2f} ha; "
            f"maize={fractions.maize_area_ha.sum():.2f} ha"
        )
    plot_comparison(county, results)
    print(f"Figure: {FIG_OUT / f'qihe_{YEAR}_wheat_maize_5km_1km_grid_comparison.png'}")


if __name__ == "__main__":
    main()
