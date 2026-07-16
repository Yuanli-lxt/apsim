"""Create 5 km and 1 km maps of the published 2020 wheat-maize rotation class."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd
import rasterio

from build_county_grid_pilot import AREA_CRS
from plot_wheat_maize_grid_comparison import (
    add_north_arrow,
    add_scale_bar,
    choose_chinese_font,
    summarize_grid,
)


ROOT = Path(__file__).resolve().parents[2]
YEAR = 2020
PROCESSED = ROOT / "data" / "processed" / "spatial" / f"county_pilot_{YEAR}"
GRID_OUT = PROCESSED / "grid_resolution_comparison"
FIG_OUT = ROOT / "outputs" / "spatial" / f"county_pilot_{YEAR}"
COUNTY_PATH = PROCESSED / "pilot_county_boundary.gpkg"
ROTATION_PATH = PROCESSED / f"qihe_{YEAR}_wheat_maize_rotation_mask.tif"


def save_grid(fractions: gpd.GeoDataFrame, grid_size_m: int) -> None:
    label = f"{grid_size_m // 1000}km"
    fractions.to_file(
        GRID_OUT / f"qihe_{YEAR}_wheat_maize_rotation_{label}_grid.gpkg",
        layer=f"rotation_fraction_{label}",
        driver="GPKG",
    )
    pd.DataFrame(fractions.drop(columns="geometry")).to_csv(
        GRID_OUT / f"qihe_{YEAR}_wheat_maize_rotation_{label}_grid.csv",
        index=False,
        encoding="utf-8-sig",
    )


def plot_rotation(county: gpd.GeoDataFrame, results: dict[int, gpd.GeoDataFrame]) -> None:
    font = choose_chinese_font()
    sizes = [5000, 1000]
    cmap = mpl.colormaps["YlGnBu"]
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 5.3), layout="constrained")

    for ax, size in zip(axes, sizes):
        data = results[size]
        data.plot(
            column="rotation_fraction",
            ax=ax,
            cmap=cmap,
            vmin=0,
            vmax=1,
            edgecolor="#494949",
            linewidth=0.35 if size == 5000 else 0.08,
            missing_kwds={"color": "#d9d9d9"},
        )
        county.boundary.plot(ax=ax, color="#151515", linewidth=0.9)
        ax.set_title(
            f"小麦—玉米轮作 · {size // 1000} km 格网（{len(data):,} 个）",
            loc="left",
            fontproperties=font,
            fontsize=10,
        )
        ax.text(
            0.02,
            0.98,
            f"轮作制图面积：{data.rotation_area_ha.sum():,.0f} ha",
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
    cbar = fig.colorbar(scalar, ax=axes, orientation="vertical", shrink=0.74, pad=0.015)
    cbar.set_label("轮作面积占县域内格网面积比例", fontproperties=font, fontsize=8)
    cbar.ax.yaxis.set_major_formatter(mpl.ticker.PercentFormatter(xmax=1.0))
    cbar.ax.tick_params(labelsize=7)
    fig.suptitle(
        "齐河县 2020 年小麦—玉米轮作：5 km / 1 km 格网覆盖比例",
        fontproperties=font,
        fontsize=13,
    )
    fig.text(
        0.5,
        0.01,
        "轮作范围来自独立发布的 ChinaCP-Wheat10m 2020 轮作分类，不是小麦与玉米图层的简单相交。",
        ha="center",
        fontproperties=font,
        fontsize=7.5,
        color="#333333",
    )
    png = FIG_OUT / f"qihe_{YEAR}_wheat_maize_rotation_5km_1km_grid_comparison.png"
    pdf = FIG_OUT / f"qihe_{YEAR}_wheat_maize_rotation_5km_1km_grid_comparison.pdf"
    fig.savefig(png, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    for path in (COUNTY_PATH, ROTATION_PATH):
        if not path.exists():
            raise FileNotFoundError(path)
    GRID_OUT.mkdir(parents=True, exist_ok=True)
    FIG_OUT.mkdir(parents=True, exist_ok=True)

    county = gpd.read_file(COUNTY_PATH).to_crs(AREA_CRS)
    county = gpd.GeoDataFrame(geometry=[county.geometry.union_all()], crs=AREA_CRS)
    with rasterio.open(ROTATION_PATH) as src:
        rotation = src.read(1)
        transform = src.transform
        raster_crs = src.crs
        pixel_area_ha = abs(src.transform.a * src.transform.e) / 10000.0
    if raster_crs != county.crs:
        raise RuntimeError("County and rotation raster do not use the same analysis CRS")

    results = {}
    for grid_size_m in (5000, 1000):
        fractions = summarize_grid(
            county,
            {"rotation": rotation},
            transform,
            pixel_area_ha,
            grid_size_m,
        )
        save_grid(fractions, grid_size_m)
        results[grid_size_m] = fractions
        print(
            f"{grid_size_m // 1000} km: {len(fractions)} cells; "
            f"rotation={fractions.rotation_area_ha.sum():.2f} ha"
        )
    plot_rotation(county, results)
    print(
        "Figure: "
        f"{FIG_OUT / f'qihe_{YEAR}_wheat_maize_rotation_5km_1km_grid_comparison.png'}"
    )


if __name__ == "__main__":
    main()
