"""Create a grid-based counterpart to the former field-parcel QC figure.

The left panels show the regular spatial units.  The right panels assign one
display category to each unit using the same 0.5 crop-fraction decision rule
previously used for the field-parcel diagnostic.  The assignment is for QC
visualisation; the continuous crop fractions remain in the output tables.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.patches import Patch

from build_county_grid_pilot import AREA_CRS
from plot_wheat_maize_grid_comparison import choose_chinese_font, summarize_grid


ROOT = Path(__file__).resolve().parents[2]
YEAR = 2020
THRESHOLD = 0.5
PROCESSED = ROOT / "data" / "processed" / "spatial" / f"county_pilot_{YEAR}"
GRID_OUT = PROCESSED / "grid_resolution_comparison"
FIG_OUT = ROOT / "outputs" / "spatial" / f"county_pilot_{YEAR}"
COUNTY_PATH = PROCESSED / "pilot_county_boundary.gpkg"
MASK_PATHS = {
    "wheat": PROCESSED / f"qihe_{YEAR}_winter_wheat_mask.tif",
    "maize": PROCESSED / f"qihe_{YEAR}_summer_maize_mask.tif",
    "rotation": PROCESSED / f"qihe_{YEAR}_wheat_maize_rotation_mask.tif",
    "cropland": PROCESSED / f"qihe_cropland_mask_for_{YEAR}.tif",
}
SYSTEMS = [
    "wheat_maize_rotation",
    "wheat",
    "maize",
    "mixed_wheat_maize_nonrotation",
    "other_cropland",
    "unclassified_grid",
]
SYSTEM_LABELS = {
    "wheat_maize_rotation": "小麦—玉米轮作",
    "wheat": "小麦为主",
    "maize": "玉米为主",
    "mixed_wheat_maize_nonrotation": "小麦、玉米混合（非轮作主导）",
    "other_cropland": "其他耕地",
    "unclassified_grid": "未分类格网",
}
SYSTEM_COLORS = {
    "wheat_maize_rotation": "#6a3d9a",
    "wheat": "#e6ab02",
    "maize": "#33a02c",
    "mixed_wheat_maize_nonrotation": "#1f78b4",
    "other_cropland": "#bdbdbd",
    "unclassified_grid": "#f0f0f0",
}


def load_aligned_masks() -> tuple[dict[str, np.ndarray], rasterio.Affine, object, float]:
    arrays = {}
    reference = None
    for name, path in MASK_PATHS.items():
        if not path.exists():
            raise FileNotFoundError(path)
        with rasterio.open(path) as src:
            signature = (src.width, src.height, src.transform, src.crs, src.nodata)
            if reference is None:
                reference = signature
                transform = src.transform
                crs = src.crs
                pixel_area_ha = abs(src.transform.a * src.transform.e) / 10000.0
            elif signature != reference:
                raise RuntimeError(f"Masks are not aligned: {path}")
            arrays[name] = src.read(1)
    return arrays, transform, crs, pixel_area_ha


def assign_crop_system(row: pd.Series) -> str:
    wheat = row.wheat_fraction
    maize = row.maize_fraction
    rotation = row.rotation_fraction
    if rotation >= THRESHOLD:
        return "wheat_maize_rotation"
    if wheat >= THRESHOLD and maize < THRESHOLD:
        return "wheat"
    if maize >= THRESHOLD and wheat < THRESHOLD:
        return "maize"
    if wheat > 0 and maize > 0:
        return "mixed_wheat_maize_nonrotation"
    if row.cropland_fraction > 0:
        return "other_cropland"
    return "unclassified_grid"


def save_grid(data: gpd.GeoDataFrame, grid_size_m: int) -> None:
    label = f"{grid_size_m // 1000}km"
    path = GRID_OUT / f"qihe_{YEAR}_grid_crop_system_quality_check_{label}.gpkg"
    data.to_file(path, layer=f"grid_crop_system_{label}", driver="GPKG")
    pd.DataFrame(data.drop(columns="geometry")).to_csv(
        GRID_OUT / f"qihe_{YEAR}_grid_crop_system_quality_check_{label}.csv",
        index=False,
        encoding="utf-8-sig",
    )


def legend_handles(results: dict[int, gpd.GeoDataFrame]) -> list[Patch]:
    present = set(pd.concat([frame.crop_system for frame in results.values()]))
    return [
        Patch(facecolor=SYSTEM_COLORS[system], label=SYSTEM_LABELS[system])
        for system in SYSTEMS
        if system in present
    ]


def plot_primary_qc(county: gpd.GeoDataFrame, data: gpd.GeoDataFrame) -> None:
    """Match the former two-panel field-parcel QC layout for the 5 km grid."""
    font = choose_chinese_font()
    fig, axes = plt.subplots(1, 2, figsize=(12, 6), layout="constrained")
    data.boundary.plot(ax=axes[0], color="#315a78", linewidth=0.5)
    county.boundary.plot(ax=axes[0], color="black", linewidth=0.9)
    axes[0].set_title(
        f"a  规则格网单元（5 km，{len(data):,} 个）",
        loc="left",
        fontproperties=font,
        fontsize=10,
    )
    axes[0].set_axis_off()

    for system in SYSTEMS:
        subset = data[data.crop_system == system]
        if not subset.empty:
            subset.plot(
                ax=axes[1],
                color=SYSTEM_COLORS[system],
                edgecolor="white",
                linewidth=0.18,
            )
    county.boundary.plot(ax=axes[1], color="black", linewidth=0.9)
    axes[1].set_title("b  格网作物系统归类", loc="left", fontproperties=font, fontsize=10)
    axes[1].set_axis_off()
    axes[1].legend(
        handles=legend_handles({5000: data}),
        loc="lower left",
        prop=font,
        fontsize=7,
        frameon=True,
    )
    fig.suptitle(
        "齐河县 2020 年：连续作物掩膜汇总至 5 km 规则格网",
        fontproperties=font,
        fontsize=13,
    )
    png = FIG_OUT / f"qihe_{YEAR}_grid_cells_quality_check.png"
    pdf = FIG_OUT / f"qihe_{YEAR}_grid_cells_quality_check.pdf"
    fig.savefig(png, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_qc(county: gpd.GeoDataFrame, results: dict[int, gpd.GeoDataFrame]) -> None:
    font = choose_chinese_font()
    fig, axes = plt.subplots(2, 2, figsize=(11.7, 10.0), layout="constrained")
    panel_letters = {(0, 0): "a", (0, 1): "b", (1, 0): "c", (1, 1): "d"}

    for row, size in enumerate((5000, 1000)):
        data = results[size]
        label = f"{size // 1000} km"
        unit_ax = axes[row, 0]
        class_ax = axes[row, 1]
        line_width = 0.5 if size == 5000 else 0.12

        data.boundary.plot(ax=unit_ax, color="#315a78", linewidth=line_width)
        county.boundary.plot(ax=unit_ax, color="black", linewidth=0.9)
        unit_ax.set_title(
            f"{panel_letters[(row, 0)]}  规则格网单元（{label}，{len(data):,} 个）",
            loc="left",
            fontproperties=font,
            fontsize=10,
        )
        unit_ax.set_axis_off()

        for system in SYSTEMS:
            subset = data[data.crop_system == system]
            if not subset.empty:
                subset.plot(
                    ax=class_ax,
                    color=SYSTEM_COLORS[system],
                    edgecolor="white" if size == 5000 else "none",
                    linewidth=0.18 if size == 5000 else 0,
                )
        county.boundary.plot(ax=class_ax, color="black", linewidth=0.9)
        class_ax.set_title(
            f"{panel_letters[(row, 1)]}  格网作物系统归类（{label}）",
            loc="left",
            fontproperties=font,
            fontsize=10,
        )
        class_ax.set_axis_off()

    handles = legend_handles(results)
    fig.legend(
        handles=handles,
        loc="lower center",
        ncol=3,
        prop=font,
        fontsize=8,
        frameon=True,
        bbox_to_anchor=(0.5, 0.005),
    )
    fig.suptitle(
        "齐河县 2020 年：连续作物掩膜汇总至规则格网单元",
        fontproperties=font,
        fontsize=13,
    )
    png = FIG_OUT / f"qihe_{YEAR}_grid_cells_5km_1km_quality_check.png"
    pdf = FIG_OUT / f"qihe_{YEAR}_grid_cells_5km_1km_quality_check.pdf"
    fig.savefig(png, dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    if not COUNTY_PATH.exists():
        raise FileNotFoundError(COUNTY_PATH)
    GRID_OUT.mkdir(parents=True, exist_ok=True)
    FIG_OUT.mkdir(parents=True, exist_ok=True)
    county = gpd.read_file(COUNTY_PATH).to_crs(AREA_CRS)
    county = gpd.GeoDataFrame(geometry=[county.geometry.union_all()], crs=AREA_CRS)
    arrays, transform, raster_crs, pixel_area_ha = load_aligned_masks()
    if raster_crs != county.crs:
        raise RuntimeError("County and masks do not use the same analysis CRS")

    results = {}
    for grid_size_m in (5000, 1000):
        data = summarize_grid(
            county,
            arrays,
            transform,
            pixel_area_ha,
            grid_size_m,
        )
        data["crop_system"] = data.apply(assign_crop_system, axis=1)
        data["assignment_threshold"] = THRESHOLD
        save_grid(data, grid_size_m)
        results[grid_size_m] = data
        counts = data.crop_system.value_counts().to_dict()
        print(f"{grid_size_m // 1000} km: {len(data)} cells; categories={counts}")
    plot_primary_qc(county, results[5000])
    plot_qc(county, results)
    print(f"Figure: {FIG_OUT / f'qihe_{YEAR}_grid_cells_quality_check.png'}")


if __name__ == "__main__":
    main()
