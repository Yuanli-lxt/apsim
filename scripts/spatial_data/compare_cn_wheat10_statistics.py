"""Compare 2024 CN_Wheat10 harvested area with Shandong city statistics.

Figure contract
---------------
Claim: CN_Wheat10 reproduces the broad city-level wheat-area gradient, while
city residuals reveal where the crop mask should not be treated as exact truth.
Evidence: (A) spatial wheat fraction, (B) city area agreement, (C) residual map.
Archetype: image plate + quantitative validation.
Review risk: this validates harvested area, not pixel yield or APSIM yield.
Exports: editable SVG/PDF, 600-dpi TIFF, and a PNG preview.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from pyproj import Geod
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds
from sklearn.metrics import mean_absolute_error, r2_score


ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "shandong_public"
PROCESSED = ROOT / "data" / "processed" / "spatial"
FIGURES = ROOT / "outputs" / "figures" / "shandong_public_validation"
STATS_XLS = RAW / "statistics" / "shandong_yearbook_2025_table_13-09.xls"
CITY_GEOJSON = RAW / "boundaries" / "shandong_prefecture_datav.geojson"
WHEAT_10M = RAW / "crop_masks" / "cn_wheat10" / "CN-Wheat10_2024" / "CN-Wheat10_2024_WW_H.tif"


def configure_plotting() -> None:
    mpl.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Microsoft YaHei", "Arial", "DejaVu Sans", "sans-serif"],
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
        "font.size": 7,
        "axes.spines.right": False,
        "axes.spines.top": False,
        "axes.linewidth": 0.7,
        "legend.frameon": False,
    })


def read_statistics() -> pd.DataFrame:
    raw = pd.read_excel(STATS_XLS, header=None)
    out = raw.iloc[12:29, [0, 1, 12, 13, 14, 27, 28, 29]].copy()
    out.columns = [
        "city_cn", "city_en", "wheat_area_stat_ha", "wheat_production_t",
        "wheat_yield_kg_ha", "maize_area_stat_ha", "maize_production_t",
        "maize_yield_kg_ha",
    ]
    out["city_cn"] = out["city_cn"].astype(str).str.replace(" ", "", regex=False)
    out = out[out["city_cn"].str.endswith("市")].copy()
    for col in out.columns[2:]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.reset_index(drop=True)


def aggregate_wheat_area(cities: gpd.GeoDataFrame) -> pd.DataFrame:
    """Count class-1 pixels by city using block windows and geodesic pixel area.

    CN_Wheat10 uses value 1 for wheat and 255 outside the class. Treating 255 as
    raster nodata during average resampling would turn every mixed cell into
    100% wheat, so area is computed explicitly at native resolution.
    """
    cities_ll = cities.to_crs("EPSG:4326")
    geod = Geod(ellps="WGS84")
    rows = []
    with rasterio.open(WHEAT_10M) as src:
        dx, dy = abs(src.transform.a), abs(src.transform.e)
        for _, city in cities_ll.iterrows():
            raw_window = from_bounds(*city.geometry.bounds, transform=src.transform)
            col0 = max(0, int(np.floor(raw_window.col_off)))
            row0 = max(0, int(np.floor(raw_window.row_off)))
            col1 = min(src.width, int(np.ceil(raw_window.col_off + raw_window.width)))
            row1 = min(src.height, int(np.ceil(raw_window.row_off + raw_window.height)))
            area_m2 = 0.0
            block = 2048
            for r0 in range(row0, row1, block):
                h = min(block, row1 - r0)
                for c0 in range(col0, col1, block):
                    w = min(block, col1 - c0)
                    win = Window(c0, r0, w, h)
                    arr = src.read(1, window=win)
                    if not np.any(arr == 1):
                        continue
                    tr = src.window_transform(win)
                    inside = geometry_mask([city.geometry], out_shape=arr.shape,
                                           transform=tr, invert=True)
                    wheat = (arr == 1) & inside
                    if not wheat.any():
                        continue
                    # Pixel area is effectively constant along a raster row.
                    for rr in np.flatnonzero(wheat.any(axis=1)):
                        lat_top = tr.f + rr * tr.e
                        lat_bottom = lat_top + tr.e
                        lon_left = tr.c
                        pixel_area, _ = geod.polygon_area_perimeter(
                            [lon_left, lon_left + dx, lon_left + dx, lon_left],
                            [lat_top, lat_top, lat_bottom, lat_bottom],
                        )
                        area_m2 += wheat[rr].sum() * abs(pixel_area)
            area_ha = area_m2 / 10_000
            rows.append({"city_cn": city["name"], "wheat_area_map_ha": area_ha})
    return pd.DataFrame(rows)


def save_figure(data: gpd.GeoDataFrame) -> None:
    configure_plotting()
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(7.2, 3.8), constrained_layout=True)
    gs = fig.add_gridspec(1, 3, width_ratios=[1.25, 1, 1])
    ax0, ax1, ax2 = [fig.add_subplot(gs[0, i]) for i in range(3)]

    data.plot(column="wheat_area_map_ha", cmap="YlGn", linewidth=0.45,
              edgecolor="white", legend=True, ax=ax0,
              legend_kwds={"label": "CN_Wheat10面积 (ha)", "shrink": 0.62})
    ax0.set_title("a  2024年冬小麦空间分布", loc="left", weight="bold")
    ax0.set_axis_off()

    x = data["wheat_area_stat_ha"].to_numpy(float) / 1e4
    y = data["wheat_area_map_ha"].to_numpy(float) / 1e4
    lim = max(x.max(), y.max()) * 1.06
    ax1.scatter(x, y, s=24, color="#287D5B", edgecolor="white", linewidth=0.5)
    ax1.plot([0, lim], [0, lim], ls="--", lw=0.9, color="#666666")
    label_cities = {"德州市", "菏泽市", "聊城市", "青岛市", "临沂市", "日照市"}
    for _, row in data[data["city_cn"].isin(label_cities)].iterrows():
        ax1.annotate(row["city_cn"].replace("市", ""),
                     (row["wheat_area_stat_ha"] / 1e4, row["wheat_area_map_ha"] / 1e4),
                     xytext=(2, 2), textcoords="offset points", fontsize=5.4)
    ax1.set(xlim=(0, lim), ylim=(0, lim), xlabel="统计播种面积 (万ha)",
            ylabel="CN_Wheat10面积 (万ha)")
    r2 = r2_score(x, y)
    mae = mean_absolute_error(x, y)
    bias = float(np.mean(y - x))
    ax1.text(0.04, 0.96, f"$R^2$={r2:.2f}\nMAE={mae:.2f} 万ha\nBias={bias:+.2f} 万ha",
             transform=ax1.transAxes, va="top")
    ax1.set_title("b  市级面积一致性", loc="left", weight="bold")

    vmax = float(np.nanmax(np.abs(data["area_error_pct"])))
    data.plot(column="area_error_pct", cmap="RdBu_r", vmin=-vmax, vmax=vmax,
              linewidth=0.45, edgecolor="white", legend=True, ax=ax2,
              legend_kwds={"label": "面积相对误差 (%)", "shrink": 0.62})
    ax2.set_title("c  市级面积偏差", loc="left", weight="bold")
    ax2.set_axis_off()

    fig.suptitle("CN_Wheat10与山东统计年鉴：2024年冬小麦面积对比", fontsize=9, weight="bold")
    base = FIGURES / "cn_wheat10_vs_city_statistics_2024"
    fig.savefig(base.with_suffix(".svg"), bbox_inches="tight")
    fig.savefig(base.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(base.with_suffix(".tiff"), dpi=600, bbox_inches="tight")
    fig.savefig(base.with_suffix(".png"), dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    stats = read_statistics()
    cities = gpd.read_file(CITY_GEOJSON)
    areas = aggregate_wheat_area(cities)
    table = stats.merge(areas, on="city_cn", how="left", validate="one_to_one")
    table["area_error_ha"] = table["wheat_area_map_ha"] - table["wheat_area_stat_ha"]
    table["area_error_pct"] = 100 * table["area_error_ha"] / table["wheat_area_stat_ha"]
    out_csv = PROCESSED / "shandong_city_crop_statistics_and_cn_wheat10_2024.csv"
    table.to_csv(out_csv, index=False, encoding="utf-8-sig")
    mapped = cities.merge(table, left_on="name", right_on="city_cn", how="left")
    mapped.to_file(PROCESSED / "shandong_city_wheat_validation_2024.gpkg", driver="GPKG")
    save_figure(mapped)
    print(table[["city_cn", "wheat_area_stat_ha", "wheat_area_map_ha", "area_error_pct"]].to_string(index=False))
    print(f"Saved: {out_csv}")


if __name__ == "__main__":
    main()
