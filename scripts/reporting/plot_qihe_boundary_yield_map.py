"""Plot Qihe boundary and 2015-2023 mean APSIM wheat/maize yields in Python."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
from matplotlib.patches import FancyArrowPatch


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "outputs" / "spatial" / "county_pilot_2020" / "warmup_sensitivity" / "qihe_warmup_10km_full_2010_2023_20260716_v1"
DATA_RUN = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020" / "warmup_sensitivity" / "qihe_warmup_2010_2023_v2"
GRID = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020" / "grid_resolution_experiment" / "grids" / "qihe_2020_rotation_10km.gpkg"
BOUNDARY = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020" / "pilot_county_boundary.gpkg"
OUT = RUN / "figures" / "qihe_boundary_2015_2023_mean_yield"
SOURCE = OUT.parent / "source_data_qihe_2015_2023_mean_yield_10km.csv"
QA = OUT.parent / "qihe_boundary_2015_2023_mean_yield_QA.json"


def weighted_mean(group: pd.DataFrame, column: str) -> float:
    values = pd.to_numeric(group[column], errors="coerce")
    weights = pd.to_numeric(group["soil_rotation_area_ha"], errors="coerce")
    valid = values.notna() & weights.notna() & (weights > 0)
    return float(np.average(values[valid], weights=weights[valid]))


def prepare_source() -> gpd.GeoDataFrame:
    units = pd.read_csv(DATA_RUN / "simulation_units_10km.csv")
    results = pd.read_csv(RUN / "annual_case_yield_and_mineral_n.csv")
    selected = results.query(
        "management_scenario == 'statistical_constraint_central' and "
        "initial_n_multiplier == 1 and 2015 <= year <= 2023"
    ).copy()
    if len(selected) != 88 * 9:
        raise RuntimeError(f"Expected 792 selected case-years, got {len(selected)}")
    mapped = units[["grid_id", "case_id", "soil_rotation_area_ha"]].merge(
        selected, on="case_id", how="left", validate="many_to_many"
    )
    if mapped[["wheat_yield_kg_ha", "maize_yield_kg_ha"]].isna().any().any():
        raise RuntimeError("Yield results are missing after mapping cases to grid subunits")
    annual_rows = []
    for (grid_id, year), group in mapped.groupby(["grid_id", "year"], sort=True):
        annual_rows.append({
            "grid_id": grid_id,
            "year": int(year),
            "represented_rotation_area_ha": float(group.soil_rotation_area_ha.sum()),
            "wheat_yield_kg_ha": weighted_mean(group, "wheat_yield_kg_ha"),
            "maize_yield_kg_ha": weighted_mean(group, "maize_yield_kg_ha"),
        })
    annual = pd.DataFrame(annual_rows)
    if annual.grid_id.nunique() != 28 or len(annual) != 28 * 9:
        raise RuntimeError("Incomplete 28-grid by 9-year source table")
    summary = annual.groupby("grid_id", as_index=False).agg(
        represented_rotation_area_ha=("represented_rotation_area_ha", "first"),
        wheat_mean_yield_kg_ha=("wheat_yield_kg_ha", "mean"),
        wheat_interannual_cv_percent=("wheat_yield_kg_ha", lambda x: float(x.std(ddof=1) / x.mean() * 100)),
        maize_mean_yield_kg_ha=("maize_yield_kg_ha", "mean"),
        maize_interannual_cv_percent=("maize_yield_kg_ha", lambda x: float(x.std(ddof=1) / x.mean() * 100)),
    )
    grid = gpd.read_file(GRID, layer="rotation_10km")
    output = grid[["grid_id", "rotation_area_ha", "geometry"]].merge(summary, on="grid_id", how="left", validate="one_to_one")
    if output[["wheat_mean_yield_kg_ha", "maize_mean_yield_kg_ha"]].isna().any().any():
        raise RuntimeError("Some grid polygons lack aggregated yield")
    if not np.allclose(output.rotation_area_ha, output.represented_rotation_area_ha, atol=0.02):
        raise RuntimeError("Mapped soil rotation area does not reproduce grid rotation area")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    output.drop(columns="geometry").to_csv(SOURCE, index=False, encoding="utf-8-sig")
    return output


def rounded_limits(values: pd.Series, step: float = 250.0) -> tuple[float, float]:
    return np.floor(values.min() / step) * step, np.ceil(values.max() / step) * step


def add_north_arrow(ax):
    arrow = FancyArrowPatch((0.91, 0.79), (0.91, 0.93), transform=ax.transAxes,
                            arrowstyle="-|>", mutation_scale=11, linewidth=0.9,
                            color="#1F252B", zorder=10)
    ax.add_patch(arrow)
    ax.text(0.91, 0.955, "N", transform=ax.transAxes, ha="center", va="center",
            fontsize=7, fontweight="bold", color="#1F252B")


def add_scale_bar(ax, length_m: float = 20_000):
    xmin, xmax = ax.get_xlim(); ymin, ymax = ax.get_ylim()
    x0 = xmin + 0.075 * (xmax - xmin); y0 = ymin + 0.075 * (ymax - ymin)
    ax.plot([x0, x0 + length_m], [y0, y0], color="#1F252B", lw=2.2, solid_capstyle="butt", zorder=10)
    ax.plot([x0, x0], [y0 - 500, y0 + 500], color="#1F252B", lw=0.8, zorder=10)
    ax.plot([x0 + length_m, x0 + length_m], [y0 - 500, y0 + 500], color="#1F252B", lw=0.8, zorder=10)
    ax.text(x0 + length_m / 2, y0 + 1100, "20 km", ha="center", va="bottom", fontsize=6.2, color="#1F252B")


def plot_map(data: gpd.GeoDataFrame) -> dict:
    boundary = gpd.read_file(BOUNDARY, layer="county_boundary").to_crs(data.crs)
    mpl.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Microsoft YaHei", "Arial", "DejaVu Sans"],
        "font.size": 6.5,
        "axes.linewidth": 0.6,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    })
    panels = [
        ("wheat_mean_yield_kg_ha", "a", "冬小麦", "#2F6F9F"),
        ("maize_mean_yield_kg_ha", "b", "夏玉米", "#C48A32"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(183 / 25.4, 100 / 25.4))
    cmap = mpl.colormaps["YlGnBu"]
    stats = {}
    for ax, (column, label, crop, accent) in zip(axes, panels):
        vmin, vmax = rounded_limits(data[column])
        data.plot(column=column, ax=ax, cmap=cmap, norm=Normalize(vmin=vmin, vmax=vmax),
                  edgecolor="white", linewidth=0.45, antialiased=True)
        boundary.boundary.plot(ax=ax, color="#20252A", linewidth=1.05, zorder=8)
        minx, miny, maxx, maxy = boundary.total_bounds
        padx = (maxx - minx) * 0.045; pady = (maxy - miny) * 0.045
        ax.set_xlim(minx - padx, maxx + padx); ax.set_ylim(miny - pady, maxy + pady)
        ax.set_aspect("equal"); ax.set_axis_off()
        ax.text(0.01, 0.985, label, transform=ax.transAxes, ha="left", va="top",
                fontsize=8.5, fontweight="bold", color="#17324D")
        ax.text(0.075, 0.955, f"{crop}多年平均产量", transform=ax.transAxes, ha="left", va="top",
                fontsize=8.0, fontweight="bold", color=accent)
        ax.text(0.075, 0.905, "2015—2023 · 统计中央施氮", transform=ax.transAxes,
                ha="left", va="top", fontsize=5.7, color="#66717B")
        add_north_arrow(ax); add_scale_bar(ax)
        cax = ax.inset_axes([0.17, -0.005, 0.66, 0.028])
        cb = fig.colorbar(ScalarMappable(norm=Normalize(vmin=vmin, vmax=vmax), cmap=cmap),
                          cax=cax, orientation="horizontal")
        cb.set_label("原始APSIM产量 (kg ha$^{-1}$)", fontsize=5.8, labelpad=1.5)
        cb.ax.tick_params(labelsize=5.3, length=2, width=0.45, pad=1.2)
        cb.outline.set_linewidth(0.45)
        stats[crop] = {
            "min": float(data[column].min()), "max": float(data[column].max()),
            "area_weighted_mean": float(np.average(data[column], weights=data.represented_rotation_area_ha)),
            "color_scale": [float(vmin), float(vmax)],
        }
    fig.suptitle("齐河县冬小麦—夏玉米模拟产量空间异质性", fontsize=9.5, fontweight="bold", y=0.995, color="#17324D")
    fig.text(0.5, 0.018,
             "黑线：齐河县行政边界；白线：10 km格网。颜色为格网内2020轮作掩膜对应区域的面积加权产量；尚未经过乡镇或地块产量空间验证。",
             ha="center", va="bottom", fontsize=5.4, color="#4F5962")
    fig.subplots_adjust(left=0.025, right=0.985, top=0.91, bottom=0.115, wspace=0.10)
    fig.savefig(OUT.with_suffix(".svg"), facecolor="white")
    fig.savefig(OUT.with_suffix(".pdf"), facecolor="white")
    fig.savefig(OUT.with_suffix(".tiff"), dpi=600, facecolor="white", pil_kwargs={"compression": "tiff_lzw"})
    fig.savefig(OUT.with_suffix(".png"), dpi=300, facecolor="white")
    plt.close(fig)
    return stats


def main():
    data = prepare_source()
    stats = plot_map(data)
    qa = {
        "backend": "Python only",
        "figure_contract": {
            "core_conclusion": "Under a common statistically constrained N scenario, Qihe retains simulated within-county yield heterogeneity from soil-weather combinations.",
            "archetype": "quantitative spatial grid",
            "final_size_mm": [183, 100],
            "panels": {"a": "2015-2023 mean winter-wheat yield", "b": "2015-2023 mean summer-maize yield"},
        },
        "scenario": "statistical_constraint_central",
        "initial_n_multiplier": 1.0,
        "years": list(range(2015, 2024)),
        "resolution_m": 10000,
        "grid_cells": int(len(data)),
        "represented_rotation_area_ha": float(data.represented_rotation_area_ha.sum()),
        "yield_statistics": stats,
        "source_data": str(SOURCE.relative_to(ROOT)).replace("\\", "/"),
        "exports": [str(OUT.with_suffix(ext).relative_to(ROOT)).replace("\\", "/") for ext in (".svg", ".pdf", ".tiff", ".png")],
        "image_integrity": "No geometry or yield values were manually altered; grid polygons were filled by area-weighted source values and the official working county boundary was overlaid.",
        "reviewer_risks": [
            "10 km cells are coarse for within-county interpretation.",
            "The 2020 rotation mask is held fixed across 2015-2023.",
            "County-wide fertilizer statistics do not observe crop-specific field management.",
            "No township or field yield observations currently validate the simulated spatial ranking.",
            "Raw APSIM yields are shown without fixed statistical yield correction.",
        ],
    }
    QA.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
