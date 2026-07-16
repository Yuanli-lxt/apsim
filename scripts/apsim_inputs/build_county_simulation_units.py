"""Convert county grid crop fractions into APSIM representative units."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio


ROOT = Path(__file__).resolve().parents[2]
PILOT_YEAR = 2020
PILOT = ROOT / "data" / "processed" / "spatial" / f"county_pilot_{PILOT_YEAR}"
FRACTIONS = PILOT / "grid_crop_fractions.gpkg"
BOUNDARY = PILOT / "pilot_county_boundary.gpkg"
HWSD_RASTER = ROOT / "data" / "raw" / "hwsd" / "HWSD2_RASTER" / "HWSD2.bil"
HWSD_DB = ROOT / "data" / "raw" / "hwsd" / "HWSD2_DB" / "HWSD2.mdb"
SOIL_CONVERTER = ROOT / "scripts" / "soil" / "hwsd_to_apsimsoil.py"
TEMPLATE = ROOT / "models" / "apsim_classic" / "modified_from_truth.apsim"
WEATHER_DIR = ROOT / "data" / "raw" / "shandong_public" / "weather" / "nasa_power"


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def hwsd_at_points(frame: gpd.GeoDataFrame) -> np.ndarray:
    coords = list(zip(frame.longitude, frame.latitude))
    with rasterio.open(HWSD_RASTER) as src:
        return np.array([int(value[0]) for value in src.sample(coords)])


def weather_cell(lon: float, lat: float) -> tuple[str, float, float]:
    # POWER regional CSV coordinates are on a 0.5-degree grid for this request.
    wlon, wlat = round(lon * 2) / 2, round(lat * 2) / 2
    return f"NASA_POWER_{wlat:.1f}_{wlon:.1f}", wlon, wlat


def generate_soils(frame: gpd.GeoDataFrame) -> dict[int, Path]:
    paths: dict[int, Path] = {}
    for unit, group in frame.groupby("hwsd_soil_unit"):
        sample = group.iloc[0]
        outdir = PILOT / "soil_profiles" / f"hwsd_{int(unit)}"
        profile = outdir / "soil_profile.json"
        if not profile.exists():
            command = [sys.executable, str(SOIL_CONVERTER), "--hwsd-raster", str(HWSD_RASTER),
                       "--hwsd-db", str(HWSD_DB), "--lon", str(sample.longitude),
                       "--lat", str(sample.latitude), "--crop", "Wheat", "--outdir", str(outdir)]
            subprocess.run(command, check=True)
        paths[int(unit)] = profile
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-area-ha", type=float, default=1.0)
    parser.add_argument("--generate-soil-profiles", action="store_true")
    args = parser.parse_args()
    fractions = gpd.read_file(FRACTIONS, layer="grid_crop_fractions")
    fractions["hwsd_soil_unit"] = hwsd_at_points(fractions)
    soil_paths = generate_soils(fractions) if args.generate_soil_profiles else {}
    soil_index = fractions[["grid_id", "longitude", "latitude", "hwsd_soil_unit"]].copy()
    soil_index["sampling_method"] = "grid centre; major within-cell soil fractions not yet split"
    soil_index.to_csv(PILOT / "grid_hwsd_soil_index.csv", index=False, encoding="utf-8-sig")

    specs = [
        ("wheat", "wheat_area_ha", "wheat_fraction", "05-Oct to 31-Dec; must sow", "Jimai70_v132_joint_iter353"),
        ("maize", "maize_area_ha", "maize_fraction", "10-Jun to 20-Jun; must sow", "P01_shandong_2025_v503_joint_iter60"),
        ("wheat_maize_rotation", "wheat_maize_rotation_area_ha", "rotation_fraction",
         "continuous sequence: wheat window 05-Oct–31-Dec then maize 10-Jun–20-Jun",
         "Jimai70_v132_joint_iter353 + P01_shandong_2025_v503_joint_iter60"),
    ]
    rows: list[dict] = []
    for _, grid in fractions.iterrows():
        weather_id, weather_lon, weather_lat = weather_cell(grid.longitude, grid.latitude)
        unit = int(grid.hwsd_soil_unit)
        profile = soil_paths.get(unit, PILOT / "soil_profiles" / f"hwsd_{unit}" / "soil_profile.json")
        for system, area_col, fraction_col, sowing, cultivar in specs:
            area = grid[area_col]
            if pd.isna(area) or float(area) < args.min_area_ha:
                continue
            # Wheat and maize are marginal crop areas; rotation is their spatial
            # intersection.  Do not sum all three represented areas together.
            quality = str(grid.quality_flag)
            quality += ";MANAGEMENT_AND_CULTIVAR_TRANSFERRED_FROM_EXISTING_TEMPLATE;NOT_YEAR_SPECIFIC"
            if system == "wheat" and pd.isna(grid.maize_area_ha):
                quality += ";WHEAT_OCCURRENCE_ROTATION_STATUS_UNKNOWN"
            if not profile.exists():
                quality += ";SOIL_PROFILE_PENDING"
            simulation_id = f"{grid.grid_id}__{system}__HWSD{unit}"
            rows.append({
                "simulation_id": simulation_id, "grid_id": grid.grid_id,
                "county_code": str(grid.county_code), "county_name": grid.county_name,
                "longitude": grid.longitude, "latitude": grid.latitude,
                "crop_system": system, "crop_fraction": grid[fraction_col],
                "crop_fraction_denominator": "county_intersection_area_ha",
                "represented_area_ha": float(area),
                "represented_area_semantics": "marginal crop occurrence" if system != "wheat_maize_rotation" else "wheat-maize pixel intersection",
                "weather_source": "NASA POWER daily regional CSV (pilot fallback; AgERA5 preferred later)",
                "weather_grid_id": weather_id, "weather_longitude": weather_lon, "weather_latitude": weather_lat,
                "weather_file_path": f"{rel(WEATHER_DIR / '2019')}|{rel(WEATHER_DIR / '2020')}",
                "soil_source": "HWSD v2.0", "hwsd_soil_unit": unit,
                "soil_assignment_method": "grid-centre HWSD cell; not soil-area split",
                "soil_profile_path": rel(profile), "apsim_soil_node_index": f"HWSD2_MU_{unit}",
                "sowing_rule": sowing, "sowing_rule_source": rel(TEMPLATE), "cultivar": cultivar,
                "simulation_start": "2019-10-01", "simulation_end": "2020-12-30",
                "apsim_template_path": rel(TEMPLATE),
                "output_file_path": f"outputs/spatial/county_pilot_{PILOT_YEAR}/apsim_runs/{simulation_id}.out",
                "quality_flag": quality,
            })
    out = pd.DataFrame(rows)
    out.to_csv(PILOT / "apsim_simulation_units.csv", index=False, encoding="utf-8-sig")
    print(f"Simulation units: {len(out)}; soil units: {fractions.hwsd_soil_unit.nunique()}")
    print(PILOT / "apsim_simulation_units.csv")


if __name__ == "__main__":
    main()
