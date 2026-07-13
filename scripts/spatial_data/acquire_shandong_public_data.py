"""Acquire no-login public baseline data for the Shandong yield workflow.

Downloads:
1. geoBoundaries China ADM1 and extracts Shandong;
2. NASA POWER daily regional grids needed by APSIM (one CSV per variable/year).

AgERA5, Sentinel-2, SoilGrids and crop maps are tracked in the accompanying
manifest because they require an account, a catalogue-specific client, or
dataset-provider download URLs that may change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import geopandas as gpd
import requests


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "raw" / "shandong_public"
GB_API = "https://www.geoboundaries.org/api/current/gbOpen/CHN/ADM1/"
POWER_API = "https://power.larc.nasa.gov/api/temporal/daily/regional"
POWER_PARAMETERS = ["T2M_MAX", "T2M_MIN", "PRECTOTCORR", "ALLSKY_SFC_SW_DWN"]
# Deliberately wider than Shandong; exact clipping uses the downloaded boundary.
BBOX = {"latitude-min": 34, "latitude-max": 39, "longitude-min": 114, "longitude-max": 123}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=180) as response:
        response.raise_for_status()
        with path.open("wb") as f:
            for block in response.iter_content(1024 * 1024):
                if block:
                    f.write(block)
    return {"path": str(path.relative_to(ROOT)), "url": url, "bytes": path.stat().st_size, "sha256": sha256(path)}


def acquire_boundary(records: list[dict]) -> Path:
    meta = requests.get(GB_API, timeout=60).json()
    china_path = OUT / "boundaries" / "geoBoundaries_CHN_ADM1.geojson"
    records.append(download(meta["gjDownloadURL"], china_path))
    frame = gpd.read_file(china_path)
    name_col = next(c for c in ["shapeName", "NAME_1", "name"] if c in frame.columns)
    mask = frame[name_col].astype(str).str.contains("Shandong|山东", case=False, regex=True)
    if mask.sum() != 1:
        raise RuntimeError(f"Expected one Shandong boundary, found {mask.sum()}; names={frame[name_col].tolist()}")
    shandong = frame.loc[mask].copy().to_crs("EPSG:4326")
    out = OUT / "boundaries" / "shandong_adm1.geojson"
    shandong.to_file(out, driver="GeoJSON")
    records.append({"path": str(out.relative_to(ROOT)), "derived_from": str(china_path.relative_to(ROOT)), "bytes": out.stat().st_size, "sha256": sha256(out)})
    return out


def acquire_power(years: list[int], records: list[dict]) -> None:
    for year in years:
        for parameter in POWER_PARAMETERS:
            query = {
                **BBOX,
                "parameters": parameter,
                "community": "AG",
                "start": f"{year}0101",
                "end": f"{year}1231",
                "format": "CSV",
                "time-standard": "LST",
            }
            url = f"{POWER_API}?{urlencode(query)}"
            path = OUT / "weather" / "nasa_power" / str(year) / f"{parameter}.csv"
            records.append(download(url, path))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="+", type=int, default=[2024, 2025])
    args = parser.parse_args()
    records: list[dict] = []
    acquire_boundary(records)
    acquire_power(args.years, records)
    manifest = OUT / "download_manifest.json"
    manifest.write_text(json.dumps({"years": args.years, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Downloaded {len(records)} files; manifest: {manifest}")


if __name__ == "__main__":
    main()

