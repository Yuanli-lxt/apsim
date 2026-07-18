"""Prepare 5 km Qihe baseline inputs with AgERA5 and within-grid HWSD splits."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from netCDF4 import Dataset
from pyproj import Transformer
from rasterio.features import geometry_mask
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window, from_bounds
from rasterio.warp import Resampling


ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020"
BASELINE = PILOT / "corrected_baseline"
ROTATION_PATH = PILOT / "qihe_2020_wheat_maize_rotation_mask.tif"
HWSD_RASTER = ROOT / "data" / "raw" / "hwsd" / "HWSD2_RASTER" / "HWSD2.bil"
SOIL_DIR = PILOT / "soil_profiles"
HWSD_DB = ROOT / "data" / "raw" / "hwsd" / "HWSD2_DB" / "HWSD2.mdb"
SOIL_CONVERTER = ROOT / "scripts" / "soil" / "hwsd_to_apsimsoil.py"
AGERA_RAW = ROOT / "data" / "raw" / "shandong_public" / "weather" / "agera5_v2"
WEATHER_DIR = BASELINE / "weather"
YEARS = (2017, 2018, 2019, 2020)
VARIABLES = ("tmax", "tmin", "rain", "radn")


def profile_is_usable(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        layers = json.loads(path.read_text(encoding="utf-8"))["profile"]["layers"]
        for layer in layers:
            values = [float(layer[key]) for key in ("BD", "AirDry", "LL15", "DUL", "SAT", "SoilCNRatio", "PH")]
            if not all(math.isfinite(value) for value in values):
                return False
            if not (0.8 <= values[0] <= 2.0 and 0 < values[1] <= values[2] < values[3] < values[4] < 0.9):
                return False
            if values[5] <= 0 or not 3 <= values[6] <= 10:
                return False
        return True
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def aligned_hwsd(rotation_source: rasterio.DatasetReader) -> np.ndarray:
    with rasterio.open(HWSD_RASTER) as soil_source:
        with WarpedVRT(
            soil_source,
            crs=rotation_source.crs,
            transform=rotation_source.transform,
            width=rotation_source.width,
            height=rotation_source.height,
            resampling=Resampling.nearest,
        ) as vrt:
            return vrt.read(1)


def build_soil_subunits(resolution_m: int = 5000, generate_missing_soils: bool = False) -> pd.DataFrame:
    grid_path = PILOT / "grid_resolution_experiment" / "grids" / f"qihe_2020_rotation_{resolution_m // 1000}km.gpkg"
    if not grid_path.exists():
        raise FileNotFoundError(grid_path)
    grids = gpd.read_file(grid_path).sort_values("grid_id").reset_index(drop=True)
    with rasterio.open(ROTATION_PATH) as source:
        rotation = source.read(1)
        transform = source.transform
        pixel_area_ha = abs(transform.a * transform.e) / 10000.0
        soil = aligned_hwsd(source)
        raster_crs = source.crs
    if grids.crs != raster_crs:
        grids = grids.to_crs(raster_crs)
    to_lonlat = Transformer.from_crs(raster_crs, "OGC:CRS84", always_xy=True)
    records: list[dict] = []

    for grid in grids.itertuples():
        geom = grid.geometry
        raw = from_bounds(*geom.bounds, transform=transform)
        col0 = max(0, math.floor(raw.col_off))
        row0 = max(0, math.floor(raw.row_off))
        col1 = min(rotation.shape[1], math.ceil(raw.col_off + raw.width))
        row1 = min(rotation.shape[0], math.ceil(raw.row_off + raw.height))
        window = Window(col0, row0, col1 - col0, row1 - row0)
        window_transform = rasterio.windows.transform(window, transform)
        inside = geometry_mask(
            [geom], out_shape=(int(window.height), int(window.width)),
            transform=window_transform, invert=True,
        )
        rotation_part = rotation[row0:row1, col0:col1]
        soil_part = soil[row0:row1, col0:col1]
        valid = inside & (rotation_part == 1) & (soil_part > 0)
        units, counts = np.unique(soil_part[valid], return_counts=True)
        mapped_area = float(counts.sum() * pixel_area_ha)
        expected_area = float(grid.rotation_area_ha)
        if not np.isclose(mapped_area, expected_area, atol=max(0.02, pixel_area_ha * 2)):
            raise RuntimeError(
                f"Rotation/soil area mismatch in {grid.grid_id}: mapped={mapped_area}, expected={expected_area}"
            )
        for unit, count in zip(units, counts):
            local_rows, local_cols = np.where(valid & (soil_part == unit))
            # ``xy(..., offset='center')`` adds the half-pixel offset itself.
            mean_row = float(row0 + local_rows.mean())
            mean_col = float(col0 + local_cols.mean())
            x, y = rasterio.transform.xy(transform, mean_row, mean_col, offset="center")
            lon, lat = to_lonlat.transform(x, y)
            area_ha = float(count * pixel_area_ha)
            records.append({
                "subunit_id": f"{grid.grid_id}__HWSD{int(unit)}",
                "grid_id": grid.grid_id,
                "resolution_m": int(grid.grid_size_m),
                "hwsd_soil_unit": int(unit),
                "soil_rotation_area_ha": area_ha,
                "soil_fraction_of_grid_rotation": area_ha / expected_area if expected_area else np.nan,
                "input_longitude": float(lon),
                "input_latitude": float(lat),
                "grid_rotation_area_ha": expected_area,
                "soil_profile_path": str(
                    (SOIL_DIR / f"hwsd_{int(unit)}" / "soil_profile.json").relative_to(ROOT)
                ).replace("\\", "/"),
            })
    frame = pd.DataFrame(records)
    unusable = sorted({
        int(row.hwsd_soil_unit)
        for row in frame.itertuples()
        if not profile_is_usable(ROOT / row.soil_profile_path)
    })
    if unusable and generate_missing_soils:
        for unit in unusable:
            sample = frame.loc[frame.hwsd_soil_unit == unit].iloc[0]
            outdir = SOIL_DIR / f"hwsd_{unit}"
            subprocess.run([
                sys.executable, str(SOIL_CONVERTER),
                "--hwsd-raster", str(HWSD_RASTER),
                "--hwsd-db", str(HWSD_DB),
                "--lon", str(sample.input_longitude),
                "--lat", str(sample.input_latitude),
                "--crop", "Wheat",
                "--outdir", str(outdir),
            ], cwd=ROOT, check=True)
        unusable = [unit for unit in unusable if not profile_is_usable(SOIL_DIR / f"hwsd_{unit}" / "soil_profile.json")]
    if unusable:
        raise RuntimeError(f"Missing/invalid soil profiles: {unusable}")
    checks = frame.groupby("grid_id").agg(
        grid_rotation_area_ha=("grid_rotation_area_ha", "first"),
        split_rotation_area_ha=("soil_rotation_area_ha", "sum"),
        soil_types=("hwsd_soil_unit", "nunique"),
    ).reset_index()
    checks["area_difference_ha"] = checks.split_rotation_area_ha - checks.grid_rotation_area_ha
    BASELINE.mkdir(parents=True, exist_ok=True)
    label = f"{resolution_m // 1000}km"
    frame.to_csv(BASELINE / f"grid_soil_subunits_{label}.csv", index=False, encoding="utf-8-sig")
    checks.to_csv(BASELINE / f"grid_soil_split_checks_{label}.csv", index=False, encoding="utf-8-sig")
    return frame


def read_zip_daily(zip_path: Path, short_name: str, storage: dict) -> tuple[np.ndarray, np.ndarray]:
    with zipfile.ZipFile(zip_path) as archive:
        members = sorted(name for name in archive.namelist() if name.lower().endswith(".nc"))
        if not members:
            raise RuntimeError(f"No NetCDF members in {zip_path}")
        latitudes = longitudes = None
        for name in members:
            match = re.search(r"_(\d{8})_", name)
            if not match:
                raise ValueError(f"Cannot parse AgERA5 date: {name}")
            date = datetime.strptime(match.group(1), "%Y%m%d").date()
            payload = archive.read(name)
            with Dataset("inmemory.nc", memory=payload) as dataset:
                lat = np.asarray(dataset.variables["lat"][:], dtype=float)
                lon = np.asarray(dataset.variables["lon"][:], dtype=float)
                data_names = [
                    key for key, value in dataset.variables.items()
                    if key not in {"lat", "lon", "time", "crs"} and value.dimensions[-2:] == ("lat", "lon")
                ]
                if len(data_names) != 1:
                    raise RuntimeError(f"Unexpected AgERA5 variables in {name}: {data_names}")
                values = np.asarray(dataset.variables[data_names[0]][0, :, :], dtype=float)
                if short_name in {"tmax", "tmin"}:
                    values = values - 273.15
                elif short_name == "radn":
                    values = values / 1_000_000.0
                for row, plat in enumerate(lat):
                    for col, plon in enumerate(lon):
                        storage[(round(float(plat), 4), round(float(plon), 4))][date][short_name] = float(values[row, col])
                latitudes, longitudes = lat, lon
    assert latitudes is not None and longitudes is not None
    return latitudes, longitudes


def build_agera5_met_files() -> tuple[np.ndarray, np.ndarray]:
    storage: dict = defaultdict(lambda: defaultdict(dict))
    latitudes = longitudes = None
    for year in YEARS:
        for short_name in VARIABLES:
            path = AGERA_RAW / f"agera5_v2_qihe_{year}_{short_name}.zip"
            if not path.exists():
                raise FileNotFoundError(
                    f"AgERA5 download missing: {path}; run download_agera5_qihe.py first"
                )
            latitudes, longitudes = read_zip_daily(path, short_name, storage)
    assert latitudes is not None and longitudes is not None
    WEATHER_DIR.mkdir(parents=True, exist_ok=True)
    for (lat, lon), daily in storage.items():
        rows = []
        for date, values in sorted(daily.items()):
            if set(values) != set(VARIABLES):
                raise RuntimeError(f"Incomplete AgERA5 day at {(lat, lon)} {date}: {values.keys()}")
            rows.append({"date": date, **values})
        frame = pd.DataFrame(rows)
        frame["year"] = [value.year for value in frame.date]
        frame["day"] = [int(value.strftime("%j")) for value in frame.date]
        tmean = (frame.tmax + frame.tmin) / 2.0
        monthly = pd.Series(tmean.to_numpy(), index=pd.to_datetime(frame.date)).resample("MS").mean()
        lines = [
            "[weather.met.weather]",
            f"latitude = {lat:.4f} (dec deg)",
            f"longitude = {lon:.4f} (dec deg)",
            f"tav = {float(tmean.mean()):.3f} (oC)",
            f"amp = {float(monthly.max() - monthly.min()):.3f} (oC)",
            "! source = Copernicus AgERA5 v2.0; DOI 10.24381/cds.6c68c9bb",
            "year day radn maxt mint rain",
            "() () (MJ/m2) (oC) (oC) (mm)",
        ]
        for row in frame.itertuples(index=False):
            lines.append(
                f"{row.year} {row.day} {row.radn:.3f} {row.tmax:.3f} {row.tmin:.3f} {max(0.0, row.rain):.3f}"
            )
        (WEATHER_DIR / f"AgERA5_v2_{lat:.1f}_{lon:.1f}_2017_2020.met").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    return np.asarray(latitudes, dtype=float), np.asarray(longitudes, dtype=float)


def existing_agera5_coordinates() -> tuple[np.ndarray, np.ndarray]:
    """Reuse the frozen 2017-2020 AgERA5 files without rewriting them."""
    pattern = re.compile(r"AgERA5_v2_(-?\d+\.\d+)_(-?\d+\.\d+)_2017_2020\.met$")
    coordinates = []
    for path in WEATHER_DIR.glob("AgERA5_v2_*_2017_2020.met"):
        match = pattern.match(path.name)
        if match:
            coordinates.append((float(match.group(1)), float(match.group(2))))
    if not coordinates:
        raise FileNotFoundError(f"No frozen AgERA5 met files found in {WEATHER_DIR}")
    return (
        np.asarray(sorted({lat for lat, _ in coordinates}), dtype=float),
        np.asarray(sorted({lon for _, lon in coordinates}), dtype=float),
    )


def attach_weather(subunits: pd.DataFrame, latitudes: np.ndarray, longitudes: np.ndarray, resolution_m: int = 5000) -> pd.DataFrame:
    weather_lat = []
    weather_lon = []
    weather_path = []
    for row in subunits.itertuples():
        lat = float(latitudes[int(np.argmin(np.abs(latitudes - row.input_latitude)))])
        lon = float(longitudes[int(np.argmin(np.abs(longitudes - row.input_longitude)))])
        weather_lat.append(lat)
        weather_lon.append(lon)
        weather_path.append(str(
            (WEATHER_DIR / f"AgERA5_v2_{lat:.1f}_{lon:.1f}_2017_2020.met").relative_to(ROOT)
        ).replace("\\", "/"))
    result = subunits.copy()
    result["weather_latitude"] = weather_lat
    result["weather_longitude"] = weather_lon
    result["weather_grid_id"] = [f"AgERA5_v2_{lat:.1f}_{lon:.1f}" for lat, lon in zip(weather_lat, weather_lon)]
    result["weather_file_path"] = weather_path
    result["case_id"] = [
        f"HWSD{int(unit)}__AgERA5_{lat:.1f}_{lon:.1f}"
        for unit, lat, lon in zip(result.hwsd_soil_unit, weather_lat, weather_lon)
    ]
    result.to_csv(BASELINE / f"corrected_baseline_units_{resolution_m // 1000}km.csv", index=False, encoding="utf-8-sig")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--soil-only", action="store_true")
    parser.add_argument("--generate-missing-soils", action="store_true")
    parser.add_argument("--reuse-existing-weather", action="store_true",
                        help="Attach the existing frozen AgERA5 met files without rewriting them.")
    parser.add_argument("--resolution-m", type=int, choices=(1000, 2000, 5000, 10000), default=5000)
    args = parser.parse_args()
    subunits = build_soil_subunits(args.resolution_m, args.generate_missing_soils)
    if args.soil_only:
        print(f"soil subunits={len(subunits)}, soils={subunits.hwsd_soil_unit.nunique()}")
        return
    if args.reuse_existing_weather:
        latitudes, longitudes = existing_agera5_coordinates()
    else:
        latitudes, longitudes = build_agera5_met_files()
    units = attach_weather(subunits, latitudes, longitudes, args.resolution_m)
    metadata = {
        "resolution_m": args.resolution_m,
        "rotation_area_ha": float(units.soil_rotation_area_ha.sum()),
        "grid_cells": int(units.grid_id.nunique()),
        "soil_subunits": len(units),
        "soil_units": int(units.hwsd_soil_unit.nunique()),
        "agera5_cells": int(units.weather_grid_id.nunique()),
        "unique_cases": int(units.case_id.nunique()),
        "weather": "AgERA5 v2.0, 0.1 degree, 2017-2020",
        "soil_split": "10 m rotation pixels allocated by aligned HWSD v2 mapping unit",
    }
    (BASELINE / f"corrected_baseline_metadata_{args.resolution_m // 1000}km.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
