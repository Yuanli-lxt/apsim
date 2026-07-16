"""Plot Qihe 2020 wheat-maize rotation grids at 1, 5 and 10 km."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.font_manager import FontProperties, findfont


ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020"
GRID_DIR = PILOT / "grid_resolution_experiment" / "grids"
COUNTY_PATH = PILOT / "pilot_county_boundary.gpkg"
OUT_DIR = ROOT / "outputs" / "spatial" / "county_pilot_2020"
STEM = OUT_DIR / "qihe_2020_rotation_grid_1km_5km_10km"


def chinese_font() -> FontProperties:
    for family in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Arial Unicode MS"):
        try:
            path = findfont(FontProperties(family=family), fallback_to_default=False)
        except ValueError:
            continue
        if Path(path).exists():
            return FontProperties(fname=path)
    raise RuntimeError("No Chinese-capable font was found for the selected Python backend")


def add_north_arrow(ax: plt.Axes, font: FontProperties) -> None:
    ax.annotate(
        "N", xy=(0.93, 0.92), xytext=(0.93, 0.78), xycoords="axes fraction",
        ha="center", va="center", fontproperties=font, fontsize=7.2,
        arrowprops={"arrowstyle": "-|>", "color": "#252525", "lw": 0.8},
    )


def add_scale_bar(ax: plt.Axes, font: FontProperties, length_km: int = 10) -> None:
    xmin, xmax = ax.get_xlim()
    ymin, ymax = ax.get_ylim()
    x0 = xmin + 0.07 * (xmax - xmin)
    y0 = ymin + 0.055 * (ymax - ymin)
    length = length_km * 1000
    ax.plot([x0, x0 + length], [y0, y0], color="#252525", lw=1.7, solid_capstyle="butt")
    for x in (x0, x0 + length):
        ax.plot([x, x], [y0 - 240, y0 + 240], color="#252525", lw=0.65)
    ax.text(x0 + length / 2, y0 + 420, f"{length_km} km", ha="center", va="bottom",
            fontproperties=font, fontsize=5.8, color="#252525")


def main() -> None:
    font = chinese_font()
    mpl.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": [font.get_name(), "Arial", "Helvetica", "DejaVu Sans"],
        "font.size": 6.5,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
        "axes.linewidth": 0.7,
        "legend.frameon": False,
    })
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    county = gpd.read_file(COUNTY_PATH)
    grids = {}
    for km in (1, 5, 10):
        path = GRID_DIR / f"qihe_2020_rotation_{km}km.gpkg"
        if not path.exists():
            raise FileNotFoundError(path)
        frame = gpd.read_file(path)
        # Raster/vector edge discretisation can produce fractions a few per-mille
        # outside [0, 1]; retain raw data in the GeoPackage and clip only display.
        frame["rotation_fraction_display"] = frame.rotation_fraction.clip(0, 1)
        if frame.crs != county.crs:
            county = county.to_crs(frame.crs)
        grids[km] = frame

    county = gpd.GeoDataFrame(geometry=[county.geometry.union_all()], crs=county.crs)
    xmin, ymin, xmax, ymax = county.total_bounds
    padx, pady = (xmax - xmin) * 0.035, (ymax - ymin) * 0.035
    cmap = mpl.colormaps["YlGnBu"]
    norm = mpl.colors.Normalize(vmin=0, vmax=1)

    fig, axes = plt.subplots(1, 3, figsize=(7.20, 3.25))
    fig.subplots_adjust(left=0.025, right=0.985, top=0.84, bottom=0.19, wspace=0.045)
    edge_width = {1: 0.055, 5: 0.22, 10: 0.36}
    summary = []
    for panel, (ax, km) in enumerate(zip(axes, (1, 5, 10))):
        frame = grids[km]
        county.plot(ax=ax, color="#f1f1ee", edgecolor="none")
        frame.plot(
            column="rotation_fraction_display", ax=ax, cmap=cmap, norm=norm,
            edgecolor="#3d4650", linewidth=edge_width[km],
        )
        county.boundary.plot(ax=ax, color="#171717", linewidth=0.75)
        ax.set_xlim(xmin - padx, xmax + padx)
        ax.set_ylim(ymin - pady, ymax + pady)
        ax.set_aspect("equal")
        ax.set_axis_off()
        ax.text(0.015, 0.985, chr(ord("a") + panel), transform=ax.transAxes,
                ha="left", va="top", fontsize=8, fontweight="bold", color="#111111")
        ax.set_title(f"{km} km 格网", fontproperties=font, fontsize=8.2, pad=3.5, fontweight="bold")
        ax.text(
            0.5, -0.055,
            f"{len(frame):,} 个县域格网  ·  轮作面积 {frame.rotation_area_ha.sum():,.0f} ha",
            transform=ax.transAxes, ha="center", va="top", fontproperties=font,
            fontsize=5.7, color="#444444",
        )
        if panel == 0:
            add_north_arrow(ax, font)
            add_scale_bar(ax, font)
        summary.append({
            "resolution_km": km,
            "county_grid_cells": len(frame),
            "rotation_area_ha": float(frame.rotation_area_ha.sum()),
            "minimum_display_rotation_fraction": float(frame.rotation_fraction_display.min()),
            "maximum_display_rotation_fraction": float(frame.rotation_fraction_display.max()),
            "area_weighted_mean_rotation_fraction": float(
                (frame.rotation_fraction * frame.county_intersection_area_ha).sum()
                / frame.county_intersection_area_ha.sum()
            ),
        })

    fig.suptitle("齐河县2020年小麦—玉米轮作区格网分辨率对比",
                 fontproperties=font, fontsize=10.5, fontweight="bold", y=0.965)
    cax = fig.add_axes([0.325, 0.085, 0.35, 0.025])
    colorbar = fig.colorbar(mpl.cm.ScalarMappable(norm=norm, cmap=cmap), cax=cax, orientation="horizontal")
    colorbar.set_label("格网内轮作面积比例", fontproperties=font, fontsize=6.3, labelpad=2)
    colorbar.ax.xaxis.set_major_formatter(mpl.ticker.PercentFormatter(xmax=1.0))
    colorbar.ax.tick_params(labelsize=5.5, length=2.2, width=0.55, pad=1.2)
    colorbar.outline.set_linewidth(0.55)

    fig.savefig(f"{STEM}.svg", bbox_inches="tight", facecolor="white")
    fig.savefig(f"{STEM}.pdf", bbox_inches="tight", facecolor="white")
    fig.savefig(f"{STEM}.png", dpi=400, bbox_inches="tight", facecolor="white")
    fig.savefig(f"{STEM}.tiff", dpi=600, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    pd.DataFrame(summary).to_csv(f"{STEM}_source_data.csv", index=False, encoding="utf-8-sig")
    qa = {
        "core_conclusion": "Grid aggregation increases from 1 to 10 km while preserving county rotation area.",
        "archetype": "image plate + quant",
        "backend": "Python/geopandas/matplotlib only",
        "final_size_inches": [7.20, 3.25],
        "shared_projection": str(grids[1].crs),
        "shared_extent": [float(xmin - padx), float(ymin - pady), float(xmax + padx), float(ymax + pady)],
        "editable_text": {"svg": True, "pdf_fonttype": 42},
        "area_check_ha": {str(item["resolution_km"]): item["rotation_area_ha"] for item in summary},
    }
    Path(f"{STEM}_qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"outputs": [f"{STEM}{suffix}" for suffix in ('.svg', '.pdf', '.png', '.tiff')], "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
