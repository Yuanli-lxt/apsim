"""Analyse the Qihe 1 km APSIM test against 10 km and official yields."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib as mpl
import numpy as np
import pandas as pd
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_multiyear_and_resolution import crop_column, metrics, read_apsim_output  # noqa: E402

PILOT = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020"
BASELINE = PILOT / "corrected_baseline"
GRID_DIR = PILOT / "grid_resolution_experiment" / "grids"
BOUNDARY = PILOT / "pilot_county_boundary.gpkg"
OFFICIAL = PILOT / "calibration" / "qihe_2018_2020_official_crop_statistics.csv"
OLD_VALIDATION = ROOT / "outputs" / "spatial" / "county_pilot_2020" / "corrected_baseline" / "multiyear_resolution_validation" / "validation_metadata.json"
TEN_KM_RUN = ROOT / "outputs" / "spatial" / "county_pilot_2020" / "corrected_baseline" / "resolution_10km" / "ordinary_farmer"

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "Arial", "DejaVu Sans", "Liberation Sans"]
plt.rcParams["svg.fonttype"] = "none"
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["font.size"] = 7
plt.rcParams["axes.linewidth"] = 0.7
plt.rcParams["axes.spines.top"] = False
plt.rcParams["axes.spines.right"] = False
plt.rcParams["legend.frameon"] = False

INK = "#272727"
GREY = "#767676"
BLUE = "#3775BA"
TEAL = "#42949E"
ORANGE = "#D98B38"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_resolution(resolution_m: int, scenario_root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    units_path = BASELINE / f"corrected_baseline_units_{resolution_m // 1000}km.csv"
    units = pd.read_csv(units_path)
    rows = []
    for case_id in sorted(units.case_id.unique()):
        path = scenario_root / "cases" / case_id / f"{case_id} Harvest.out"
        if not path.exists():
            raise FileNotFoundError(path)
        harvest = read_apsim_output(path)
        for year in (2018, 2019, 2020):
            annual = harvest.loc[harvest.Date.dt.year == year]
            rows.append({
                "case_id": case_id, "year": year,
                "wheat_yield_kg_ha": float(pd.to_numeric(annual[crop_column(harvest, "wheat")], errors="coerce").fillna(0).sum()),
                "maize_yield_kg_ha": float(pd.to_numeric(annual[crop_column(harvest, "maize")], errors="coerce").fillna(0).sum()),
            })
    cases = pd.DataFrame(rows)
    mapped = units.merge(cases, on="case_id", how="left", validate="many_to_many")
    if mapped[["wheat_yield_kg_ha", "maize_yield_kg_ha"]].isna().any().any():
        raise RuntimeError(f"Missing mapped yields for {resolution_m} m")
    county_rows, grid_rows = [], []
    for year, group in mapped.groupby("year"):
        weights = group.soil_rotation_area_ha
        for crop in ("wheat", "maize"):
            county_rows.append({
                "resolution_m": resolution_m, "year": int(year), "crop": crop,
                "raw_apsim_yield_kg_ha": float(np.average(group[f"{crop}_yield_kg_ha"], weights=weights)),
                "represented_rotation_area_ha": float(weights.sum()),
                "grid_cells": int(group.grid_id.nunique()), "soil_subunits": int(group.subunit_id.nunique()),
                "unique_cases": int(group.case_id.nunique()), "weather_cells": int(group.weather_grid_id.nunique()),
            })
            for grid_id, grid in group.groupby("grid_id"):
                grid_weights = grid.soil_rotation_area_ha
                grid_rows.append({
                    "resolution_m": resolution_m, "year": int(year), "crop": crop, "grid_id": grid_id,
                    "rotation_area_ha": float(grid_weights.sum()),
                    "raw_yield_kg_ha": float(np.average(grid[f"{crop}_yield_kg_ha"], weights=grid_weights)),
                    "soil_types": int(grid.hwsd_soil_unit.nunique()), "weather_cells": int(grid.weather_grid_id.nunique()),
                })
    return pd.DataFrame(county_rows), pd.DataFrame(grid_rows)


def add_map_basics(ax, boundary: gpd.GeoDataFrame, label: str, subtitle: str) -> None:
    boundary.boundary.plot(ax=ax, color=INK, linewidth=0.8, zorder=10)
    minx, miny, maxx, maxy = boundary.total_bounds
    padx, pady = (maxx - minx) * 0.035, (maxy - miny) * 0.035
    ax.set_xlim(minx - padx, maxx + padx); ax.set_ylim(miny - pady, maxy + pady)
    ax.set_aspect("equal"); ax.set_axis_off()
    ax.text(0.01, 0.99, label, transform=ax.transAxes, ha="left", va="top", fontsize=8, fontweight="bold")
    ax.text(0.08, 0.975, subtitle, transform=ax.transAxes, ha="left", va="top", fontsize=7.2, fontweight="bold")


def export_figure(fig, stem: Path) -> list[str]:
    outputs = []
    for suffix, kwargs in [
        (".svg", {}), (".pdf", {}), (".png", {"dpi": 350}),
        (".tiff", {"dpi": 600, "pil_kwargs": {"compression": "tiff_lzw"}}),
    ]:
        path = stem.with_suffix(suffix)
        fig.savefig(path, bbox_inches="tight", facecolor="white", **kwargs)
        outputs.append(str(path.relative_to(ROOT)).replace("\\", "/"))
    plt.close(fig)
    return outputs


def plot_maps(grid_results: pd.DataFrame, factors: dict[str, float], outdir: Path, corrected: bool) -> list[str]:
    data = grid_results.query("year == 2020").copy()
    value = "corrected_yield_kg_ha" if corrected else "raw_yield_kg_ha"
    boundary = gpd.read_file(BOUNDARY).to_crs(gpd.read_file(GRID_DIR / "qihe_2020_rotation_1km.gpkg").crs)
    frames = {}
    for resolution in (1000, 10000):
        label = f"{resolution // 1000}km"
        grid = gpd.read_file(GRID_DIR / f"qihe_2020_rotation_{label}.gpkg")
        if grid.crs != boundary.crs:
            grid = grid.to_crs(boundary.crs)
        frames[resolution] = grid[["grid_id", "geometry"]].merge(
            data.query("resolution_m == @resolution")[["grid_id", "crop", value]],
            on="grid_id", how="left", validate="one_to_many",
        )
    fig, axes = plt.subplots(2, 2, figsize=(183 / 25.4, 150 / 25.4))
    panels = [(1000, "wheat"), (10000, "wheat"), (1000, "maize"), (10000, "maize")]
    letters = "abcd"
    crop_zh = {"wheat": "冬小麦", "maize": "夏玉米"}
    cmap = mpl.colormaps["YlGnBu"]
    norms = {}
    for crop in ("wheat", "maize"):
        values = data.loc[data.crop == crop, value]
        step = 250
        norms[crop] = Normalize(np.floor(values.min() / step) * step, np.ceil(values.max() / step) * step)
    for ax, (resolution, crop), letter in zip(axes.flat, panels, letters):
        frame = frames[resolution].query("crop == @crop")
        frame.plot(column=value, ax=ax, cmap=cmap, norm=norms[crop], edgecolor="white",
                   linewidth=0.05 if resolution == 1000 else 0.35, missing_kwds={"color": "#F1F1EE"})
        add_map_basics(ax, boundary, letter, f"{crop_zh[crop]} · {resolution // 1000} km")
        cax = ax.inset_axes([0.20, -0.01, 0.60, 0.025])
        cb = fig.colorbar(ScalarMappable(norm=norms[crop], cmap=cmap), cax=cax, orientation="horizontal")
        cb.ax.tick_params(labelsize=5.2, length=2); cb.set_label("kg ha$^{-1}$", fontsize=5.5, labelpad=1)
    state = "固定5 km—2020系数校正" if corrected else "原始APSIM（未校正）"
    fig.suptitle(f"齐河县2020年1 km与10 km模拟产量空间对比 · {state}", fontsize=10, fontweight="bold", y=0.99)
    fig.text(0.5, 0.012, "统一作物色标；灰色为无轮作面积格网。细格网仅表示模拟空间异质性，尚无乡镇或地块产量空间验证。",
             ha="center", fontsize=5.5, color=GREY)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.92, bottom=0.08, hspace=0.16, wspace=0.03)
    stem = outdir / ("qihe_2020_yield_map_1km_vs_10km_corrected" if corrected else "qihe_2020_yield_map_1km_vs_10km_raw")
    return export_figure(fig, stem)


def plot_official_comparison(comparison: pd.DataFrame, outdir: Path) -> list[str]:
    fig, axes = plt.subplots(1, 2, figsize=(183 / 25.4, 82 / 25.4), sharex=True)
    crop_zh = {"wheat": "冬小麦", "maize": "夏玉米"}
    for ax, crop, letter in zip(axes, ("wheat", "maize"), "ab"):
        d = comparison.query("crop == @crop").sort_values(["resolution_m", "year"])
        official = d.drop_duplicates("year").sort_values("year")
        ax.plot(official.year, official.official_yield_kg_ha, "o-", color=INK, lw=2.0, ms=4.2, label="正式统计")
        styles = {1000: (BLUE, "o"), 10000: (ORANGE, "s")}
        for resolution, (color, marker) in styles.items():
            r = d.query("resolution_m == @resolution")
            ax.plot(r.year, r.raw_apsim_yield_kg_ha, marker=marker, ls="--", color=color, lw=1.2, ms=3.5,
                    label=f"{resolution // 1000} km 原始")
            ax.plot(r.year, r.corrected_yield_kg_ha, marker=marker, ls="-", color=color, lw=1.8, ms=3.5,
                    label=f"{resolution // 1000} km 固定系数")
        ax.set(title=crop_zh[crop], ylabel="县域面积加权单产 (kg/ha)", xticks=[2018, 2019, 2020])
        ax.grid(axis="y", alpha=0.18); ax.text(-0.08, 1.04, letter, transform=ax.transAxes, fontweight="bold", fontsize=8)
    axes[0].legend(fontsize=6.2, ncol=1, loc="best")
    fig.suptitle("1 km与10 km模拟单产相对正式统计的比较", fontsize=10, fontweight="bold", y=1.01)
    fig.text(0.5, -0.01, "固定系数来源：2020年正式统计 ÷ 2020年5 km原始APSIM；未针对1 km或10 km重新估计。2020为校准年份，2018—2019为跨年验证年份。",
             ha="center", fontsize=5.5, color=GREY)
    fig.tight_layout(pad=1.3)
    return export_figure(fig, outdir / "official_vs_apsim_1km_10km_2018_2020")


def main(args: argparse.Namespace) -> None:
    outdir = args.output.resolve(); figures = outdir / "figures"
    if outdir.exists() and any(outdir.iterdir()) and not args.force:
        raise FileExistsError(f"Refusing to overwrite non-empty output: {outdir}")
    figures.mkdir(parents=True, exist_ok=True)
    one_run = args.one_km_run.resolve() / "ordinary_farmer"
    county1, grids1 = collect_resolution(1000, one_run)
    county10, grids10 = collect_resolution(10000, TEN_KM_RUN)
    county = pd.concat([county1, county10], ignore_index=True)
    grids = pd.concat([grids1, grids10], ignore_index=True)
    official = pd.read_csv(OFFICIAL)[["year", "crop", "crop_zh", "yield_kg_ha"]].rename(columns={"yield_kg_ha": "official_yield_kg_ha"})
    factors = json.loads(OLD_VALIDATION.read_text(encoding="utf-8"))["fixed_factors"]
    comparison = county.merge(official, on=["year", "crop"], how="left", validate="many_to_one")
    comparison["fixed_2020_factor_from_5km"] = comparison.crop.map(factors)
    comparison["corrected_yield_kg_ha"] = comparison.raw_apsim_yield_kg_ha * comparison.fixed_2020_factor_from_5km
    for prefix, column in [("raw", "raw_apsim_yield_kg_ha"), ("corrected", "corrected_yield_kg_ha")]:
        comparison[f"{prefix}_bias_kg_ha"] = comparison[column] - comparison.official_yield_kg_ha
        comparison[f"{prefix}_relative_bias_percent"] = comparison[f"{prefix}_bias_kg_ha"] / comparison.official_yield_kg_ha * 100
        comparison[f"{prefix}_absolute_error_kg_ha"] = comparison[f"{prefix}_bias_kg_ha"].abs()
    grids["fixed_2020_factor_from_5km"] = grids.crop.map(factors)
    grids["corrected_yield_kg_ha"] = grids.raw_yield_kg_ha * grids.fixed_2020_factor_from_5km
    comparison.to_csv(outdir / "annual_yield_comparison_1km_10km.csv", index=False, encoding="utf-8-sig")
    grids.to_csv(outdir / "grid_cell_annual_yields_1km_10km.csv", index=False, encoding="utf-8-sig")

    metric_rows = []
    for (resolution, crop), group in comparison.query("year in [2018, 2019]").groupby(["resolution_m", "crop"]):
        metric_rows.append({"resolution_m": int(resolution), "crop": crop, "validation_years": "2018-2019",
                            **{f"raw_{k}": v for k, v in metrics(group, "raw_apsim_yield_kg_ha").items()},
                            **{f"corrected_{k}": v for k, v in metrics(group, "corrected_yield_kg_ha").items()}})
    pd.DataFrame(metric_rows).to_csv(outdir / "official_validation_metrics_1km_10km.csv", index=False, encoding="utf-8-sig")

    wide = comparison.pivot(index=["year", "crop"], columns="resolution_m", values=["raw_apsim_yield_kg_ha", "corrected_yield_kg_ha"]).reset_index()
    resolution_rows = []
    for _, row in wide.iterrows():
        item = {"year": int(row[("year", "")]), "crop": row[("crop", "")]}
        for prefix in ("raw_apsim_yield_kg_ha", "corrected_yield_kg_ha"):
            one, ten = row[(prefix, 1000)], row[(prefix, 10000)]
            short = "raw" if prefix.startswith("raw") else "corrected"
            item.update({f"{short}_1km_kg_ha": one, f"{short}_10km_kg_ha": ten,
                         f"{short}_10km_minus_1km_kg_ha": ten - one,
                         f"{short}_10km_minus_1km_percent": (ten - one) / one * 100})
        resolution_rows.append(item)
    pd.DataFrame(resolution_rows).to_csv(outdir / "resolution_10km_vs_1km.csv", index=False, encoding="utf-8-sig")

    spatial_rows = []
    for (resolution, year, crop), group in grids.groupby(["resolution_m", "year", "crop"]):
        weights, values = group.rotation_area_ha.to_numpy(), group.raw_yield_kg_ha.to_numpy()
        mean = float(np.average(values, weights=weights)); sd = float(np.sqrt(np.average((values - mean) ** 2, weights=weights)))
        spatial_rows.append({"resolution_m": int(resolution), "year": int(year), "crop": crop, "grid_cells": len(group),
                             "area_weighted_mean_kg_ha": mean, "area_weighted_sd_kg_ha": sd,
                             "area_weighted_cv_percent": sd / mean * 100, "minimum_grid_yield_kg_ha": float(values.min()),
                             "maximum_grid_yield_kg_ha": float(values.max())})
    pd.DataFrame(spatial_rows).to_csv(outdir / "spatial_variability_1km_10km.csv", index=False, encoding="utf-8-sig")

    for resolution in (1000, 10000):
        label = f"{resolution // 1000}km"
        geometry = gpd.read_file(GRID_DIR / f"qihe_2020_rotation_{label}.gpkg")[["grid_id", "geometry"]]
        layer = geometry.merge(grids.query("resolution_m == @resolution"), on="grid_id", how="inner", validate="one_to_many")
        layer.to_file(outdir / "grid_yields_2018_2020.gpkg", layer=f"yield_{label}", driver="GPKG")

    exports = {
        "raw_map": plot_maps(grids, factors, figures, corrected=False),
        "corrected_map": plot_maps(grids, factors, figures, corrected=True),
        "official_comparison": plot_official_comparison(comparison, figures),
    }
    status = pd.read_csv(one_run / "case_run_status.csv")
    contract = {
        "core_conclusion": "At county mean, 1 km and 10 km APSIM yields are nearly identical, while 1 km exposes finer simulated heterogeneity but does not remove bias against official yields.",
        "archetype": "image plate + quant", "backend": "Python only", "final_width_mm": 183,
        "panel_logic": {"maps": "shared-scale 1 km versus 10 km spatial evidence", "lines": "official-yield validation evidence"},
        "reviewer_risks": ["No township or field yield observations validate the 1 km spatial ranking.",
                             "AgERA5 and HWSD are coarser than 1 km; fine cells do not imply 1 km input accuracy.",
                             "The 2020 rotation mask and uniform management are held fixed for 2018-2020.",
                             "The yield factor was calibrated at 5 km in 2020 and is not an independent 2020 validation."],
    }
    metadata = {
        "status": "success", "resolution_m": [1000, 10000], "years": [2018, 2019, 2020],
        "one_km_cases": int(len(status)), "one_km_successes": int((status.status == "success").sum()),
        "one_km_grid_cells": int(grids1.grid_id.nunique()), "one_km_soil_subunits": int(pd.read_csv(BASELINE / "corrected_baseline_units_1km.csv").subunit_id.nunique()),
        "represented_rotation_area_ha": float(county1.represented_rotation_area_ha.iloc[0]),
        "fixed_factors_from_2020_5km": factors, "figure_contract": contract, "exports": exports,
        "sha256": {"one_km_units": sha256(BASELINE / "corrected_baseline_units_1km.csv"),
                    "analysis_script": sha256(Path(__file__)), "official_statistics": sha256(OFFICIAL)},
    }
    (outdir / "analysis_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--one-km-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    main(parse_args())
