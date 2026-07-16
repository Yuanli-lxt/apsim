"""Build non-overwriting 2010-2023 AgERA5 met files for Qihe warm-up tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from netCDF4 import Dataset


ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020"
RAW = ROOT / "data" / "raw" / "shandong_public" / "weather" / "agera5_v2"
SOURCE_UNITS = PILOT / "corrected_baseline" / "corrected_baseline_units_10km.csv"
VARIABLES = ("tmax", "tmin", "rain", "radn")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_zip(zip_path: Path, short_name: str, wanted: set[tuple[float, float]], storage: dict) -> None:
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        members = sorted(name for name in archive.namelist() if name.lower().endswith(".nc"))
        expected = 366 if int(re.search(r"_(\d{4})_", zip_path.name).group(1)) % 4 == 0 else 365
        if len(members) != expected:
            raise RuntimeError(f"Unexpected day count in {zip_path}: {len(members)} != {expected}")
        for name in members:
            match = re.search(r"_(\d{8})_", name)
            if not match:
                raise ValueError(f"Cannot parse AgERA5 date: {name}")
            current = datetime.strptime(match.group(1), "%Y%m%d").date()
            with Dataset("inmemory.nc", memory=archive.read(name)) as dataset:
                lat = np.asarray(dataset.variables["lat"][:], dtype=float)
                lon = np.asarray(dataset.variables["lon"][:], dtype=float)
                data_names = [
                    key for key, value in dataset.variables.items()
                    if key not in {"lat", "lon", "time", "crs"} and value.dimensions[-2:] == ("lat", "lon")
                ]
                if len(data_names) != 1:
                    raise RuntimeError(f"Unexpected variables in {name}: {data_names}")
                values = np.asarray(dataset.variables[data_names[0]][0, :, :], dtype=float)
                for plat, plon in wanted:
                    row = int(np.argmin(np.abs(lat - plat)))
                    col = int(np.argmin(np.abs(lon - plon)))
                    if abs(float(lat[row]) - plat) > 0.011 or abs(float(lon[col]) - plon) > 0.011:
                        raise RuntimeError(f"Weather node {(plat, plon)} absent in {name}")
                    value = float(values[row, col])
                    if short_name in {"tmax", "tmin"}:
                        value -= 273.15
                    elif short_name == "radn":
                        value /= 1_000_000.0
                    storage[(plat, plon)][current][short_name] = value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="qihe_warmup_2010_2023_v1")
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=2023)
    args = parser.parse_args()
    if args.start_year > args.end_year:
        raise ValueError("start year exceeds end year")
    years = list(range(args.start_year, args.end_year + 1))
    run_root = PILOT / "warmup_sensitivity" / args.run_id
    weather_dir = run_root / "weather"
    if run_root.exists() and any(run_root.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty run directory: {run_root}")
    weather_dir.mkdir(parents=True)

    units = pd.read_csv(SOURCE_UNITS)
    wanted = {
        (round(float(row.weather_latitude), 1), round(float(row.weather_longitude), 1))
        for row in units.itertuples()
    }
    storage: dict = defaultdict(lambda: defaultdict(dict))
    inputs = []
    for year in years:
        for variable in VARIABLES:
            path = RAW / f"agera5_v2_qihe_{year}_{variable}.zip"
            read_zip(path, variable, wanted, storage)
            inputs.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(path)})

    expected_days = sum(366 if year % 4 == 0 else 365 for year in years)
    weather_rows = []
    for (lat, lon), daily in sorted(storage.items()):
        if len(daily) != expected_days:
            raise RuntimeError(f"Incomplete dates at {(lat, lon)}: {len(daily)} != {expected_days}")
        rows = []
        for current, values in sorted(daily.items()):
            if set(values) != set(VARIABLES):
                raise RuntimeError(f"Incomplete variables at {(lat, lon)} {current}: {values.keys()}")
            if values["tmax"] < values["tmin"]:
                raise RuntimeError(f"Tmax below Tmin at {(lat, lon)} {current}")
            rows.append({"date": current, **values})
        frame = pd.DataFrame(rows)
        frame["year"] = [item.year for item in frame.date]
        frame["day"] = [int(item.strftime("%j")) for item in frame.date]
        tmean = (frame.tmax + frame.tmin) / 2.0
        monthly = pd.Series(tmean.to_numpy(), index=pd.to_datetime(frame.date)).resample("MS").mean()
        name = f"AgERA5_v2_{lat:.1f}_{lon:.1f}_{args.start_year}_{args.end_year}.met"
        path = weather_dir / name
        lines = [
            "[weather.met.weather]", f"latitude = {lat:.4f} (dec deg)", f"longitude = {lon:.4f} (dec deg)",
            f"tav = {float(tmean.mean()):.3f} (oC)", f"amp = {float(monthly.max()-monthly.min()):.3f} (oC)",
            "! source = Copernicus AgERA5 v2.0; DOI 10.24381/cds.6c68c9bb",
            f"! actual-year series = {args.start_year}-{args.end_year}; no weather recycling",
            "year day radn maxt mint rain", "() () (MJ/m2) (oC) (oC) (mm)",
        ]
        lines.extend(
            f"{row.year} {row.day} {row.radn:.3f} {row.tmax:.3f} {row.tmin:.3f} {max(0.0,row.rain):.3f}"
            for row in frame.itertuples(index=False)
        )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        weather_rows.append({
            "weather_grid_id": f"AgERA5_v2_{lat:.1f}_{lon:.1f}", "latitude": lat, "longitude": lon,
            "days": len(frame), "start_date": str(frame.date.min()), "end_date": str(frame.date.max()),
            "mean_tmax_c": float(frame.tmax.mean()), "mean_tmin_c": float(frame.tmin.mean()),
            "annual_mean_rain_mm": float(frame.groupby("year").rain.sum().mean()),
            "annual_mean_radn_mj_m2": float(frame.groupby("year").radn.sum().mean()),
            "met_path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(path),
        })
    audit = pd.DataFrame(weather_rows)
    audit.to_csv(run_root / "weather_node_audit.csv", index=False, encoding="utf-8-sig")
    path_map = dict(zip(audit.weather_grid_id, audit.met_path))
    units["weather_file_path"] = units.weather_grid_id.map(path_map)
    if units.weather_file_path.isna().any():
        raise RuntimeError("Some 10 km units lack warm-up weather")
    units.to_csv(run_root / "simulation_units_10km.csv", index=False, encoding="utf-8-sig")
    metadata = {
        "run_id": args.run_id, "generated": date.today().isoformat(), "resolution_m": 10000,
        "years": years, "weather_nodes": len(wanted), "days_per_node": expected_days,
        "source_units": str(SOURCE_UNITS.relative_to(ROOT)).replace("\\", "/"), "inputs": inputs,
        "truthfulness_note": "Actual AgERA5 daily sequence is retained for every year and node; no climatology or repeated year is used.",
    }
    (run_root / "weather_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(run_root)


if __name__ == "__main__":
    main()
