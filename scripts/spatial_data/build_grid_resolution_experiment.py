"""Build the Qihe 2020 grid-resolution experiment inputs.

The experiment uses the same 10 m published wheat-maize rotation mask at
1, 2, 5 and 10 km.  Each county-clipped cell receives a representative HWSD
soil unit and the nearest coordinate actually present in the NASA POWER
regional CSV files.  Outputs are a reproducible grid catalogue, a rotation
simulation-unit table and APSIM .met files for the unique weather cells.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio

from build_county_grid_pilot import AREA_CRS
from plot_wheat_maize_grid_comparison import summarize_grid


ROOT = Path(__file__).resolve().parents[2]
YEAR = 2020
PILOT = ROOT / "data" / "processed" / "spatial" / f"county_pilot_{YEAR}"
EXPERIMENT = PILOT / "grid_resolution_experiment"
GRID_DIR = EXPERIMENT / "grids"
WEATHER_DIR = EXPERIMENT / "weather"
SOIL_DIR = PILOT / "soil_profiles"
COUNTY_PATH = PILOT / "pilot_county_boundary.gpkg"
ROTATION_PATH = PILOT / f"qihe_{YEAR}_wheat_maize_rotation_mask.tif"
HWSD_RASTER = ROOT / "data" / "raw" / "hwsd" / "HWSD2_RASTER" / "HWSD2.bil"
HWSD_DB = ROOT / "data" / "raw" / "hwsd" / "HWSD2_DB" / "HWSD2.mdb"
SOIL_CONVERTER = ROOT / "scripts" / "soil" / "hwsd_to_apsimsoil.py"
POWER_DIR = ROOT / "data" / "raw" / "shandong_public" / "weather" / "nasa_power"
POWER_VARIABLES = ("T2M_MAX", "T2M_MIN", "PRECTOTCORR", "ALLSKY_SFC_SW_DWN")
DEFAULT_SIZES_M = (1000, 2000, 5000, 10000)


def read_power_csv(path: Path) -> pd.DataFrame:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle):
            if line.strip() == "-END HEADER-":
                break
        else:
            raise ValueError(f"NASA POWER header terminator not found: {path}")
    return pd.read_csv(path, skiprows=line_number + 1)


def available_power_coordinates() -> np.ndarray:
    frame = read_power_csv(POWER_DIR / str(YEAR) / "T2M_MAX.csv")
    return frame[["LAT", "LON"]].drop_duplicates().to_numpy(dtype=float)


def nearest_power_coordinate(lat: float, lon: float, coordinates: np.ndarray) -> tuple[float, float]:
    # Latitude and longitude degree distances are sufficient for selecting
    # among the immediately adjacent 0.5 x 0.625 degree POWER cells here.
    distance2 = (coordinates[:, 0] - lat) ** 2 + (coordinates[:, 1] - lon) ** 2
    plat, plon = coordinates[int(np.argmin(distance2))]
    return float(plat), float(plon)


def write_met_file(lat: float, lon: float, destination: Path) -> None:
    merged: pd.DataFrame | None = None
    source_coordinates: dict[str, tuple[float, float]] = {}
    for year in (2019, 2020):
        yearly: pd.DataFrame | None = None
        for variable in POWER_VARIABLES:
            source = read_power_csv(POWER_DIR / str(year) / f"{variable}.csv")
            coordinates = source[["LAT", "LON"]].drop_duplicates().to_numpy(dtype=float)
            variable_lat, variable_lon = nearest_power_coordinate(lat, lon, coordinates)
            source_coordinates.setdefault(variable, (variable_lat, variable_lon))
            subset = source.loc[
                np.isclose(source["LAT"], variable_lat) & np.isclose(source["LON"], variable_lon),
                ["YEAR", "DOY", variable],
            ]
            if subset.empty:
                raise ValueError(
                    f"Nearest POWER coordinate ({variable_lat}, {variable_lon}) missing in {year} {variable}"
                )
            yearly = subset if yearly is None else yearly.merge(subset, on=["YEAR", "DOY"], validate="one_to_one")
        merged = yearly if merged is None else pd.concat([merged, yearly], ignore_index=True)

    assert merged is not None
    merged = merged.sort_values(["YEAR", "DOY"]).reset_index(drop=True)
    if (merged[list(POWER_VARIABLES)] <= -900).any().any():
        raise ValueError(f"Missing POWER values at ({lat}, {lon})")
    tmean = (merged["T2M_MAX"] + merged["T2M_MIN"]) / 2.0
    dates = pd.to_datetime(merged["YEAR"].astype(str) + merged["DOY"].astype(str).str.zfill(3), format="%Y%j")
    monthly = pd.Series(tmean.to_numpy(), index=dates).resample("MS").mean()
    tav = float(tmean.mean())
    amp = float(monthly.max() - monthly.min())

    lines = [
        "[weather.met.weather]",
        f"latitude = {lat:.6f} (dec deg)",
        f"longitude = {lon:.6f} (dec deg)",
        f"tav = {tav:.3f} (oC)",
        f"amp = {amp:.3f} (oC)",
        "! source coordinates: " + "; ".join(
            f"{variable}=({coord[0]:.3f},{coord[1]:.3f})"
            for variable, coord in source_coordinates.items()
        ),
        "year day radn maxt mint rain",
        "() () (MJ/m2) (oC) (oC) (mm)",
    ]
    for row in merged.itertuples(index=False):
        lines.append(
            f"{int(row.YEAR)} {int(row.DOY)} {float(row.ALLSKY_SFC_SW_DWN):.3f} "
            f"{float(row.T2M_MAX):.3f} {float(row.T2M_MIN):.3f} {max(0.0, float(row.PRECTOTCORR)):.3f}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def profile_is_usable(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))["profile"]["layers"]
        required = ("BD", "AirDry", "LL15", "DUL", "SAT", "SoilCNRatio", "PH")
        for layer in profile:
            values = [float(layer[key]) for key in required]
            if not all(math.isfinite(value) for value in values):
                return False
            if not (0.8 <= values[0] <= 2.0 and 0 < values[1] <= values[2] < values[3] < values[4] < 0.9):
                return False
            if values[5] <= 0 or not (3 <= values[6] <= 10):
                return False
        return True
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def generate_profile(unit: int, lon: float, lat: float) -> Path:
    outdir = SOIL_DIR / f"hwsd_{unit}"
    command = [
        sys.executable,
        str(SOIL_CONVERTER),
        "--hwsd-raster", str(HWSD_RASTER),
        "--hwsd-db", str(HWSD_DB),
        "--lon", str(lon),
        "--lat", str(lat),
        "--crop", "Wheat",
        "--outdir", str(outdir),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    profile = outdir / "soil_profile.json"
    if not profile_is_usable(profile):
        raise RuntimeError(f"Generated soil profile failed APSIM input checks: {profile}")
    return profile


def sample_hwsd(points: gpd.GeoSeries) -> np.ndarray:
    lonlat = points.to_crs("OGC:CRS84")
    coordinates = list(zip(lonlat.x, lonlat.y))
    with rasterio.open(HWSD_RASTER) as source:
        return np.asarray([int(value[0]) for value in source.sample(coordinates)], dtype=int)


def build(args: argparse.Namespace) -> None:
    for path in (COUNTY_PATH, ROTATION_PATH, HWSD_RASTER, HWSD_DB):
        if not path.exists():
            raise FileNotFoundError(path)
    GRID_DIR.mkdir(parents=True, exist_ok=True)
    WEATHER_DIR.mkdir(parents=True, exist_ok=True)

    county = gpd.read_file(COUNTY_PATH).to_crs(AREA_CRS)
    county = gpd.GeoDataFrame(geometry=[county.geometry.union_all()], crs=AREA_CRS)
    with rasterio.open(ROTATION_PATH) as source:
        rotation = source.read(1)
        transform = source.transform
        if source.crs != county.crs:
            raise RuntimeError("County and rotation raster CRS differ")
        pixel_area_ha = abs(source.transform.a * source.transform.e) / 10000.0

    power_coordinates = available_power_coordinates()
    all_units: list[pd.DataFrame] = []
    soil_examples: dict[int, tuple[float, float]] = {}
    scale_summary: list[dict[str, float | int]] = []

    for size_m in args.sizes_m:
        grid = summarize_grid(county, {"rotation": rotation}, transform, pixel_area_ha, size_m)
        representative = grid.geometry.representative_point()
        representative_lonlat = representative.to_crs("OGC:CRS84")
        grid["input_longitude"] = representative_lonlat.x
        grid["input_latitude"] = representative_lonlat.y
        grid["hwsd_soil_unit"] = sample_hwsd(representative)
        weather = [
            nearest_power_coordinate(lat, lon, power_coordinates)
            for lat, lon in zip(grid.input_latitude, grid.input_longitude)
        ]
        grid["weather_latitude"] = [item[0] for item in weather]
        grid["weather_longitude"] = [item[1] for item in weather]
        grid["weather_grid_id"] = [f"NASA_POWER_{lat:.3f}_{lon:.3f}" for lat, lon in weather]

        label = f"{size_m // 1000}km"
        gpkg = GRID_DIR / f"qihe_{YEAR}_rotation_{label}.gpkg"
        csv = GRID_DIR / f"qihe_{YEAR}_rotation_{label}.csv"
        grid.to_file(gpkg, layer=f"rotation_{label}", driver="GPKG")
        pd.DataFrame(grid.drop(columns="geometry")).to_csv(csv, index=False, encoding="utf-8-sig")

        selected = grid.loc[grid.rotation_area_ha > args.min_area_ha].copy()
        selected["resolution_m"] = size_m
        selected["soil_profile_path"] = selected.hwsd_soil_unit.map(
            lambda unit: str((SOIL_DIR / f"hwsd_{int(unit)}" / "soil_profile.json").relative_to(ROOT)).replace("\\", "/")
        )
        selected["weather_file_path"] = [
            str((WEATHER_DIR / f"NASA_POWER_{lat:.3f}_{lon:.3f}_2019_2020.met").relative_to(ROOT)).replace("\\", "/")
            for lat, lon in zip(selected.weather_latitude, selected.weather_longitude)
        ]
        selected["case_id"] = [
            f"HWSD{int(unit)}__POWER_{lat:.3f}_{lon:.3f}"
            for unit, lat, lon in zip(selected.hwsd_soil_unit, selected.weather_latitude, selected.weather_longitude)
        ]
        selected["simulation_id"] = selected.grid_id + "__rotation"
        all_units.append(pd.DataFrame(selected.drop(columns="geometry")))
        for row in selected.itertuples():
            soil_examples.setdefault(int(row.hwsd_soil_unit), (float(row.input_longitude), float(row.input_latitude)))
        scale_summary.append({
            "resolution_m": size_m,
            "grid_cells": len(grid),
            "simulation_units": len(selected),
            "county_area_ha": float(grid.county_intersection_area_ha.sum()),
            "rotation_area_ha": float(grid.rotation_area_ha.sum()),
            "area_weighted_rotation_fraction": float(grid.rotation_area_ha.sum() / grid.county_intersection_area_ha.sum()),
        })
        print(f"{label}: {len(grid)} cells, {len(selected)} rotation units")

    units = pd.concat(all_units, ignore_index=True)
    units.to_csv(EXPERIMENT / "rotation_simulation_units.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(scale_summary).to_csv(EXPERIMENT / "scale_summary.csv", index=False, encoding="utf-8-sig")

    for lat, lon in units[["weather_latitude", "weather_longitude"]].drop_duplicates().itertuples(index=False):
        met = WEATHER_DIR / f"NASA_POWER_{lat:.3f}_{lon:.3f}_2019_2020.met"
        write_met_file(float(lat), float(lon), met)

    unusable = []
    for unit, (lon, lat) in sorted(soil_examples.items()):
        profile = SOIL_DIR / f"hwsd_{unit}" / "soil_profile.json"
        if not profile_is_usable(profile):
            if args.generate_missing_soils:
                print(f"Generating usable HWSD profile {unit} ...")
                generate_profile(unit, lon, lat)
            else:
                unusable.append(unit)
    if unusable:
        raise RuntimeError(
            "Missing or invalid soil profiles for HWSD units " + ", ".join(map(str, unusable)) +
            "; rerun with --generate-missing-soils"
        )

    metadata = {
        "year": YEAR,
        "resolutions_m": list(args.sizes_m),
        "minimum_rotation_area_ha": args.min_area_ha,
        "grid_origin": "floor(county minimum coordinate / resolution) * resolution",
        "cell_input_point": "representative point of county-clipped grid geometry",
        "soil_assignment": "HWSD v2 raster sampled at cell input point",
        "weather_assignment": "nearest coordinate present in NASA POWER regional CSV",
        "weather_years": [2019, 2020],
        "unique_soil_units": int(units.hwsd_soil_unit.nunique()),
        "unique_weather_cells": int(units.weather_grid_id.nunique()),
        "unique_soil_weather_cases": int(units.case_id.nunique()),
    }
    (EXPERIMENT / "experiment_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Units: {len(units)}; unique APSIM cases: {units.case_id.nunique()}")
    print(EXPERIMENT / "rotation_simulation_units.csv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes-m", nargs="+", type=int, default=list(DEFAULT_SIZES_M))
    parser.add_argument("--min-area-ha", type=float, default=0.0)
    parser.add_argument("--generate-missing-soils", action="store_true")
    args = parser.parse_args()
    args.sizes_m = tuple(sorted(set(args.sizes_m)))
    if any(size <= 0 or size % 1000 for size in args.sizes_m):
        parser.error("--sizes-m values must be positive whole kilometres")
    return args


if __name__ == "__main__":
    build(parse_args())
